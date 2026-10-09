"""Loops in a plan: `repeat <n> times`, `repeat until <condition>`, `repeat until
end`, each closed by `end`. Read, checked against the rules, and run."""
import json
import math
import time
from datetime import timedelta
from pathlib import Path

import pytest

from formslab import config, rscripts
from formslab.sequence import parse_plan
from formslab.sequence.plan import _pair_loops, review
from formslab.sequence.runner import SequenceError
from formslab.sequence.spec import Segment

from test_lab_sequence import fake_time, hold, run, script_dir, start, until, write  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parents[1]


def loop(times=None, cond=None, forever=False):
    return Segment("repeat", {"times": times, "until": cond, "forever": forever},
                   label="repeat " + (f"{times} times" if times else "until end" if forever else "until ..."))


def paired(*segments):
    errors = []
    out = [seg for _, seg in _pair_loops(list(enumerate(segments, 1)), errors)]
    assert errors == []
    return out


def log(message):
    return Segment("log", {"message": message})


def cond(variable, op, value, timeout_s, go_on=False):
    return until(variable, op, value, None, timeout_s, go_on).params


COUNTER = 'def rScript(run):\n    run.publish("N", (run.get("N") or 0) + 1)\n'


# --- reading ---------------------------------------------------------------------------

PLAN = """# Image every umbra, then hold the chamber until the run is ended
load rLACO rOrbit rPSU rSLTA
record every 10 s

orbit replay leo_noon
psu1 ch1 set 5.0 0.5
repeat 10 times
  eclipse within 120 min
  psu1 ch1 on
  slta image
  sunrise within 60 min
  psu1 ch1 off
end
repeat until platenT < 60 C within 2 h
  hvc platen 20
  hold 5 min
end
repeat until end
  hold 1 min
end
"""


def test_each_form_reads_and_pairs_with_its_end():
    segs = parse_plan(PLAN).sequence.segments
    verbs = [(s.verb, s.params.get("end_at"), s.params.get("start_at")) for s in segs]
    assert verbs[2] == ("repeat", 8, None) and verbs[8] == ("end", None, 2)
    assert segs[2].params["times"] == 10 and segs[9].params["until"]["variable"] == "platenT"
    assert segs[9].params["until"]["unit"] == "C" and segs[13].params["forever"] is True
    assert parse_plan(PLAN).sequence.open_ended and review(PLAN)[0] == []


@pytest.mark.parametrize("text, line, message", [
    ("load rPSU\nend\n", 2, "`end` with no `repeat` above it"),
    ("load rPSU\nrepeat 3 times\nhold 1 s\n", 2, "`repeat` with no `end` below it"),
    ("load rPSU\nrepeat 0 times\nhold 1 s\nend\n", 2, "outside 1..10000"),
    ("load rPSU\nrepeat until TC01 > 3 within 1 min\nend\n", 2, "published by rSMTC08; add it to `load`"),
    ("load rLACO\nrepeat until platenT > 3 Torr\nend\n", 2, "platenT is in K, not Torr"),
    ("load rPSU\n" + "repeat 2 times\n" * 9 + "end\n" * 9, 10, "loops nest more than 8 deep"),
])
def test_what_a_loop_must_have(text, line, message):
    errors = review(text)[0]
    assert any(n == line and message in m for n, m in errors), errors


def test_a_broken_repeat_does_not_also_orphan_its_end():
    assert [n for n, _ in review("load rPSU\nrepeat 0 times\nhold 1 s\nend\n")[0]] == [2]


def test_indentation_is_for_reading_only():
    flat = parse_plan("load rPSU\nrepeat 2 times\nhold 1 s\nend\n").sequence.segments
    indented = parse_plan("load rPSU\nrepeat 2 times\n    hold 1 s\nend\n").sequence.segments
    assert flat == indented


# --- in blocks ---------------------------------------------------------------------------

@pytest.fixture
def blocks(tmp_path, monkeypatch):
    from formslab.sequence.plan import ENV
    d = tmp_path / "bench"
    d.mkdir()
    monkeypatch.setenv(ENV, str(d))
    return d


