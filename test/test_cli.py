"""`labcli <command>`: one command and an exit status, for SSH and scripts."""
import sys
import threading
import time

import pytest

from formslab import app, cli, lab
from formslab.console.cast import castutils
from formslab.console.cast.castutils import ReadCommand
from formslab.host import runstate
from formslab.host import sequence as host

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-03T12:00:00+00:00", "output": "/x"}
LIVE_HVC = {"connected": True, "fault_severity": "N", "pressure": 700.0, "platen C": 20.0,
            "shroud C": 20.0, "rough": False, "vent": False, "fill": False,
            "foreline": False, "gate": False, "pump": False, "turbo": False, "turbo_spinning": False}


def run(capsys, *argv):
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.fixture
def no_host(monkeypatch):
    monkeypatch.setattr(lab, "host", lambda: None)


@pytest.fixture
def host_up(monkeypatch):
    """A run is going and the chamber has just reported (sealed, at rest)."""
    monkeypatch.setattr(lab, "host", lambda: HOST)
    castutils.UpdateStatus("hvc", LIVE_HVC)


@pytest.fixture
def owner(host_up):
    """The run's rScripts take hvc requests as they arrive and answer as rLACO
    does: the rough valve is refused, the rest done."""
    taken, stop = [], threading.Event()

    def take():
        while not stop.is_set():
            req, ids = castutils.TakeCommand("hvc")
            if req:
                taken.append(req)
                refused = "rough" in req
                castutils.ReportResult("hvc", ids, not refused,
                                       ["rough: interlock"] if refused else ["verified"])
            time.sleep(0.02)

    t = threading.Thread(target=take, daemon=True)
    t.start()
    yield taken
    stop.set()
    t.join(1)


def test_help_lists_every_verb(capsys):
    code, out = run(capsys, "help")
    assert code == 0
    for usage, summary in cli.VERBS:
        assert usage in out and summary in out


def test_an_unknown_command_is_a_usage_error(capsys):
    assert run(capsys, "teleport")[0] == 2


# --- status -----------------------------------------------------------------------------

def test_status_with_no_run_exits_1(capsys, no_host):
    code, out = run(capsys, "status")
    assert code == 1 and "No run is going" in out


def test_status_of_a_run_says_where_it_is(capsys, host_up):
    code, out = run(capsys, "status")
    assert code == 0 and "tvac running" in out and "data in /x" in out
    assert "hvc   live" in out and "Vacuum valve Closed" in out


def test_status_of_a_lost_run_says_so_and_where_it_died(capsys):
    host.lock_path().write_text("4321\ntvac\n2026-10-03T12:00:00+00:00\n/x\n", encoding="utf-8")
    runstate.begin(HOST)
    runstate.write(step={"index": 2, "total": 5, "text": "hvc pump on"})
    try:
        code, out = run(capsys, "status")
    finally:
        host.lock_path().unlink(missing_ok=True)
        runstate.clear()
    assert code == 1 and "stopped without its end" in out and "`labcli end` runs its end now" in out
    assert "step 2 of 5: hvc pump on" in out


def test_status_of_one_instrument(capsys, host_up):
    code, out = run(capsys, "status", "hvc")
    assert code == 0 and "Vacuum valve Closed" in out and "tvac running" not in out
    code, out = run(capsys, "status", "nothing")
    assert code == 1 and "no instrument 'nothing'" in out


# --- plans ------------------------------------------------------------------------------

def test_plans_lists_plans_blocks_and_orbits(capsys):
    code, out = run(capsys, "plans")
    assert code == 0 and "tvac" in out and "until end" in out and "blocks" in out and "orbits" in out


def test_check_a_plan(capsys):
    assert run(capsys, "check", "tvac")[0] == 0
    assert run(capsys, "check", "nowhere")[0] == 2


def test_check_a_block_and_an_orbit(capsys):
    from formslab.orbit import file as orbitfile
    from formslab.sequence import block as blockfile
    block = blockfile.discover()[0].stem
    orbit = orbitfile.discover()[0].stem
    code, out = run(capsys, "check", block)
    assert code == 0 and f"block     {block}" in out
    code, out = run(capsys, "check", orbit)
    assert code == 0 and f"orbit     {orbit}" in out


# --- run, end, pause ---------------------------------------------------------------------

def test_run_an_unknown_plan_is_a_usage_error(capsys, no_host):
    code, out = run(capsys, "run", "nowhere")
    assert code == 2 and "no plan 'nowhere'" in out


def test_end_when_nothing_runs_is_done(capsys, no_host):
    assert run(capsys, "end")[0] == 0
    assert run(capsys, "pause")[0] == 1


def test_end_that_the_host_does_not_take_says_it_is_still_running(capsys, monkeypatch):
    monkeypatch.setattr(lab, "host", lambda: HOST)
    monkeypatch.setattr(lab, "is_host", lambda pid: True)
    monkeypatch.setattr(lab, "END_WAIT_S", 0.3)
    code, out = run(capsys, "end")
    assert code == 1 and "ending tvac" in out and "has not stopped" in out


# --- cast --------------------------------------------------------------------------------

def test_cast_with_no_run_sends_nothing(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "vent", "open")
    assert code == 1 and "nothing sent" in out
    assert ReadCommand("hvc") == {}


def test_cast_reports_a_bad_command(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "platen", "900")
    assert code == 1 and "900 is outside -180..200 C" in out


def test_cast_waits_until_the_rscript_takes_it(capsys, owner):
    code, out = run(capsys, "cast", "hvc", "shroud", "-20")       # a negative number, not a flag
    assert code == 0 and "done: verified" in out
    assert owner == [{"shroud": -20.0}]


def test_cast_a_refused_command_fails_with_the_reason(capsys, owner):
    code, out = run(capsys, "cast", "hvc", "rough", "open")
    assert code == 1 and "✗ hvc" in out and "refused: rough: interlock" in out


def test_cast_what_the_rules_forbid_is_not_sent(capsys, owner):
    code, out = run(capsys, "cast", "hvc", "gate", "open")
    assert code == 1 and "refused, nothing sent. Needs Turbo pump on, Foreline valve open" in out
    assert owner == []


def test_cast_nobody_takes_fails(capsys, host_up, monkeypatch):
    monkeypatch.setattr(lab, "CAST_TAKE_S", 0.3)
    code, out = run(capsys, "cast", "hvc", "stop")
    assert code == 1 and "not taken within 0.3 s" in out


def test_cast_to_an_instrument_that_is_not_live_sends_nothing(capsys, monkeypatch):
    monkeypatch.setattr(lab, "host", lambda: HOST)
    castutils.GenerateCleanCast()                                   # hvc says connected: False
    code, out = run(capsys, "cast", "hvc", "vent", "open")
    assert code == 1 and "hvc is not live" in out and ReadCommand("hvc") == {}


def test_cast_question_mark_lists_what_comes_next(capsys, no_host):
    code, out = run(capsys, "cast", "hvc", "pump", "?")
    assert code == 0 and "on" in out and "off" in out


def test_labcli_with_a_command_runs_it_and_exits(monkeypatch, capsys, no_host):
    monkeypatch.setattr(sys, "argv", ["labcli", "status"])
    with pytest.raises(SystemExit) as e:
        app.main()
    assert e.value.code == 1 and "No run is going" in capsys.readouterr().out
