"""Cast commands declared by the rScripts, and the cast tab that sends them.

The LACO table is checked against the controller's whole command surface: every
command a user can type turns into a request that `LACO.apply` accepts on the
simulator, so the panel and the chamber object cannot drift apart.
"""
import json

import pytest

from formslab import config, rscripts
from formslab.console.cast import castcli
from formslab.console.cast.castutils import ReadCommand
from formslab.devices.hvc3500 import BenchProfile
from formslab.devices.hvc3500.simulator import Simulator
from formslab.devices.hvc3500.laco import LACO
from formslab.rscripts import cast

# every LACO command, as typed after `hvc`, and the request it must produce
HVC = {
    "platen 25": {"platen": 25.0},
    "shroud -20": {"shroud": -20.0},
    "platen on": {"platen_control": True},
    "shroud off": {"shroud_control": False},
    "platen rate 2": {"platen_rate": 2.0},
    "shroud range 1.5": {"shroud_range": 1.5},
    "vacuum 1e-3": {"vacuum": 1e-3},
    "vacuum range 0.5": {"vacuum_range": 0.5},
    "vacuum rate 0.2": {"vacuum_rate": 0.2},
    "hold 600": {"hold_s": 600.0},
    "recipe 3": {"recipe": 3},
    "recipe start": {"recipe_run": True},
    "recipe stop": {"recipe_run": False},
    "start": {"start": True},
    "abort": {"abort": True},
    "reset": {"reset": True},
    "vent2atm": {"vent2atm": True},
    "fill2atm": {"fill2atm": True},
    "purge": {"purge": True},
    "closeall": {"close_all": True},
    "rough open": {"rough": "open"},
    "vent close": {"vent": "close"},
    "fill open": {"fill": "open"},
    "foreline close": {"foreline": "close"},
    "gate close": {"gate": "close"},
    "pump on": {"pump": "on"},
    "turbo off": {"turbo": "off"},
    "stop": {"stop_pumping": True},
}


@pytest.fixture(autouse=True)
def checkout_scripts(monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)


@pytest.mark.parametrize("typed, expected", HVC.items())
def test_every_laco_command_builds_its_request(typed, expected):
    assert cast.request("hvc", typed.split()) == expected


