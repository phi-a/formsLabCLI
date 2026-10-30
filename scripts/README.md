Tools that are not part of the `formslab` package.

**Launchers.** `labcli.cmd` (Windows) and `labcli.sh` (Linux, macOS) start
labcli from the checkout's venv, for a desktop shortcut or a double-click.
`labgui.cmd` opens the GUI the same way, as a window of its own (memory/use/gui.md).

**GUI demo.** `gui_demo.py` runs the web GUI against a simulated chamber with a throwaway
config and login (`demo` / `demo`), so it can be tried without touching the bench
(`formslab.demo`). It prints the three environment variables that let a second
terminal watch the same bench with `labcli`; `labcli --demo` opens the watch
screen on a bench of its own.

**Chamber scripts.** `vent_test.py` and `pumpdown.py` run the LACO chamber's vent
and rough pumpdown with pre-checks (simulator by default; `--live` is read-only
unless told otherwise). They need the controller's single connection, so use them
only when no run is going and formsLAB is not reading the chamber (`labcli gui
--no-monitor`, or a watch screen started the same way); otherwise use
`labcli cast` or a plan.

**sLTA.** `fz2fits.py` converts `.fz` frames to `.fits` (needs
`pip install -e ".[images]"`). `slta_sequencer_*_v1.xml` are the CCD clock-state
tables deployed on the sLTA host; nothing here loads them, they are the
reference copy.
