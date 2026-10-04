"""CAST and ctrl between processes: the console, the host and the web GUI all
read and write the same two files, so a change must not overwrite another's,
and a bad read must not become a wipe."""
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from formslab.console.cast import castutils
from formslab.console.ctrl import ctrlcli, ctrlutils
from formslab.console.safefile import atomic_write_text, file_lock, read_json
from formslab.state import cast_state_path, ctrl_state_path

N = 60

WORKER = """
import sys
from formslab.console.cast.castutils import UpdateStatus, WriteCommand
kind, n = sys.argv[1], int(sys.argv[2])
for i in range(1, n + 1):
    if kind == "command":
        WriteCommand({f"k{i}": i}, "hvc")          # merges into the unread request
    else:
        UpdateStatus(kind, {"n": i})
"""


def test_processes_writing_cast_at_once_lose_nothing():
    """Three processes each make N read-modify-write changes to castfile.json.
    Without the cross-process lock, later writes overwrite earlier ones and the
    final file holds stale counts and missing commands."""
    castutils.GenerateCleanCast()
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, kind, str(N)])
             for kind in ("psu1", "tc", "command")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0]

    data = read_json(cast_state_path())
    assert data["psu1"]["status"] == {"n": N}
    assert data["tc"]["status"] == {"n": N}
    assert data["hvc"]["request"] == {f"k{i}": i for i in range(1, N + 1)}
    assert not list(cast_state_path().parent.glob("castfile.json.*.tmp"))


def test_a_damaged_cast_file_is_kept_and_regenerated_not_wiped():
    castutils.GenerateCleanCast()
    cast_state_path().write_text('{"hvc": {"request": {"pump', encoding="utf-8")   # torn

    from formslab.state import build_default_cast_state
    assert castutils.ReadStatus("hvc") == build_default_cast_state()["hvc"]["status"]   # not an error
    castutils.WriteCommand({"stop_pumping": True}, "hvc")

    data = read_json(cast_state_path())
    assert {"hvc", "psu1", "psu2", "tc", "cryo", "slta"} <= set(data)   # no block lost
    assert data["hvc"]["request"] == {"stop_pumping": True}
    assert cast_state_path().with_name("castfile.json.bad").read_text(encoding="utf-8").startswith('{"hvc"')


def test_cast_writes_fail_loudly_rather_than_write_in_place(monkeypatch):
    castutils.GenerateCleanCast()
    before = cast_state_path().read_bytes()
    with monkeypatch.context() as m:
        m.setattr("formslab.console.safefile.REPLACE_TIMEOUT_S", 0.1)
        m.setattr(Path, "replace", mock.Mock(side_effect=PermissionError("held by a reader")))
        with pytest.raises(PermissionError):
            castutils.UpdateStatus("tc", {"TC01 C": 20.0})

    assert cast_state_path().read_bytes() == before                    # untouched, never half-written
    assert not list(cast_state_path().parent.glob("*.tmp"))


def test_atomic_write_retries_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    real = Path.replace
    fails = iter([PermissionError(), PermissionError()])

    def flaky(self, dest):
        if (err := next(fails, None)) is not None:
            raise err
        return real(self, dest)

    monkeypatch.setattr(Path, "replace", flaky)
    atomic_write_text(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"


def test_the_file_lock_is_exclusive(tmp_path):
    path = tmp_path / "state.json"
    with file_lock(path):
        with pytest.raises(TimeoutError):
            with file_lock(path, timeout=0.1):
                pass
    with file_lock(path, timeout=0.1):                                  # released afterwards
        pass


def test_read_json_retries_then_raises_on_a_bad_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(bad, attempts=2, delay=0)
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "missing.json")


# --- ctrl -----------------------------------------------------------------------------

def test_an_unreadable_ctrl_table_is_unknown_not_empty():
    ctrlutils.LoadCommands()                                  # seeds the defaults
    ctrl_state_path().write_text("{ not json", encoding="utf-8")

    assert ctrlutils.LoadCommands() is None                   # never {}
    assert ctrlcli._end_pending() is True                     # unknown is not "taken"


