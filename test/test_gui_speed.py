"""What keeps the GUI quick: the server does not repeat work it has done, and
the editor gets every row's choices in one request.

A plan check once read the bench profile about 1,100 times (rLACO's thermocouple
list asked for its own sensors once per sensor); these tests keep that from
coming back.
"""
import json
import os

import pytest

from formslab import config
from formslab.devices.hvc3500 import profile
from formslab.gui import api
from formslab.sequence.plan import review

from gui_helpers import Client, running_server

PLAN = ("load rLACO rSMTC08\nrecord every 10 s\n\nhvc platen 40 at TC01\nhvc platen on\n"
        "until TC01 >= 39.5 C within 30 min\nwhen platenT > 90 C then hvc platen off\nhold 1 h\n")


@pytest.fixture
def parses(monkeypatch):
    """How many times the profile's JSON is parsed."""
    count = []
    real = json.load
    monkeypatch.setattr(profile.json, "load", lambda f: (count.append(1), real(f))[1])
    profile._read.clear()
    return count


def test_the_profile_is_read_once_until_it_changes(parses):
    first, second = profile.load_profile(), profile.load_profile()
    assert first == second and len(parses) == 1
    path = profile.profile_path()
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))   # as an edit would
    profile.load_profile()
    assert len(parses) == 2


def test_a_plan_check_reads_the_profile_once(parses):
    assert review(PLAN) == ([], [])
    assert len(parses) == 1


def test_a_plan_check_asks_for_the_profile_a_few_times_not_once_per_sensor(monkeypatch):
    import formslab.devices.hvc3500 as hvc
    calls = []
    real = hvc.load_profile
    monkeypatch.setattr(hvc, "load_profile", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    review(PLAN)
    assert len(calls) < 60, len(calls)           # about 20; once per sensor per step was ~700


def test_a_new_config_folder_is_still_made(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere"
    monkeypatch.setenv(config.CONFIG_ENV, str(target))
    assert config.config_dir() == target and target.is_dir()


def test_every_rows_choices_in_one_request_are_the_same_as_one_by_one():
    lines = [ln.split() for ln in PLAN.splitlines()[3:] if ln.strip()]
    assert api.plan_lines(["rLACO", "rSMTC08"], lines) == [api.plan_line(["rLACO", "rSMTC08"], w) for w in lines]


def test_the_rows_route_answers_and_checks_its_body(monkeypatch):
    with running_server(monkeypatch) as server:
        client = Client(server).login()
        code, body = client.json("POST", "/api/plan/lines", {"scripts": ["rLACO"], "lines": [["hvc", "platen"], []]})
        assert code == 200 and len(body["lines"]) == 2 and body["lines"][0]["positions"]
        assert client.json("POST", "/api/plan/lines", {"scripts": ["rLACO"], "lines": "hvc"})[0] == 400