@pytest.fixture
def laco():
    with Simulator() as sim:
        d = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        d["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
        with LACO(BenchProfile.from_dict(d), inter_command_delay=0.0) as chamber:
            chamber.client.toggle_settle_s = 1.2
            yield chamber


def test_laco_apply_understands_every_command_request(laco):
    for typed, request in HVC.items():
        events = laco.apply(request)
        assert events and not any("unknown request key" in m or "expected" in m
                                  for _, m in events), (typed, events)


@pytest.mark.parametrize("typed, message", [
    ("platen 500", "hvc platen: 500 is outside -180..200 C"),     # profile limit 200 C
    ("shroud 150", "outside -180..120 C"),                        # each zone its own limits
    ("platen hot", "expected <C> (-180..200 C), on, off, rate or range after 'hvc platen', got 'hot'"),
    ("pump open", "expected on or off after 'hvc pump', got 'open'"),
    ("recipe 99", "outside 1..20"),
    ("vacuum -5", "must be >= 0"),                                # was a TypeError
    ("teleport", "after 'hvc', got 'teleport'"),
    ("vnt open", "did you mean 'vent'?"),
    ("", "incomplete: expected one of platen"),
])
def test_laco_refuses_what_it_cannot_send(typed, message):
    with pytest.raises(cast.GrammarError) as e:
        cast.request("hvc", typed.split())
    assert message in str(e.value)


@pytest.mark.parametrize("label, typed, expected", [
    ("psu1", "ch1 set 12 1.25", {"1": {"voltage": 12.0, "current": 1.25}}),
    ("psu2", "ch2 on", {"2": {"on": True}}),
    ("psu1", "ch3 protect 13 1.5", {"3": {"ovp": 13.0, "ocp": 1.5, "protect": True}}),
    ("psu1", "ch1 protect off", {"1": {"protect": False}}),
    ("psu1", "update", {"update": True}),
    ("cryo", "ccv 14", {"voltage": 14.0}),
    ("cryo", "on", {"enabled": True}),
    ("cryo", "ccvres 270", {"resistance": 270.0}),
    ("cryo", "code 12", {"code": 12}),
    ("cryo", "startup", {"startup": True}),
    ("slta", "image", {"image": True}),
    ("slta", "run on", {"SLTARUN": True}),
    ("slta", "exposure auto", {"exposureAuto": True}),
    ("slta", "exposure 600", {"exposure": 600}),
    ("slta", "imagedir darks", {"IMAGEDIR": "darks"}),
])
def test_instrument_commands(label, typed, expected):
    assert cast.request(label, typed.split()) == expected


@pytest.mark.parametrize("label, typed", [
    ("psu1", "ch4 on"), ("psu1", "ch1 set 40 1"), ("cryo", "ccv 25"), ("tc", "anything"),
])
def test_instrument_commands_refuse_bad_input(label, typed):
    with pytest.raises(cast.GrammarError):
        cast.request(label, typed.split())


def test_a_read_only_label_says_so():
    with pytest.raises(cast.GrammarError, match="tc takes no commands"):
        cast.request("tc", ["anything"])


def test_an_unknown_label_suggests_one():
    with pytest.raises(cast.GrammarError, match="did you mean 'hvc'"):
        cast.request("hcv", ["stop"])


def test_labels_and_keywords_ignore_case():
    assert cast.request("HVC", ["Vent", "OPEN"]) == {"vent": "open"}
    assert cast.request("psu1", ["CH2", "On"]) == {"2": {"on": True}}


def test_complete_is_what_a_dropdown_lists():
    first = [o.text for o in cast.complete([])]
    assert {"hvc", "psu1", "psu2", "cryo", "slta"} <= set(first) and "tc" not in first
    after = cast.complete(["hvc", "platen"])
    assert str(after[0]) == "<C> (-180..200 C)"
    assert [o.text for o in after[1:]] == ["on", "off", "rate", "range"]
    assert [o.text for o in cast.complete(["psu1", "ch1", "set"])] == ["V"]


def _declared(script):
    labels, _ = cast.owners()
    module = next(m for m in labels.values() if cast.script_name(m) == script)
    return dict(cast.variables(module))


def test_rlaco_declares_what_it_publishes(laco):
    """Every value rLACO publishes off a real reading is in its VARIABLES, with
    the same unit -- a plan's `until` is checked against that list."""
    import importlib.util
    from formslab.rscripts import Run
    path = next(d / "rLACO.py" for d in rscripts.search_dirs() if (d / "rLACO.py").exists())
    spec = importlib.util.spec_from_file_location("rLACO_publish_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    laco.apply({"platen": 25.0})
    run = Run()
    module._publish(run, laco, laco.status())
    declared = _declared("rLACO")
    assert run.names() and set(run.names()) <= set(declared)
    assert all(declared[n] == run.variable(n).unit for n in run.names())


def test_every_other_script_declares_its_published_names():
    psu = _declared("rPSU")
    assert psu["PSU1_CH1_V"] == "V" and psu["PSU2_CH3_I"] == "A" and psu["PSU1_CH2_ON"] is None
    tc = _declared("rSMTC08")
    assert list(tc) == [f"TC{i:02d}" for i in range(1, 17)] and set(tc.values()) == {"K"}
    assert _declared("rCryoBoard") == {} and _declared("rSLTA") == {}


def test_reading_the_declarations_touches_nothing(tmp_path, monkeypatch):
    """The console imports every rScript to read its commands: no threads, no
    files, no hardware. Only building rLACO's list reads (and seeds) the profile."""
    import threading
    from formslab import config
    cast._cache.clear()
    threads = threading.active_count()
    labels, errors = cast.owners()
    assert errors == {} and threading.active_count() == threads
    assert list(config.config_dir().iterdir()) == [] and list(config.output_dir().iterdir()) == []


def test_the_shipped_commands_are_unambiguous():
    """Every command in the tables parses to exactly one meaning (a zone named
    like a keyword would make two match)."""
    for typed in HVC:
        cast.request("hvc", typed.split())


def test_every_label_has_an_owner_and_a_cast_block():
    from formslab.state import build_default_cast_state

    labels, errors = cast.owners()
    assert errors == {}
    assert set(labels) == {"hvc", "psu1", "psu2", "cryo", "slta", "tc"}
    assert set(labels) <= set(build_default_cast_state())


@pytest.fixture
def run_going(monkeypatch):
    from formslab.console.cast import castutils
    from formslab.console.ctrl import ctrlcli
    monkeypatch.setattr(ctrlcli, "running", lambda: {"pid": 1, "plan": "tvac"})
    monkeypatch.setattr(castutils, "TAKE_S", 0.3)
    castutils.UpdateStatus("hvc", {"connected": True, "fault_severity": "N", "pressure": 700.0, "platen C": 20.0,
                                        "shroud C": 20.0, "rough": False, "vent": False, "fill": False,
                                        "foreline": False, "gate": False, "pump": False, "turbo": False})   # the chamber has just reported: sealed, at rest, no fault


def test_the_tab_writes_the_request_and_says_what_became_of_it(run_going):
    result = castcli.execute_command(["hvc", "vent", "open"])
    assert "hvc ←" in result.content.plain and "not taken within 0.3 s" in result.content.plain
    assert not result.ok
    assert ReadCommand("hvc") == {"vent": "open"}


def test_the_tab_refuses_when_no_run_is_going(monkeypatch):
    from formslab.console.ctrl import ctrlcli
    monkeypatch.setattr(ctrlcli, "running", lambda: None)
    result = castcli.execute_command(["hvc", "vent", "open"])
    assert "refused: no run is going" in result.content.plain and not result.ok
    assert ReadCommand("hvc") == {}


def test_the_tab_shows_the_owners_refusal(run_going):
    import threading
    from formslab.console.cast.castutils import ReportResult, TakeCommand

    def owner():
        for _ in range(300):
            req, ids = TakeCommand("hvc")
            if req:
                ReportResult("hvc", ids, False, ["rough: refused by the PLC"])
                return
            threading.Event().wait(0.01)

    thread = threading.Thread(target=owner, daemon=True)
    thread.start()
    result = castcli.execute_command(["hvc", "rough", "open"])
    thread.join(5)
    assert "✗ hvc ←" in result.content.plain
    assert "refused: rough: refused by the PLC" in result.content.plain


def test_the_tab_refuses_what_the_rules_forbid_and_sends_nothing(run_going):
    from formslab.console.cast.castutils import ReadStatus, UpdateStatus
    UpdateStatus("hvc", {**ReadStatus("hvc"), "gate": True})
    result = castcli.execute_command(["hvc", "rough", "open"])
    assert "refused, nothing sent: needs gate closed" in result.content.plain and not result.ok
    assert ReadCommand("hvc") == {}


def test_the_tab_lists_what_can_come_next():
    text = castcli.execute_command(["hvc", "pump", "?"]).content.plain
    assert "on" in text and "off" in text and "A pump" in text
    assert ReadCommand("hvc") == {}


def test_the_tab_reports_usage_without_writing():
    result = castcli.execute_command(["hvc", "platen", "900"])
    assert "outside" in result.content.plain
    assert ReadCommand("hvc") == {}


def test_help_lists_every_scripts_commands():
    text = castcli.help_panel().content.plain
    for usage in ("hvc platen <C>", "hvc stop", "psu1|psu2 <ch> set <V> <A>",
                  "cryo ccv <V>", "slta image", "hvc <valve> open|close"):
        assert usage in text
