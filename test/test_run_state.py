"""Where the run is, published by the host for both front ends (run.json): the
step, paused and resumed, ending; gone once the host has stopped. Against the
simulated chamber, with this test as the host."""
import json
import os
import threading
import time
from pathlib import Path

import pytest

from formslab import config, lab, rscripts
from formslab.state.ctrlfile import WriteCommand as ctrl
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import runstate
from formslab.host import sequence as host
from formslab.sequence import plan as planfile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def shipped(monkeypatch):
    """The checkout's own plans, so the end runs the shipped end script."""
    monkeypatch.setattr(planfile, "shipped_dir", lambda: ROOT / "plans")
    monkeypatch.setenv(planfile.ENV, str(ROOT / "plans"))


@pytest.fixture
def chamber(monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    with Simulator() as sim:
        profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0, poll_interval_s=0.5)
        (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
        with sim.state.lock:
            sim.state.mode = "MANUAL"
        yield sim


def _seen(pred, limit=20.0):
    """The first run.json that satisfies `pred`, within `limit` seconds."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        s = runstate.read()
        if s and pred(s):
            return s
        time.sleep(0.05)
    return None


def test_the_host_publishes_its_step_pause_resume_and_end(chamber, tmp_path):
    plan = tmp_path / "t.plan"
    plan.write_text("load rLACO\nrecord every 1 s\nhvc platen 20\nhold until end\n", encoding="utf-8")
    seen = {}

    def operator():
        seen["step"] = _seen(lambda s: (s.get("step") or {}).get("index") == 2)
        ctrl("pause")
        seen["paused"] = _seen(lambda s: s.get("paused") is True)
        ctrl("resume")
        seen["resumed"] = _seen(lambda s: s.get("paused") is False and s.get("step"))
        ctrl("end")
        seen["ending"] = _seen(lambda s: s.get("ending") is True)

    t = threading.Thread(target=operator, daemon=True)
    t.start()
    host.channel(plan_path=plan)
    t.join(30)

    assert seen["step"]["step"] == {"index": 2, "total": 2, "text": "hold until end"}
    assert seen["step"]["plan"] == "t" and seen["step"]["pid"] == os.getpid()
    assert seen["step"]["paused"] is False and seen["step"]["ending"] is False
    assert seen["paused"]["paused"] is True and seen["paused"]["paused_since"]
    assert seen["resumed"]["paused_since"] is None
    assert seen["ending"]["ending"] is True and seen["ending"]["paused"] is False
    assert runstate.read() is None and lab.run_state() is None        # gone with the lock


def test_the_end_of_a_lost_run_is_published_as_ending(chamber, tmp_path):
    plan = tmp_path / "t.plan"
    plan.write_text("load rLACO\nrecord every 1 s\nhold until end\n", encoding="utf-8")
    host.lock_path().write_text("999999\nt\n2026-10-03T12:00:00+00:00\n/x\n", encoding="utf-8")
    runstate.begin({"pid": 999999, "plan": "t", "started": "t", "output": "/x"})
    assert host.lost_run()["plan"] == "t"
    seen = {}
    t = threading.Thread(target=lambda: seen.update(ending=_seen(lambda s: s.get("ending") is True, 60)),
                         daemon=True)
    t.start()
    host.channel(plan_path=plan, end_only=True)
    t.join(5)
    assert seen["ending"]["pid"] == os.getpid() and seen["ending"]["step"] is None
    assert runstate.read() is None and host.read_lock() is None
