"""`labcli <command>`: one command and an exit status, for SSH and scripts."""
import sys
import threading
import time

import pytest

from formslab import app, cli
from formslab.console.cast import castutils
from formslab.console.cast.castutils import ReadCommand
from formslab.console.ctrl import ctrlcli

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-03T12:00:00+00:00", "output": "/x"}


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.fixture
def no_host(monkeypatch):
    monkeypatch.setattr(ctrlcli, "running", lambda: None)


@pytest.fixture
def host(monkeypatch):
    """A run is going, the chamber has just reported (sealed, at rest), and its
    rScripts take hvc requests as they arrive and answer as rLACO does: the
    rough valve is refused, the rest done."""
    monkeypatch.setattr(ctrlcli, "running", lambda: HOST)
    castutils.UpdateStatus("hvc", {"connected": True, "fault_severity": "N", "pressure": 700.0, "platen C": 20.0,
                                        "shroud C": 20.0, "rough": False, "vent": False, "fill": False,
                                        "foreline": False, "gate": False, "pump": False, "turbo": False})
    taken, stop = [], threading.Event()

    def owner():
        while not stop.is_set():
            req, ids = castutils.TakeCommand("hvc")
            if req:
                taken.append(req)
                refused = "rough" in req
                castutils.ReportResult("hvc", ids, not refused,
                                       ["rough: interlock"] if refused else ["verified"])
            time.sleep(0.02)

    t = threading.Thread(target=owner, daemon=True)
    t.start()
    yield taken
    stop.set()
    t.join(1)


def test_help(capsys):
    code, out = run(capsys, "help")
    assert code == 0 and "labcli cast <label> <words>" in out


def test_an_unknown_command_is_a_usage_error(capsys):
    assert run(capsys, "teleport")[0] == 2


def test_status_with_no_run_exits_1(capsys, no_host):
    code, out = run(capsys, "status")
    assert code == 1 and "not running" in out


def test_status_of_a_run(capsys, host):
    code, out = run(capsys, "status")
    assert code == 0 and "plan tvac" in out and "CSV in /x" in out


def test_check_a_plan(capsys):
    assert run(capsys, "check", "tvac")[0] == 0
    assert run(capsys, "check", "nowhere")[0] == 2


def test_run_an_unknown_plan_is_a_usage_error(capsys, no_host):
    code, out = run(capsys, "run", "nowhere")
    assert code == 2 and "no plan 'nowhere'" in out


def test_end_when_nothing_runs_is_done(capsys, no_host):
    assert run(capsys, "end")[0] == 0
    assert run(capsys, "pause")[0] == 1


def test_cast_with_no_run_sends_nothing(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "vent", "open")
    assert code == 1 and "nothing sent" in out
    assert ReadCommand("hvc") == {}


def test_cast_reports_a_bad_command(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "platen", "900")
    assert code == 1 and "900 is outside -180..200 C" in out


def test_cast_waits_until_the_rscript_takes_it(capsys, host):
    code, out = run(capsys, "cast", "hvc", "shroud", "-20")       # a negative number, not a flag
    assert code == 0 and "done: verified" in out
    assert host == [{"shroud": -20.0}]


def test_cast_a_refused_command_fails_with_the_reason(capsys, host):
    code, out = run(capsys, "cast", "hvc", "rough", "open")
    assert code == 1 and "✗ hvc" in out and "refused: rough: interlock" in out


def test_cast_what_the_rules_forbid_is_not_sent(capsys, host):
    code, out = run(capsys, "cast", "hvc", "gate", "open")
    assert code == 1 and "refused, nothing sent. Needs Turbo pump on, Foreline valve open" in out
    assert host == []


def test_cast_nobody_takes_fails(capsys, monkeypatch):
    monkeypatch.setattr(ctrlcli, "running", lambda: HOST)
    monkeypatch.setattr(castutils, "TAKE_S", 0.3)
    code, out = run(capsys, "cast", "hvc", "stop")
    assert code == 1 and "not taken within 0.3 s" in out


def test_cast_question_mark_lists_what_comes_next(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "pump", "?")
    assert code == 0 and "on" in out and "off" in out


def test_labcli_with_a_command_runs_it_and_exits(monkeypatch, capsys, no_host):
    monkeypatch.setattr(sys, "argv", ["labcli", "status"])
    with pytest.raises(SystemExit) as e:
        app.main()
    assert e.value.code == 1 and "not running" in capsys.readouterr().out
