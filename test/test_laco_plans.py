"""The LACO lab plans, run by the host against the HVC-3500 simulator: the
`cast` plan step, pumpdown, vent, and that a failed pumpdown leaves the chamber
sealed with the pump off (rLACO's rShutdown)."""
import json
from dataclasses import replace

import pytest

from formslab import config, rscripts
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import sequence
from formslab.sequence import PlanError, SequenceError, find_plan, load_plan, parse_plan


@pytest.fixture
def chamber(monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    with Simulator() as sim:
        profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0,
                                     poll_interval_s=0.5)
        (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
        with sim.state.lock:
            sim.state.mode = "MANUAL"
        yield sim


def _quick(monkeypatch, **overrides):
    """Load plans with holds cut to 1.5 s and any step params overridden."""
    def load(path):
        p = load_plan(path)
        segs = []
        for s in p.sequence.segments:
            params = {**s.params, **overrides.get(s.verb, {})}
            if s.verb == "hold":
                params["seconds"] = 1.5
            segs.append(replace(s, params=params))
        return replace(p, sequence=replace(p.sequence, segments=tuple(segs)))
    monkeypatch.setattr(sequence, "load_plan", load)


def test_a_step_uses_the_cast_tab_grammar():
    plan = parse_plan("load rLACO rPSU\nhvc pump on\npsu1 ch1 off\n")
    first, second = plan.sequence.segments
    assert first.verb == "command" and first.params["request"] == {"pump": "on"}
    assert second.params == {"label": "psu1", "request": {"1": {"on": False}}, "timeout_s": 10.0}
    with pytest.raises(PlanError, match="outside"):
        parse_plan("load rLACO\nhvc platen 900\n")


def test_pumpdown_plan_pumps_down_and_seals(chamber, monkeypatch):
    with chamber.state.lock:
        chamber.state.pressure = 743.0
    _quick(monkeypatch)
    sequence.channel(plan_path=find_plan("laco_pumpdown"))
    s = chamber.state
    assert s.pressure < 5.0
    assert not s.devices["OR"] and not s.devices["OP"]


def test_vent_plan_vents_and_leaves_the_valve_open(chamber, monkeypatch):
    with chamber.state.lock:
        chamber.state.pressure = 4.4
    _quick(monkeypatch)
    sequence.channel(plan_path=find_plan("laco_vent"))
    assert chamber.state.pressure >= 700 and chamber.state.devices["OV"] is True


def test_vent_plan_refuses_outside_the_vent_window(chamber, monkeypatch):
    with chamber.state.lock:
        chamber.state.temps = [80.0] * len(chamber.state.temps)
    _quick(monkeypatch, until={"timeout_s": 2.0})
    with pytest.raises(SequenceError, match="platenT not below 60"):
        sequence.channel(plan_path=find_plan("laco_vent"))
    assert chamber.state.devices["OV"] is False


def test_a_failed_pumpdown_still_ends_with_rough_closed_and_pump_off(chamber, monkeypatch):
    with chamber.state.lock:
        chamber.state.pressure = 743.0
        chamber.state.vacuum_setpoint = 600.0      # the "pump" cannot get below 600
    _quick(monkeypatch, until={"timeout_s": 4.0})
    with pytest.raises(SequenceError, match="chamberP not below 5"):
        sequence.channel(plan_path=find_plan("laco_pumpdown"))
    assert not chamber.state.devices["OR"] and not chamber.state.devices["OP"]


def test_a_refused_command_stops_the_plan_at_that_step(chamber, monkeypatch, tmp_path):
    """The gate valve will not open with the turbo off: the step fails with the
    controller's reason instead of the plan running on, and rShutdown still runs.
    (The rules would stop it before sending; here they are off, to reach the PLC.)"""
    from formslab.rscripts import rules
    monkeypatch.setattr(rules, "assess", lambda *a, **k: [])
    plan = tmp_path / "gate.plan"
    plan.write_text("load rLACO\nhvc pump on\nhvc gate open\nlog never reached\n", encoding="utf-8")
    _quick(monkeypatch)
    with pytest.raises(SequenceError, match="refused: .*gate"):
        sequence.channel(plan_path=plan)
    s = chamber.state
    assert not s.devices["OG"]
    assert not s.devices["OP"]          # the pump this run started was stopped


def test_the_rules_stop_a_step_before_it_is_sent(chamber, monkeypatch, tmp_path):
    plan = tmp_path / "gate.plan"
    plan.write_text("load rLACO\nhvc pump on\nhvc gate open\nlog never reached\n", encoding="utf-8")
    _quick(monkeypatch)
    with pytest.raises(SequenceError, match="not sent: needs turbo on"):
        sequence.channel(plan_path=plan)
    assert not chamber.state.devices["OG"] and not chamber.state.devices["OP"]


def test_a_command_with_the_chamber_unreachable_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
    profile["connection"].update(host="127.0.0.1", port=9, timeout_s=0.3)
    (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
    plan = tmp_path / "x.plan"
    plan.write_text("load rLACO\nhvc rough close\n", encoding="utf-8")
    with pytest.raises(SequenceError, match="refused: chamber not connected"):
        sequence.channel(plan_path=plan)
