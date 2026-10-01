# rScripts — the routines that own the instruments

An rScript is a file `<name>.py` with a module-level `def rScript(forms):`. The
host loads the ones a mode or plan names and calls each once per loop (10 Hz).
A routine owns its instruments for the run: it applies CAST requests for them,
publishes readings as variables on `forms`, and leaves them safe in
`rShutdown(forms)`, which the host calls however the run ends.

    from formslab.rscripts import RScriptControl

    name = "rMine"

    def rScript(forms):
        if RScriptControl(forms, name).tick(seconds=5):   # True = not yet
            return
        forms.log("five seconds passed", component=name)

    def rShutdown(forms):
        ...                                               # outputs off, ports closed

Gates count wall-clock seconds: `tick(seconds=N)` runs now and every N s,
`hold(seconds=N)` waits N s, runs once, and re-arms. `forms` offers `log`,
`types.scalar` / `get_variable`, `record` and `time` (see
`src/formslab/rscripts/handle.py`). `enable = False` at module level keeps a
script from loading.

Found by name in `$FORMSLAB_RSCRIPTS_DIR`, then `<cwd>/rScripts`, then here.

## The routines

| rScript | Owns | CAST label | Publishes |
|---|---|---|---|
| `rPSU` | Rigol DP832A supplies psu1/psu2 (enabled ones only) | `psu1`, `psu2` | `PSU1_CH<n>_V/_I/_ON` |
| `rSMTC08` | SMTC08 thermocouple boards A (TC01-08), B (TC09-16) | — | `TC01`..`TC16` (K) |
| `rTVAC` | Rigol/RTD bench: RTD16, SMTC08, PI shroud heaters on PSU1 CH1 (MY) / CH2 (PY) | `tvac` | `PYsT`, `MYsT`, `target_*`, `TCnn` (K) |
| `rLACO` | LACO chamber via `devices.laco.LACO` (HVC-3500) | `hvc` | `chamberP`, zone temps and setpoints (K), `HVC_*` |
| `rCryoBoard` | cryocooler control board (Pico I2C) and its PSU1 CH1 supply | `cryo` | status on CAST |
| `rSLTA` | sLTA camera, powered from PSU2 CH1 | `slta` | status on CAST |

Do not load two routines that own the same instrument: rPSU and rTVAC share
PSU1 by design in the `tvac` mode (rPSU for console requests, rTVAC's heater
loop on CH1/CH2), but rCryoBoard and rTVAC both claim PSU1 CH1.

rSLTA's automatic capture follows `InUmbra`, `UmbraDuration` and
`UmbraTimeRemaining` variables. A FORMS-computed eclipse profile will publish
them; until then only forced captures (`{"image": true}`) run.

Test timelines (chilldowns, shroud ramps) are lab plans now (`plans/`, see
docs/SEQUENCE.md), not rScripts.
