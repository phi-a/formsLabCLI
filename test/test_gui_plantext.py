"""Where the header lines (`load`, then `record`) go when the editor adds them
back (static/plantext.js, under Node): always where they belong, so deleting one
is never a dead end."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import formslab.gui
from formslab.sequence import find_plan

PLANTEXT = Path(formslab.gui.__file__).parent / "static" / "plantext.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
SHIPPED = ("tvac", "laco_pumpdown", "laco_vent", "psu1_smtc08_first")


def run_js(payload, body):
    script = f"const T = require({str(PLANTEXT)!r}); const P = {json.dumps(payload)}; console.log(JSON.stringify({body}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def add(lines, kind, scripts=()):
    return run_js({"l": lines, "k": kind, "s": list(scripts)}, "T.withHeader(P.l, P.k, P.s)")


def test_load_goes_before_the_first_step_and_after_a_comment_banner():
    got = add(["# banner", "", "hold 5 s", "hvc stop"], "load", ["rLACO"])
    assert got == {"lines": ["# banner", "", "load rLACO", "hold 5 s", "hvc stop"], "index": 2}


def test_load_goes_before_record_when_only_record_is_left():
    assert add(["record every 2 s", "hold 1 s"], "load", ["rPSU"])["lines"] == ["load rPSU", "record every 2 s", "hold 1 s"]


def test_record_goes_straight_after_load():
    got = add(["# c", "load rPSU", "", "hold 1 s"], "record")
    assert got == {"lines": ["# c", "load rPSU", "record every 10 s", "", "hold 1 s"], "index": 2}


def test_record_without_a_load_goes_before_the_first_step():
    assert add(["# c", "hold 1 s"], "record")["lines"] == ["# c", "record every 10 s", "hold 1 s"]


def test_a_plan_with_nothing_but_comments_gets_the_line_at_the_end():
    assert add(["# only a note"], "load", ["rSMTC08"])["lines"] == ["# only a note", "load rSMTC08"]
    assert add([], "load", [])["lines"] == ["load"]                              # no scripts yet: just the word


def test_a_header_line_that_is_there_is_not_added_twice():
    lines = ["load rPSU", "record every 5 s", "hold 1 s"]
    assert add(lines, "load", ["rLACO"]) == {"lines": lines, "index": -1}
    assert add(lines, "record") == {"lines": lines, "index": -1}


def test_which_headers_are_missing():
    assert run_js(["hold 1 s"], "T.missingHeaders(P)") == ["load", "record"]
    assert run_js(["load rPSU", "hold 1 s"], "T.missingHeaders(P)") == ["record"]
    assert run_js(["Load rPSU", "RECORD every 2 s", "hold 1 s"], "T.missingHeaders(P)") == []   # case-insensitive


def header_lines(name):
    text = find_plan(name).read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.rstrip("\n").split("\n")
    load = next(ln for ln in lines if ln.split()[:1] == ["load"])
    record = next(ln for ln in lines if ln.split()[:1] == ["record"])
    return text, lines, load, record


@pytest.mark.parametrize("name", SHIPPED)
def test_deleting_load_and_adding_it_back_restores_the_plan_exactly(name):
    """The case you hit: a deleted load comes back in its place, for every plan that ships."""
    text, lines, load, _ = header_lines(name)
    got = add([ln for ln in lines if ln != load], "load", load.split()[1:])["lines"]
    assert "\n".join(got) + "\n" == text


@pytest.mark.parametrize("name", SHIPPED)
def test_deleting_record_and_adding_it_back_restores_the_plan_exactly(name):
    text, lines, _, record = header_lines(name)
    got = add([ln for ln in lines if ln != record], "record")["lines"]
    got[got.index("record every 10 s")] = record                 # the default cadence, set back to the plan's
    assert "\n".join(got) + "\n" == text


@pytest.mark.parametrize("name", SHIPPED)
def test_deleting_both_and_adding_them_back_restores_the_plan(name):
    """Both gone, both back (load first, then record): the layout the shipped plans use comes back exactly."""
    text, lines, load, record = header_lines(name)
    got = add([ln for ln in lines if ln not in (load, record)], "load", load.split()[1:])["lines"]
    got = add(got, "record")["lines"]
    got[got.index("record every 10 s")] = record
    assert got.index(record) == got.index(load) + 1                # record straight after load
    assert "\n".join(got) + "\n" == text


def test_one_blank_line_above_the_steps_stays_above_the_header():
    assert add(["# c", "", "hold 1 s"], "load", ["rPSU"])["lines"] == ["# c", "", "load rPSU", "hold 1 s"]
    assert add(["# c", "", "", "", "hold 1 s"], "load", ["rPSU"])["lines"] == ["# c", "", "load rPSU", "", "", "hold 1 s"]


# --- orbit files ------------------------------------------------------------------------------

def test_which_elements_an_orbit_still_lacks():
    assert run_js([], "T.missingElements(P)") == ["epoch", "a", "e", "i", "raan", "argp", "nu"]
    lines = ["# an orbit", "", "A 6928 km", "nu 0 deg", "epoch 2026-10-05T12:00:00Z", "# e 0.1"]
    assert run_js(lines, "T.missingElements(P)") == ["e", "i", "raan", "argp"]     # case-insensitive; a comment names nothing


def test_the_elements_are_the_orbit_files_own():
    from formslab.orbit.file import ORDER
    assert run_js([], "T.ELEMENTS") == list(ORDER)


# --- block files ------------------------------------------------------------------------------

def test_a_blocks_own_line_is_its_kind_and_load_goes_after_it():
    assert run_js({"l": "block pumpdown to <pressure:number>"}, "T.kindOf(P.l)") == "block"
    got = add(["# what it does", "block seal", "", "hvc gate close"], "load", ["rLACO"])
    assert got["lines"] == ["# what it does", "block seal", "", "load rLACO", "hvc gate close"]


# --- loops: how deep each line is, and the file's indentation --------------------------------

LOOP = ["load rPSU", "repeat 2 times", "psu1 ch1 on", "# a note", "", "repeat until end", "hold 1 s",
        "end", "end", "log done"]


def test_each_line_knows_how_deep_in_loops_it_is():
    assert run_js(LOOP, "T.depths(P)") == [0, 0, 1, 1, 1, 1, 2, 1, 0, 0]
    assert run_js(["end", "hold 1 s"], "T.depths(P)") == [0, 0]                 # a stray end stays at 0


def test_the_steps_in_a_loop_are_written_two_spaces_in():
    assert run_js(["load rPSU", "  repeat 2 times", "hold 1 s", "      end", "   "], "T.indent(P)") == \
        ["load rPSU", "repeat 2 times", "  hold 1 s", "end", ""]
    assert run_js(LOOP, "T.indent(P)")[6] == "    hold 1 s"


def test_each_repeat_knows_its_end_and_each_end_its_repeat():
    got = [run_js({"l": LOOP, "i": i}, "T.partner(P.l, P.i)") for i in range(len(LOOP))]
    assert got == [-1, 8, -1, -1, -1, 7, -1, 5, 1, -1]
    assert run_js({"l": ["repeat 2 times", "hold 1 s"], "i": 0}, "T.partner(P.l, P.i)") == -1


def test_choosing_repeat_adds_its_end_and_a_step_inside():
    got = run_js({"l": ["load rPSU", "repeat", "log done"], "i": 1}, "T.withLoopEnd(P.l, P.i)")
    assert got == {"lines": ["load rPSU", "repeat", "", "end", "log done"], "fresh": 2}
    closed = ["repeat 2 times", "hold 1 s", "end"]
    assert run_js({"l": closed, "i": 0}, "T.withLoopEnd(P.l, P.i)") == {"lines": closed, "fresh": -1}


def test_deleting_a_repeat_or_its_end_deletes_both_and_keeps_the_steps():
    for i in (1, 8):
        assert run_js({"l": LOOP, "i": i}, "T.withoutLoop(P.l, P.i)") == \
            ["load rPSU", "psu1 ch1 on", "# a note", "", "repeat until end", "hold 1 s", "end", "log done"]
