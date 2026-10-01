"""scripts/vent_test.py against the HVC-3500 simulator: both vent methods, the
temperature window, and the rough/gate interlock pre-check."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vent_test.py"


@pytest.fixture
def vent_test():
    spec = importlib.util.spec_from_file_location("vent_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _with(vent_test, monkeypatch, **state):
    real = vent_test.simulated_chamber

    def patched():
        sim, profile = real()
        with sim.state.lock:
            for key, value in state.items():
                if key == "devices":
                    sim.state.devices.update(value)
                else:
                    setattr(sim.state, key, value)
        return sim, profile

    monkeypatch.setattr(vent_test, "simulated_chamber", patched)


@pytest.mark.parametrize("method", ["valve", "cycle"])
def test_simulated_vent_reaches_atmosphere(vent_test, capsys, method):
    assert vent_test.main(["--method", method, "--poll", "0.2"]) == 0
    out = capsys.readouterr().out
    assert "Pre-checks passed" in out and "PASSED" in out


def test_refuses_outside_the_vent_window(vent_test, capsys, monkeypatch):
    _with(vent_test, monkeypatch, temps=[80.0] * 21)
    assert vent_test.main([]) == 2
    assert "outside the vent window" in capsys.readouterr().out


def test_refuses_the_valve_while_the_rough_valve_is_open(vent_test, capsys, monkeypatch):
    _with(vent_test, monkeypatch, devices={"OR": True})
    assert vent_test.main([]) == 2
    assert "rough valve is open" in capsys.readouterr().out
