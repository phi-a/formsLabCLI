"""`formslab.devices.laco.LACO`: the UIUC chamber by name, over the simulator.

The vendor client is covered in test_hvc3500.py. These check the layer above
it: zone names to numbers, the profile's limits, one structured status, and
the CAST request grammar the console writes.
"""
import json

import pytest

from formslab import config
from formslab.devices.hvc3500 import BenchProfile, WriteRefused
from formslab.devices.hvc3500.simulator import Simulator
from formslab.devices.laco import LACO, LacoStatus


@pytest.fixture
def sim():
    with Simulator() as s:
        yield s


@pytest.fixture
def profile(sim):
    d = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
    d["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
    return BenchProfile.from_dict(d)


@pytest.fixture
def laco(profile):
    with LACO(profile, inter_command_delay=0.0) as chamber:
        yield chamber


def test_zones_are_reachable_by_chamber_name(laco):
    assert laco.platen is laco.zone("platen") and laco.platen.number == 1
    assert laco.shroud.number == 2
    assert laco.platen.control_sensor == 2 and laco.shroud.control_sensor == 3
    with pytest.raises(KeyError, match="no zone"):
        laco.zone("hull")


def test_reads_use_the_profile_mapping(laco, sim):
    sim.state.temps[2] = -40.0        # platen control sensor is T2
    sim.state.pressure = 2.5e-4
    assert laco.platen.temperature() == pytest.approx(-40.0)
    assert laco.sensor("platen_ctrl") == pytest.approx(-40.0)
    assert laco.sensor("T14") == pytest.approx(22.0 + 0.1 * 14)
    assert laco.pressure() == pytest.approx(2.5e-4)
    with pytest.raises(KeyError, match="no sensor"):
        laco.sensor("T99")


def test_setpoint_is_clamped_to_the_profile_and_remembered(laco, sim):
    assert laco.platen.bounds == (-180.0, 200.0)
    assert laco.platen.set(25.0) == pytest.approx(25.0)
    assert sim.state.zone_setpoint[1] == pytest.approx(25.0)
    assert laco.platen.target_c == pytest.approx(25.0)

    assert laco.shroud.set(500.0) == pytest.approx(120.0)      # shroud max
    with pytest.raises(ValueError, match="outside"):
        laco.shroud.set(500.0, clamp=False)
    assert sim.state.zone_setpoint[2] == pytest.approx(120.0)


def test_zone_on_off_and_thermal_control_flag(laco, sim):
    laco.platen.set(30.0)
    laco.platen.on()
    assert sim.state.zone_active[1] is True
    assert laco.status().thermal_control is True
    assert laco.platen.setpoint() == pytest.approx(30.0)   # effective = commanded once active
    laco.platen.off()
    assert sim.state.zone_active[1] is False
    assert laco.status().thermal_control is False


def test_status_is_one_structured_snapshot(laco, sim):
    sim.state.mask, sim.state.severity = 1 << 16, "W"
    s = laco.status()

    assert isinstance(s, LacoStatus)
    assert s.mode == "AUTO" and s.pressure_unit == "Torr"
    assert s.pressure == pytest.approx(760.0)
    assert s.zones["platen"].temperature_c == pytest.approx(22.2)
    assert s.zones["shroud"].effective_setpoint_c == pytest.approx(22.1)   # idle: tracks T1
    assert s.sensors["T14"] == pytest.approx(23.4)
    assert s.devices == {"rough": False, "vent": False, "fill": False, "foreline": False,
                         "gate": False, "pump": False, "turbo": False}
    assert s.faults == ["Control air low"] and s.fault_severity == "W" and not s.ok
    assert s.errors == {} and laco.connected

    cast = s.as_cast()
    assert cast["connected"] is True and cast["faults"] == "Control air low"
    assert cast["platen C"] == pytest.approx(22.2) and cast["vent"] is False
    rec = s.as_record()
    assert rec["T_platen_ctrl"] == pytest.approx(22.2) and rec["Z1_setpoint"] is not None


def test_status_raises_when_the_controller_is_gone(profile):
    sim = Simulator()
    p = BenchProfile.from_dict({**json.loads(config.default_path("tvac_bench.json").read_text()),
                                "connection": {"host": sim.host, "port": sim.port, "timeout_s": 0.5}})
    laco = LACO(p, inter_command_delay=0.0)
    with pytest.raises(OSError):
        laco.status()
    assert laco.connected is False


def test_actions_go_through_without_a_confirm_flag(laco, sim):
    laco.start()
    assert sim.state.cycle_running is True
    laco.abort()
    assert sim.state.test_status == "ABORTED"
    laco.reset()
    assert sim.state.test_status == "IDLE" and sim.state.severity == "N"
    laco.operation("vent2atm")
    assert sim.state.devices["OV"] is True
    with pytest.raises(KeyError, match="no operation"):
        laco.operation("evacuate")
    # the client itself still refuses an unconfirmed toggle
    with pytest.raises(WriteRefused):
        laco.client.set_device("OR", True)


@pytest.fixture
def fast(laco):
    laco.client.toggle_settle_s = 1.2        # the simulator actuates after 1 s
    return laco


def test_device_opens_and_closes_by_name_and_verifies(fast, sim):
    assert fast.device("vent", True) is True and sim.state.devices["OV"] is True
    assert fast.device("vent", False) is False and sim.state.devices["OV"] is False
    assert fast.device("vent", False) is False          # already closed: nothing sent
    with pytest.raises(KeyError, match="no valve or pump"):
        fast.device("door", True)


def test_stop_pumping_closes_rough_before_the_pump(fast, sim):
    sim.interlocks = True
    sim.state.devices.update(OP=True, OR=True)
    assert fast.stop_pumping() == ["rough valve closed", "pump off"]
    assert not sim.state.devices["OR"] and not sim.state.devices["OP"]
    assert fast.stop_pumping() == []


def test_stop_pumping_refuses_while_the_turbo_runs(fast, sim):
    sim.state.devices.update(OP=True, OT=True, O4=True)
    with pytest.raises(Exception, match="stop the turbo"):
        fast.stop_pumping()
    assert sim.state.devices["OP"] is True


def test_apply_covers_setpoints_recipe_and_vacuum_settings(laco, sim):
    events = laco.apply({"platen_rate": 2.0, "shroud_range": 1.5, "vacuum_range": 0.25,
                         "vacuum_rate": 0.5, "hold_s": 900, "recipe": 3})
    assert all(level == "INFO" for level, _ in events), events
    assert sim.state.zone_rate[1] == pytest.approx(2.0)
    assert sim.state.zone_range[2] == pytest.approx(1.5)
    assert sim.state.vacuum_range == pytest.approx(0.25)
    assert sim.state.vacuum_rate == pytest.approx(0.5)
    assert sim.state.hold_time == 900 and sim.state.recipe == 3


def test_apply_drives_valves_pumps_and_operations(fast, sim):
    events = fast.apply({"pump": "on", "vent": "open", "recipe_run": "start", "purge": True})
    assert all(level == "INFO" for level, _ in events), events
    assert any("pump verified on" in m for _, m in events)
    assert sim.state.cycle_running is True
    assert sim.state.devices["OV"] is False             # purge closed it again
    events = fast.apply({"stop_pumping": True, "recipe_run": False})
    assert ("INFO", "stop_pumping: pump off") in events
    assert sim.state.cycle_running is False


def test_apply_reports_a_valve_the_plc_kept_closed(fast, sim):
    sim.interlocks = True                    # gate will not open without the turbo
    events = fast.apply({"gate": "open"})
    assert events[0][0] == "ERROR" and "interlock" in events[0][1]


def test_apply_handles_a_whole_cast_request(laco, sim):
    events = laco.apply({"platen": 25.0, "shroud": 500.0, "platen_control": True,
                         "vacuum": 1e-3, "start": True, "nonsense": 1})

    assert sim.state.zone_setpoint == {**sim.state.zone_setpoint, 1: 25.0, 2: 120.0}
    assert sim.state.zone_active[1] is True
    assert sim.state.vacuum_setpoint == pytest.approx(1e-3)
    assert sim.state.cycle_running is True

    levels = [lvl for lvl, _ in events]
    assert "WARNING" in levels and "ERROR" not in levels
    assert any("shroud setpoint 500.0 clamped to 120.0" in m for _, m in events)


def test_apply_warns_on_booleans_as_setpoints_and_writes_nothing(laco, sim):
    before = dict(sim.state.zone_setpoint)
    assert laco.apply({"platen": True}) == [
        ("WARNING", "platen: expected a number, got True; ignored")]
    assert sim.state.zone_setpoint == before


def test_quick_status_reads_what_a_command_changes(fast, sim):
    sim.state.pressure = 3.5
    fast.device("vent", True)
    q = fast.quick_status()
    assert q["vent"] is True and q["pressure"] > 3.5          # rising
    assert q["fault_severity"] == "N" and q["faults"] == "none"
    assert set(q) >= {"rough", "fill", "foreline", "gate", "pump", "turbo"}
