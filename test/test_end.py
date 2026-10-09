"""Ending a run: the shipped end script (plans/end.plan), run by the host with every
routine live when a run is cut short, then each routine's shutdown.

Against the simulated chamber: what End leaves, with the turbo running, during a
fault, and with the chamber unreachable; the chamber's states a plan can confirm;
a waiting request dropped; the shutdown order; and the script itself.
"""
import json
import sys
import threading
from pathlib import Path

import pytest

from formslab import config, rscripts
from formslab.console.cast.castutils import ClearPending, CommandPending, CommandState, WriteCommand
from formslab.console.ctrl.ctrlutils import WriteCommand as ctrl
from formslab.devices.dp832a.wiring import wiring
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import sequence as host
from formslab.sequence import plan as planfile
from formslab.sequence.plan import review

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = ROOT / "plans"


@pytest.fixture(autouse=True)
def shipped(monkeypatch):
    """The checkout's own plans, so End runs the shipped end script."""
    monkeypatch.setattr(planfile, "shipped_dir", lambda: SHIPPED)
    monkeypatch.setenv(planfile.ENV, str(SHIPPED))


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


def run_then_end(tmp_path, steps, after_s=6.0):
    """Run a plan of `steps` and `hold until end`, with an operator ending it."""
    plan = tmp_path / "t.plan"
    plan.write_text("load rLACO\nrecord every 1 s\n" + steps + "hold until end\n", encoding="utf-8")
    timer = threading.Timer(after_s, lambda: ctrl("end"))
    timer.start()
    host.channel(plan_path=plan)
    timer.join()
    return json.loads(host.ended_path().read_text(encoding="utf-8"))


def test_end_mid_pumpdown_leaves_the_chamber_sealed_off_and_at_20_c(chamber, tmp_path, capsys):
    ended = run_then_end(tmp_path, "hvc platen 60\nhvc platen on\nhvc pump on\nhold 2 s\nhvc rough open\n", after_s=8.0)
    s = chamber.state
    assert not any(s.devices[c] for c in ("OR", "OV", "OF", "OG", "OP", "OT"))
    assert not any(s.zone_active.values())
    assert s.zone_setpoint[1] == 20.0 and s.zone_setpoint[2] == 20.0
    assert ended["how"] == "ended" and ended["warnings"] == []
    assert ended["chamber"].startswith("Chamber left: sealed") and "zones off" in ended["chamber"]
    out = capsys.readouterr().out
    assert "[end 1/" in out and "Chamber left" in out


def test_end_with_the_turbo_running_keeps_its_backing_and_says_so(chamber, tmp_path):
    with chamber.state.lock:
        chamber.state.devices.update(OT=True, O4=True, OP=True)
    ended = run_then_end(tmp_path, "")
    s = chamber.state
    assert s.devices["OG"] is False and s.devices["OT"] is False          # isolated, spinning down
    assert s.devices["OP"] is True and s.devices["O4"] is True            # still backing it
    assert any("hvc stop" in w and "foreline" in w for w in ended["warnings"]), ended["warnings"]
    assert "pump on" in ended["chamber"]


def test_end_during_a_fault_closes_valves_and_zones_but_not_setpoints(chamber, tmp_path):
    with chamber.state.lock:
        chamber.state.severity = "F"
        chamber.state.mask = 1
        chamber.state.devices.update(OV=True, OP=True, OR=True)
        chamber.state.zone_active[1] = True
    ended = run_then_end(tmp_path, "")
    s = chamber.state
    assert not any(s.devices[c] for c in ("OR", "OV", "OF", "OG", "OP"))
    assert not any(s.zone_active.values())
    assert any("hvc platen 20" in w for w in ended["warnings"])          # a setpoint is not a fault-time command
    assert ended["chamber"].startswith("Chamber left: sealed")


