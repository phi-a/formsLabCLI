# Architecture

formsLabCLI controls thermal-vacuum test benches from the terminal and runs
hardware test sequences against them. Running those sequences is its central job.

```
 labcli (the watch screen, or `labcli <command>`)   sequence host (one process per run)
 labcli gui (formsLAB, the web page)                ┌───────────────────────────────────┐
 ┌──────────────────────────┐── ctrlfile ────────▶  │ poll ctrl                          │
 │ formslab.lab: what is    │── castfile ◀───────▶  │ tick every rScript ─▶ devices/*     │
 │ going, run, pause, end,  │◀── lock, run.json ──  │ plan step (hold/command/until/log) │
 │ commands                 │◀── host.log ────────  │ a row recorded when due            │
 └──────────────────────────┘                       │ the end script, rShutdown on exit  │
   between runs, one of them reads the chamber      └───────────────────────────────────┘
   formslab.safety (labcli only): supplies off, the PowerSwitch; refused while a run is going
```

## Layers

| Layer | Package | Owns |
|---|---|---|
| Drivers | `formslab.devices` | One folder per instrument (`hvc3500`, `dp832a`, `smtc08`, `cryocooler`, `slta`, `powerswitch`), plus device objects (`hvc3500.LACO`). Opens ports, speaks protocols; knows nothing about runs. |
| Routines | `rScripts/*.py` on `formslab.rscripts` | The instrument during a run: apply CAST requests for it, publish its readings as variables, leave it safe in `rShutdown`. One owner per instrument. |
| Grammar | `formslab.rscripts.grammar`, `cast` | The commands each routine declares, as data: parses a typed command or a plan step, lists what can come next (completion, the GUI's dropdowns), and builds the help cards. |
| Sequences | `formslab.sequence` | Lab plans, one step per line: which routines run, and the ordered steps of a test. Steps talk to routines through CAST, never to a driver. |
| Host | `formslab.host` | One run of one plan. Lock, ctrl, pacing, recording, shutdown. |
| Orbits | `formslab.orbit` | `.orbit` files (`file`: Keplerian elements, read with the same `Grammar`; the GUI opens them, and the rScript rOrbit follows one during a run, [orbit](../use/orbit.md)), and the models they feed, in layers: `propagate` (Kepler motion with J2 drift, the `Orbit` the models sweep), `geometry` (attitude, spacecraft), `viewfactor`, `thermal` (flux, environment temperature), `visibility` and `imaging`. numpy; the transient solver needs scipy and the plots matplotlib (the `orbit` extra). |
| Front ends' backend | `formslab.lab` | What is going (the lock, where the run is, each block live or not, how the last run ended), start a plan, end it (never killing the host), pause and resume, a command, the audit line. One set of functions, so labcli and formsLAB cannot disagree. |
| Console | `formslab.cli`, `formslab.console.watch`, `formslab.app` | The operator at a terminal or over SSH: `labcli <command>`, and the watch screen that repaints every second. |
| Off switches | `formslab.safety` | Below End, for when no run is there to do it: every supply's outputs off over VISA; a PowerSwitch outlet on, off or cycled. Refused while a run is going ([safety](../rules/safety.md)). |

CAST (`castfile.json`) is the bus between them: a request block per instrument
label (`hvc`, `tc`, `psu1`, `psu2`, `cryo`, `slta`, `orbit`), written by the console or a
plan and taken by the routine that owns that label, which writes back a status
block. Each routine declares its own commands and published values as data
(`CAST_LABELS`, `COMMANDS`, `VARIABLES`, and what must be true first, `RULES`
-- see rScripts/README.md), read by one
grammar (`rscripts/grammar.py`), so a typed command, a plan and the routine always
agree on what a command means, and the same declarations answer "what can come
next" for completion.

### The shared files

`castfile.json` and `ctrlfile.json` (in the config dir) are written by several
processes at once: the host, formsLAB and labcli. Every change
is a read-modify-write under a cross-process lock (`state/safefile.py`, a
`<name>.lock` sidecar the OS releases if its holder dies), and a write replaces
the file in one step, retried while a reader has it open and never done in
place. A file that will not parse is kept as `<name>.bad` and regenerated from
the defaults, rather than read as empty and written back over every other
block. `end` is sent again while a front end waits for a host that is starting,
because a starting host clears the ctrl file after it takes its lock. The host also replaces `.run/run.json` on each change (the step, paused,
ending), which both front ends read as where the run is. A block's
`timestamp` is when its owner last reported; a command (`request_timestamp`) or a
host start does not change it, so old values read as old.

## Everything is a plan

Every run is a plan (`plans/*.plan`). Manual chamber operation is the plan
`tvac`: it loads rLACO, rSMTC08 and rPSU and holds until `end`, while the
operator works from the watch screen or `labcli cast` over SSH. The running host's
lock, events and log are kept per machine (`~/.formslab/.run`). Pumpdown, vent, a soak, a test are plans with
steps and an end. A computer with different instruments keeps its own copy of
a plan in `$FORMSLAB_PLANS_DIR`, which is searched before the checkout.

## FORMS

FORMS, the astrodynamics engine, is not imported anywhere here
(`test/test_no_forms_runtime.py` holds that). Its role is offline: it computes
orbit-driven profiles -- eclipse entry and exit, shroud temperature targets --
and formsLabCLI replays them on the wall clock. Orbit-driven inputs reach
routines as ordinary variables (rSLTA reads `InUmbra`, `UmbraDuration`,
`UmbraTimeRemaining`). A step that replays a FORMS profile file is not built yet.

An orbit file is lab-side configuration, not FORMS: one orbit, by Kepler motion with J2,
which the GUI shows live, the environment models in `formslab.orbit` sweep
(`file.load` gives the `Orbit`), and the rScript rOrbit follows during a run,
publishing the umbra variables above from it. `formslab.orbit` is part of formsLabCLI; its
models import no other part of it, and only the orbit file reaches into the
grammar (`test/test_orbit_layout.py`).

## Known conflicts

- **One controller connection:** the HVC-3500 takes one TCP client. While a
  host runs rLACO, `scripts/vent_test.py` and `scripts/pumpdown.py` cannot
  connect; use `labcli cast` or a plan. Between runs, formsLAB's chamber
  monitor holds it, or a labcli watch screen's ([gui](../use/gui.md), Between runs): start
  them with `--no-monitor` to use the scripts. A host waits for the monitor to let go before it starts.
- **Owned supply channels:** `usbmap.json` records what each channel feeds and
  which rScript drives it (`"channels"`; shipped: PSU1 CH1 → cryocooler board,
  rCryoBoard; PSU2 CH1 → sLTA camera, rSLTA). Do not command an owned channel
  from a plan or the console while its owner runs.
- **PSU2:** disabled in the shipped usbmap, but rSLTA powers the camera from it
  (12 V, 2.0 A). Enable it in the live usbmap before an sLTA run.
- **The off switches:** `labcli supplies off` and `labcli power` open a supply or the
  switch themselves, so they are refused while a run is going; End is the way then
  ([safety](../rules/safety.md), When something must go off).
