"""rOrbit: an orbit file followed during a run, publishing the umbra rSLTA reads,
and the eclipse and sunrise blocks that wait on it."""
import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from formslab.orbit import file as orbitfile
from formslab.orbit.propagate import kepler
from formslab.rscripts import Run
from formslab.sequence.plan import check_text, parse_plan

ROOT = Path(__file__).resolve().parents[1]


def load_rorbit():
    spec = importlib.util.spec_from_file_location("rOrbit_test", ROOT / "rScripts" / "rOrbit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rorbit(monkeypatch):
    """A fresh rOrbit, its CAST calls recorded instead of written."""
    module = load_rorbit()
    module.sent = {"requests": [], "results": [], "status": []}
    monkeypatch.setattr(module, "TakeCommand",
                        lambda label: (module.sent["requests"].pop(0), ["id1"]) if module.sent["requests"] else ({}, []))
    monkeypatch.setattr(module, "ReportResult", lambda label, ids, ok, msgs: module.sent["results"].append((ok, msgs)))
    monkeypatch.setattr(module, "UpdateStatus", lambda label, status: module.sent["status"].append(status))
    return module


def elements(name):
    return orbitfile.parse((ROOT / "plans" / f"{name}.orbit").read_text(encoding="utf-8"))


def test_it_publishes_what_rslta_reads_and_offers_the_orbit_files(rorbit):
    names = [n for n, _ in rorbit.VARIABLES]
    assert {"InUmbra", "UmbraDuration", "UmbraTimeRemaining"} <= set(names)
    patterns = [p for p, _, _ in rorbit.COMMANDS()]
    assert patterns == ["follow <orbit:leo_dawn_dusk|leo_noon>", "replay <orbit:leo_dawn_dusk|leo_noon>"]


def test_in_and_out_of_umbra_as_the_propagator_finds_it():
    el = elements("leo_noon")
    m = load_rorbit()
    module_spans, _ = m.spans_around(el, el.epoch)
    assert module_spans == kepler.umbra_spans(el, el.epoch - timedelta(seconds=el.period),
                                              el.epoch + timedelta(seconds=3 * el.period))
    start, end = next((a, b) for a, b in module_spans if a > el.epoch)

    before = m.where(el, start - timedelta(seconds=60), module_spans)
    assert before["InUmbra"] == 0 and before["UmbraTimeRemaining"] == 0
    assert before["NextUmbra"] == pytest.approx(60, abs=0.2)
    assert before["UmbraDuration"] == pytest.approx((end - start).total_seconds())       # the coming one

    inside = m.where(el, start + timedelta(seconds=1), module_spans)
    assert inside["InUmbra"] == 1 and kepler.umbra_at(el, start + timedelta(seconds=1))
    assert inside["UmbraDuration"] == pytest.approx((end - start).total_seconds())
    assert inside["UmbraTimeRemaining"] == pytest.approx((end - start).total_seconds() - 1, abs=0.2)
    assert 30 * 60 < inside["UmbraDuration"] < 40 * 60                                   # leo_noon: about 35 min
    assert 540 < inside["OrbitAltitude"] < 560 and abs(inside["OrbitBeta"]) < 10


def test_a_dawn_dusk_orbit_never_enters_umbra():
    el = elements("leo_dawn_dusk")
    m = load_rorbit()
    spans, renew = m.spans_around(el, el.epoch)
    assert spans == [] and renew == el.epoch + timedelta(seconds=el.period)
    v = m.where(el, el.epoch, spans)
    assert v["InUmbra"] == 0 and v["UmbraDuration"] == 0 and math.isnan(v["NextUmbra"])


def test_replay_starts_at_the_epoch_and_follow_at_the_wall_clock(rorbit):
    run = Run()
    rorbit.sent["requests"].append({"replay": "leo_noon"})
    rorbit.rScript(run)
    assert rorbit.sent["results"][-1][0] is True and "from its epoch" in rorbit.sent["results"][-1][1][0]
    el = elements("leo_noon")
    assert run.get("InUmbra") == (1 if kepler.umbra_at(el, el.epoch) else 0)
    assert 540 < run.get("OrbitAltitude") < 560 and run.variable("UmbraDuration").unit == "s"
    assert abs((rorbit.rg.offset - (el.epoch - datetime.now(timezone.utc))).total_seconds()) < 5
    rorbit.sent["requests"].append({"follow": "LEO_NOON"})                               # any case, as typed
    rorbit.rScript(run)
    assert rorbit.sent["results"][-1] == (True, ["follow leo_noon"]) and rorbit.rg.offset == timedelta(0)
    assert rorbit.sent["status"][-1]["orbit"] == "leo_noon" and rorbit.sent["status"][-1]["mode"] == "follow"


def test_an_unknown_orbit_is_refused_and_nothing_is_published_until_one_is_chosen(rorbit):
    run = Run()
    rorbit.rScript(run)
    assert run.names() == [] and rorbit.sent["status"][-1] == {"orbit": None, "mode": "none chosen"}
    rorbit.sent["requests"].append({"follow": "nowhere"})
    rorbit.rScript(run)
    assert rorbit.sent["results"] == [(False, ["no orbit file named 'nowhere'"])] and run.names() == []


# --- in a plan ------------------------------------------------------------------------

PLAN = """# wait for an eclipse
load rOrbit rSLTA
record every 10 s

orbit follow leo_noon
slta run on
eclipse within 120
log umbra began
sunrise within 60
"""


def test_a_plan_follows_an_orbit_and_waits_for_the_umbra():
    assert check_text(PLAN) == []
    labels = [s.label for s in parse_plan(PLAN).sequence.segments]
    assert labels[2] == "eclipse > until InUmbra above 0.5 timeout 120 min"
    assert labels[4] == "sunrise > until InUmbra below 0.5 timeout 60 min"


def test_a_run_replays_an_orbit_and_the_eclipse_block_ends_at_the_umbra(tmp_path, monkeypatch):
    """On the host: an orbit whose epoch is 8 s before an umbra, replayed, so the
    eclipse block's wait ends about 8 s in, well inside its one-minute limit."""
    import json
    import time
    from formslab import config, rscripts
    from formslab.host import sequence
    from formslab.sequence.plan import ENV

    el = elements("leo_noon")
    entry = next(a for a, _ in kepler.umbra_spans(el, el.epoch, el.epoch + timedelta(seconds=el.period * 2)))
    epoch = entry - timedelta(seconds=8)
    nu = math.degrees(kepler.true_anomaly(el, epoch))
    text = (ROOT / "plans" / "leo_noon.orbit").read_text(encoding="utf-8").splitlines()
    text = [f"epoch {epoch:%Y-%m-%dT%H:%M:%S.%f}Z" if l.startswith("epoch") else f"nu {nu:.6f} deg"
            if l.startswith("nu") else l for l in text]
    folder = tmp_path / "bench"
    folder.mkdir()
    (folder / "near_umbra.orbit").write_text("\n".join(text) + "\n", encoding="utf-8")
    (folder / "umbra_run.plan").write_text("load rOrbit\nrecord every 1 s\n\norbit replay near_umbra\n"
                                           "eclipse within 1\nlog umbra began\n", encoding="utf-8")
    monkeypatch.setenv(ENV, str(folder))
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()

    began = time.monotonic()
    sequence.channel(plan_path=folder / "umbra_run.plan")
    took = time.monotonic() - began
    events = [json.loads(l) for l in (config.run_dir() / "sequence.events.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = events[-1]
    assert finished["kind"] == "sequence_finished" and finished["error"] is None and 5 < took < 40


def test_the_blocks_need_rorbit_loaded_and_the_orbit_must_exist():
    assert check_text("load rSLTA\neclipse within 5\n") == [(2, "eclipse needs rOrbit; add it to `load`")]
    assert "expected leo_dawn_dusk or leo_noon" in check_text("load rOrbit\norbit follow nowhere\n")[0][1]
