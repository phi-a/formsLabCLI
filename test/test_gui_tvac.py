"""The chamber view's mapping (static/tvac.js, run under Node): the `hvc` CAST
block -> what is drawn. Includes a contract test against the block a real LACO
produces (on the simulator), so a renamed key cannot silently blank the diagram."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import formslab.gui
from formslab import config
from formslab.devices.hvc3500 import BenchProfile
from formslab.devices.hvc3500.laco import LACO
from formslab.devices.hvc3500.simulator import Simulator

TVAC = Path(formslab.gui.__file__).parent / "static" / "tvac.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(payload, body):
    """Evaluate `body` (JS, with T = the module and P = the payload) under Node."""
    script = f"const T = require({str(TVAC)!r}); const P = {json.dumps(payload)}; console.log(JSON.stringify({body}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def chamber():
    with Simulator() as sim:
        d = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        d["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
        with LACO(BenchProfile.from_dict(d), inter_command_delay=0.0) as laco:
            yield laco


# --- against the real producer ----------------------------------------------------------------

def test_the_view_model_reads_what_a_real_chamber_publishes(chamber):
    block = chamber.status().as_cast()
    vm = run_js(block, "T.viewModel(P, 'C')")

    assert vm["connected"] is True and vm["pressure"]["unit"] == "Torr"
    assert vm["pressure"]["value"] == block["pressure"] and isinstance(block["pressure"], (int, float))
    assert [z["key"] for z in vm["zones"]] == ["platen", "shroud", "t2"]
    assert [z["title"] for z in vm["zones"]] == ["Platen (Cntrl P)", "Shroud (Cntrl S)", "t2 (monitor)"]
    assert vm["zones"][0]["temp"] == block["platen C"] and vm["zones"][0]["setpoint"] == block["platen setpoint C"]
    assert vm["zones"][2]["setpoint"] is None                                  # t2 only reports
    assert set(vm["valves"]) == {"vent", "fill", "rough", "foreline", "gate"}
    assert set(vm["pumps"]) == {"pump", "turbo"}
    assert all(v in ("open", "closed") for v in vm["valves"].values())          # none left "unknown": the keys match
    assert all(v in ("on", "off") for v in vm["pumps"].values())
    names = [s["name"] for s in vm["sensors"]]
    assert "ot1_ptn" in names and "ot2_shd" in names and "T5" in names
    assert not {"platen", "shroud", "t2"} & set(names)                          # the zones are not repeated as sensors
    assert vm["holding"] in (True, False) and vm["faults"] in ("", block["faults"])


def test_a_valve_the_chamber_opens_is_drawn_open(chamber):
    chamber.apply({"vent": "open"})
    vm = run_js(chamber.status().as_cast(), "T.viewModel(P, 'C')")
    assert vm["valves"]["vent"] == "open" and vm["valves"]["gate"] == "closed"
    chamber.apply({"stop_pumping": True})


def test_kelvin_converts_every_temperature_and_leaves_pressure_alone(chamber):
    block = chamber.status().as_cast()
    c, k = run_js(block, "[T.viewModel(P, 'C'), T.viewModel(P, 'K')]")
    assert k["unit"] == "K" and c["unit"] == "C"
    assert k["zones"][0]["temp"] == pytest.approx(c["zones"][0]["temp"] + 273.15)
    assert k["sensors"][0]["value"] == pytest.approx(c["sensors"][0]["value"] + 273.15)
    assert k["pressure"] == c["pressure"]


def test_the_quick_status_merged_into_a_block_still_draws(chamber):
    """After a command the host merges a short block (pressure, faults, valves) into the full one."""
    full = chamber.status().as_cast()
    quick = chamber.quick_status()
    vm = run_js({**full, **quick}, "T.viewModel(P, 'C')")
    assert vm["zones"] and vm["connected"] is True


# --- by hand ------------------------------------------------------------------------------------------

def test_what_the_block_does_not_say_is_unknown_not_guessed():
    vm = run_js({"connected": True, "vent": True}, "T.viewModel(P, 'C')")
    assert vm["valves"]["vent"] == "open" and vm["valves"]["gate"] == "unknown" and vm["pumps"]["turbo"] == "unknown"
    assert vm["holding"] is None and vm["zones"] == [] and vm["pressure"]["value"] is None


def test_a_chamber_that_is_not_connected_has_nothing_to_draw_but_does_not_break():
    vm = run_js({"connected": False, "error": "timed out"}, "T.viewModel(P, 'C')")
    assert vm["connected"] is False and vm["zones"] == [] and vm["sensors"] == []
    assert run_js(None, "T.viewModel(P, 'C')")["connected"] is False                     # no block at all


def test_faults_are_text_and_none_means_none():
    assert run_js({"faults": "none"}, "T.viewModel(P, 'C')")["faults"] == ""
    got = run_js({"faults": "Control air low, Turbo fault", "fault_severity": "W"}, "T.viewModel(P, 'C')")
    assert got["faults"] == "Control air low, Turbo fault" and got["severity"] == "W"


@pytest.mark.parametrize("value, unit, shown", [
    (82.26, "Torr", "82.26 Torr"), (743.0, "Torr", "743.0 Torr"), (3.632, "Torr", "3.63 Torr"),
    (0.0053, "Torr", "0.0053 Torr"), (2e-5, "Torr", "2.00e-5 Torr"), (0, "Torr", "0 Torr"), (None, "Torr", "-"),
])
def test_pressure_is_shown_the_way_the_screen_shows_it(value, unit, shown):
    assert run_js({"v": value, "u": unit}, "T.fmtPressure(P.v, P.u)") == shown


def test_temperatures_carry_their_unit():
    assert run_js({}, "[T.fmtTemp(19.54, 'C'), T.fmtTemp(292.7, 'K'), T.fmtTemp(null, 'C')]") == ["19.5 °C", "292.7 K", "-"]


def test_the_script_is_valid_and_never_uses_innerhtml():
    out = subprocess.run([NODE, "--check", str(TVAC)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "innerHTML" not in TVAC.read_text(encoding="utf-8")
