# FormsLabCLI

A terminal console for benchtop lab hardware: Rigol programmable supplies, a
cryocooler control board reached over a Raspberry Pi Pico I2C bridge, RTD and
thermocouple readers, TVAC shroud heaters, a Digital Loggers PowerSwitch, and
the sLTA imaging chain.

It runs standalone. When [FORMS](https://github.com/phi-a/FORMS) — the
astrodynamics library — is also installed, two extra commands light up: the
API-catalog browser and the external-resource registry.

**New computer? Start with [the setup guide](docs/SETUP.md)** for Python,
NI-VISA, finding the current COM ports, and editing your bench's hardware map.

```
pip install -e .          # a working lab console
fconsole                  # start it
```

## Tabs

| Tab | What it drives |
|---|---|
| `ctrl` | Sequence control: launch, pause, resume, end |
| `cast` | Live hardware status panel |
| `psu` | Rigol supplies and PowerSwitch outlets |
| `log` | Tail the run log |

Switch with `--psu`, `--cast`, … or start on one: `fconsole --psu`.

## Install

The base install is the console and the transports its drivers open — nothing
else. No numpy, no matplotlib, no astrodynamics library; driving a PSU needs
none of them, and that absence is why this package was split out of FORMS.

```
pip install -e .              # console + transports
pip install -e ".[pico]"      # + mpremote, to talk to the cryo board's Pico bridge
pip install -e ".[analysis]"  # + matplotlib/PyQt6 plotting
pip install -e ".[forms]"     # + FORMS, for missions/catalog/resource commands
pip install -e ".[dev]"       # + pytest
```

## Layout

```
src/formslab/
├── app.py       the `fconsole` entry point: the REPL and its tab bar
├── bridge.py    the ONLY module allowed to import `forms` (host/ excepted)
├── config.py    config and output directory resolution
├── state.py     CTRL command table and CAST device state
├── console/     the tabs, sessions, and command tables
├── devices/     one module per instrument
├── defaults/    shipped usbmap.json
└── host/        the sequence host — needs [forms]
rScripts/        hardware routines, workspace content (see its README)
scripts/         standalone bench tools: plotting, image conversion, GUI bridge
```

### The FORMS seam

`formslab/bridge.py` is the single place this package names `forms`. Accessors
import at call time and raise `FormsUnavailable` when the library is absent, so
the console loads and runs on a machine that has only the transports installed;
a library-backed command reports that FORMS is missing instead of raising
through the REPL. `bridge.SURFACES` is the whole dependency — five read-only
reporting modules. `test/test_console_bridge.py` enforces both properties.

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

## Running a mission

`ctrl`'s `run` starts the sequence host — the process that drives a FORMS
mission against the bench:

```
fconsole --ctrl
ctrl> missions          # the .zen library, as FORMS resolves it
ctrl> run tvac          # or: run darkness, run 1
log>  tail 50           # the host's output
```

This needs the `[forms]` extra. Without it `run` says so rather than failing
part-way. The host can also be started directly:

```
python -m formslab.host.sequence --mode tvac
```

`missions` asks FORMS where the library is (`$FORMS_MISSIONS_DIR`, then a walk
up for `missions/`, then a remembered workspace) rather than keeping its own
idea of it, so the console and the host always agree about which missions exist.

## Status

Extracted from the FORMS repository, where this was `python/cli/` +
`python/lab/` plus the host scripts at `python/`. Everything is reconnected; the
remaining work is on the FORMS side, where the original copies still need
deleting.

## Relationship to FORMS

FORMS is the astrodynamics library and Astrid is the agent built on it; this is
the lab tool that used to live in that repository. The dependency points one way
only — FORMS knows nothing about this package — and is optional in this
direction. A later release adds an `astrid-mcp` path so the console can reach
FORMS through the agent as well as directly.
