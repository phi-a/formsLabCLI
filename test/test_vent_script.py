"""scripts/vent_test.py against the HVC-3500 simulator: it vents a pumped-down
chamber, and it refuses outside the vent temperature window."""
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


def test_simulated_vent_reaches_atmosphere(vent_test, capsys):
    assert vent_test.main(["--poll", "0.2"]) == 0
    out = capsys.readouterr().out
    assert "Pre-checks passed" in out and "PASSED" in out


def test_refuses_outside_the_vent_window(vent_test, capsys, monkeypatch):
    real = vent_test.simulated_chamber

    def hot():
        sim, profile = real()
        with sim.state.lock:
            sim.state.temps = [80.0] * len(sim.state.temps)
        return sim, profile

    monkeypatch.setattr(vent_test, "simulated_chamber", hot)
    assert vent_test.main([]) == 2
    assert "outside the vent window" in capsys.readouterr().out
