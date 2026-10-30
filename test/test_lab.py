"""`formslab.lab`: what formsLAB and labcli share. Starting a run, finding the
host, where its run is, ending it (never killing it), and the audit line. With
`Popen` and the host's lock faked: no instrument, no process."""
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from formslab import config, lab
from formslab.console.ctrl import ctrlutils
from formslab.host import runstate
from formslab.host import sequence as host

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-02T21:35:02+00:00", "output": "/x"}


class Launch(unittest.TestCase):

    def _start(self, name, running=(None, None, HOST)):
        with mock.patch.object(lab, "host", side_effect=list(running)), \
                mock.patch.object(lab.subprocess, "Popen") as popen:
            result = lab.start(name)
        return popen, result

    def test_tvac_is_a_plan_run_by_the_host_module(self):
        """`-m` is what stops us guessing where the package lives on disk."""
        popen, result = self._start("tvac")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[1:3], ["-m", "formslab.host.sequence"])
        self.assertTrue(Path(cmd[3]).is_file() and cmd[3].endswith("tvac.plan"))
        self.assertEqual(result, {"state": "started", "plan": "tvac", "pid": 4321})

    def test_on_windows_the_host_is_detached_from_the_console(self):
        popen, _ = self._start("tvac")
        kwargs = popen.call_args.kwargs
        if sys.platform == "win32":
            self.assertTrue(kwargs["creationflags"] & subprocess.DETACHED_PROCESS)
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_a_plan_is_found_by_name_and_passed_by_path(self):
        popen, _ = self._start("psu1_smtc08_first")
        cmd = popen.call_args.args[0]
        self.assertTrue(Path(cmd[3]).is_file() and cmd[3].endswith("psu1_smtc08_first.plan"))

    def test_an_unknown_target_launches_nothing(self):
        with mock.patch.object(lab.subprocess, "Popen") as popen, self.assertRaises(lab.LabError) as e:
            lab.start("darkness")
        popen.assert_not_called()
        self.assertEqual(e.exception.code, 404)
        self.assertIn("no plan 'darkness'", str(e.exception))

    def test_the_log_the_launch_writes_is_the_log_read_back(self):
        popen, _ = self._start("tvac")
        self.assertEqual(Path(popen.call_args.kwargs["stdout"].name), lab.log_path())

    def test_the_last_runs_log_is_kept_one_back(self):
        """End and start again must not erase what the last run logged."""
        log = lab.log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("the last run\n", encoding="utf-8")
        self._start("tvac")
        self.assertEqual(log.with_name("host.log.1").read_text(encoding="utf-8"), "the last run\n")

    def test_a_second_run_is_refused_while_one_is_alive(self):
        with mock.patch.object(lab, "host", return_value=HOST), \
                mock.patch.object(lab.subprocess, "Popen") as popen, \
                self.assertRaises(lab.LabError) as e:
            lab.start("tvac")
        popen.assert_not_called()
        self.assertEqual(e.exception.code, 409)
        self.assertIn("already going", str(e.exception))

    def test_a_lost_runs_end_is_launched_with_no_plan(self):
        """`--end` names no plan. A launch whose lock does not appear in time used
        to raise on `Path(None)` instead of saying it is starting."""
        with mock.patch.object(lab, "host", return_value=None), \
                mock.patch.object(lab.subprocess, "Popen") as popen, \
                mock.patch.object(lab, "LAUNCH_WAIT_S", 0.1):
            r = lab.launch(None, end_only=True, fast=True)
        self.assertEqual(popen.call_args.args[0][3:], ["--end", "--fast"])
        self.assertEqual(r["state"], "starting")


