"""The console-to-host seam: mission discovery, launch, and the log.

Three things were reconnected when the sequence host moved over, and all three
were previously computed independently in two places -- which is how the console
came to tail a log nothing was writing.
"""

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from formslab import bridge, config
from formslab.console.ctrl import ctrlcli
from formslab.console.log import logcli


class MissionDiscovery(unittest.TestCase):
    """`ctrl` asks FORMS where the missions are; it does not guess."""

    def test_missions_dir_comes_from_the_library(self):
        fake = mock.Mock()
        fake.missions_root.return_value = Path("/somewhere/missions")
        with mock.patch.object(bridge, "paths", return_value=fake):
            self.assertEqual(ctrlcli.missions_dir(),
                             Path("/somewhere/missions"))

    def test_missions_dir_is_none_without_forms(self):
        """There is no mission library without FORMS. Say so, do not invent one."""
        def unavailable():
            raise bridge.FormsUnavailable("forms.core.paths")

        with mock.patch.object(bridge, "paths", side_effect=unavailable):
            self.assertIsNone(ctrlcli.missions_dir())

    def test_discovery_is_empty_rather_than_raising(self):
        with mock.patch.object(ctrlcli, "missions_dir", return_value=None):
            self.assertEqual(ctrlcli.discover_missions(), [])

    def test_discovery_reads_forms_files(self):
        """FORMS #753 retired `.zen`; a leftover one is not offered as runnable."""
        root = config.config_dir() / "missions"
        root.mkdir(parents=True, exist_ok=True)
        (root / "demo.forms").write_text(
            'mission.name = "Demo"\nsatellite.name = "SAT-1"\n',
            encoding="utf-8")
        (root / "old.zen").write_text('satellite.name = "OLD"\n', encoding="utf-8")

        with mock.patch.object(ctrlcli, "missions_dir", return_value=root):
            found = ctrlcli.discover_missions()

        self.assertEqual([m["name"] for m in found], ["demo"])
        self.assertEqual(found[0]["mission"], "Demo")
        self.assertEqual(found[0]["satellite"], "SAT-1")

    def test_run_launches_a_mission_in_forms_mode(self):
        missions = [{"name": "demo", "path": "/m/demo.forms"}]
        with mock.patch.object(ctrlcli, "discover_missions", return_value=missions), \
                mock.patch.object(ctrlcli, "_launch_sequence") as launch:
            ctrlcli.run_sequence(["demo"])

        launch.assert_called_once_with(mode="forms", config_path="/m/demo.forms")


class LaunchRefusesWithoutForms(unittest.TestCase):

    def test_run_reports_the_missing_extra_instead_of_launching(self):
        with mock.patch.object(bridge, "available", return_value=False), \
                mock.patch.object(ctrlcli.subprocess, "Popen") as popen:
            result = ctrlcli._launch_sequence("tvac")

        popen.assert_not_called()
        self.assertIn("formslab[forms]", result.content.plain)

    def test_a_lab_mode_launches_without_forms(self):
        """`run laco` is chamber control; it must not wait on the astrodynamics
        library being installed."""
        with mock.patch.object(bridge, "available", return_value=False), \
                mock.patch.object(ctrlcli.subprocess, "Popen",
                                  return_value=mock.Mock(pid=99)) as popen:
            ctrlcli._launch_sequence("laco")

        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][-2:], ["--mode", "laco"])


class LaunchTargetsTheInstalledHost(unittest.TestCase):

    def _launch(self):
        proc = mock.Mock(pid=4321)
        with mock.patch.object(bridge, "available", return_value=True), \
                mock.patch.object(ctrlcli.subprocess, "Popen",
                                  return_value=proc) as popen:
            result = ctrlcli._launch_sequence("tvac")
        return popen.call_args, result

    def test_host_is_started_as_a_module_not_a_file_path(self):
        """`-m` is what stops us guessing where the package lives on disk."""
        call, _ = self._launch()
        cmd = call.args[0]

        self.assertEqual(cmd[1:4], ["-m", "formslab.host.sequence", "--mode"])
        self.assertEqual(cmd[4], "tvac")

    def test_pid_log_and_session_share_one_directory(self):
        """They used to resolve against three different roots."""
        self._launch()
        out = config.output_dir()

        self.assertEqual(ctrlcli._get_pid_path().parent, out)
        self.assertEqual(logcli.log_path().parent, out)
        self.assertTrue((out / "sequence.session.json").exists())

    def test_the_log_ctrl_writes_is_the_log_the_tab_reads(self):
        """The regression this phase exists to close."""
        call, _ = self._launch()
        written_to = call.kwargs["stdout"].name

        self.assertEqual(Path(written_to), logcli.log_path())

    def test_the_pid_is_recorded_so_a_second_run_is_refused(self):
        self._launch()
        self.assertEqual(ctrlcli._get_pid_path().read_text(), "4321")


class HostRunsWithoutForms(unittest.TestCase):
    """FORMS is imported when a FORMS mode starts, never at host import."""

    def test_host_imports_without_the_extra(self):
        import importlib
        import sys
        with mock.patch.dict(sys.modules, {"forms": None}):
            for name in [m for m in sys.modules if m.startswith("formslab.host")]:
                sys.modules.pop(name)
            importlib.import_module("formslab.host.sequence")

    def test_host_runs_as_a_module(self):
        """What `ctrl` actually invokes."""
        import sys
        proc = subprocess.run(
            [sys.executable, "-m", "formslab.host.sequence", "--help"],
            capture_output=True, text=True, timeout=180)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--mode", proc.stdout)
        self.assertIn("forms", proc.stdout)
        self.assertNotIn("zen", proc.stdout)


if __name__ == "__main__":
    unittest.main()
