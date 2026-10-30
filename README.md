# formsLabCLI

A terminal console and a web GUI for thermal-vacuum testing: control chamber
temperatures, supplies and readouts, and run hardware test sequences (lab
plans) against them. It drives the LACO chamber's HVC-3500 controller, Rigol
programmable supplies, SMTC08 thermocouple readers, a
cryocooler control board behind a Raspberry Pi Pico I2C bridge, a Digital
Loggers PowerSwitch, and the sLTA imaging chain.

[FORMS](https://github.com/phi-a/FORMS), the astrodynamics engine, is not a
dependency. Its part is offline: it computes orbit-driven profiles (eclipse
timing, temperatures), and formsLabCLI runs them on the bench.

**New computer? Start with [the setup guide](memory/use/setup.md)** for Python,
NI-VISA, finding the current COM ports, and editing your bench's hardware map.

**Everything else is in [memory/](memory/README.md)**, an Obsidian vault: how to
use it, how it is built and why, where the work stands, and the rules the bench
runs by. Read [the safety rules](memory/rules/safety.md) before a first run on
the real chamber.

```
pip install -e .          # a working lab console
labcli                    # start it
```

## Updating without losing your settings

Keep each computer's settings and files separate from the shared code:

- Edit the live hardware map at `%USERPROFILE%\.formslab\usbmap.json` on
  Windows (`~/.formslab/usbmap.json` on Linux). This says how to find each
  device on this computer. Do not edit `src/formslab/defaults/usbmap.json`; it is a
  shared template. The live map is created only when missing and is not
  replaced by an update.
- Keep personal data and custom files in `outputs/` (ignored by Git), or
  outside the checkout. Do not customize tracked examples or source files
  unless you intend to maintain code changes.
- If you set `FORMSLAB_CONFIG_DIR` or `FORMSLAB_OUTPUT_DIR`, use a location
  outside the checkout or a Git-ignored folder.

Close the console, open PowerShell in the checkout, and run:

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -e .
```

Then restart the console. On Linux, use `.venv/bin/python` for the second
command. Existing extras remain installed; include the extras you use (for
example `-e ".[pico]"`) when refreshing their dependencies too.

These commands update the tool while retaining the local map and ignored
outputs. If Git reports local changes or diverged branches, stop and ask the
maintainer to reconcile them; do not discard your files to force an update.
Git does not back up ignored files or the external configuration directory.

Two launchers sit in `scripts/`, one per platform, for a desktop shortcut or
a double-click: `labcli.cmd` on Windows and `labcli.sh` on Linux and macOS.
Both resolve the venv relative to the checkout and run from the repo root, so
`outputs/` lands beside the code. The Windows one also switches the console to
UTF-8, because the default OEM codepage mangles the box-drawing characters.
`labgui.cmd` opens the GUI, formsLAB, as a desktop window on Windows (memory/use/gui.md).

## The watch screen

A bare `labcli` opens one screen that repaints itself every second: the run and
where it is (the step, paused, ending), the chamber and the other instruments as
formsLAB shows them, the last lines of the host log, and how the last run
ended. Capital letters act on the run: `E` End, `F` Fast End, `P` Pause, `R`
Resume, `Q` Quit; an End first lists the steps it will run and asks `y/n`. The
line at the bottom takes the same words as `labcli <command>` (`cast hvc platen
20`, `run tvac`, `status psu1`), and the last command's output shows above the
cards. Lowercase always types, so every command can be typed.

The screen reads only. While no run is going and nothing else reads the
chamber, it starts the same read-only monitor formsLAB runs and lets go when it
quits or a run starts (`--no-monitor` leaves the controller to `scripts/`).
`labcli --demo` opens it on a simulated bench of its own, nothing real touched.

It is one fixed frame (`console/frame.py`): a header, a banner when there is
something to say, a pane for the cards and the log, and the prompt on the last
row, so nothing walks down the terminal; `PgUp`/`PgDn` and `↑`/`↓` move the
pane. It reads keys, not lines (`console/keys.py`), so a resize and the
once-a-second tick repaint between keystrokes. Where keys cannot be read, a
pipe, a dumb terminal or a window under 40 by 12, it prints the status once and
reads commands line by line.

## Install

The base install is the console, the transports its drivers open, and numpy for
the orbit files and environment models (`formslab.orbit`). No matplotlib, no
scipy, no astrodynamics library; driving a PSU needs none of them.

```
pip install -e .              # console + transports
pip install -e ".[pico]"      # + mpremote, to talk to the cryo board's Pico bridge
pip install -e ".[images]"    # + astropy, for scripts/fz2fits.py (sLTA frames)
pip install -e ".[orbit]"     # + scipy/matplotlib, for formslab.orbit's transient solver and plots
pip install -e ".[dev]"       # + pytest
```

## Layout

```
src/formslab/
├── app.py       the `labcli` entry point: one command, or the watch screen
├── cli.py       `labcli <command>`: one command, then an exit status
├── lab.py       what is going, start, end, pause, commands: one backend for labcli and formsLAB
├── safety.py    the off switches below End: supplies off, the PowerSwitch's outlets
├── demo.py      a simulated bench of its own, for the GUI demo and `labcli --demo`
├── config.py    config, output and run directory resolution
├── state.py     CTRL command table and CAST device state
├── console/     the watch screen (keys, frame, style) and the shared CAST and ctrl files
├── devices/     one folder per instrument (hvc3500, dp832a, smtc08, cryocooler, slta, powerswitch)
├── defaults/    shipped usbmap.json and tvac_bench.json
├── rscripts/    the rScripts runtime: loader, gates, the `Run` each script receives
├── sequence/    lab plans: test sequences, read and run
├── host/        the process that runs a plan
├── gui/         the web GUI (`labcli gui`; memory/use/gui.md)
└── orbit/       orbit files and the orbit, view-factor and environment models (memory/use/orbit.md)
rScripts/        the routines that own the instruments during a run
plans/           the bench's tests in folders (vacuum/ambient_to_rest.plan), blocks/, orbits/
scripts/         launchers and standalone analysis tools
```

How the pieces fit is in [memory/design/architecture.md](memory/design/architecture.md).

### Configuration and state

Nothing is written inside the installed package. Three locations, by what the
thing is:

| Location | Holds | Override |
|---|---|---|
| package | code, Pico firmware, shipped defaults | — |
| config | live `usbmap.json`, CTRL command table, CAST device state, and in `.run/` the running host's lock, event log and `host.log` | `$FORMSLAB_CONFIG_DIR` (default `~/.formslab`) |
| output | run records (JSON Lines), captured frames, instrument logs | `$FORMSLAB_OUTPUT_DIR` (default `<cwd>/outputs`) |

`usbmap.json` is the hardware map: how to find each USB device (its USB id
and serial number, or its place on the USB tree), the PowerSwitch host, and the
cryo board's I2C pins and firmware file. It differs per bench, so it ships as a default in
`src/formslab/defaults/` and is copied into the config directory the first time
a driver asks for it — edit the copy, and an upgrade will not overwrite it. The
PowerSwitch password is read from the environment variable named by its
`password_env` key, never stored in the file.

A supply's entry also says what its channels are wired to, and which rScript
drives each one:

```json
"psu1": { "...": "...",
  "channels": { "1": { "feeds": "cryocooler board", "owner": "rCryoBoard" } } }
```

rCryoBoard and rSLTA take their supply channel from here, so rewiring the bench
is an edit to this file, not to code. A map without `channels` uses the shipped
wiring (psu1 CH1 → cryocooler board, psu2 CH1 → sLTA camera).

Point `$FORMSLAB_CONFIG_DIR` somewhere else to run a second bench from one
machine.

## Tests

```
pytest
```

Instrument tests are quarantined in `conftest.py` — they need hardware on the
bench and are run by naming the file (`pytest test/test_DP832A.py`). Everything
else runs against fakes and passes on a bare install with nothing plugged in.

## Running a test

A lab plan is a hardware test sequence, one step per line: `load` names the
rScripts that own the instruments, then commands (the same words as `labcli cast`),
`hold`, `until` and `log`. See [memory/use/plans.md](memory/use/plans.md).

```
load rLACO
hvc pump on
hold 15 s
hvc rough open
until chamberP < 5 within 20 min
hvc stop
```

```
labcli check ambient_to_rest                     # read a plan; touches no hardware
labcli plans                                     # the plans, blocks and orbits found
labcli run tvac                                  # manual operation: then `labcli cast`, or the watch screen
labcli run ambient_to_rest                       # or any plan
labcli pause / resume / end
labcli log 50                                    # the host's output
```

`end` asks the host to stop, so the end script and every rScript's `rShutdown`
run (a plan's PSU outputs go off, pumping the run started stops) before it
exits, and says how the chamber was left. `tvac` runs the bench's rScripts
until `end` while you operate from the watch screen or `labcli cast`; a plan
ends by itself. Every run records its variables to `outputs/<plan>_<UTC>.jsonl`.

## One command at a time (SSH, scripts)

`labcli` with a command runs it and exits, with an exit status (0 done, 1
refused or failed, 2 usage). What is running is kept per machine in
`~/.formslab/.run`, so this works from any folder or an SSH session:

```
labcli status                       # what is going, where the run is, each instrument (exit 1 with no run)
labcli run tvac                     # start one; it keeps running after you log out
labcli cast hvc platen 20           # waits until the chamber has done it, or says why not
labcli cast hvc platen '?'          # what can come next
labcli status hvc                   # one instrument's readings
labcli log 50                       # the host's output
labcli hardware                     # what is plugged in, beside the hardware map
labcli end                          # waits for the host, and says how the chamber was left
labcli kill                         # a host that will not take end; then `labcli end` again
labcli supplies off                 # every supply output off, when no run is going
labcli power 3 cycle                # a PowerSwitch outlet off and on again
```

`labcli cast` refuses when no run is going (nothing would apply the command),
when the instrument is not live, and when the command's prerequisites are not
met now (the rough valve open for a vent; see
[memory/use/plans.md](memory/use/plans.md)). `kill`, `supplies off` and an outlet
off or cycled ask first; add the word `yes` from a script. The order when
something must go off is in [memory/rules/safety.md](memory/rules/safety.md).
`labcli help` lists every command.

`labcli gui` starts a small web GUI on this machine: every instrument's status,
starting and ending runs, the command box, the chamber drawn as its own screen
draws it, a plan editor with help for every step, and plots of recorded runs;
see [memory/use/gui.md](memory/use/gui.md). A practice session for learning it on a simulated
chamber is in [memory/use/session.md](memory/use/session.md).

## Windows and Linux (Raspberry Pi)

One checkout runs on both. What differs is below the drivers, and each piece is
chosen for you:

| | Windows bench | Linux / Pi |
|---|---|---|
| VISA backend | NI-VISA | pyvisa-py (installed by `pip install -e .`) |
| USB transport | NI-VISA's USB driver | pyusb + libusb |

`pyvisa` picks the backend itself: NI-VISA when its library is found, pyvisa-py
otherwise. To force one, set `PYVISA_LIBRARY` (`@ivi` or `@py`). A Rigol on
RS232 does not go through VISA at all; it uses pyserial on both systems.

All Rigol supply control lives in `devices/dp832a/`, for the console and the
routines alike. The buffer clear is optional and runs only before a retry:
pyvisa-py's USBTMC session does not implement it (before this was made
non-fatal every PSU query on the Pi failed with `VI_ERROR_NSUP_OPER
(-1073807257)` even though `*IDN?` answered), and on the DP832A a clear sent
right after a channel switch makes the supply drop its next reply.

Setting up a Pi for a DP832A over USB:

```bash
sudo apt install libusb-1.0-0
lsusb -d 1ab1:0e11          # the Rigol shows up (vendor:product)
.venv/bin/labcli hardware   # the supply is found by its serial number
```

The operator also needs permission to open the USB device, usually a udev rule
for `1ab1:0e11`. Each supply is found by its serial number,
`"usb": "1ab1:0e11", "serial": "DP8B224001812"`, in that Pi's **local**
`~/.formslab/usbmap.json` rather than the shipped defaults; `labcli hardware`
shows the line for each device plugged in.
