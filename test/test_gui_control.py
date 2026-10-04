"""The GUI's control actions: start and end a run, pause, resume, send a command.
Against a fake host (a thread playing the owner), never a real instrument."""
import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from formslab import config
from formslab.console.cast import castutils
from formslab.console.ctrl import ctrlcli, ctrlutils
from formslab.gui import api
from formslab.gui.server import CSRF_HEADER

from gui_helpers import Client, running_server

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-04T12:00:00+00:00", "output": "/x"}
ACTIONS = [("/api/run", {"plan": "tvac"}), ("/api/end", {}), ("/api/pause", {}),
           ("/api/resume", {}), ("/api/cast", {"line": "hvc stop"})]


@pytest.fixture
def server(monkeypatch):
    with running_server(monkeypatch) as srv:
        yield srv


@pytest.fixture
def client(server):
    return Client(server).login()


@pytest.fixture
def host_up(monkeypatch):
    """A run is going."""
    monkeypatch.setattr(api, "host", lambda: HOST)


def wait_idle(timeout=5.0):
    deadline = time.monotonic() + timeout
    while api.current_action() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert api.current_action() is None


# --- who may act ------------------------------------------------------------------------------

def test_every_action_needs_a_login_and_the_request_header(server, host_up):
    anon = Client(server)
    for path, body in ACTIONS:
        assert anon.json("POST", path, body)[0] == 401, path
    c = Client(server).login()
    for path, body in ACTIONS:
        resp, _ = c.call("POST", path, body, headers={CSRF_HEADER: ""})
        assert resp.status == 403, path
    assert ReadAll() == {}                                                # nothing was sent


def ReadAll():
    return {**castutils.ReadAllCommands(["hvc", "psu1", "tc", "cryo", "slta", "psu2"]),
            **ctrlutils.ReadCommands(["end", "pause", "resume", "reset"])}


# --- plans ----------------------------------------------------------------------------------------

def test_the_plans_that_can_be_started_are_listed(client):
    code, body = client.json("GET", "/api/plans")
    plans = {p["name"]: p for p in body["plans"]}
    assert code == 200 and {"tvac", "laco_pumpdown", "laco_vent", "psu1_smtc08_first"} <= set(plans)
    assert plans["tvac"]["open_ended"] is True and plans["tvac"]["rscripts"] == ["rLACO", "rSMTC08", "rPSU"]
    assert plans["laco_pumpdown"]["open_ended"] is False and plans["laco_pumpdown"]["steps"] == 6


# --- start -------------------------------------------------------------------------------------------

