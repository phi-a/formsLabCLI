"""The plan editor's backend: every problem at once, what can come next at each
position of a line, and the plan files (read-only shipped plans, a folder of
your own, name rules, changed-on-disk refusals)."""
import hashlib
import os
from pathlib import Path

import pytest

from formslab import config
from formslab.gui import api, plans
from formslab.sequence import PlanError, discover, find_plan, parse_plan
from formslab.sequence.plan import (
    available_rscripts, check_text, line_options, search_dirs, user_plans_dir,
)

from gui_helpers import Client, running_server

SHIPPED = ("tvac", "laco_pumpdown", "laco_vent", "psu1_smtc08_first")
DRAFT = """# a draft with several mistakes
load rLACO rNotThere
record every 0 s
hvc pump onn
hold 30s
until chamberp below 5
orbit.a = 7
log fine
psu1 ch1 on
"""


# --- every problem, with its line ---------------------------------------------------------------

def test_a_draft_reports_every_problem_with_its_line():
    errors = dict(check_text(DRAFT))
    assert errors[2] == "rScript rNotThere not found"
    assert errors[3] == "record needs a positive duration"
    assert "did you mean 'on'" in errors[4]
    assert "write `30 s`, with a space" in errors[5]
    assert "until needs `timeout" in errors[6]
    assert "FORMS mission configuration" in errors[7]
    assert errors[9] == "psu1 is declared by rPSU; add it to `load`"
    assert 8 not in errors and 1 not in errors                         # the log line and the comment are fine
    assert [n for n, _ in check_text(DRAFT)] == sorted(n for n, _ in check_text(DRAFT))


def test_the_first_error_is_still_the_message_and_all_are_on_the_exception():
    with pytest.raises(PlanError) as e:
        parse_plan(DRAFT.replace("rNotThere", "rSMTC08"))
    assert str(e.value).startswith("<plan>:3: record needs a positive duration")
    assert len(e.value.errors) >= 5 and e.value.errors[0][0] == 3


def test_problems_with_the_whole_document_are_line_zero():
    assert check_text("hold 1 s\n") == [(0, "a plan starts with `load <rScript> ...`, the rScripts that own its instruments")]
    assert check_text("load rPSU\n") == [(0, "the plan has no steps")]
    assert check_text("") == [(0, "a plan starts with `load <rScript> ...`, the rScripts that own its instruments")]
    assert check_text("load rPSU\nsequence.operations = []\n")[0][1].startswith("this is the old plan format")


def test_a_missing_rscript_is_flagged_for_the_editor_though_reading_accepts_it():
    parse_plan("load rNotThere\nhold 1 s\n")                           # the host refuses it later
    assert check_text("load rNotThere\nhold 1 s\n") == [(1, "rScript rNotThere not found")]


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_plan_checks_clean(name):
    assert check_text(find_plan(name).read_text(encoding="utf-8")) == []


# --- what can come next at each position ----------------------------------------------------------

def test_options_at_every_position_of_a_step():
    r = line_options(["rLACO"], ["hvc", "platen"])
    first, second, third = r["positions"]
    assert [o["text"] for o in first] == ["hold", "log", "until", "hvc"]
    assert [o["text"] for o in second][:3] == ["platen", "shroud", "vacuum"]
    assert third[0] == {"kind": "number", "text": "C", "help": "platen setpoint (refused outside the profile limits)",
                        "lo": -180.0, "hi": 200.0, "unit": "C"}
    assert [o["text"] for o in third[1:]] == ["on", "off", "rate", "range"]
    assert r["complete"] is False and r["error"] is None               # unfinished is a valid start


def test_a_finished_line_is_complete_and_a_wrong_one_says_why():
    assert line_options(["rLACO"], ["hvc", "platen", "20"])["complete"] is True
    assert line_options(["rLACO"], ["hold", "5", "min"])["complete"] is True
    assert "900 is outside -180..200 C" in line_options(["rLACO"], ["hvc", "platen", "900"])["error"]
    assert "write `30 s`" in line_options(["rLACO"], ["hold", "30s"])["error"]
    assert line_options(["rLACO"], ["hold", "30"])["error"] is None
    assert [o["text"] for o in line_options(["rLACO"], ["hold", "30"])["positions"][-1]] == ["s", "min", "h"]


