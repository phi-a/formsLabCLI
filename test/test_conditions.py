"""Conditions in `until` and `repeat until`: numbers with < <= > >=, on/off values
with = or != true or false, the time limit `within`, and `or go on`."""
import pytest

from formslab import rscripts
from formslab.sequence import parse_plan
from formslab.sequence.plan import review
from formslab.sequence.runner import SequenceError
from formslab.sequence.spec import Segment

from test_lab_sequence import fake_time, run, script_dir, start, until, write  # noqa: F401 (fixtures)
from test_loops import cond, loop, paired


def params(line, load="rLACO rPSU rOrbit"):
    return parse_plan(f"load {load}\norbit follow leo_noon\n{line}\n").sequence.segments[1].params


# --- reading -----------------------------------------------------------------------------------

@pytest.mark.parametrize("op", ["<", "<=", ">", ">="])
def test_a_number_takes_each_comparison(op):
    p = params(f"until chamberP {op} 5 within 20 min")
    assert (p["variable"], p["op"], p["value"], p["unit"], p["timeout_s"], p["go_on"]) == \
        ("chamberP", op, 5.0, None, 1200.0, False)


def test_a_temperature_takes_c_or_k():
    assert params("until platenT >= 10 C within 30 s")["unit"] == "C"
    assert params("until platenT <= 333.15 K within 30 s")["unit"] == "K"


@pytest.mark.parametrize("line, op, value", [
    ("until InUmbra = true within 2 h", "=", True),
    ("until InUmbra = false within 2 h", "=", False),
    ("until PSU1_CH1_ON != true within 1 min", "!=", True),
])
def test_an_on_off_value_is_true_or_false(line, op, value):
    p = params(line)
    assert (p["op"], p["value"]) == (op, value) and p["value"] is value


def test_or_go_on():
    assert params("until chamberP < 5 within 20 min or go on")["go_on"] is True
    loop_ = parse_plan("load rOrbit\norbit follow leo_noon\nrepeat until InUmbra = true within 2 h or go on\n"
                       "hold 1 s\nend\n").sequence.segments[1]
    assert loop_.params["until"]["go_on"] is True


@pytest.mark.parametrize("line, message", [
    ("until chamberP above 5 within 20 min", "`above` is no longer a word of a condition: write > (or >=)"),
    ("until chamberP below 5 within 20 min", "`below` is no longer a word of a condition: write < (or <=)"),
    ("until chamberP < 5 timeout 20 min", "`timeout` is no longer a word of a condition: write within"),
    ("until chamberP<5 within 20 min", "write the value, the comparison and the limit apart, with spaces"),
    ("until chamberP = 5 within 20 min", "chamberP is a number, compared with <, <=, > or >="),
    ("until InUmbra > 0.5 within 2 h", "InUmbra is on or off: write InUmbra = true or InUmbra = false"),
    ("until InUmbra = yes within 2 h", "got 'yes'"),
    ("until chamberP < 5", "until needs `within <time> s|min|h`"),
    ("repeat until chamberP < 5\nend", "repeat until needs `within <time> s|min|h`"),
])
def test_what_a_condition_refuses(line, message):
    errors = review(f"load rLACO rOrbit\norbit follow leo_noon\n{line}\n")[0]
    assert any(message in m for _, m in errors), errors


def test_every_shipped_plan_and_block_reads_in_the_new_words():
    from pathlib import Path
    from formslab.sequence.block import review as review_block
    plans = Path(__file__).resolve().parents[1] / "plans"
    for p in plans.glob("*.plan"):
        assert review(p.read_text(encoding="utf-8"))[0] == [], p.name
    for b in plans.glob("*.block"):
        assert review_block(b.read_text(encoding="utf-8"))[0] == [], b.name


# --- running ---------------------------------------------------------------------------------

@pytest.mark.parametrize("op, value, met", [("<", 5, False), ("<=", 5, True), (">", 5, False), (">=", 5, True),
                                            ("<", 6, True), (">", 4, True)])
def test_each_comparison_at_its_boundary(run, fake_time, script_dir, op, value, met):
    write(script_dir, "rA", 'def rScript(run):\n    run.publish("N", 5)\n')
    rscripts.load(run, ["rA"])
    _, _, go = start(run, [until("N", op, float(value), None, 1)], fake_time)
    if met:
        assert go().segment_steps == [1]                      # published on the first loop
    else:
        with pytest.raises(SequenceError, match=rf"N {op} {value} not met within 1 s \(last 5\)"):
            go()


@pytest.mark.parametrize("published, op, want, met", [(1.0, "=", True, True), (0.0, "=", False, True),
                                                      (0.0, "=", True, False), (1.0, "!=", True, False),
                                                      (float("nan"), "=", False, False)])
def test_an_on_off_value_compares_as_true_when_not_zero(run, fake_time, script_dir, published, op, want, met):
    write(script_dir, "rA", f'def rScript(run):\n    run.publish("F", {published!r}, "bool")\n'
                            .replace("nan", 'float("nan")'))
    rscripts.load(run, ["rA"])
    _, _, go = start(run, [until("F", op, want, None, 1)], fake_time)
    if met:
        go()
    else:
        with pytest.raises(SequenceError, match=f"F {op} {str(want).lower()} not met"):
            go()


def test_or_go_on_carries_on_after_an_until(run, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(run):\n    run.publish("N", 0)\n')
    rscripts.load(run, ["rA"])
    _, sink, go = start(run, [until("N", ">", 1.0, None, 1, go_on=True), Segment("log", {"message": "after"})],
                        fake_time)
    assert len(go().segment_steps) == 2 and sink.events[-1].error is None


def test_or_go_on_leaves_a_repeat_until(run, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(run):\n    run.publish("N", 0)\n')
    rscripts.load(run, ["rA"])
    segs = paired(loop(cond=cond("N", ">", 1, 2.5, go_on=True)), Segment("hold", {"seconds": 1.0}),
                  Segment("end", {}), Segment("log", {"message": "after"}))
    _, sink, go = start(run, segs, fake_time)
    assert go().segment_steps == [4, 4, 4, 0] and sink.events[-1].error is None
