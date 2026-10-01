"""scripts/pumpdown.py against the HVC-3500 simulator: the sequence, the
pre-check refusals, and that it always leaves the rough valve closed and the
pump off."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def pumpdown(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("pumpdown", SCRIPTS / "pumpdown.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sim_with(pumpdown, monkeypatch, **devices):
    real = pumpdown.simulated_chamber
    sims = []

    def patched():
        sim, profile = real()
        with sim.state.lock:
            sim.state.devices.update(devices)
        sims.append(sim)
        return sim, profile

    monkeypatch.setattr(pumpdown, "simulated_chamber", patched)
    return sims


def test_pumps_down_then_seals(pumpdown, capsys, monkeypatch):
    sims = _sim_with(pumpdown, monkeypatch)
    assert pumpdown.main(["--poll", "0.2"]) == 0
    out = capsys.readouterr().out
    assert "PASSED" in out and "rough valve CLOSED: verified" in out
    assert "vacuum pump OFF: verified" in out
    assert not sims[0].state.devices["OR"] and not sims[0].state.devices["OP"]


def test_refuses_with_the_vent_valve_open(pumpdown, capsys, monkeypatch):
    _sim_with(pumpdown, monkeypatch, OV=True)
    assert pumpdown.main([]) == 2
    out = capsys.readouterr().out
    assert "vent is open/on" in out and "vent_test.py --live --close" in out


def test_stopping_short_still_closes_rough_and_stops_pump(pumpdown, capsys, monkeypatch):
    sims = _sim_with(pumpdown, monkeypatch)
    assert pumpdown.main(["--target", "1e-9", "--max-min", "0.05", "--poll", "0.2"]) == 1
    out = capsys.readouterr().out
    assert "STOPPED" in out and "vacuum pump OFF: verified" in out
    assert not sims[0].state.devices["OR"] and not sims[0].state.devices["OP"]


def test_stop_closes_rough_then_stops_the_pump(pumpdown, capsys, monkeypatch):
    sims = _sim_with(pumpdown, monkeypatch, OP=True, OR=True)
    assert pumpdown.main(["--stop"]) == 0
    out = capsys.readouterr().out
    assert out.index("rough valve CLOSED: verified") < out.index("vacuum pump OFF: verified")
    assert not sims[0].state.devices["OR"] and not sims[0].state.devices["OP"]


def test_stop_sends_nothing_when_already_off(pumpdown, capsys, monkeypatch):
    _sim_with(pumpdown, monkeypatch)
    assert pumpdown.main(["--stop"]) == 0
    assert "nothing sent" in capsys.readouterr().out