def test_a_label_whose_rscript_is_not_loaded_says_to_load_it():
    r = line_options(["rLACO"], ["psu1", "ch1"])
    assert r["error"] == "psu1 is declared by rPSU; add it to `load`"
    assert "psu1" in [o["text"] for o in line_options(["rLACO", "rPSU"], [])["positions"][0]]


def test_until_offers_exactly_what_the_loaded_scripts_publish():
    names = [o["text"] for o in line_options(["rSMTC08"], ["until"])["positions"][1]]
    assert names == [f"TC{i:02d}" for i in range(1, 17)]
    assert "until" not in [o["text"] for o in line_options(["rCryoBoard"], [])["positions"][0]]   # nothing to wait on


def test_the_rscripts_a_plan_can_load():
    assert available_rscripts() == ["rCryoBoard", "rLACO", "rPSU", "rSLTA", "rSMTC08"]


# --- where plans live -------------------------------------------------------------------------------

def test_this_machines_plans_are_searched_last_so_they_cannot_shadow_a_shipped_plan():
    user = user_plans_dir()
    assert user == config.config_dir() / "plans" and user not in search_dirs()     # not there yet: not listed
    user.mkdir()
    (user / "tvac.plan").write_text("load rSMTC08\nhold 1 s\n", encoding="utf-8")
    (user / "mine.plan").write_text("load rSMTC08\nhold 1 s\n", encoding="utf-8")
    assert search_dirs()[-1] == user
    assert find_plan("tvac") != user / "tvac.plan" and find_plan("tvac").parent.name == "plans"
    assert [p.parent for p in discover() if p.stem == "tvac"] == [find_plan("tvac").parent]
    assert find_plan("mine") == user / "mine.plan"


# --- the plan files -----------------------------------------------------------------------------------

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def test_shipped_plans_are_read_only_and_listed_as_such():
    listing = {p["name"]: p for p in api.list_plans()}
    assert all(listing[n]["editable"] is False for n in SHIPPED)
    r = plans.read("tvac")
    assert r["editable"] is False and r["errors"] == [] and r["text"].rstrip().endswith("hold until end")
    assert r["hash"] == sha(find_plan("tvac"))


def test_save_as_makes_a_copy_in_this_machines_folder():
    text = find_plan("laco_pumpdown").read_text(encoding="utf-8")
    saved = plans.save("my_pumpdown", text, None, as_new=True)
    path = user_plans_dir() / "my_pumpdown.plan"
    assert saved["errors"] == [] and saved["editable"] is True
    assert path.read_text(encoding="utf-8") == text                    # comments, blank lines, all of it
    assert saved["hash"] == sha(path)
    assert {p["name"]: p["editable"] for p in api.list_plans()}["my_pumpdown"] is True


@pytest.mark.parametrize("name", SHIPPED)
def test_a_copy_of_each_shipped_plan_is_the_same_text(name):
    text = find_plan(name).read_text(encoding="utf-8")
    plans.save(f"copy_of_{name}", text, None, as_new=True)
    assert (user_plans_dir() / f"copy_of_{name}.plan").read_text(encoding="utf-8") == text


@pytest.mark.parametrize("name", ["", "../x", "..\\x", "a b", "a/b", ".hidden", "x" * 65, "ünï", "tvac.plan", "-lead"])
def test_a_plan_name_is_a_short_plain_word(name):
    with pytest.raises(plans.PlanFileError) as e:
        plans.save(name, "load rPSU\nhold 1 s\n", None, as_new=True)
    assert e.value.code == 400
    assert not user_plans_dir().exists() or list(user_plans_dir().glob("*.plan")) == []


