"""The console-to-host seam: what `run` launches, how ctrl finds the running
host (its lock file), and how `end` stops it."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from formslab.console.ctrl import ctrlcli
from formslab.console.log import logcli
from formslab.host import sequence as host

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-02T21:35:02+00:00"}


class Launch(unittest.TestCase):

    def _launch(self, *args, running=(None, HOST)):
        with mock.patch.object(ctrlcli, "_running", side_effect=list(running)), \
                mock.patch.object(ctrlcli.subprocess, "Popen") as popen:
            result = ctrlcli.run_sequence(list(args))
        return popen, result

    def test_tvac_is_a_plan_run_by_the_host_module(self):
        """`-m` is what stops us guessing where the package lives on disk."""
        popen, result = self._launch("tvac")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[1:3], ["-m", "formslab.host.sequence"])
        self.assertTrue(Path(cmd[3]).is_file() and cmd[3].endswith("tvac.plan"))
        self.assertIn("pid 4321, plan tvac", result.content)

    def test_on_windows_the_host_is_detached_from_the_console(self):
        popen, _ = self._launch("tvac")
        kwargs = popen.call_args.kwargs
        if sys.platform == "win32":
            self.assertTrue(kwargs["creationflags"] & subprocess.DETACHED_PROCESS)
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_a_plan_is_found_by_name_and_passed_by_path(self):
        popen, _ = self._launch("psu1_smtc08_first")
        cmd = popen.call_args.args[0]
        self.assertTrue(Path(cmd[3]).is_file() and cmd[3].endswith("psu1_smtc08_first.plan"))

    def test_an_unknown_target_launches_nothing(self):
        popen, result = self._launch("darkness")
        popen.assert_not_called()
        self.assertIn("No plan 'darkness'", result.content)

    def test_the_log_ctrl_writes_is_the_log_the_tab_reads(self):
        popen, _ = self._launch("tvac")
        self.assertEqual(Path(popen.call_args.kwargs["stdout"].name), logcli.log_path())

    def test_a_second_run_is_refused_while_one_is_alive(self):
        popen, result = self._launch("tvac", running=(HOST,))
        popen.assert_not_called()
        self.assertIn("already running (pid 4321, plan tvac)", result.content)

    def test_plans_lists_tvac_as_open_ended(self):
        text = ctrlcli.plans_command().content.plain
        line = next(ln for ln in text.splitlines() if " tvac " in ln)
        self.assertIn("until end", line)
        self.assertIn("rLACO, rSMTC08, rPSU", line)


class FindingTheHost(unittest.TestCase):
    """The lock the host writes is the one source of truth."""

    def _lock(self, pid):
        host.lock_path().write_text(f"{pid}\ntvac\n2026-10-02T21:35:02+00:00\n", encoding="utf-8")

    def tearDown(self):
        host.lock_path().unlink(missing_ok=True)

    def test_status_names_the_plan_of_a_live_host(self):
        self._lock(4321)
        with mock.patch.object(ctrlcli, "is_host", return_value=True):
            self.assertIn("running: plan tvac (pid 4321", ctrlcli.status_panel().content)

    def test_status_warns_about_a_run_that_died_without_cleanup(self):
        self._lock(4321)
        with mock.patch.object(ctrlcli, "is_host", return_value=False):
            text = ctrlcli.status_panel().content.plain
        self.assertIn("ended without cleanup", text)

    def test_status_with_no_lock(self):
        self.assertIn("not running", ctrlcli.status_panel().content)

    def test_a_reused_pid_is_not_mistaken_for_the_host(self):
        import os
        self.assertTrue(host.is_host(-1) is False)
        self.assertFalse(host.is_host(os.getpid()))     # pytest is not a host


class End(unittest.TestCase):
    """`end` asks through ctrl so rShutdown runs; a hung host is killed."""

    def test_end_asks_the_host_and_waits_for_it(self):
        alive = iter([True, True, False])
        with mock.patch.object(ctrlcli, "_running", return_value=HOST), \
                mock.patch.object(ctrlcli, "WriteCommand") as write, \
                mock.patch.object(ctrlcli, "LoadCommands", return_value={"end": {"processed": True}}), \
                mock.patch.object(ctrlcli, "is_host", side_effect=lambda pid: next(alive, False)), \
                mock.patch.object(ctrlcli, "_kill_tree") as kill:
            result = ctrlcli.end_sequence(take_s=2, finish_s=2)
        write.assert_called_once_with("end")
        kill.assert_not_called()
        self.assertIn("stopped cleanly", result.content)

    def test_a_host_still_cleaning_up_is_left_alone(self):
        with mock.patch.object(ctrlcli, "_running", return_value=HOST), \
                mock.patch.object(ctrlcli, "WriteCommand"), \
                mock.patch.object(ctrlcli, "LoadCommands", return_value={"end": {"processed": True}}), \
                mock.patch.object(ctrlcli, "is_host", return_value=True), \
                mock.patch.object(ctrlcli, "_kill_tree") as kill:
            result = ctrlcli.end_sequence(take_s=0.3, finish_s=0.3)
        kill.assert_not_called()
        self.assertIn("still cleaning up", result.content)

    def test_a_host_that_never_takes_end_is_killed_with_its_launcher(self):
        with mock.patch.object(ctrlcli, "_running", return_value=HOST), \
                mock.patch.object(ctrlcli, "WriteCommand"), \
                mock.patch.object(ctrlcli, "LoadCommands", return_value={"end": {"processed": False}}), \
                mock.patch.object(ctrlcli, "is_host", return_value=True), \
                mock.patch.object(ctrlcli, "_kill_tree") as kill:
            result = ctrlcli.end_sequence(take_s=0.3, finish_s=0.3)
        kill.assert_called_once_with(4321)
        self.assertIn("rShutdown did not run", result.content.plain)


class HostModule(unittest.TestCase):

    def test_host_runs_as_a_module(self):
        """What `ctrl` actually invokes."""
        proc = subprocess.run([sys.executable, "-m", "formslab.host.sequence", "--help"],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("plan", proc.stdout)


if __name__ == "__main__":
    unittest.main()
