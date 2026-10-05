# Architecture

formsLabCLI controls thermal-vacuum test benches from the terminal and runs
hardware test sequences against them. Running those sequences is its central job.

```
 labcli (console, or `labcli <command>`)  sequence host (one process per run)
 ┌──────────────────────┐                 ┌───────────────────────────────────┐
 │ ctrl  run/pause/end  │── ctrlfile ───▶ │ poll ctrl                          │
 │ cast  status, cmds   │── castfile ◀──▶ │ tick every rScript ─▶ devices/*     │
 │ psu   direct control │                 │ plan step (hold/command/until/log) │
 │ log   host output    │◀── host.log ─── │ CSV row when due                   │
 └──────────────────────┘                 │ rShutdown on any exit              │
 labcli gui (web page)                    └───────────────────────────────────┘
 ┌──────────────────────┐                                  ▲
 │ status, chamber,     │── the same ctrl and CAST files, ─┘
 │ plans, plots         │   lock and log; it never opens an instrument
 └──────────────────────┘
```

## Layers

| Layer | Package | Owns |
|---|---|---|
| Drivers | `formslab.devices` | One folder per instrument (`hvc3500`, `dp832a`, `smtc08`, `cryocooler`, `slta`, `powerswitch`), plus device objects (`hvc3500.LACO`). Opens ports, speaks protocols; knows nothing about runs. |
| Routines | `rScripts/*.py` on `formslab.rscripts` | The instrument during a run: apply CAST requests for it, publish its readings as variables, leave it safe in `rShutdown`. One owner per instrument. |
| Grammar | `formslab.rscripts.grammar`, `cast` | The commands each routine declares, as data: parses a cast-tab line or a plan step, lists what can come next (completion, the GUI's dropdowns), and builds the help cards. |
| Sequences | `formslab.sequence` | Lab plans, one step per line: which routines run, and the ordered steps of a test. Steps talk to routines through CAST, never to a driver. |
| Host | `formslab.host` | One run of one plan. Lock, ctrl, pacing, recording, shutdown. |
| Orbit files | `formslab.kepler` | `.orbit` files: Keplerian elements, their grammar (the same `Grammar`), two-body propagation, umbra and beta. Standard library only; the GUI opens them, nothing runs them (docs/ORBIT.md). |
| Console | `formslab.console`, `formslab.app` | The operator: start and stop runs, watch and command instruments. |

CAST (`castfile.json`) is the bus between them: a request block per instrument
label (`hvc`, `tc`, `psu1`, `psu2`, `cryo`, `slta`), written by the console or a
plan and taken by the routine that owns that label, which writes back a status
block. Each routine declares its own commands and published values as data
(`CAST_LABELS`, `COMMANDS`, `VARIABLES`, and what must be true first, `RULES`
-- see rScripts/README.md), read by one
grammar (`rscripts/grammar.py`), so the cast tab, a plan and the routine always
agree on what a command means, and the same declarations answer "what can come
next" for completion.

### The shared files

`castfile.json` and `ctrlfile.json` (in the config dir) are written by several
processes at once: the console, the host and, later, the web GUI. Every change
is a read-modify-write under a cross-process lock (`console/safefile.py`, a
`<name>.lock` sidecar the OS releases if its holder dies), and a write replaces
the file in one step, retried while a reader has it open and never done in
place. A file that will not parse is kept as `<name>.bad` and regenerated from
the defaults, rather than read as empty and written back over every other
block. `end` is sent again while the console waits for a host that is starting,
because a starting host clears the ctrl file after it takes its lock. A block's
`timestamp` is when its owner last reported; a command (`request_timestamp`) or a
host start does not change it, so old values read as old.

## Everything is a plan

Every run is a plan (`plans/*.plan`). Manual chamber operation is the plan
`tvac`: it loads rLACO, rSMTC08 and rPSU and holds until `end`, while the
operator works from the cast tab or `labcli cast` over SSH. The running host's
lock, events and log are kept per machine (`~/.formslab/.run`). Pumpdown, vent, a soak, a test are plans with
steps and an end. A computer with different instruments keeps its own copy of
a plan in `$FORMSLAB_PLANS_DIR`, which is searched before the checkout.

## FORMS

FORMS, the astrodynamics engine, is not imported anywhere here
(`test/test_no_forms_runtime.py` holds that). Its role is offline: it computes
orbit-driven profiles -- eclipse entry and exit, shroud temperature targets --
and formsLabCLI replays them on the wall clock. Orbit-driven inputs reach
routines as ordinary variables (rSLTA reads `InUmbra`, `UmbraDuration`,
`UmbraTimeRemaining`). The replay step itself (a plan step that follows a
profile file) is not built yet.

An orbit file (`formslab.kepler`) is lab-side configuration, not FORMS: one orbit
by two-body motion, for the GUI to show and, later, for the environment models in
`formslab.orbit` to start from. Its constants and Sun ephemeris are copies of
`formslab.orbit.propagate`'s, because that package imports nothing outside itself
and this one must not need numpy; a test holds the copies identical.

## Known conflicts

- **One controller connection:** the HVC-3500 takes one TCP client. While a
  host runs rLACO, `scripts/vent_test.py` and `scripts/pumpdown.py` cannot
  connect; use the cast tab or the `laco_*` plans.
- **Owned supply channels:** `usbmap.json` records what each channel feeds and
  which rScript drives it (`"channels"`; shipped: PSU1 CH1 → cryocooler board,
  rCryoBoard; PSU2 CH1 → sLTA camera, rSLTA). Do not command an owned channel
  from a plan or the console while its owner runs.
- **PSU2:** disabled in the shipped usbmap, but rSLTA powers the camera from it
  (12 V, 2.0 A). Enable it in the live usbmap before an sLTA run.
- **Console PSU tab:** it opens its own VISA session. Do not use it on a supply
  a run is commanding; watch the run from `cast`.
