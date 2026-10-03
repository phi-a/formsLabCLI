# rScripts — the routines that own the instruments

An rScript is a file `<name>.py` with a module-level `def rScript(forms):`. The
host loads the ones the `tvac` mode or a plan names and calls each once per
loop (10 Hz). A routine owns its instruments for the run: it applies CAST
requests for them, publishes readings as variables on `forms` and as a CAST
status block, and leaves them safe in `rShutdown(forms)`, which the host calls
however the run ends.

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
script from loading. Importing a script must not touch hardware -- the console
imports them to read their commands.

Found by name in `$FORMSLAB_RSCRIPTS_DIR`, then `<cwd>/rScripts`, then here.

## Cast commands

A routine declares its console commands beside the code that applies them
(`src/formslab/rscripts/cast.py`):

    CAST_LABELS = ("hvc",)
    CAST_HELP = [("hvc vent open|close", "Vent valve ..."), ...]
    def cast_request(label, words): ...      # words after the label -> request dict

The cast tab turns `hvc vent open` into `{"vent": "open"}` and writes it to the
CAST `hvc` block; rLACO, running in the host, applies it. A plan step
`{"cast": "hvc vent open"}` means exactly the same. `help` in the cast tab lists
every command the routines declare.

## The routines

| rScript | Owns | CAST label | Publishes |
|---|---|---|---|
| `rLACO` | LACO chamber via `devices.laco.LACO` (HVC-3500): every controller command | `hvc` | `chamberP`, `<zone>T`, `target_<zone>`, `<zone>_effSP`, `HVC_<sensor>` (K); `outputs/LACO.jsonl` |
| `rSMTC08` | SMTC08 thermocouple boards A (TC01-08), B (TC09-16) | `tc` (read-only) | `TC01`..`TC16` (K) |
| `rPSU` | Rigol DP832A supplies psu1/psu2 (enabled ones only) | `psu1`, `psu2` | `PSU1_CH<n>_V/_I/_ON` |
| `rCryoBoard` | cryocooler control board (Pico I2C) and its PSU1 CH1 supply | `cryo` | status on CAST |
| `rSLTA` | sLTA camera, powered from PSU2 CH1 | `slta` | status on CAST |

`run tvac` runs `plans/tvac.plan`, which loads rLACO, rSMTC08 and rPSU and
runs until ctrl `end`.

rLACO's `rShutdown` ends pumping its run started (rough valve closed, pump off)
and releases the controller, which takes one client at a time. rPSU turns off
the channels its run switched on. Do not load two routines that own the same
instrument: rCryoBoard uses PSU1 CH1, so do not also command that channel from
a plan or the console while it runs.

rSLTA's automatic capture follows `InUmbra`, `UmbraDuration` and
`UmbraTimeRemaining` variables. A FORMS-computed eclipse profile will publish
them; until then only forced captures (`slta image`) run.

Test timelines (pumpdown, vent, soaks) are lab plans (`plans/`, see
docs/SEQUENCE.md), not rScripts.
