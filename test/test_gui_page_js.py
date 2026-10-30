"""The Status page's script (static/app.js), run under Node against a stand-in
document: logged in, one status poll, and the page is filled. `node --check` finds
a syntax slip; this finds a reference slip, which the poll loop swallows (it tries
again next second) and which leaves the page blank with nothing in the console
either. Skipped where Node is not installed: the GUI itself needs only a browser."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import formslab.gui

APP = Path(formslab.gui.__file__).parent / "static" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# What the server answers: no run going, two instrument blocks, the last run's end.
STATUS = {
    "now": 1793379052.0, "host": None, "last_run": {"plan": "DARKNESS"}, "action": None,
    "cast_unreadable": False, "lost": None,
    "blocks": {
        "hvc": {"live": True, "watched": True, "age_s": 2.1, "reason": "", "pending": False,
                "status": {"connected": True},
                "groups": [{"title": "Valves", "part": "valve",
                            "rows": [{"key": "vent", "name": "Vent", "value": False, "text": "Closed"}]},
                           {"title": "Pumps", "part": "pump",
                            "rows": [{"key": "pump", "name": "Vacuum pump", "value": False, "text": "Off"}]}]},
        "psu1": {"live": False, "age_s": 86400.0, "reason": "no run is going", "pending": False,
                 "status": {}, "groups": [{"title": "CH1 cooler", "rows": [{"key": "v", "name": "V", "value": 12.0}]}]},
    },
    "log": ["12:00:00 host: run DARKNESS ended", "12:00:01 host: bye"],
    "ended": {"plan": "DARKNESS", "when": "2026-10-29T14:36:46+00:00", "how": "ended", "fast": False,
              "chamber": "Chamber left: sealed, 749 Torr, pump off, turbo off, zones off", "warnings": []},
}
PLANS = [
    {"name": "tvac", "kind": "plan", "rscripts": ["rLACO", "rPSU"], "steps": 2, "editable": False,
     "warnings": [], "open_ended": True},
    {"name": "gevs_unit", "kind": "plan", "rscripts": ["rLACO"], "steps": 100, "editable": False,
     "warnings": ["line 4: checks at the start"], "open_ended": False},
    {"name": "broken", "kind": "plan", "error": "line 2: unknown word", "editable": True},
    {"name": "end", "kind": "plan", "rscripts": ["rLACO"], "steps": 19, "editable": False,
     "warnings": [], "open_ended": False, "end": True},
    {"name": "pumpdown", "kind": "block", "editable": False},
    {"name": "LEO", "kind": "orbit", "editable": True},
]

# A document just big enough for app.js: every element answers every call, one
# element per selector, so what the script puts on the page can be read back.
SCRIPT = r"""
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.dataset = {}; this.style = {};
    this.hidden = false; this.disabled = false; this.value = ""; this.textContent = ""; this.className = "";
    this.scrollTop = 0; this.clientHeight = 0; this.scrollHeight = 0; this.title = "";
    this.classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } }; }
  setAttribute(k, v) { this.attrs[k] = v; }
  append(...c) { this.children.push(...c); }
  replaceChildren(...c) { this.children = [...c]; }
  addEventListener() {}
  querySelector() { return new El("x"); }
  querySelectorAll() { return []; }
  focus() {} select() {} getContext() { return {}; }
}
const byId = new Map();
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.document = {
  hidden: false, title: "", body: new El("body"),
  querySelector: (sel) => { if (!byId.has(sel)) byId.set(sel, new El(sel)); return byId.get(sel); },
  querySelectorAll: () => [], createElement: (tag) => new El(tag), addEventListener() {},
};
globalThis.location = { hash: "" };
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.confirm = () => false;
const ANSWERS = %s;
const asked = [];
globalThis.fetch = async (path) => {
  asked.push(path);
  const key = Object.keys(ANSWERS).find((k) => path.startsWith(k));
  const data = key ? ANSWERS[key] : { error: "not found" };
  return { status: key ? 200 : 404, ok: Boolean(key), statusText: "", json: async () => data };
};
process.on("uncaughtException", (e) => { console.error(e.stack); process.exit(2); });
process.on("unhandledRejection", (e) => { console.error(e && e.stack || e); process.exit(2); });

require(%r);

setTimeout(() => {
  const $ = document.querySelector;
  const text = (e) => e.textContent + e.children.map(text).join("");
  console.log(JSON.stringify({
    asked,
    app: !$("#app").hidden, login: $("#login").hidden,
    banner: $("#run-banner").textContent,
    plans: $("#plan").children.map((o) => [o.attrs.value, o.textContent, o.disabled]),
    cards: $("#cards").children.map(text),
    log: $("#log").textContent,
    ended: text($("#ended")), endedHidden: $("#ended").hidden,
    startBox: $("#start-box").hidden, runBox: $("#run-box").hidden, end: $("#end").hidden,
  }));
  process.exit(0);
}, 200);
"""


@pytest.fixture(scope="module")
def page():
    answers = {"/api/info": {"demo": False}, "/api/me": {"user": "tester", "demo": False},
               "/api/status": STATUS, "/api/plans": {"plans": PLANS}}
    script = SCRIPT % (json.dumps(answers), str(APP))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_page_logs_in_and_polls_the_status(page):
    assert page["app"] and page["login"]
    assert any(p.startswith("/api/status") for p in page["asked"])
    assert page["banner"].startswith("No run is going. Last run: plan DARKNESS.")


def test_the_start_list_holds_every_plan_that_can_be_run_and_nothing_else(page):
    """A plan is listed with its rScripts; one that cannot run is greyed and says so.
    The end script, a block and an orbit are never offered: End runs the first, a
    plan runs the others."""
    assert "/api/plans" in page["asked"]
    names = [name for name, _, _ in page["plans"]]
    assert names == ["tvac", "gevs_unit", "broken"]
    labels = dict((name, label) for name, label, _ in page["plans"])
    assert labels["tvac"] == "tvac - rLACO, rPSU - until you end it"
    assert labels["gevs_unit"].endswith("checks at the start")
    assert labels["broken"] == "broken (cannot run)"
    assert [disabled for _, _, disabled in page["plans"]] == [False, False, True]


def test_every_instrument_has_a_card_and_the_log_is_shown(page):
    hvc, psu1 = page["cards"]                                 # in the page's order: the chamber first
    assert hvc.startswith("hvc") and "live" in hvc and "Valves" in hvc and "Closed" in hvc
    assert psu1.startswith("psu1") and "no run is going" in psu1 and "CH1 cooler" in psu1
    assert page["log"] == "12:00:00 host: run DARKNESS ended\n12:00:01 host: bye"


def test_with_no_run_going_start_is_offered_and_the_run_buttons_are_not(page):
    assert page["startBox"] is False
    assert page["runBox"] is True and page["end"] is True
    assert page["endedHidden"] is False
    assert "DARKNESS ended at" in page["ended"] and "749 Torr" in page["ended"]