def test_a_block_may_hold_a_loop_and_a_loop_may_call_a_block(blocks):
    (blocks / "blink.block").write_text("# Blink PSU1 CH1\nblock blink <count:number 1..9>\nload rPSU\n\n"
                                        "repeat {count} times\n  psu1 ch1 on\n  hold 1 s\n  psu1 ch1 off\nend\n",
                                        encoding="utf-8")
    segs = parse_plan("load rPSU\nrepeat 2 times\nblink 3\nend\n").sequence.segments
    assert [s.verb for s in segs] == ["repeat", "repeat", "command", "hold", "command", "end", "end"]
    assert segs[1].params == {"times": 3, "until": None, "forever": False, "end_at": 5}
    assert segs[0].params["end_at"] == 6 and segs[5].label == "blink > end"


def test_a_blocks_loop_closes_inside_it(blocks):
    (blocks / "opener.block").write_text("# Open a loop\nblock opener\nload rPSU\n\nrepeat 2 times\nhold 1 s\n",
                                         encoding="utf-8")
    errors = review("load rPSU\nopener\nend\n")[0]
    assert (2, "In opener: a `repeat` with no `end`; a block's loops close inside it") in errors


def test_a_block_cannot_loop_until_the_run_ends(blocks):
    (blocks / "forever.block").write_text("# Hold\nblock forever\nload rPSU\n\nrepeat until end\nhold 1 s\nend\n",
                                          encoding="utf-8")
    assert "cannot `repeat until end`" in review("load rPSU\nforever\n")[0][0][1]


# --- the rules see a loop as its later passes find things ----------------------------------

def test_what_a_pass_undoes_is_caught_on_the_next():
    errors = dict(review("load rLACO\nrepeat 2 times\nhvc vent open\nhvc rough open\nend\n")[0])
    assert errors[3].startswith("Needs Vacuum valve closed (line 4 changed it)")


def test_a_state_set_before_the_loop_and_kept_in_it_holds_on_every_pass():
    text = "load rLACO\nhvc rough close\nhvc gate close\nrepeat 3 times\nhvc vent open\nhold 1 s\nend\n"
    errors, warnings = review(text)
    assert errors == [] and "Vacuum valve" not in dict(warnings)[5]


def test_a_condition_loop_proves_its_condition_after_it():
    text = ("load rLACO\nhvc rough close\nhvc gate close\n"
            "repeat until platenT > 10 C within 1 h\nhold 1 min\nend\n"
            "until platenT < 60 C within 1 h\nuntil shroudT > 10 C within 1 h\n"
            "until shroudT < 60 C within 1 h\nhvc vent open\n")
    assert review(text) == ([], [])


# --- running --------------------------------------------------------------------------------

def test_n_times_runs_n_passes(run, fake_time, script_dir):
    _, sink, go = start(run, paired(loop(times=3), hold(1), Segment("end", {})), fake_time)
    assert go().segment_steps == [4, 4, 4]
    assert [e.verb for e in sink.events if e.kind == "segment_started"] == ["repeat", "hold"] * 3