def test_a_name_already_taken_is_refused_even_by_a_shipped_plan():
    with pytest.raises(plans.PlanFileError, match="already exists") as e:
        plans.save("tvac", "load rPSU\nhold 1 s\n", None, as_new=True)
    assert e.value.code == 409
    plans.save("mine", "load rPSU\nhold 1 s\n", None, as_new=True)
    with pytest.raises(plans.PlanFileError, match="already exists"):
        plans.save("mine", "load rPSU\nhold 2 s\n", None, as_new=True)


def test_your_own_plan_can_be_overwritten_when_unchanged_since_it_was_opened():
    first = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    second = plans.save("mine", "load rSMTC08\nhold 2 s\n", first["hash"], as_new=False)
    assert plans.read("mine")["text"] == "load rSMTC08\nhold 2 s\n" and second["hash"] != first["hash"]


def test_a_file_changed_underneath_is_not_overwritten():
    first = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    path = user_plans_dir() / "mine.plan"
    path.write_text("load rSMTC08\nhold 99 s\n", encoding="utf-8")        # someone else saved meanwhile
    with pytest.raises(plans.PlanFileError, match="changed on disk") as e:
        plans.save("mine", "load rSMTC08\nhold 2 s\n", first["hash"], as_new=False)
    assert e.value.code == 409 and path.read_text(encoding="utf-8") == "load rSMTC08\nhold 99 s\n"
    with pytest.raises(plans.PlanFileError, match="changed on disk"):       # no hash at all is no proof either
        plans.save("mine", "x\n", None, as_new=False)


def test_a_shipped_plan_is_never_overwritten():
    before = find_plan("tvac").read_bytes()
    with pytest.raises(plans.PlanFileError, match="read-only") as e:
        plans.save("tvac", "load rPSU\nhold 1 s\n", sha(find_plan("tvac")), as_new=False)
    assert e.value.code == 403 and find_plan("tvac").read_bytes() == before


def test_a_plan_in_the_bench_plans_folder_is_editable(tmp_path, monkeypatch):
    bench = tmp_path / "bench"
    bench.mkdir()
    (bench / "bench_tvac.plan").write_text("load rSMTC08\nhold until end\n", encoding="utf-8")
    monkeypatch.setenv("FORMSLAB_PLANS_DIR", str(bench))
    assert plans.read("bench_tvac")["editable"] is True
    saved = plans.save("bench_tvac", "load rSMTC08\nhold 5 s\n", sha(bench / "bench_tvac.plan"), as_new=False)
    assert (bench / "bench_tvac.plan").read_text(encoding="utf-8") == "load rSMTC08\nhold 5 s\n"
    assert saved["errors"] == []


def test_text_is_saved_with_unix_newlines_and_a_final_newline():
    plans.save("crlf", "load rSMTC08\r\nhold 1 s", None, as_new=True)
    assert (user_plans_dir() / "crlf.plan").read_bytes() == b"load rSMTC08\nhold 1 s\n"


def test_a_draft_with_mistakes_is_saved_and_the_mistakes_come_back():
    saved = plans.save("draft", DRAFT, None, as_new=True)
    assert (user_plans_dir() / "draft.plan").read_text(encoding="utf-8") == DRAFT
    assert {e["line"] for e in saved["errors"]} >= {2, 3, 4, 5, 6, 7, 9}
    assert {p["name"]: p for p in api.list_plans()}["draft"]["error"].startswith("draft.plan:")   # cannot be started


def test_reading_an_unknown_or_path_like_name_is_a_404():
    for name in ("nope", "../tvac", "..\\tvac", "", "tvac.plan"):
        with pytest.raises(plans.PlanFileError) as e:
            plans.read(name)
        assert e.value.code == 404


# --- over HTTP ----------------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    with running_server(monkeypatch) as server:
        yield Client(server).login()


