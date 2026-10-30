"""The ctrl tab over `formslab.lab`: its handlers render what the shared backend
returns. The backend itself is tested in test_lab.py."""
import unittest
from unittest import mock

from formslab import lab
from formslab.console.ctrl import ctrlcli
from formslab.host import sequence as host

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-02T21:35:02+00:00", "output": "/x"}


class Tab(unittest.TestCase):

    def _lock(self, pid):
        host.lock_path().write_text(f"{pid}\ntvac\n2026-10-02T21:35:02+00:00\n", encoding="utf-8")

    def tearDown(self):
        host.lock_path().unlink(missing_ok=True)

    def test_status_names_the_plan_of_a_live_host(self):
        self._lock(4321)
        with mock.patch.object(lab, "is_host", return_value=True):
            self.assertIn("running: plan tvac (pid 4321", ctrlcli.status_panel().content)

    def test_status_warns_about_a_run_that_died_without_cleanup(self):
        self._lock(4321)
        text = ctrlcli.status_panel().content.plain
        self.assertIn("ended without cleanup", text)

    def test_status_with_no_lock(self):
        self.assertIn("not running", ctrlcli.status_panel().content)

    def test_run_of_an_unknown_plan_says_so(self):
        with mock.patch.object(lab, "host", return_value=None):
            result = ctrlcli.run_sequence(["darkness"])
        self.assertFalse(result.ok)
        self.assertIn("No plan 'darkness'", result.content)

    def test_run_renders_what_the_launch_returned(self):
        with mock.patch.object(lab, "start", return_value={"state": "started", "plan": "tvac", "pid": 4321}):
            self.assertIn("pid 4321, plan tvac", ctrlcli.run_sequence(["tvac"]).content)

    def test_end_fast_asks_the_backend_for_a_fast_end(self):
        with mock.patch.object(lab, "end", return_value={"state": "ending", "plan": "tvac"}) as end, \
                mock.patch.object(lab, "wait_ended", return_value=None), \
                mock.patch.object(lab, "host", return_value=None):
            result = ctrlcli.execute_command(["end", "fast"])
        end.assert_called_once_with(True)
        self.assertIn("tvac ended", result.content)

    def test_a_host_that_has_not_stopped_is_said_to_be_still_running(self):
        with mock.patch.object(lab, "end", return_value={"state": "ending", "plan": "tvac"}), \
                mock.patch.object(lab, "wait_ended", return_value=None), \
                mock.patch.object(lab, "host", return_value=HOST):
            result = ctrlcli.end_sequence()
        self.assertFalse(result.ok)
        self.assertIn("still running", result.content.plain)

    def test_plans_lists_tvac_as_open_ended(self):
        text = ctrlcli.plans_command().content.plain
        line = next(ln for ln in text.splitlines() if " tvac " in ln)
        self.assertIn("until end", line)
        self.assertIn("rLACO, rSMTC08, rPSU", line)


if __name__ == "__main__":
    unittest.main()
