# formsLabCLI: intent and architecture

Updated 2026-10-05. What the project is for and how it is put together, kept short
enough to read before starting any work. The detail is in docs/ARCHITECTURE.md (the
layers), docs/SEQUENCE.md (plans), docs/GUI.md and docs/ORBIT.md. Progress is in
[progress.md](progress.md).

## What it is for

formsLabCLI runs the thermal-vacuum bench at the University of Illinois: the LACO
chamber ([tvac-chamber.md](tvac-chamber.md)) with its HVC-3500 controller, Rigol
supplies, SMTC08 thermocouple readers, a cryocooler board, a PowerSwitch and the sLTA
imaging chain. It is used from a terminal console, over SSH (`labcli <command>`) and
from a web GUI.

**Its central job is running test sequences** (plans) against that hardware safely:
a pumpdown, a soak, a vent, an imaging test in a simulated umbra.

**Its long-term goal** is to put a satellite's orbit into the chamber: from an orbit
and a spacecraft model, work out the background temperatures each face sees, then
drive the chamber to reproduce them while the satellite's own instruments run. The
orbit files, rOrbit and the environment models in `formslab.orbit` are the first
parts of that; the rest is staged in [gui.md](gui.md), section 4.

**Who uses it:** operators at the bench, often students who do not know the
controller's protocol, and the engineers who write the tests. Every piece of text is
written for the first of them (docs/WRITING.md).

## Principles

1. **Simple and elementary.** The author's lens is Maniacal Simplicity
   (docs/WRITING.md): the clearest sufficient form. It applies to the code, the plan
   grammar and the text alike. A plan should read like the test it describes.
2. **Everything is a plan.** Every run is a plan file, even manual operation (`tvac`).
   One host process runs one plan; the console and the GUI only start, watch and end it.
3. **One owner per instrument.** During a run, one rScript owns each instrument and
   is the only thing that talks to it. Everything else sends it requests through CAST.
4. **Declarations as data.** Each rScript declares its commands, published values and
   prerequisites (`COMMANDS`, `VARIABLES`, `RULES`). One grammar reads them, so the
   console, a plan, the GUI's dropdowns and the help all agree, and a plan is checked
   before anything runs.
5. **The files are the truth.** Plans, blocks, orbits and the hardware map are text;
   runs are CSVs; state between processes is the shared CAST and ctrl files. There is
   no database.
6. **Works without the bench.** Simulators, `scripts/gui_demo.py` and the test suite
   (about 1,200 tests, on Windows and Linux in CI) run with no hardware.
7. **FORMS stays offline.** The astrodynamics engine is never imported
   (`test/test_no_forms_runtime.py`). It computes profiles; formsLabCLI runs them.
   The orbit models it needs at the bench now live in `formslab.orbit`.

## Architecture at a glance

```
 labcli console ─┐                         ┌─ sequence host (one per run) ─┐
 labcli <cmd>  ──┼── ctrl file ─────────▶  │ plan steps, one per tick      │
 labcli gui    ──┘── CAST file  ◀───────▶  │ rScripts ─▶ devices/ drivers  │──▶ instruments
                     (requests, status)    │ recorder ─▶ <plan>_<UTC>.csv  │
                                           └───────────────────────────────┘
```

| Layer | Package | Owns |
|---|---|---|
| Drivers | `formslab.devices` | One folder per instrument; ports and protocols, no idea of a run |
| Routines | `rScripts/*.py` | An instrument during a run: take its CAST requests, publish its readings, leave it safe |
| Grammar | `formslab.rscripts` | The declared commands: parsing, completion, help, prerequisites |
| Sequences | `formslab.sequence` | Plans, blocks, loops, conditions; the checks a plan gets when read |
| Host | `formslab.host` | One run: lock, ctrl, pacing, recording, shutdown on any exit |
| Orbits | `formslab.orbit` | Orbit files and the models they feed: propagation (Kepler + J2), geometry, view factors, thermal |
| Console, GUI | `formslab.console`, `formslab.gui` | The operator's two front ends over the same files |

A plan is one step per line: `load` (which rScripts run), `record every`, then
commands (`hvc platen 20`), waits (`until chamberP < 5 within 20 min`), holds, logs,
calls to blocks and loops. Three kinds of file share one name space on the plan path:
`.plan`, `.block` and `.orbit`.

## Where things are

| | |
|---|---|
| Code | `src/formslab/`; the routines in `rScripts/` |
| Shipped plans, blocks, orbits | `plans/` (read-only in the GUI; *Edit* takes one out to change it) |
| A computer's own files | `~/.formslab/`: `usbmap.json` (the hardware map), `plans/`, `gui.json`, `.run/` (the host's lock, events and log) |
| Recorded runs | `outputs/` (ignored by git) |
| Docs (what exists) | `docs/` and the READMEs |
| Notebook (intent, decisions, progress) | here |
