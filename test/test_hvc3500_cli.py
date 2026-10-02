"""`python -m formslab.devices.hvc3500` against the simulator."""
import json

import pytest

from formslab import config
from formslab.devices.hvc3500 import cli
from formslab.devices.hvc3500.simulator import Simulator


@pytest.fixture
def sim():
    with Simulator() as s:
        yield s


@pytest.fixture
def profile(sim, tmp_path):
    d = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
    d["connection"].update(host=sim.host, port=sim.port)
    path = tmp_path / "bench.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    return str(path)


def test_probe_passes_on_a_documented_controller(profile, capsys):
    assert cli.main(["probe", "--profile", profile, "--quiet"]) == 0
    assert "PROBE PASSED" in capsys.readouterr().out


def test_snapshot_names_sensors_and_zones_from_the_profile(profile, capsys):
    assert cli.main(["snapshot", "--profile", profile, "--quiet"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["temp.platen_ctrl"] == pytest.approx(22.2)
    assert "zone.platen.setpoint" in snap and snap["units"]["pressure"] == "Torr"


def test_host_and_port_override_the_profile(sim, capsys):
    assert cli.main(["raw", "?VP", "--host", sim.host, "--port", str(sim.port), "--quiet"]) == 0
    assert "VP" in capsys.readouterr().out


def test_raw_refuses_a_write_without_allow_write(profile):
    with pytest.raises(SystemExit, match="allow-write"):
        cli.main(["raw", "!OV", "--profile", profile])


def test_device_needs_confirm(profile):
    with pytest.raises(SystemExit, match="confirm"):
        cli.main(["device", "OV", "open", "--profile", profile])


def test_transactions_are_logged_under_outputs(profile):
    cli.main(["raw", "?MC", "--profile", profile, "--quiet"])
    assert list((config.output_dir() / "hvc3500").glob("*_raw.jsonl"))