def test_the_editor_routes_need_a_login_and_the_header(monkeypatch):
    with running_server(monkeypatch) as server:
        anon = Client(server)
        assert anon.json("GET", "/api/plans/tvac")[0] == 401
        assert anon.json("GET", "/api/rscripts")[0] == 401
        for path, body in (("/api/plan/check", {"text": ""}), ("/api/plan/line", {"scripts": [], "words": []}),
                           ("/api/plan/save", {"name": "x", "text": "x", "as_new": True})):
            assert anon.json("POST", path, body)[0] == 401
            c = Client(server).login()
            assert c.call("POST", path, body, headers={"X-Requested-With": ""})[0].status == 403
        assert not user_plans_dir().exists() or list(user_plans_dir().glob("*.plan")) == []


def test_read_check_and_save_over_http(client):
    code, body = client.json("GET", "/api/plans/laco_vent")
    assert code == 200 and body["editable"] is False and body["errors"] == [] and "vent open" in body["text"]
    assert client.json("GET", "/api/plans/..%2ftvac")[0] == 400
    assert client.json("GET", "/api/plans/nope")[0] == 404
    assert client.json("GET", "/api/rscripts")[1]["rscripts"] == ["rCryoBoard", "rLACO", "rPSU", "rSLTA", "rSMTC08"]

    code, body = client.json("POST", "/api/plan/check", {"text": DRAFT})
    assert code == 200 and {"line": 3, "message": "record needs a positive duration"} in body["errors"]

    code, body = client.json("POST", "/api/plan/save", {"name": "mine", "text": "load rSMTC08\nhold 1 s\n", "as_new": True})
    assert code == 200 and body["errors"] == [] and body["editable"] is True
    code, body2 = client.json("POST", "/api/plan/save", {"name": "mine", "text": "load rSMTC08\nhold 2 s\n", "base_hash": "stale"})
    assert code == 409 and "changed on disk" in body2["error"]
    assert client.json("POST", "/api/plan/save", {"name": "tvac", "text": "x", "base_hash": "x"})[0] == 403
    assert client.json("POST", "/api/plan/save", {"name": "../x", "text": "x", "as_new": True})[0] == 400
    log = (config.run_dir() / "gui.log").read_text(encoding="utf-8")
    assert "tester save plan mine (new)" in log and "refused plan/save" in log


def test_line_options_over_http(client):
    code, body = client.json("POST", "/api/plan/line", {"scripts": ["rLACO"], "words": ["hvc", "pump"]})
    assert code == 200 and [o["text"] for o in body["positions"][2]] == ["on", "off"] and body["complete"] is False
    assert client.json("POST", "/api/plan/line", {"scripts": ["rLACO"], "words": [1, 2]})[0] == 400
    assert client.json("POST", "/api/plan/line", {"scripts": "rLACO", "words": []})[0] in (200, 400, 500)


def test_a_started_plan_can_be_one_you_saved(client, monkeypatch):
    from formslab.console.ctrl import ctrlcli
    plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    launched = []
    monkeypatch.setattr(ctrlcli, "_launch_sequence", lambda path: launched.append(path))
    monkeypatch.setattr(api, "host", lambda: None)
    assert client.json("POST", "/api/run", {"plan": "mine"})[0] == 200
    for _ in range(100):
        if launched:
            break
        __import__("time").sleep(0.02)
    assert Path(launched[0]) == (user_plans_dir() / "mine.plan").resolve()


# --- the documented example is a real plan ----------------------------------------------------------

def test_the_example_in_the_plan_docs_is_a_plan_that_checks_clean():
    """A docs example the parser rejects (an inline # on a load line, say) teaches
    the wrong thing; the first block in docs/SEQUENCE.md must be a working plan."""
    docs = (Path(__file__).resolve().parents[1] / "docs" / "SEQUENCE.md").read_text(encoding="utf-8")
    block = docs.split("```")[1]
    assert block.lstrip().startswith("# PSU1 CH1") and "load rPSU rSMTC08" in block
    assert check_text(block) == []
    assert parse_plan(block).rscripts == ("rPSU", "rSMTC08")
