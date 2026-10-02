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
from formslab.devices.laco import LACO
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
    ("platen 500", "outside"),          # profile limit 200 C
    ("platen hot", "not a number"),
    ("pump open", "expected on or off"),
    ("recipe 99", "outside"),
    ("teleport", "unknown hvc command"),
    ("", "hvc <command>"),
])
def test_laco_refuses_what_it_cannot_send(typed, message):
    with pytest.raises(cast.CastUsage, match=message):
        cast.request("hvc", typed.split())


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
    with pytest.raises(cast.CastUsage):
        cast.request(label, typed.split())


def test_every_label_has_an_owner_and_a_cast_block():
    from formslab.state import build_default_cast_state

    labels, errors = cast.owners()
    assert errors == {}
    assert set(labels) == {"hvc", "psu1", "psu2", "cryo", "slta", "tc"}
    assert set(labels) <= set(build_default_cast_state())


def test_the_tab_writes_the_request_and_says_so():
    result = castcli.execute_command(["hvc", "vent", "open"])
    assert "hvc ←" in result.content.plain
    assert ReadCommand("hvc") == {"vent": "open"}


def test_the_tab_reports_usage_without_writing():
    result = castcli.execute_command(["hvc", "platen", "900"])
    assert "outside" in result.content.plain
    assert ReadCommand("hvc") == {}


def test_help_lists_every_scripts_commands():
    text = castcli.help_panel().content.plain
    for usage in ("hvc platen <C>", "hvc stop", "psu1|psu2 ch<n> set <V> <A>",
                  "cryo ccv <V>", "slta image"):
        assert usage in text