def test_a_condition_met_at_the_start_runs_no_pass(run, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(run):\n    run.publish("N", 5)\n')
    rscripts.load(run, ["rA"])
    run.publish("N", 5)
    _, _, go = start(run, paired(loop(cond=cond("N", ">", 1, 60)), hold(1), Segment("end", {})), fake_time)
    assert go().segment_steps == []


def test_a_condition_loop_stops_when_the_value_passes(run, fake_time, script_dir):
    write(script_dir, "rA", COUNTER)
    rscripts.load(run, ["rA"])
    _, _, go = start(run, paired(loop(cond=cond("N", ">", 7, 60)), hold(1), Segment("end", {})), fake_time)
    assert go().segment_steps == [4, 4]                  # N is 4 after one pass, 8 after two


def test_a_condition_loop_that_runs_out_of_time_stops_the_run(run, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(run):\n    run.publish("N", 0)\n')
    rscripts.load(run, ["rA"])
    _, _, go = start(run, paired(loop(cond=cond("N", ">", 1, 2.5)), hold(1), Segment("end", {})), fake_time)
    with pytest.raises(SequenceError, match=r"N > 1 not met within 2.5 s, after 3 pass\(es\) \(last 0\)"):
        go()


def test_until_end_loops_until_the_run_is_ended(run, fake_time, script_dir):
    polls = []

    def poll():
        polls.append(1)
        if len(polls) > 20:
            raise SystemExit
    _, sink, go = start(run, paired(loop(forever=True), hold(1), Segment("end", {})), fake_time, poll=poll)
    with pytest.raises(SystemExit):
        go()
    assert sink.events[-1].ended == "operator"


def test_a_pass_that_waits_for_nothing_still_polls(run, fake_time, script_dir):
    """A `repeat until end` of log lines alone must still reach ctrl, or `end` could never stop it."""
    polls = []

    def poll():
        polls.append(1)
        if len(polls) > 3:
            raise SystemExit
    _, _, go = start(run, paired(loop(forever=True), log("tick"), Segment("end", {})), fake_time, poll=poll)
    with pytest.raises(SystemExit):
        go()


def test_nested_loops_count_each_pass(run, fake_time, script_dir):
    segs = paired(loop(times=2), loop(times=3), hold(0.25), Segment("end", {}), Segment("end", {}))
    assert go_steps(run, fake_time, segs) == [1] * 6


def go_steps(run, fake_time, segs):
    _, _, go = start(run, segs, fake_time)
    return go().segment_steps


# --- on the host ---------------------------------------------------------------------------

@pytest.fixture
def bench(tmp_path, monkeypatch):
    from formslab.sequence.plan import ENV
    d = tmp_path / "bench"
    d.mkdir()
    monkeypatch.setenv(ENV, str(d))
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    return d


def events():
    return [json.loads(line) for line in
            (config.run_dir() / "sequence.events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_run_repeats_a_hold_three_times(bench):
    from formslab.host import sequence
    (bench / "thrice.plan").write_text("load rOrbit\n\nrepeat 3 times\n  hold 0.3 s\n  log pass done\nend\n",
                                       encoding="utf-8")
    sequence.channel(plan_path=bench / "thrice.plan")
    ev = events()
    assert ev[-1]["kind"] == "sequence_finished" and ev[-1]["error"] is None
    assert [e["verb"] for e in ev if e["kind"] == "segment_started"] == ["repeat", "hold", "log"] * 3


def test_a_run_loops_until_the_umbra(bench):
    """rOrbit, an orbit 6 s before an umbra, and a loop of 1 s holds until it comes."""
    from formslab.host import sequence
    from formslab.orbit import file as orbitfile
    from formslab.orbit.propagate import kepler

    el = orbitfile.parse((ROOT / "test" / "fixtures" / "plans" / "leo_noon.orbit").read_text(encoding="utf-8"))
    entry = next(a for a, _ in kepler.umbra_spans(el, el.epoch, el.epoch + timedelta(seconds=el.period * 2)))
    epoch = entry - timedelta(seconds=6)
    nu = math.degrees(kepler.true_anomaly(el, epoch))
    lines = [f"epoch {epoch:%Y-%m-%dT%H:%M:%S.%f}Z" if l.startswith("epoch") else f"nu {nu:.6f} deg"
             if l.startswith("nu") else l
             for l in (ROOT / "test" / "fixtures" / "plans" / "leo_noon.orbit").read_text(encoding="utf-8").splitlines()]
    (bench / "near_umbra.orbit").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (bench / "until_umbra.plan").write_text(
        "load rOrbit\n\norbit replay near_umbra\nrepeat until InUmbra = true within 1 min\n"
        "  hold 1 s\nend\nlog umbra\n", encoding="utf-8")
    began = time.monotonic()
    sequence.channel(plan_path=bench / "until_umbra.plan")
    took = time.monotonic() - began
    ev = events()
    passes = sum(1 for e in ev if e["kind"] == "segment_started" and e["verb"] == "repeat")
    assert ev[-1]["error"] is None and 3 <= passes <= 9 and 4 < took < 30