def test_end_with_the_chamber_unreachable_still_ends_and_says_to_check_the_hmi(tmp_path, monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
    profile["connection"].update(host="127.0.0.1", port=9, timeout_s=0.3)
    (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
    import time
    t0 = time.monotonic()
    ended = run_then_end(tmp_path, "", after_s=2.0)
    assert ended["chamber"] == "Chamber not reached: check it at the HMI"
    assert ended["warnings"] and all("not connected" in w or "never read" in w or "not sent" in w
                                     for w in ended["warnings"])
    assert time.monotonic() - t0 < 60                  # nothing waits its full limit on a chamber never reached


def test_a_plan_that_runs_to_its_last_step_leaves_what_it_said(chamber, tmp_path, capsys):
    """The end script is End's: a completed plan keeps the vent open it opened."""
    with chamber.state.lock:
        chamber.state.pressure = 3.0
    plan = tmp_path / "v.plan"
    plan.write_text("load rLACO\nrecord every 1 s\nhvc vent open\nuntil chamberP > 700 Torr within 1 min\n",
                    encoding="utf-8")
    host.channel(plan_path=plan)
    ended = json.loads(host.ended_path().read_text(encoding="utf-8"))
    assert chamber.state.devices["OV"] is True and ended["how"] == "complete"
    assert "[end 1/" not in capsys.readouterr().out


# --- the pieces ------------------------------------------------------------------------

def test_the_chamber_publishes_its_states_for_plans_to_confirm(chamber, tmp_path):
    run = rscripts.Run(name="T", record_dir=tmp_path)
    rscripts.load(run, ["rLACO"])
    rscripts.tick(run)
    assert run.get("VentValve") == 0 and run.get("VacuumPump") == 0 and run.get("HoldingTemperature") == 0
    WriteCommand({"vent": "open", "platen_control": True}, "hvc")
    for _ in range(3):
        rscripts.tick(run)
    assert run.get("VentValve") == 1 and run.get("HoldingTemperature") == 1
    rscripts.shutdown(run)


def test_a_request_still_waiting_is_dropped_before_its_owner_takes_it():
    rid = WriteCommand({"1": {"on": True}}, "psu1")
    assert CommandPending("psu1")
    assert ClearPending() == {"psu1": [rid]}
    assert not CommandPending("psu1") and CommandState("psu1", rid)["state"] == "cleared"


def test_shutdown_runs_a_script_before_the_one_it_depends_on(tmp_path, monkeypatch):
    d = tmp_path / "rScripts"
    d.mkdir()
    monkeypatch.setenv(rscripts.ENV, str(d))
    rscripts.disabled.clear()
    body = 'import sys\ndef rScript(run): pass\ndef rShutdown(run): sys.modules["rScripts.rSupply"].order.append("{n}")\n'
    (d / "rSupply.py").write_text("order = []\n" + body.format(n="rSupply"), encoding="utf-8")
    (d / "rUser.py").write_text('SHUTDOWN_BEFORE = ("rSupply",)\n' + body.format(n="rUser"), encoding="utf-8")
    run = rscripts.Run(name="T", record_dir=tmp_path)
    rscripts.load(run, ["rUser", "rSupply"])        # last loaded first would shut rSupply down first
    rscripts.shutdown(run)
    assert sys.modules["rScripts.rSupply"].order == ["rUser", "rSupply"]


def test_the_end_script_checks_clean_and_turns_off_the_channels_the_map_owns():
    text = (SHIPPED / "end.plan").read_text(encoding="utf-8")
    assert review(text) == ([], [])
    owned = {f"{label} ch{ch} off" for label, chs in wiring().items() for ch, info in chs.items() if info.get("owner")}
    assert {ln.strip() for ln in text.splitlines() if ln.startswith("psu")} == owned


def test_the_end_script_runs_only_the_steps_of_the_routines_loaded():
    from formslab.rscripts import cast
    from formslab.sequence import load_plan
    script = load_plan(SHIPPED / "end.plan")
    labels = cast.owners()[0]
    for_chamber = [s.label for s in script.sequence.segments if host._needs(s, labels) <= {"rLACO"}]
    assert "psu1 ch1 off" not in for_chamber and "hvc stop" in for_chamber and "until VentValve = false within 15 s" in for_chamber
