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
    available_rscripts, check_text, line_options, search_dirs, tokens, user_plans_dir,
)

from gui_helpers import Client, running_server

SHIPPED = ("tvac", "laco_pumpdown", "laco_vent", "psu1_smtc08_first")
DRAFT = """# a draft with several mistakes
load rLACO rNotThere
record every 0 s
hvc pump onn
hold 30s
until chamberp < 5 C
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
    assert "chamberP is in Torr; C and K only apply to temperatures" in errors[6]
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
    assert [o["text"] for o in first] == ["hold", "log", "repeat", "end", "until", "when", "hvc", "eclipse", "pumpdown", "sunrise", "vent"]   # blocks last
    assert [o["text"] for o in second][:3] == ["platen", "shroud", "vacuum"]
    assert third[0] == {"kind": "number", "text": "temperature", "help": "Set the platen temperature",
                        "lo": -180.0, "hi": 200.0, "unit": "C", "part": "zone"}
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
    assert "until" not in [o["text"] for o in line_options(["rSLTA"], [])["positions"][0]]   # nothing to wait on


def test_the_rscripts_a_plan_can_load():
    assert available_rscripts() == ["rCryoBoard", "rLACO", "rOrbit", "rPSU", "rSLTA", "rSMTC08"]


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
def server(monkeypatch):
    with running_server(monkeypatch) as srv:
        yield srv


@pytest.fixture
def client(server):
    return Client(server).login()


def test_the_editor_routes_need_a_login_and_the_header(monkeypatch):
    with running_server(monkeypatch) as server:
        anon = Client(server)
        assert anon.json("GET", "/api/plans/tvac")[0] == 401
        assert anon.json("GET", "/api/rscripts")[0] == 401
        for path, body in (("/api/plan/check", {"text": ""}), ("/api/plan/line", {"scripts": [], "words": []}),
                           ("/api/plan/save", {"name": "x", "text": "x", "as_new": True}),
                           ("/api/plan/edit", {"name": "tvac", "base_hash": "x"}),
                           ("/api/plan/ship", {"name": "tvac", "base_hash": "x"})):
            assert anon.json("POST", path, body)[0] == 401
            c = Client(server).login()
            assert c.call("POST", path, body, headers={"X-Requested-With": ""})[0].status == 403
        assert not user_plans_dir().exists() or list(user_plans_dir().glob("*.plan")) == []


def test_read_check_and_save_over_http(client):
    code, body = client.json("GET", "/api/plans/laco_vent")
    assert code == 200 and body["editable"] is False and body["errors"] == [] and "vent open" in body["text"]
    assert client.json("GET", "/api/plans/..%2ftvac")[0] == 400
    assert client.json("GET", "/api/plans/nope")[0] == 404
    assert client.json("GET", "/api/rscripts")[1]["rscripts"] == ["rCryoBoard", "rLACO", "rOrbit", "rPSU", "rSLTA", "rSMTC08"]

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


# --- each word's role, for drawing the grammar -------------------------------------------------------

def roles(text):
    from formslab.sequence.plan import tokens
    return [[(t["text"], t["role"]) for t in line] for line in tokens(text)]


def test_every_word_gets_its_role_in_the_grammar():
    got = roles("# pump down\nload rLACO rNope\nrecord every 5 s\n\nhvc pump on\nhold 15 s\n"
                "until platenT > 10 C within 30 s\nlog pumpdown done: closed\n")
    assert got == [
        [("# pump down", "comment")],
        [("load", "verb"), ("rLACO", "script"), ("rNope", "bad")],
        [("record", "verb"), ("every", "kw"), ("5", "value"), ("s", "kw")],
        [],
        [("hvc", "verb"), ("pump", "kw"), ("on", "kw")],
        [("hold", "verb"), ("15", "value"), ("s", "kw")],
        [("until", "verb"), ("platenT", "kw"), (">", "kw"), ("10", "value"), ("C", "unit"),
         ("within", "kw"), ("30", "value"), ("s", "kw")],
        [("log", "verb"), ("pumpdown", "text"), ("done:", "text"), ("closed", "text")],
    ]


def test_what_does_not_fit_is_marked_bad():
    got = roles("load rLACO\nhvc platen 900\nhvc teleport now\npsu1 ch1 on\nhvc platen 25\n")
    assert got[1] == [("hvc", "verb"), ("platen", "kw"), ("900", "bad")]          # out of range
    assert got[2] == [("hvc", "verb"), ("teleport", "bad"), ("now", "bad")]       # and all after it
    assert got[3] == [("psu1", "bad"), ("ch1", "bad"), ("on", "bad")]             # rPSU not loaded
    assert got[4] == [("hvc", "verb"), ("platen", "kw"), ("25", "value")]


@pytest.mark.parametrize("name", SHIPPED)
def test_a_shipped_plan_has_no_bad_words(name):
    flat = [r for line in roles(find_plan(name).read_text(encoding="utf-8")) for _, r in line]
    assert "bad" not in flat and "verb" in flat


# --- delete ----------------------------------------------------------------------------------------------

def test_deleting_your_plan_moves_it_to_the_trash():
    saved = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    out = plans.delete("mine", saved["hash"])
    trashed = Path(out["trash"])
    assert out["deleted"] == "mine" and trashed.parent == plans.trash_dir()
    assert trashed.read_text(encoding="utf-8") == "load rSMTC08\nhold 1 s\n"           # kept, not erased
    assert not (user_plans_dir() / "mine.plan").exists() and find_plan("mine") is None
    assert "mine" not in {p.stem for p in discover()}                                   # the trash is not searched


def test_a_shipped_plan_cannot_be_deleted():
    before = find_plan("tvac").read_bytes()
    with pytest.raises(plans.PlanFileError) as e:
        plans.delete("tvac", sha(find_plan("tvac")))
    assert e.value.code == 403 and find_plan("tvac").read_bytes() == before


def test_a_plan_changed_since_it_was_opened_is_not_deleted():
    plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    with pytest.raises(plans.PlanFileError, match="changed on disk") as e:
        plans.delete("mine", "stale-hash")
    assert e.value.code == 409 and (user_plans_dir() / "mine.plan").exists()


def test_deleting_an_unknown_or_path_like_name_is_a_404():
    for name in ("nope", "../tvac", "", "tvac.plan"):
        with pytest.raises(plans.PlanFileError) as e:
            plans.delete(name, "x")
        assert e.value.code == 404


def test_delete_and_tokens_over_http(client, monkeypatch):
    saved = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    assert client.json("POST", "/api/plan/delete", {"name": "tvac", "base_hash": "x"})[0] == 403
    monkeypatch.setattr(api, "host", lambda: {"pid": 1, "plan": "mine", "started": "t", "output": "/x"})
    code, body = client.json("POST", "/api/plan/delete", {"name": "mine", "base_hash": saved["hash"]})
    assert code == 409 and "is running" in body["error"]                                # not the plan running
    monkeypatch.setattr(api, "host", lambda: None)
    code, body = client.json("POST", "/api/plan/delete", {"name": "mine", "base_hash": saved["hash"]})
    assert code == 200 and body["deleted"] == "mine"
    assert "tester delete plan mine" in (config.run_dir() / "gui.log").read_text(encoding="utf-8")
    code, body = client.json("POST", "/api/plan/tokens", {"text": "load rLACO\nhvc vent open\n"})
    assert code == 200 and [t["role"] for t in body["lines"][1]] == ["verb", "kw", "kw"]


# --- edit and ship ----------------------------------------------------------------------------------------

@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A shipped folder of our own: the folder labcli started from, which is read-only
    here like the checkout's, and where Ship puts files."""
    folder = tmp_path / "plans"
    folder.mkdir()
    (folder / "ours.plan").write_text("load rSMTC08\nhold 1 s\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(plans.planfile, "shipped_dir", lambda: folder)
    return folder


def test_edit_takes_a_shipped_file_out_to_yours_and_ship_puts_it_back(shipped):
    before = plans.read("ours")
    assert before["editable"] is False
    edited = plans.edit("ours", before["hash"])
    assert edited["editable"] is True and edited["text"] == before["text"]
    assert not (shipped / "ours.plan").exists() and find_plan("ours") == user_plans_dir() / "ours.plan"
    changed = plans.save("ours", "load rSMTC08\nhold 2 s\n", edited["hash"], as_new=False)
    back = plans.ship("ours", changed["hash"])
    assert back["editable"] is False and back["text"] == "load rSMTC08\nhold 2 s\n"
    assert (shipped / "ours.plan").exists() and not (user_plans_dir() / "ours.plan").exists()


def test_a_file_of_yours_never_shipped_ships_into_the_shipped_folder(shipped):
    saved = plans.save("newone", "load rSMTC08\nhold 1 s\n", None, as_new=True, kind="plan")
    assert plans.ship("newone", saved["hash"])["editable"] is False and (shipped / "newone.plan").exists()


def test_edit_and_ship_refuse_what_is_already_there_or_changed(shipped):
    ours = plans.read("ours")
    with pytest.raises(plans.PlanFileError, match="already shipped"):
        plans.ship("ours", ours["hash"])
    with pytest.raises(plans.PlanFileError, match="changed on disk"):
        plans.edit("ours", "stale")
    edited = plans.edit("ours", ours["hash"])
    with pytest.raises(plans.PlanFileError, match="already yours"):
        plans.edit("ours", edited["hash"])
    (shipped / "ours.plan").write_text("load rSMTC08\nhold 9 s\n", encoding="utf-8")    # a pull put it back
    with pytest.raises(plans.PlanFileError, match="hidden behind it") as e:
        plans.ship("ours", edited["hash"])
    assert e.value.code == 409 and (user_plans_dir() / "ours.plan").exists()


def test_a_file_with_problems_is_not_shipped(shipped):
    saved = plans.save("broken", DRAFT, None, as_new=True)
    with pytest.raises(plans.PlanFileError, match="problem") as e:
        plans.ship("broken", saved["hash"])
    assert e.value.code == 409 and not (shipped / "broken.plan").exists()


def test_edit_and_ship_over_http(client, shipped, monkeypatch):
    ours = plans.read("ours")
    monkeypatch.setattr(api, "host", lambda: {"pid": 1, "plan": "ours", "started": "t", "output": "/x"})
    code, body = client.json("POST", "/api/plan/edit", {"name": "ours", "base_hash": ours["hash"]})
    assert code == 409 and "is running" in body["error"]
    monkeypatch.setattr(api, "host", lambda: None)
    code, body = client.json("POST", "/api/plan/edit", {"name": "ours", "base_hash": ours["hash"]})
    assert code == 200 and body["editable"] is True
    code, body = client.json("POST", "/api/plan/ship", {"name": "ours", "base_hash": body["hash"]})
    assert code == 200 and body["editable"] is False
    log = (config.run_dir() / "gui.log").read_text(encoding="utf-8")
    assert "tester edit plan ours" in log and "tester ship plan ours" in log


# --- rename -------------------------------------------------------------------------------------------------

SEAL = "# Seal the chamber\nblock seal\nload rLACO\n\nhvc vent close\n"


def test_renaming_a_block_renames_its_line_and_your_calls_to_it():
    saved = plans.save("seal", SEAL, None, as_new=True, kind="block")
    plans.save("mine", "load rLACO\n\nseal\n  seal\n# seal stays: a comment\nlog seal is done\n", None, as_new=True)
    out = plans.rename("seal", "close_up", saved["hash"])
    assert out["name"] == "close_up" and out["kind"] == "block" and out["updated"] == ["mine"]
    assert "block close_up\n" in out["text"] and not (user_plans_dir() / "seal.block").exists()
    assert plans.read("mine")["text"] == "load rLACO\n\nclose_up\n  close_up\n# seal stays: a comment\nlog seal is done\n"
    assert plans.read("mine")["errors"] == []


def test_renaming_an_orbit_renames_the_plans_that_follow_it():
    orbit = (Path(__file__).resolve().parent / "fixtures" / "plans" / "leo_noon.orbit").read_text(encoding="utf-8")
    saved = plans.save("my_orbit", orbit, None, as_new=True, kind="orbit")
    plans.save("watch", "load rOrbit\n\norbit follow my_orbit\nhold 1 s\n", None, as_new=True)
    out = plans.rename("my_orbit", "noon2", saved["hash"])
    assert out["updated"] == ["watch"] and "orbit follow noon2" in plans.read("watch")["text"]
    assert plans.read("watch")["errors"] == []


def test_a_plan_is_renamed_and_nothing_else_changes():
    saved = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    out = plans.rename("mine", "Mine-2", saved["hash"])
    assert out["name"] == "Mine-2" and out["updated"] == [] and find_plan("Mine-2") and find_plan("mine") is None


def test_rename_refuses_what_it_should():
    saved = plans.save("seal", SEAL, None, as_new=True, kind="block")
    for new, code in (("Two Words", 400), ("hold", 400), ("hvc", 409), ("tvac", 409), ("vent", 409)):
        with pytest.raises(plans.PlanFileError) as e:
            plans.rename("seal", new, saved["hash"])
        assert e.value.code == code, new
    with pytest.raises(plans.PlanFileError, match="changed on disk"):
        plans.rename("seal", "other", "stale")
    with pytest.raises(plans.PlanFileError, match="shipped") as e:
        plans.rename("tvac", "tvac2", sha(find_plan("tvac")))
    assert e.value.code == 403


def test_a_block_a_shipped_plan_calls_is_not_renamed(shipped):
    saved = plans.save("seal", SEAL, None, as_new=True, kind="block")
    (shipped / "uses_seal.plan").write_text("load rLACO\nseal\n", encoding="utf-8")
    with pytest.raises(plans.PlanFileError, match="uses_seal") as e:
        plans.rename("seal", "close_up", saved["hash"])
    assert e.value.code == 409 and (user_plans_dir() / "seal.block").exists()


def test_a_change_of_case_only_keeps_the_file():
    saved = plans.save("mine", "load rSMTC08\nhold 1 s\n", None, as_new=True)
    assert plans.rename("mine", "MINE", saved["hash"])["text"] == "load rSMTC08\nhold 1 s\n"
    assert [p.name for p in user_plans_dir().glob("*.plan")] == ["MINE.plan"]


def test_rename_over_http(client, monkeypatch):
    saved = plans.save("seal", SEAL, None, as_new=True, kind="block")
    monkeypatch.setattr(api, "host", lambda: {"pid": 1, "plan": "seal", "started": "t", "output": "/x"})
    assert client.json("POST", "/api/plan/rename", {"name": "seal", "new": "x", "base_hash": saved["hash"]})[0] == 409
    monkeypatch.setattr(api, "host", lambda: None)
    code, body = client.json("POST", "/api/plan/rename", {"name": "seal", "new": "close_up", "base_hash": saved["hash"]})
    assert code == 200 and body["name"] == "close_up"
    assert "tester rename block seal to close_up" in (config.run_dir() / "gui.log").read_text(encoding="utf-8")


def test_a_blocks_name_is_drawn_as_its_name():
    from formslab.sequence.block import tokens
    assert [(t["text"], t["role"]) for t in tokens(SEAL)[1]] == [("block", "verb"), ("seal", "name")]
    assert [t["role"] for t in tokens("block pumpdown to <p:number 1..2 Torr>\nload rLACO\n")[0]] == \
        ["verb", "name", "kw", "value"]


# --- which rScripts a plan's steps use (to put a deleted load line back) -----------------------------------

def test_the_rscripts_the_steps_use_are_found_from_the_steps():
    from formslab.sequence.plan import needed_rscripts
    assert needed_rscripts("hvc vent open\nuntil chamberP > 700 within 1 min\n") == ["rLACO"]
    assert needed_rscripts("psu1 ch1 on\nuntil TC01 > 30 C within 1 min\nhvc stop\n") == ["rLACO", "rPSU", "rSMTC08"]
    assert needed_rscripts("# a note\nload rLACO\nrecord every 2 s\nlog hi\nhold 5 s\n") == []   # nothing uses an instrument
    assert needed_rscripts("") == [] and needed_rscripts("hvc teleport now\nnonsense\n") == ["rLACO"]


@pytest.mark.parametrize("name", SHIPPED)
def test_what_the_steps_need_is_always_part_of_the_plans_load_line(name):
    """A plan may load more than its steps use (psu1_smtc08_first loads rSMTC08 only to
    record), never less: the inferred scripts are a subset of what the plan loads."""
    from formslab.sequence.plan import needed_rscripts
    text = find_plan(name).read_text(encoding="utf-8")
    loaded = next(ln.split()[1:] for ln in text.splitlines() if ln.split()[:1] == ["load"])
    assert set(needed_rscripts(text)) <= set(loaded)


@pytest.mark.parametrize("name, expected", [("laco_vent", ["rLACO"]), ("laco_pumpdown", ["rLACO"]),
                                            ("psu1_smtc08_first", ["rPSU"]), ("tvac", [])])
def test_the_load_line_comes_back_as_the_steps_need_it(name, expected):
    from formslab.sequence.plan import needed_rscripts
    assert needed_rscripts(find_plan(name).read_text(encoding="utf-8")) == expected


def test_needs_over_http(server, client):
    assert client.json("POST", "/api/plan/needs", {"text": "hvc vent open\n"}) == (200, {"rscripts": ["rLACO"]})
    assert Client(server).json("POST", "/api/plan/needs", {"text": ""})[0] == 401


# --- units inside their value's box ----------------------------------------------------------

def _box(scripts, line, k):
    r = line_options(scripts, line.split())
    o = next(o for o in r["positions"][k] if o["kind"] in ("number", "integer"))
    return o.get("units"), o.get("unit_default"), o.get("unit_implied")


@pytest.mark.parametrize("line, k, units", [
    ("until chamberP < 5 Torr", 3, (["Torr"], "Torr", "Torr")),          # the value's own unit
    ("until platenT > 10 C", 3, (["C", "K"], "C", "K")),                 # a temperature: C or K, Celsius first
    ("vent within 60 min", 2, (["min"], "min", None)),                    # a block input's fixed unit
    ("hold 5 min", 1, (None, None, None)),                                # a time: s, min, h stay a choice
    ("until chamberP < 5 Torr within 2 h", 6, (None, None, None)),
    ("hvc platen 40", 2, (None, None, None)),                             # no unit word follows
])
def test_a_number_says_which_units_its_box_holds(line, k, units):
    assert _box(["rLACO"], line, k) == units


def test_a_read_only_line_draws_a_unit_in_its_value():
    rows = tokens("load rLACO\nvent within 60 min\nuntil chamberP < 5 Torr within 2 h\n")
    assert [t["role"] for t in rows[1]] == ["verb", "kw", "value", "unit"]
    assert [t["role"] for t in rows[2]] == ["verb", "kw", "kw", "value", "unit", "kw", "value", "kw"]
