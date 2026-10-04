# FormsLabCLI

A terminal console for thermal-vacuum testing: control chamber temperatures,
supplies and readouts from the terminal, and run hardware test sequences (lab
plans) against them. It drives the LACO chamber's HVC-3500 controller, Rigol
programmable supplies, SMTC08 thermocouple readers, a
cryocooler control board behind a Raspberry Pi Pico I2C bridge, a Digital
Loggers PowerSwitch, and the sLTA imaging chain.

[FORMS](https://github.com/phi-a/FORMS), the astrodynamics engine, is not a
dependency. Its part is offline: it computes orbit-driven profiles (eclipse
timing, temperatures), and formsLabCLI runs them on the bench.

**New computer? Start with [the setup guide](docs/SETUP.md)** for Python,
NI-VISA, finding the current COM ports, and editing your bench's hardware map.

```
pip install -e .          # a working lab console
labcli                    # start it
```

## Updating without losing your settings

Keep each computer's settings and files separate from the shared code:

- Edit the live hardware map at `%USERPROFILE%\.formslab\usbmap.json` on
  Windows (`~/.formslab/usbmap.json` on Linux). This holds your COM ports and
  device addresses. Do not edit `src/formslab/defaults/usbmap.json`; it is a
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

## Tabs

| Tab | What it drives |
|---|---|
| `ctrl` | Runs: `plans`, `run <plan|tvac>`, pause, resume, end |
| `cast` | Instrument status, and commands to them (`hvc vent open`, `psu1 ch1 on`, `help`) |
| `psu` | Rigol supplies and PowerSwitch outlets |
| `log` | Tail the run log |

Switch with `--psu`, `--cast`, … or start on one: `labcli --psu`.

### The screen

The console redraws one fixed frame rather than printing a transcript. A tab
bar, a status region, a content pane and the prompt sit on the same rows every
time, so the prompt does not walk down the terminal as output grows and shrinks.
The regions are drawn as boxes deliberately: a pane that pads to a constant
height only reads as a window if you can see it holding its shape, and without a
border a one-line result and a thirty-line one look like the same printed text.

Output taller than the content pane is windowed in place rather than spilled
into scrollback. `PgUp`/`PgDn` move it by a page and `↑`/`↓` by a line, without
Enter; the pane's bottom edge carries the position, `1-21 of 28`.

Both that and resizing work because the console reads *keys*, not lines
(`console/keys.py`). `input()` blocks until Enter, so between keystrokes the old
console could not notice the window had changed shape or that you wanted to
scroll — the same limitation behind both. Polling for keys leaves an idle gap on
every tick, and the resize check and the scroll keys live in that gap.

Where keys cannot be read — a pipe, a capture, a dumb terminal — the console
falls back to plain `input()` and append-only text, so redirected runs stay
diffable. The typed `more`, `back` and `top` exist for that path.

The status region is the tab's `banner()`, drawn only where a tab sets
`live_status`: it is polled once per repaint. CTRL (reads `sequence.pid`) and
CAST (reads `castfile.json`) opt in. PSU's banner opens a VISA session per
supply and queries every channel, so it stays a command rather than a region —
a new tab has to declare its banner free before the frame will poll it.

## Install

The base install is the console and the transports its drivers open — nothing
else. No numpy, no matplotlib, no astrodynamics library; driving a PSU needs
none of them.

```
pip install -e .              # console + transports
pip install -e ".[pico]"      # + mpremote, to talk to the cryo board's Pico bridge
pip install -e ".[images]"    # + astropy, for scripts/fz2fits.py (sLTA frames)
pip install -e ".[orbit]"     # + numpy/scipy/matplotlib, for formslab.orbit (orbit and environment models)
pip install -e ".[dev]"       # + pytest
```

## Layout

```
src/formslab/
├── app.py       the `labcli` entry point: the REPL and its tab bar
├── config.py    config, output and run directory resolution
├── state.py     CTRL command table and CAST device state
├── console/     the tabs, sessions, and command tables
├── devices/     one folder per instrument (hvc3500, dp832a, smtc08, cryocooler, slta, powerswitch)
├── defaults/    shipped usbmap.json and tvac_bench.json
├── rscripts/    the rScripts runtime: loader, gates, the `forms` handle
├── sequence/    lab plans: test sequences, read and run
├── host/        the process that runs a plan
└── orbit/       orbit, view-factor and environment models ([orbit] extra; nothing above imports it)
rScripts/        the routines that own the instruments during a run
plans/           lab plans, e.g. psu1_smtc08_first.plan
scripts/         launchers and standalone analysis tools
```

How the pieces fit is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Configuration and state

Nothing is written inside the installed package. Three locations, by what the
thing is:

| Location | Holds | Override |
|---|---|---|
| package | code, command tables, Pico firmware, shipped defaults | — |
| config | live `usbmap.json`, CTRL command table, CAST device state | `$FORMSLAB_CONFIG_DIR` (default `~/.formslab`) |
| output | logs, captured frames, temperature histories | `$FORMSLAB_OUTPUT_DIR` (default `<cwd>/outputs`) |

`usbmap.json` is the hardware map: instrument VISA addresses and USB VID/PIDs,
hub locations, the PowerSwitch host, and the cryo board's I2C pins and firmware
version. It differs per bench, so it ships as a default in
`src/formslab/defaults/` and is copied into the config directory the first time
a driver asks for it — edit the copy, and an upgrade will not overwrite it. The
PowerSwitch password is read from the environment variable named by its
`password_env` key, never stored in the file.

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

A lab plan is a hardware test sequence: which rScripts own the instruments,
and an ordered list of steps (`command`, `hold`, `until`, `log`). The grammar
is in [docs/SEQUENCE.md](docs/SEQUENCE.md).

```
python -m formslab.sequence psu1_smtc08_first     # check a plan; touches no hardware
labcli --ctrl
ctrl> plans                                        # lab plans; tvac runs until end
ctrl> run tvac                                     # manual operation: then the cast tab
ctrl> run laco_pumpdown                            # or any plan
ctrl> pause / resume / end
log>  tail 50                                      # the host's output
```

`end` asks the host to stop, so every rScript's `rShutdown` runs (a plan's PSU
outputs go off, pumping the run started stops) before it exits. `tvac` runs
the bench's rScripts until `end` while you operate from the cast tab; a plan
ends by itself. Every run writes a CSV of its variables to `outputs/`.

## Windows and Linux (Raspberry Pi)

One checkout runs on both. What differs is below the drivers, and each piece is
chosen for you:

| | Windows bench | Linux / Pi |
|---|---|---|
| VISA backend | NI-VISA | pyvisa-py (installed by `pip install -e .`) |
| USB transport | NI-VISA's USB driver | pyusb + libusb |
| PSU resource | `resource_windows` in `usbmap.json` | `resource` in `usbmap.json` |

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
.venv/bin/labcli --psu
```

The operator also needs permission to open the USB device, usually a udev rule
for `1ab1:0e11`. Each supply's resource string contains its serial number, e.g.
`USB0::6833::3601::DP8B224001812::0::INSTR`, so map it in that Pi's **local**
`~/.formslab/usbmap.json` rather than the shipped defaults.