class FindingTheHost(unittest.TestCase):
    """The lock the host writes is the one source of truth; run.json says where it is."""

    def _lock(self, pid):
        host.lock_path().write_text(f"{pid}\ntvac\n2026-10-02T21:35:02+00:00\n/x\n", encoding="utf-8")

    def tearDown(self):
        host.lock_path().unlink(missing_ok=True)
        runstate.clear()

    def test_the_live_hosts_lock_is_the_host(self):
        self._lock(4321)
        with mock.patch.object(lab, "is_host", return_value=True):
            self.assertEqual(lab.host()["plan"], "tvac")

    def test_a_lock_whose_process_is_gone_is_a_lost_run(self):
        self._lock(4321)
        self.assertIsNone(lab.host())
        self.assertEqual(lab.lost_run()["plan"], "tvac")
        s = lab.status(log_lines=0)
        self.assertEqual(s["lost"]["pid"], 4321)
        self.assertEqual(s["last_run"]["plan"], "tvac")

    def test_a_reused_pid_is_not_mistaken_for_the_host(self):
        import os
        self.assertTrue(host.is_host(-1) is False)
        self.assertFalse(host.is_host(os.getpid()))     # pytest is not a host

    def test_status_carries_where_the_run_is_for_the_locks_host_only(self):
        self._lock(4321)
        runstate.begin(HOST)
        runstate.write(step={"index": 2, "total": 5, "text": "hold 1 s"}, paused=True, paused_since="t")
        with mock.patch.object(lab, "is_host", return_value=True):
            s = lab.status(log_lines=0)
            self.assertEqual(s["run"]["step"], {"index": 2, "total": 5, "text": "hold 1 s"})
            self.assertTrue(s["run"]["paused"])
            runstate.write(pid=99)                                 # another run's file
            self.assertIsNone(lab.status(log_lines=0)["run"])

    def test_a_lost_run_keeps_the_step_it_died_on(self):
        self._lock(4321)
        runstate.begin(HOST)
        runstate.write(step={"index": 3, "total": 5, "text": "hvc pump on"})
        s = lab.status(log_lines=0)
        self.assertEqual(s["lost"]["plan"], "tvac")
        self.assertEqual(s["run"]["step"]["text"], "hvc pump on")

    def test_with_no_lock_there_is_no_run_state(self):
        runstate.begin(HOST)
        self.assertIsNone(lab.run_state())