def test_start_runs_the_host_on_a_plan_the_server_lists(client, monkeypatch):
    launched = []
    monkeypatch.setattr(ctrlcli, "_launch_sequence", lambda path: launched.append(path))
    monkeypatch.setattr(api, "host", lambda: None)

    code, body = client.json("POST", "/api/run", {"plan": "tvac"})

    assert (code, body) == (200, {"state": "starting", "plan": "tvac"})
    wait_idle()
    assert [Path(p).name for p in launched] == ["tvac.plan"] and Path(launched[0]).is_absolute()
    assert "tester run tvac" in (config.run_dir() / "gui.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["nope", "../tvac", "..\\tvac", "C:\\x\\tvac.plan", "tvac.plan", "", "tvac "])
def test_start_takes_a_plan_name_never_a_path(client, monkeypatch, name):
    launched = []
    monkeypatch.setattr(ctrlcli, "_launch_sequence", lambda path: launched.append(path))
    monkeypatch.setattr(api, "host", lambda: None)
    assert client.json("POST", "/api/run", {"plan": name})[0] == 404
    assert launched == []


def test_start_refuses_a_second_run_and_a_second_start(client, monkeypatch):
    release, launched = threading.Event(), []
    monkeypatch.setattr(ctrlcli, "_launch_sequence", lambda path: (launched.append(path), release.wait(5)))
    monkeypatch.setattr(api, "host", lambda: None)
    assert client.json("POST", "/api/run", {"plan": "tvac"})[0] == 200
    code, body = client.json("POST", "/api/run", {"plan": "laco_vent"})          # while the first is starting
    assert code == 409 and "already starting" in body["error"]
    assert client.json("GET", "/api/status")[1]["action"] == "starting"
    release.set()
    wait_idle()
    monkeypatch.setattr(api, "host", lambda: HOST)
    code, body = client.json("POST", "/api/run", {"plan": "tvac"})
    assert code == 409 and "already going" in body["error"] and len(launched) == 1


def test_start_refuses_a_plan_whose_rscripts_are_missing(client, tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "broken.plan").write_text("load rNotThere\nhold 1 s\n", encoding="utf-8")
    monkeypatch.setenv("FORMSLAB_PLANS_DIR", str(plans))
    monkeypatch.setattr(api, "host", lambda: None)
    launched = []
    monkeypatch.setattr(ctrlcli, "_launch_sequence", lambda path: launched.append(path))
    code, body = client.json("POST", "/api/run", {"plan": "broken"})
    assert code == 400 and "rNotThere not found" in body["error"] and launched == []


# --- end -------------------------------------------------------------------------------------------------

def make_host(monkeypatch, reset_at=None):
    """A host that takes `end` from the ctrl file; optionally it first clears
    ctrl as a starting host does, erasing an end sent in that window."""
    alive = threading.Event()
    alive.set()

    def run():
        if reset_at is not None:
            time.sleep(reset_at)
            ctrlutils.ResetCtrlState()
        while alive.is_set():
            if "end" in ctrlutils.ReadCommands(["end"]):
                alive.clear()
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    monkeypatch.setattr(api, "host", lambda: HOST if alive.is_set() else None)
    monkeypatch.setattr(api, "is_host", lambda pid: alive.is_set())
    return alive, thread


def test_end_asks_the_host_and_never_kills_it(client, monkeypatch):
    ctrlutils.LoadCommands()
    alive, thread = make_host(monkeypatch)
    try:
        with mock.patch.object(ctrlcli, "_kill_tree") as kill:
            code, body = client.json("POST", "/api/end", {})
            assert (code, body) == (200, {"state": "ending", "plan": "tvac"})
            assert client.json("POST", "/api/end", {})[0] in (200, 409)       # a second click is harmless
            wait_idle()
        kill.assert_not_called()
        assert not alive.is_set()
    finally:
        alive.clear()
        thread.join(2)


def test_end_survives_a_host_that_clears_ctrl_as_it_starts(client, monkeypatch):
    ctrlutils.LoadCommands()
    monkeypatch.setattr(api, "END_RESEND_S", 0.2)
    alive, thread = make_host(monkeypatch, reset_at=0.15)                     # erases the first end
    try:
        assert client.json("POST", "/api/end", {})[0] == 200
        wait_idle()
        assert not alive.is_set()                                             # the resent end was taken
    finally:
        alive.clear()
        thread.join(2)


def test_end_with_no_run_is_refused(client, monkeypatch):
    monkeypatch.setattr(api, "host", lambda: None)
    code, body = client.json("POST", "/api/end", {})
    assert code == 409 and "no run is going" in body["error"]


# --- pause, resume ---------------------------------------------------------------------------------------

def test_pause_and_resume_reach_the_host_through_ctrl(client, host_up):
    ctrlutils.LoadCommands()
    assert client.json("POST", "/api/pause", {}) == (200, {"sent": "pause"})
    assert set(ctrlutils.ReadCommands(["pause", "resume"])) == {"pause"}
    assert client.json("POST", "/api/resume", {}) == (200, {"sent": "resume"})
    assert set(ctrlutils.ReadCommands(["pause", "resume"])) == {"resume"}


def test_pause_with_no_run_is_refused(client, monkeypatch):
    monkeypatch.setattr(api, "host", lambda: None)
    assert client.json("POST", "/api/pause", {})[0] == 409
    assert ctrlutils.ReadCommands(["pause"]) == {}


# --- commands -------------------------------------------------------------------------------------------------

@pytest.fixture
def owner():
    """The rScript that owns `hvc`: takes each command as it arrives."""
    taken, stop = [], threading.Event()

    def run():
        while not stop.is_set():
            if req := castutils.ReadCommand("hvc"):
                taken.append(req)
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    yield taken
    stop.set()
    thread.join(2)


def live_hvc():
    castutils.GenerateCleanCast()
    castutils.UpdateStatus("hvc", {"connected": True, "pressure": 4.4})


def test_a_command_is_sent_and_taken(client, host_up, owner):
    live_hvc()
    code, body = client.json("POST", "/api/cast", {"line": "hvc platen 35"})
    assert code == 200 and body == {"label": "hvc", "request": {"platen": 35.0}, "taken": True}
    assert owner == [{"platen": 35.0}]
    assert "tester cast hvc platen 35" in (config.run_dir() / "gui.log").read_text(encoding="utf-8")


def test_the_label_and_keywords_ignore_case(client, host_up, owner):
    live_hvc()
    assert client.json("POST", "/api/cast", {"line": "HVC Vent OPEN"})[1]["request"] == {"vent": "open"}


def test_a_command_nobody_takes_is_reported_as_not_taken(client, host_up, monkeypatch):
    live_hvc()
    monkeypatch.setattr(api, "CAST_TAKE_S", 0.3)
    code, body = client.json("POST", "/api/cast", {"line": "hvc stop"})
    assert code == 200 and body["taken"] is False
    assert castutils.CommandPending("hvc")


def test_a_bad_command_says_what_would_fit_and_sends_nothing(client, host_up):
    live_hvc()
    for line, needle in (("hvc platen 900", "outside -180..200 C"), ("hcv stop", "did you mean 'hvc'"),
                         ("hvc pump open", "expected on or off"), ("tc anything", "takes no commands"),
                         ("", "type a command")):
        code, body = client.json("POST", "/api/cast", {"line": line})
        assert code == 400 and needle in body["error"], line
    assert not castutils.CommandPending("hvc")


def test_nothing_is_sent_when_no_run_is_going(client, monkeypatch):
    live_hvc()
    monkeypatch.setattr(api, "host", lambda: None)
    code, body = client.json("POST", "/api/cast", {"line": "hvc vent open"})
    assert code == 409 and "no run is going" in body["error"]
    assert not castutils.CommandPending("hvc")


def test_nothing_is_sent_to_an_instrument_that_is_not_live(client, host_up):
    castutils.GenerateCleanCast()                                   # hvc says connected: False
    code, body = client.json("POST", "/api/cast", {"line": "hvc vent open"})
    assert code == 409 and "hvc is not live" in body["error"] and "not connected" in body["error"]
    assert not castutils.CommandPending("hvc")
    assert "refused cast: hvc is not live" in (config.run_dir() / "gui.log").read_text(encoding="utf-8")


# --- completion ----------------------------------------------------------------------------------------------

def test_completion_lists_what_can_come_next(server, client):
    code, body = client.json("GET", "/api/complete?words=")
    first = [o["text"] for o in body["options"]]
    assert code == 200 and {"hvc", "psu1", "psu2", "cryo", "slta"} <= set(first) and "tc" not in first

    after = client.json("GET", "/api/complete?words=hvc%20platen")[1]["options"]
    assert after[0] == {"kind": "number", "text": "C", "help": "platen setpoint (refused outside the profile limits)",
                        "lo": -180.0, "hi": 200.0, "unit": "C"}
    assert [o["text"] for o in after[1:]] == ["on", "off", "rate", "range"]
    assert client.json("GET", "/api/complete?words=hvc%20platen%2020")[1]["options"] == []
    assert Client(server).json("GET", "/api/complete?words=")[0] == 401           # not without a login


# --- it still never opens an instrument -----------------------------------------------------------------------

def test_the_actions_work_with_every_instrument_door_shut(client, monkeypatch, owner):
    """Start launches only the host module; end, pause, resume and commands touch
    only the ctrl and CAST files."""
    spawned = []

    class FakeProc:
        def wait(self):
            return 0

    def popen(cmd, *a, **k):
        spawned.append(cmd)
        monkeypatch.setattr(ctrlcli, "running", lambda: HOST)          # the host's lock appears
        return FakeProc()

    monkeypatch.setattr(api, "host", lambda: None)
    monkeypatch.setattr(ctrlcli, "running", lambda: None)
    with monkeypatch.context() as m:
        m.setattr(subprocess, "Popen", popen)
        assert client.json("POST", "/api/run", {"plan": "tvac"})[0] == 200
        wait_idle()
    assert len(spawned) == 1 and spawned[0][1:3] == ["-m", "formslab.host.sequence"]

    live_hvc()
    monkeypatch.setattr(api, "host", lambda: HOST)
    ctrlutils.LoadCommands()
    with monkeypatch.context() as m:
        def shut(*a, **k):
            raise AssertionError("the GUI tried to open an instrument or a process")
        m.setattr(subprocess, "Popen", shut)
        try:
            import serial
            m.setattr(serial.Serial, "__init__", shut)
        except ImportError:
            pass
        assert client.json("POST", "/api/cast", {"line": "hvc stop"})[1]["taken"] is True
        assert client.json("POST", "/api/pause", {})[0] == 200
        assert client.json("POST", "/api/resume", {})[0] == 200
        assert client.json("GET", "/api/complete?words=psu1%20ch1")[0] == 200
        assert client.json("GET", "/api/plans")[0] == 200
