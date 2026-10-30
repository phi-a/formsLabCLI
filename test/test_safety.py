"""The off switches (formslab.safety): the supplies' outputs over VISA and the
PowerSwitch's outlets, refused while a run is going. With a fake supply and
the switch's one HTTP call patched; nothing real is opened."""
import json
from urllib.error import HTTPError, URLError

import pytest

from formslab import config, lab, safety
from formslab.devices.dp832a import driver as psu_driver
from formslab.devices.powerswitch import driver as switch

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-03T12:00:00+00:00", "output": "/x"}


def write_map(ps=None, psu2=True):
    (config.config_dir() / "usbmap.json").write_text(json.dumps({
        "psu1": {"enabled": True, "address": "ASRLCOM3::INSTR"},
        "psu2": {"enabled": psu2, "address": "ASRLCOM4::INSTR"},
        "PS": {"host": "192.0.2.1", "outlet_count": 8, **(ps or {})},
    }), encoding="utf-8")


@pytest.fixture
def no_run(monkeypatch):
    monkeypatch.setattr(lab, "host", lambda: None)
    monkeypatch.setattr(safety.time, "sleep", lambda s: None)


class FakePSU:
    made = []
    failing = set()

    def __init__(self, label):
        self.label, self.calls = label, []
        FakePSU.made.append(self)

    def connect(self):
        self.calls.append("connect")
        if self.label in FakePSU.failing:
            raise OSError("no such port")

    def alloff(self):
        self.calls.append("alloff")

    def disconnect(self):
        self.calls.append("disconnect")


@pytest.fixture
def fake_psu(monkeypatch):
    FakePSU.made, FakePSU.failing = [], set()
    monkeypatch.setattr(psu_driver, "PSU", FakePSU)
    return FakePSU


# --- the supplies ---------------------------------------------------------------------

def test_supplies_off_is_refused_while_a_run_is_going(monkeypatch, fake_psu):
    monkeypatch.setattr(lab, "host", lambda: HOST)
    with pytest.raises(safety.Refused) as e:
        safety.supplies_off()
    assert "a run is going (tvac)" in str(e.value) and "labcli end" in str(e.value)
    assert fake_psu.made == []


def test_supplies_off_turns_every_enabled_supply_off_and_says_so(no_run, fake_psu):
    write_map()
    done = safety.supplies_off()
    assert [d["text"] for d in done] == ["psu1: CH1, CH2, CH3 off", "psu2: CH1, CH2, CH3 off"]
    assert all(d["ok"] for d in done)
    assert [p.calls for p in fake_psu.made] == [["connect", "alloff", "disconnect"]] * 2


def test_a_disabled_supply_is_left_alone_and_one_can_be_named(no_run, fake_psu):
    write_map(psu2=False)
    assert [d["label"] for d in safety.supplies_off()] == ["psu1"]
    assert [d["label"] for d in safety.supplies_off(["psu2"])] == ["psu2"]


def test_a_supply_that_is_not_reached_is_said_and_the_others_still_go_off(no_run, fake_psu):
    write_map()
    fake_psu.failing.add("psu1")
    done = safety.supplies_off()
    assert done[0]["ok"] is False and "psu1: not reached: no such port" in done[0]["text"]
    assert done[1] == {"label": "psu2", "ok": True, "text": "psu2: CH1, CH2, CH3 off"}
    assert fake_psu.made[0].calls == ["connect", "connect"]             # tried twice, never switched


def test_supplies_off_is_allowed_after_a_host_was_lost(no_run, fake_psu, monkeypatch):
    monkeypatch.setattr(lab, "lost_run", lambda: HOST)
    write_map()
    assert all(d["ok"] for d in safety.supplies_off())


# --- the PowerSwitch ---------------------------------------------------------------------

@pytest.fixture
def http(monkeypatch):
    """The switch's HTTP calls, recorded; answered 200 unless told otherwise."""
    calls = {"urls": [], "answer": 200}

    def get(url, user, password, timeout):
        calls["urls"].append(url)
        if isinstance(calls["answer"], Exception):
            raise calls["answer"]
        return calls["answer"]

    monkeypatch.setattr(switch, "_http_get", get)
    return calls


def test_the_switch_is_refused_while_a_run_is_going(monkeypatch, http):
    monkeypatch.setattr(lab, "host", lambda: HOST)
    with pytest.raises(safety.Refused):
        safety.outlet(3, "off")
    assert http["urls"] == []


def test_an_outlet_is_switched_on_off_or_cycled_in_order(no_run, http, monkeypatch):
    write_map({"outlets": {"3": "psu1"}})
    slept = []
    monkeypatch.setattr(safety.time, "sleep", slept.append)
    r = safety.outlet(3, "cycle", delay_s=2.5)
    assert r == {"ok": True, "lines": ["outlet 3 (psu1) off", "outlet 3 (psu1) on"]}
    assert http["urls"] == ["http://192.0.2.1/", "http://192.0.2.1/outlet?3=OFF", "http://192.0.2.1/outlet?3=ON"]
    assert slept == [2.5]
    assert safety.outlet(2, "off")["lines"] == ["outlet 2 off"]
    assert http["urls"][-1] == "http://192.0.2.1/outlet?2=OFF"


def test_a_switch_that_is_not_reached_is_sent_nothing(no_run, http):
    write_map()
    http["answer"] = URLError("timed out")
    r = safety.outlet(3, "cycle")
    assert r["ok"] is False and "192.0.2.1 not reached" in r["lines"][0] and "labcli power setup" in r["lines"][0]
    assert http["urls"] == ["http://192.0.2.1/"]
    http["answer"] = HTTPError("http://192.0.2.1/", 401, "no", {}, None)
    assert switch.PASSWORD_ENV in safety.outlets()["note"]


def test_an_outlet_outside_the_switch_or_an_unknown_action_is_refused(no_run, http):
    write_map()
    with pytest.raises(ValueError):
        safety.outlet(9, "off")
    with pytest.raises(ValueError):
        safety.outlet(0, "on")
    with pytest.raises(ValueError):
        safety.outlet(1, "toggle")
    assert http["urls"] == []


def test_outlets_lists_each_with_what_it_feeds(no_run, http):
    write_map({"outlets": {"3": "psu1", "4": "psu2"}})
    s = safety.outlets()
    assert s["host"] == "192.0.2.1" and s["reachable"] is True
    assert s["outlets"][2] == {"number": 3, "feeds": "psu1"} and s["outlets"][0] == {"number": 1, "feeds": ""}
    assert len(s["outlets"]) == 8
