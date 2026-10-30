"""The chamber monitor (formslab.host.monitor): formsLAB reads the chamber while no
run is going, and gives the controller up to a run, which it takes one client at
a time. Against the simulated HVC-3500."""
import json
import os
import time

import pytest

from formslab import config, lab, rscripts
from formslab.console.cast.castutils import ReadStatus
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import monitor
from formslab.host import sequence as host


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


@pytest.fixture
def watching(chamber):
    m = monitor.ChamberMonitor()
    m.start()
    yield m
    m.stop()


def wait_for(cond, limit=15.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _timestamp():
    """When the "hvc" block was last published."""
    return float(((lab.read_blocks() or {}).get("hvc") or {}).get("timestamp") or 0)


def test_with_no_run_the_chamber_is_read_and_shown_live(chamber, watching):
    with chamber.state.lock:
        chamber.state.pressure = 3.2
    assert wait_for(lambda: (ReadStatus("hvc") or {}).get("pressure") == pytest.approx(3.2, rel=0.01))
    assert monitor.holder() == os.getpid() and chamber.clients == 1
    blocks = lab.status(log_lines=0)["blocks"]
    assert blocks["hvc"]["live"] and blocks["hvc"].get("watched")


def test_stopping_the_monitor_lets_go_of_the_controller(chamber):
    m = monitor.ChamberMonitor()
    m.start()
    assert wait_for(lambda: (ReadStatus("hvc") or {}).get("connected"))
    m.stop()
    assert monitor.holder() is None and wait_for(lambda: chamber.clients == 0, 3)


def test_a_run_takes_the_controller_from_the_monitor_and_gives_it_back(chamber, watching, tmp_path, monkeypatch):
    real = host.is_host
    monkeypatch.setattr(host, "is_host", lambda pid: pid == os.getpid() or real(pid))   # this test is the host
    assert wait_for(lambda: monitor.holder() is not None)
    plan = tmp_path / "t.plan"
    plan.write_text("load rLACO\nrecord every 1 s\nhvc platen 20\nhold 2 s\n", encoding="utf-8")
    host.channel(plan_path=plan)
    ended = json.loads(host.ended_path().read_text(encoding="utf-8"))
    assert ended["how"] == "complete" and chamber.state.zone_setpoint[1] == 20.0
    assert chamber.peak_clients == 1                              # never the monitor and the run at once
    after = time.time()
    assert wait_for(lambda: _timestamp() > after and (ReadStatus("hvc") or {}).get("connected"))
    assert monitor.holder() == os.getpid()


def test_a_lock_left_by_a_process_that_has_gone_is_not_waited_for(capsys):
    monitor.lock_path().write_text("999999\n", encoding="utf-8")
    t0 = time.monotonic()
    monitor.wait_released(print)
    assert time.monotonic() - t0 < 1 and not monitor.lock_path().exists()
