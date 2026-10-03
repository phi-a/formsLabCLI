"""One thread per rScript: a slow one does not hold up the others, and stop
waits for each to finish its current call before rShutdown."""
import time

import pytest

from formslab import rscripts
from formslab.rscripts.workers import Workers


@pytest.fixture
def run(tmp_path, monkeypatch):
    d = tmp_path / "rScripts"
    d.mkdir()
    monkeypatch.setenv(rscripts.ENV, str(d))
    rscripts.disabled.clear()
    (d / "rSlow.py").write_text(
        "import time\ncalls = []\ndef rScript(run):\n    calls.append(1)\n    time.sleep(1.0)\n",
        encoding="utf-8")
    (d / "rFast.py").write_text("calls = []\ndef rScript(run):\n    calls.append(1)\n",
                                encoding="utf-8")
    f = rscripts.Run(record_dir=tmp_path)
    assert rscripts.load(f, ["rSlow", "rFast"]) == ["rSlow", "rFast"]
    return f


def test_a_slow_script_does_not_hold_up_a_fast_one(run):
    import sys
    workers = Workers(run, hz=20)
    workers.start()
    time.sleep(1.0)
    stuck = workers.stop(timeout=3)
    assert stuck == []
    fast = len(sys.modules["rScripts.rFast"].calls)
    slow = len(sys.modules["rScripts.rSlow"].calls)
    assert slow <= 2 and fast >= 10        # inline, fast would have waited on slow


def test_stop_reports_a_script_still_busy(run):
    workers = Workers(run, hz=20)
    workers.start()
    time.sleep(0.2)                        # rSlow is inside its 1 s call
    assert workers.stop(timeout=0.1) == ["rSlow"]
    assert workers.stop(timeout=3) == []
