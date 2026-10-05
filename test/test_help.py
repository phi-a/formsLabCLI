"""Help while writing: the card for a command or a plan step (Grammar.describe),
with its prerequisites as a plan leaves them (describe_step) or as the chamber is
now (cast.describe), and /api/describe."""
import time

import pytest

from formslab.console.cast import castutils
from formslab.rscripts import cast
from formslab.rscripts.grammar import Grammar
from formslab.sequence import find_plan
from formslab.sequence.plan import describe_step

G = Grammar([
    ("lamp on|off", "Lamp\n    Switches the lamp.\n\n    A second paragraph.", lambda s: {"lamp": s}),
    ("lamp level <n:integer 0..10>", "Brightness", lambda n: {"level": n}),
])


def test_a_whole_command_has_one_card_with_its_details_and_inputs():
    [card] = G.describe(["lamp", "level", "3"])
    assert card == {"usage": "lamp level <n>", "help": "Brightness", "details": "", "complete": True,
                    "inputs": [{"name": "n", "kind": "integer", "lo": 0.0, "hi": 10.0, "unit": ""}],
                    "words": [{"text": "lamp", "role": "word"}, {"text": "level", "role": "word"},
                              {"text": "n", "role": "slot", "kind": "integer", "unit": ""}]}
    [card] = G.describe(["lamp", "on"])
    assert card["details"] == "Switches the lamp.\n\nA second paragraph."
    assert card["inputs"] == [{"name": "", "kind": "choice", "choices": ["on", "off"]}]


def test_a_begun_line_lists_what_it_can_become_and_a_bad_value_keeps_its_card():
    assert [c["usage"] for c in G.describe(["lamp"])] == ["lamp on|off", "lamp level <n>"]
    [card] = G.describe(["lamp", "level", "99"])
    assert card["usage"] == "lamp level <n>" and not card["complete"]
    assert G.describe([]) == [] and G.describe(["kettle"]) == []


def test_the_summary_stays_one_line_for_tooltips_and_the_help_table():
    assert G.rows()[0] == ("lamp on|off", "Lamp")
    assert {o.help for o in G.complete(["lamp"]) if o.text in ("on", "off")} == {"Lamp"}


def test_every_chamber_command_explains_itself():
    for row in cast.grammar().describe(["hvc"], limit=100):
        assert row["help"], row["usage"]
    for words in (["hvc", "rough", "open"], ["hvc", "stop"], ["hvc", "platen", "20"], ["hvc", "purge"],
                  ["psu1", "ch1", "set", "1", "0.1"], ["cryo", "on"]):
        [card] = cast.grammar().describe(words)
        assert card["details"], words


def test_a_plan_step_shows_its_prerequisites_as_the_plan_leaves_them():
    text = find_plan("laco_vent").read_text(encoding="utf-8")
    vent = next(n for n, ln in enumerate(text.splitlines(), 1) if ln.startswith("hvc vent open"))
    info = describe_step(text, vent)
    assert info["cards"][0]["usage"] == "hvc <valve> open|close"
    status = {c["text"]: c["status"] for r in info["rules"] for c in r["conditions"]}
    assert status == {"rough closed": "unknown", "gate closed": "unknown", "platenT above 10 C": "ok",
                      "platenT below 60 C": "ok", "shroudT above 10 C": "ok", "shroudT below 60 C": "ok",
                      "no fault": "live"}                      # the fault rule, which covers every command


def test_a_plan_step_that_breaks_a_rule_shows_where():
    info = describe_step("load rLACO\nhvc rough open\nhvc vent open\n", 3)
    [cond] = [c for r in info["rules"] for c in r["conditions"] if c["status"] == "broken"]
    assert cond["text"] == "rough closed (line 2 changed it)"


def test_the_load_record_and_comment_lines():
    text = "# a comment\nload rLACO\nrecord every 2 s\nhvc stop\n"
    assert describe_step(text, 1) == {"cards": [], "rules": []}
    assert describe_step(text, 2)["cards"][0]["usage"] == "load <rScript> ..."
    assert describe_step(text, 3)["cards"][0]["help"] == "Set how often values are recorded"
    assert describe_step(text, 4)["rules"] == []                # `stop` is always allowed
    assert describe_step(text, 99) == {"cards": [], "rules": []}


def test_the_command_box_shows_prerequisites_as_the_chamber_is_now():
    castutils.UpdateStatus("hvc", {"connected": True, "fault_severity": "N", "rough": True, "gate": False,
                                   "platen C": 20.0, "shroud C": 20.0})
    info = cast.describe(["hvc", "vent", "open"])
    status = {c["text"]: c["status"] for r in info["rules"] for c in r["conditions"]}
    assert status["rough closed"] == "broken" and status["gate closed"] == "ok" and status["no fault"] == "ok"


@pytest.fixture
def client(monkeypatch):
    from gui_helpers import Client, running_server
    with running_server(monkeypatch) as server:
        yield Client(server).login(), Client(server)


def test_describe_over_http(client):
    me, stranger = client
    code, body = me.json("POST", "/api/describe", {"text": "load rLACO\nhvc pump on\n", "line": 2})
    assert code == 200 and body["cards"][0]["help"] == "Turn a pump on or off"
    code, body = me.json("POST", "/api/describe", {"words": ["hvc", "gate", "open"]})
    assert code == 200 and any(c["text"] == "turbo on" for r in body["rules"] for c in r["conditions"])
    assert me.json("POST", "/api/describe", {"words": "hvc"})[0] == 400
    assert stranger.json("POST", "/api/describe", {"words": []})[0] == 401


# --- friendly names on the status page ------------------------------------------------------------

def test_each_owner_names_its_readings():
    assert cast.status_labels("hvc", {"platen C": 20.0, "t2 C": 21.0, "platen setpoint C": 20.0,
                                      "rough": False, "mystery": 1}) == {
        "platen C": "Platen (C)", "t2 C": "t2 (C)", "platen setpoint C": "Platen setpoint (C)",
        "rough": "Rough valve"}                                      # an unnamed key keeps its own
    assert cast.status_labels("psu1", {"1": {"vset": 24.0}, "2": {"on": False}}) == {
        "1 vset": "CH1 set (V) - cryocooler board", "2 on": "CH2 output"}
    assert cast.status_labels("cryo", {"CCVINM": 24.0})["CCVINM"] == "Supply (V)"
    assert cast.status_labels("tc", {"TC01 C": 20.0}) == {"TC01 C": "TC01 (C)"}


def test_the_status_page_and_the_cast_tab_use_them(monkeypatch):
    from formslab.console.cast import castcli
    from formslab.gui import api
    castutils.UpdateStatus("hvc", {"connected": True, "rough": False})
    assert api.status()["blocks"]["hvc"]["labels"] == {"connected": "Connected", "rough": "Rough valve"}
    text = castcli.status_panel("hvc").content.plain
    assert "Rough valve" in text and "rough " not in text
    castutils.UpdateStatus("psu1", {"1": {"on": True, "vset": 24.0, "cset": 2.0, "vmeas": 24.0, "cmeas": 0.4}})
    assert "cryocooler board" in castcli.status_panel("psu1").content.plain
