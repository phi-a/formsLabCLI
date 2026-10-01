"""The console-to-host seam: what `run` launches, where its output goes, and
how `end` stops it."""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from formslab import config
from formslab.console.ctrl import ctrlcli
from formslab.console.log import logcli


class Launch(unittest.TestCase):

    def _launch(self, *args):
        proc = mock.Mock(pid=4321)
        with mock.patch.object(ctrlcli, "_running_pid", return_value=None), \
                mock.patch.object(ctrlcli.subprocess, "Popen", return_value=proc) as popen:
            result = ctrlcli.run_sequence(list(args))
        return popen, result

    def test_a_mode_starts_the_host_module(self):
        """`-m` is what stops us guessing where the package lives on disk."""
        popen, _ = self._launch("laco")
        self.assertEqual(popen.call_args.args[0][1:], ["-m", "formslab.host.sequence",
                                                       "--mode", "laco"])

    def test_a_plan_is_found_by_name_and_passed_by_path(self):
        popen, result = self._launch("psu1_smtc08_first")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[3], "--plan")
        self.assertTrue(Path(cmd[4]).is_file() and cmd[4].endswith("psu1_smtc08_first.forms"))
        self.assertIn("plan=psu1_smtc08_first", result.content)

    def test_an_unknown_target_launches_nothing(self):
        popen, result = self._launch("darkness")
        popen.assert_not_called()
        self.assertIn("No plan or mode", result.content)

    def test_the_log_ctrl_writes_is_the_log_the_tab_reads(self):
        popen, _ = self._launch("laco")
        self.assertEqual(Path(popen.call_args.kwargs["stdout"].name), logcli.log_path())

    def test_the_pid_is_recorded_beside_the_log(self):
        self._launch("laco")
        self.assertEqual(ctrlcli._get_pid_path().parent, config.output_dir())
        self.assertEqual(ctrlcli._get_pid_path().read_text(), "4321")

    def test_a_second_run_is_refused_while_one_is_alive(self):
        with mock.patch.object(ctrlcli, "_running_pid", return_value=77), \
                mock.patch.object(ctrlcli.subprocess, "Popen") as popen:
            result = ctrlcli.run_sequence(["laco"])
        popen.assert_not_called()
        self.assertIn("already running (pid 77)", result.content)


class End(unittest.TestCase):
    """`end` asks through ctrl so rShutdown runs; a signal is the fallback."""

    def test_end_asks_the_host_and_waits_for_it(self):
        alive = iter([True, True, False])
        with mock.patch.object(ctrlcli, "_running_pid", return_value=55), \
                mock.patch.object(ctrlcli, "process_exists", side_effect=lambda pid: next(alive)), \
                mock.patch.object(ctrlcli, "WriteCommand") as write, \
                mock.patch.object(ctrlcli.os, "kill") as kill:
            result = ctrlcli.end_sequence(grace_s=5)
        write.assert_called_once_with("end")
        kill.assert_not_called()
        self.assertIn("stopped cleanly", result.content)

    def test_end_kills_a_host_that_does_not_stop_and_says_so(self):
        with mock.patch.object(ctrlcli, "_running_pid", return_value=55), \
                mock.patch.object(ctrlcli, "process_exists", return_value=True), \
                mock.patch.object(ctrlcli, "WriteCommand"), \
                mock.patch.object(ctrlcli.os, "kill") as kill:
            result = ctrlcli.end_sequence(grace_s=0.3)
        kill.assert_called_once()
        self.assertIn("rShutdown did not run", result.content.plain)


class HostModule(unittest.TestCase):

    def test_host_runs_as_a_module(self):
        """What `ctrl` actually invokes."""
        proc = subprocess.run([sys.executable, "-m", "formslab.host.sequence", "--help"],
                              capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--plan", proc.stdout)


if __name__ == "__main__":
    unittest.main()