def test_a_damaged_ctrl_table_is_kept_and_regenerated_for_a_write():
    ctrlutils.LoadCommands()
    ctrl_state_path().write_text("{ not json", encoding="utf-8")

    ctrlutils.WriteCommand("end")

    table = read_json(ctrl_state_path())
    assert table["end"]["processed"] is False and "pause" in table
    assert ctrl_state_path().with_name("ctrlfile.json.bad").exists()


def test_read_commands_takes_every_pending_request_in_one_pass():
    ctrlutils.LoadCommands()
    ctrlutils.WriteCommand("pause")
    ctrlutils.WriteCommand("end")

    taken = ctrlutils.ReadCommands(["end", "pause", "resume", "reset"])

    assert set(taken) == {"end", "pause"}
    assert ctrlutils.ReadCommands(["end", "pause"]) == {}               # consumed
    assert read_json(ctrl_state_path())["end"]["desc"]                   # layout and desc kept


def test_ctrl_writers_in_two_threads_lose_nothing():
    ctrlutils.LoadCommands()
    labels = ["pause", "resume", "reset", "end"]
    threads = [threading.Thread(target=lambda lab=lab: [ctrlutils.WriteCommand(lab) for _ in range(20)])
               for lab in labels]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert set(ctrlutils.ReadCommands(labels)) == set(labels)


# --- ending a run -------------------------------------------------------------------------

HOST = {"pid": 4321, "plan": "tvac", "started": "t", "output": "/x"}


def test_end_is_sent_again_while_it_waits(monkeypatch):
    """A host that is starting clears ctrl after taking its lock, so one `end`
    can be erased. It is sent again, and a healthy host is never killed."""
    ctrlutils.LoadCommands()
    alive = threading.Event()
    alive.set()

    def host():
        time.sleep(0.15)
        ctrlutils.ResetCtrlState()                              # the start-up reset: erases the first end
        while alive.is_set():
            if "end" in ctrlutils.ReadCommands(["end"]):
                alive.clear()
            time.sleep(0.02)

    t = threading.Thread(target=host, daemon=True)
    t.start()
    monkeypatch.setattr(ctrlcli, "END_RESEND_S", 0.2)
    try:
        with mock.patch.object(ctrlcli, "running", return_value=HOST), \
                mock.patch.object(ctrlcli, "is_host", side_effect=lambda pid: alive.is_set()), \
                mock.patch.object(ctrlcli, "_kill_tree") as kill:
            result = ctrlcli.end_sequence(take_s=5, finish_s=2)
    finally:
        alive.clear()          # the helper thread must never outlive the test's isolated dirs
        t.join(2)

    kill.assert_not_called()
    assert "stopped cleanly" in result.content


def test_end_reports_a_ctrl_file_it_cannot_write(monkeypatch):
    with mock.patch.object(ctrlcli, "running", return_value=HOST), \
            mock.patch.object(ctrlcli, "WriteCommand", side_effect=PermissionError("locked")):
        result = ctrlcli.end_sequence(take_s=0.1, finish_s=0.1)
    assert result.ok is False and "could not send `end`" in result.content.plain


# --- what a block's timestamp means -------------------------------------------------------------

def test_a_timestamp_is_when_the_owner_reported_not_when_something_else_touched_the_block():
    """So the GUI can tell a live instrument from one a host start or a command merely touched."""
    castutils.GenerateCleanCast()
    castutils.UpdateStatus("tc", {"TC01 C": 20.0})
    reported = read_json(cast_state_path())["tc"]["timestamp"]
    time.sleep(0.05)

    castutils.WriteCommand({"x": 1}, "tc")                       # a command written to the block
    block = read_json(cast_state_path())["tc"]
    assert block["timestamp"] == reported and block["request_timestamp"] > reported

    castutils.ResetJson()                                         # a host starting
    block = read_json(cast_state_path())["tc"]
    assert block["timestamp"] == reported                         # still when it last reported
    assert block["status"] == {"TC01 C": 20.0}                    # the old values stay, as old
    assert block["request"] == {} and block["processed"] is True  # the pending command is cleared