class End(unittest.TestCase):
    """`end` asks through ctrl, keeps asking, and never kills: a host that does
    not take it is left alive."""

    def setUp(self):
        ctrlutils.LoadCommands()
        lab._finish()

    def _host(self, reset_at=None):
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
        return alive, thread

    def test_end_asks_the_host_and_wait_ended_returns_once_it_is_gone(self):
        alive, thread = self._host()
        try:
            with mock.patch.object(lab, "host", side_effect=lambda: HOST if alive.is_set() else None), \
                    mock.patch.object(lab, "is_host", side_effect=lambda pid: alive.is_set()), \
                    mock.patch.object(lab, "_kill_tree") as kill:
                self.assertEqual(lab.end(), {"state": "ending", "plan": "tvac"})
                lab.wait_ended(5)
        finally:
            alive.clear()
            thread.join(2)
        kill.assert_not_called()
        self.assertFalse(alive.is_set())
        self.assertIsNone(lab.action())

    def test_end_is_sent_again_while_it_waits(self):
        """A host that is starting clears ctrl after taking its lock, so one `end`
        can be erased. It is sent again, and a healthy host is never killed."""
        alive, thread = self._host(reset_at=0.15)
        try:
            with mock.patch.object(lab, "END_RESEND_S", 0.2), \
                    mock.patch.object(lab, "host", side_effect=lambda: HOST if alive.is_set() else None), \
                    mock.patch.object(lab, "is_host", side_effect=lambda pid: alive.is_set()), \
                    mock.patch.object(lab, "_kill_tree") as kill:
                lab.end()
                lab.wait_ended(5)
        finally:
            alive.clear()
            thread.join(2)
        kill.assert_not_called()
        self.assertFalse(alive.is_set())                           # the resent end was taken

    def test_end_fast_asks_for_a_fast_end_also_of_a_run_already_ending(self):
        alive = threading.Event()
        alive.set()
        try:
            with mock.patch.object(lab, "host", side_effect=lambda: HOST if alive.is_set() else None), \
                    mock.patch.object(lab, "is_host", side_effect=lambda pid: alive.is_set()):
                lab.end()
                deadline = time.monotonic() + 2
                while "end" not in (taken := ctrlutils.ReadCommands(["end"])) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIsNone(taken["end"]["key"])                           # End
                self.assertEqual(lab.end(fast=True), {"state": "ending", "plan": "tvac"})
                self.assertEqual(ctrlutils.ReadCommands(["end"])["end"]["key"], "fast")
        finally:
            alive.clear()
            lab.wait_ended(5)

    def test_a_host_that_never_takes_end_is_left_alive_and_said_so(self):
        with mock.patch.object(lab, "host", return_value=HOST), \
                mock.patch.object(lab, "is_host", return_value=True), \
                mock.patch.object(lab, "END_WAIT_S", 0.3), \
                mock.patch.object(lab, "_kill_tree") as kill:
            lab.end()
            lab.wait_ended(5)
            kill.assert_not_called()
            self.assertIsNotNone(lab.host())                       # still there, for `kill` to decide
            self.assertIsNone(lab.action())

    def test_end_that_cannot_write_ctrl_keeps_trying_and_gives_up_quietly(self):
        with mock.patch.object(lab, "host", return_value=HOST), \
                mock.patch.object(lab, "is_host", return_value=True), \
                mock.patch.object(lab, "END_WAIT_S", 0.3), \
                mock.patch.object(ctrlutils, "WriteCommand", side_effect=PermissionError("locked")):
            lab.end()
            lab.wait_ended(5)
        self.assertIsNone(lab.action())

    def test_end_with_nothing_to_end_is_refused(self):
        with mock.patch.object(lab, "host", return_value=None), \
                mock.patch.object(lab, "lost_run", return_value=None), \
                self.assertRaises(lab.LabError) as e:
            lab.end()
        self.assertEqual(e.exception.code, 409)

    def test_end_on_a_lost_run_starts_a_host_for_its_end_and_sends_no_end(self):
        """Its host was killed: End runs that run's end from a fresh host (`--end`)."""
        launched = []
        with mock.patch.object(lab, "host", return_value=None), \
                mock.patch.object(lab, "lost_run", return_value=HOST), \
                mock.patch.object(lab, "launch",
                                  side_effect=lambda path, end_only=False, fast=False: launched.append((path, end_only, fast))):
            self.assertEqual(lab.end(fast=True), {"state": "ending", "plan": "tvac"})
            lab.wait_ended(5)
        self.assertEqual(launched, [(None, True, True)])
        self.assertNotIn("end", ctrlutils.ReadCommands(["end"]))      # it stops by itself

    def test_end_keeps_asking_for_as_long_as_the_end_script_may_take(self):
        self.assertGreater(lab.END_WAIT_S, host.END_CAP_S)


class Audit(unittest.TestCase):

    def test_one_line_per_action_says_when_who_where_and_what(self):
        lab.audit("tester", "run tvac", "gui")
        lab.audit("darkn", "end")
        lines = (config.run_dir() / lab.ACTIONS_LOG).read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].endswith(" tester gui run tvac"), lines)
        self.assertTrue(lines[1].endswith(" darkn labcli end"), lines)
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\dT")


def test_only_the_chamber_is_live_between_runs():
    now = time.time()
    hvc = {"timestamp": now - 2, "status": {"connected": True}}
    assert lab.freshness("hvc", hvc, None, now) == {"live": True, "watched": True, "age_s": pytest.approx(2), "reason": ""}
    assert not lab.freshness("hvc", {**hvc, "timestamp": now - 60}, None, now)["live"]
    assert lab.freshness("psu1", {"timestamp": now - 1, "status": {}}, None, now)["reason"] == "no run is going"


class HostModule(unittest.TestCase):

    def test_host_runs_as_a_module(self):
        """What `launch` actually invokes."""
        proc = subprocess.run([sys.executable, "-m", "formslab.host.sequence", "--help"],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("plan", proc.stdout)


if __name__ == "__main__":
    unittest.main()
