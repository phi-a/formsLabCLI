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
    laco.vent()
    assert sim.state.devices["OV"] is True
    # the raw toggles stay behind the client's guard
    with pytest.raises(WriteRefused):
        laco.client.set_device("OR", True)


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


def test_apply_ignores_booleans_as_setpoints(laco, sim):
    before = dict(sim.state.zone_setpoint)
    assert laco.apply({"platen": True}) == []
    assert sim.state.zone_setpoint == before
