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
label (`psu1`, `tvac`, `hvc`, `cryo`, `slta`), written by the console or a plan
and taken by the routine that owns that label, which writes back a status block.

## FORMS

FORMS, the astrodynamics engine, is not imported anywhere here
(`test/test_no_forms_runtime.py` holds that). Its role is offline: it computes
orbit-driven profiles -- eclipse entry and exit, shroud temperature targets --
and formsLabCLI replays them on the wall clock. Orbit-driven inputs reach
routines as ordinary variables (rSLTA reads `InUmbra`, `UmbraDuration`,
`UmbraTimeRemaining`). The replay step itself (a plan step that follows a
profile file) is not built yet.

## Known conflicts

- **PSU1 CH1:** the cryocooler supply (`cryo_config`) and rTVAC's MY shroud
  heater both use it. The `tvac` mode does not load rCryoBoard for that reason;
  do not put both in one plan until the bench wiring is settled.
- **PSU2:** disabled in the shipped usbmap, but rSLTA powers the camera from
  PSU2 CH1 (12 V, 2.0 A). Enable it in the live usbmap before an sLTA run.
- **Console PSU tab:** it opens its own VISA session. Do not use it on a supply
  a run is commanding; watch the run from `cast`.
