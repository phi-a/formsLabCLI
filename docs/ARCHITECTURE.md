# Architecture

formsLabCLI controls thermal-vacuum test benches from the terminal and runs
hardware test sequences against them. Running those sequences is its central job.

```
 labcli (console)                         sequence host (one process per run)
 ┌──────────────────────┐                 ┌───────────────────────────────────┐
 │ ctrl  run/pause/end  │── ctrlfile ───▶ │ poll ctrl                          │
 │ cast  status, cmds   │── castfile ◀──▶ │ tick every rScript ─▶ devices/*     │
 │ psu   direct control │                 │ plan step (hold/command/until/log) │
 │ log   host output    │◀── forms.log ── │ CSV row when due                   │
 └──────────────────────┘                 │ rShutdown on any exit              │
                                          └───────────────────────────────────┘
```

## Layers

| Layer | Package | Owns |
|---|---|---|
| Drivers | `formslab.devices` | One module per instrument, plus device objects (`laco.LACO`). Opens ports, speaks protocols; knows nothing about runs. |
| Routines | `rScripts/*.py` on `formslab.rscripts` | The instrument during a run: apply CAST requests for it, publish its readings as variables, leave it safe in `rShutdown`. One owner per instrument. |
| Sequences | `formslab.sequence` | Lab plans: which routines run, and the ordered steps of a test. Steps talk to routines through CAST, never to a driver. |
| Host | `formslab.host` | One run: a mode (fixed routines until `end`) or a plan. Lock, ctrl, pacing, recording, shutdown. |
| Console | `formslab.console`, `formslab.app` | The operator: start and stop runs, watch and command instruments. |

CAST (`castfile.json`) is the bus between them: a request block per instrument
label (`hvc`, `tc`, `psu1`, `psu2`, `cryo`, `slta`), written by the console or a
plan and taken by the routine that owns that label, which writes back a status
block. Each routine declares its own console commands (`CAST_LABELS`,
`CAST_HELP`, `cast_request` -- see rScripts/README.md), so the cast tab, a plan's
`cast` step and the routine always agree on what a command means.

## Modes

One mode, `tvac`: manual chamber operation. It loads the rScripts listed in the
bench config (`tvac_bench.json` -> `tvac`, default rLACO, rSMTC08, rPSU) and runs
them until `end`; the operator works from the cast tab. Everything with an end
-- pumpdown, vent, a soak, a test -- is a lab plan.

## FORMS

FORMS, the astrodynamics engine, is not imported anywhere here
(`test/test_no_forms_runtime.py` holds that). Its role is offline: it computes
orbit-driven profiles -- eclipse entry and exit, shroud temperature targets --
and formsLabCLI replays them on the wall clock. Orbit-driven inputs reach
routines as ordinary variables (rSLTA reads `InUmbra`, `UmbraDuration`,
`UmbraTimeRemaining`). The replay step itself (a plan step that follows a
profile file) is not built yet.

## Known conflicts

- **One controller connection:** the HVC-3500 takes one TCP client. While a
  host runs rLACO, `scripts/vent_test.py` and `scripts/pumpdown.py` cannot
  connect; use the cast tab or the `laco_*` plans.
- **PSU1 CH1:** the cryocooler supply (`cryo_config`). Do not command that
  channel from a plan or the console while rCryoBoard runs.
- **PSU2:** disabled in the shipped usbmap, but rSLTA powers the camera from
  PSU2 CH1 (12 V, 2.0 A). Enable it in the live usbmap before an sLTA run.
- **Console PSU tab:** it opens its own VISA session. Do not use it on a supply
  a run is commanding; watch the run from `cast`.
