"""The plot viewer's pure helpers (static/chart.js), run under Node. Skipped
where Node is not installed: the GUI itself needs only a browser."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import formslab.gui

CHART = Path(formslab.gui.__file__).parent / "static" / "chart.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

SCRIPT = """
const C = require(%r);
const out = {};
const t = C.niceTicks(-3, 47, 5); out.nice = [t.ticks, t.step];
out.niceSmall = C.niceTicks(0.0, 0.0005, 5).ticks.map(v => +v.toPrecision(6));
out.niceFlat = C.niceTicks(5, 5, 5).ticks;
const tt = C.timeTicks(1791115200, 1791115200 + 600, 6); out.timeStep = tt.step;
out.timeInside = tt.ticks.every(x => x >= 1791115200 && x <= 1791115200 + 600);
out.timeCount = tt.ticks.length;
out.timeAligned = tt.ticks.every(x => (x - (-new Date(x * 1000).getTimezoneOffset() * 60)) %% tt.step === 0);
out.fmt = [C.formatNumber(20.5, 0.5), C.formatNumber(1234, 1), C.formatNumber(0.00002, 1e-5), C.formatNumber(3e-7, 1e-7), C.formatNumber(null), C.formatNumber(0, 1)];
out.unit = [C.displayUnit("K", "C"), C.displayUnit("K", "K"), C.displayUnit("Torr", "C"), C.displayUnit("", "C")];
out.conv = [C.convert(273.15, "K", "C"), C.convert(300, "K", "K"), C.convert(5, "Torr", "C"), C.convert(null, "K", "C")];
out.near = [C.nearestIndex([], 5), C.nearestIndex([10, 20, 30], 14), C.nearestIndex([10, 20, 30], 16), C.nearestIndex([10, 20, 30], 99), C.nearestIndex([10, 20, 30], -5), C.nearestIndex([7], 1)];
out.ext = [C.visibleExtent([1, 2, 3, 4], [{values: [5, null, 9, 100]}], 1, 3), C.visibleExtent([1, 2], [{values: [null, null]}], 0, 9)];
out.zoomIn = C.zoomRange(0, 100, 50, 0.5, 0, 100);
out.zoomEdge = C.zoomRange(0, 100, 0, 0.5, 0, 100);
out.zoomOut = C.zoomRange(40, 60, 50, 10, 0, 100);
out.zoomFloor = C.zoomRange(0, 100, 50, 1e-9, 0, 100)[1] - C.zoomRange(0, 100, 50, 1e-9, 0, 100)[0];
out.pan = [C.panRange(10, 20, 5, 0, 100), C.panRange(10, 20, -50, 0, 100), C.panRange(10, 20, 500, 0, 100)];
out.palette = C.PALETTE.length;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js():
    out = subprocess.run([NODE, "-e", SCRIPT % str(CHART)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_ticks_are_round_numbers_inside_the_range(js):
    ticks, step = js["nice"]
    assert step == 10 and ticks == [0, 10, 20, 30, 40]
    assert js["niceSmall"] == [0, 0.0001, 0.0002, 0.0003, 0.0004, 0.0005]      # no float dust
    assert js["niceFlat"] == [5]


def test_time_ticks_land_on_round_clock_values(js):
    assert js["timeStep"] == 120 and js["timeInside"] and js["timeAligned"] and 3 <= js["timeCount"] <= 6


def test_numbers_get_the_decimals_their_step_needs(js):
    # tiny values (a high-vacuum pressure) print in exponent form
    assert js["fmt"] == ["20.5", "1234", "2.00e-5", "3.00e-7", "", "0"]


def test_kelvin_shows_as_celsius_only_when_asked(js):
    assert js["unit"] == ["C", "K", "Torr", ""]
    assert js["conv"] == [0.0, 300, 5, None]


def test_the_nearest_sample_to_the_pointer(js):
    assert js["near"] == [-1, 0, 1, 2, 0, 0]


def test_the_visible_extent_ignores_gaps_and_what_is_off_screen(js):
    assert js["ext"] == [[5, 9], None]


def test_zoom_and_pan_stay_inside_the_data(js):
    assert js["zoomIn"] == [25, 75]                         # about the cursor
    assert js["zoomEdge"] == [0, 50]
    assert js["zoomOut"] == [0, 100]                        # never past the data
    assert js["zoomFloor"] >= 1                             # never collapses to nothing
    assert js["pan"] == [[15, 25], [0, 10], [90, 100]]
    assert js["palette"] >= 8


@pytest.mark.parametrize("script", ["app.js", "chart.js", "editor.js", "tvac.js"])
def test_every_page_script_is_valid_javascript(script):
    """A syntax slip blanks the whole page, and no Python test would notice."""
    out = subprocess.run([NODE, "--check", str(CHART.parent / script)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
