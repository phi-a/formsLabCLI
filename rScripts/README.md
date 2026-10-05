# rScripts — the routines that own the instruments

An rScript is a file `<name>.py` with a module-level `def rScript(run):`. The
host loads the ones the plan names (`load`) and runs each in its
own thread at 10 Hz. A routine owns its instruments for the run: it applies CAST
requests for them, publishes readings as variables on `run` and as a CAST
status block, and leaves them safe in `rShutdown(run)`, which the host calls
however the run ends.

    from formslab.rscripts import RScriptControl

    name = "rMine"

    def rScript(run):
        if RScriptControl(run, name).tick(seconds=5):   # True = not yet
            return
        run.log("five seconds passed", component=name)

    def rShutdown(run):
        ...                                             # outputs off, ports closed

Gates count wall-clock seconds: `tick(seconds=N)` runs now and every N s,
`hold(seconds=N)` waits N s, runs once, and re-arms. `run` offers `log`,
`publish(name, value, unit)` / `get(name)` / `variable(name)`, `record` and
`time` (see `src/formslab/rscripts/run.py`). `enable = False` at module level keeps a
script from loading. Importing a script must not touch hardware -- the console
imports them to read their commands.

Found by name in `$FORMSLAB_RSCRIPTS_DIR`, then `<cwd>/rScripts`, then here.

## Cast commands

A routine declares, as data beside the code that applies them, the commands it
takes and the values it publishes (`src/formslab/rscripts/grammar.py` has the
pattern syntax):

    CAST_LABELS = ("psu1", "psu2")
    COMMANDS = [
        ("<ch:ch1|ch2|ch3> on|off", "Channel output",
         lambda ch, s: {ch[2:]: {"on": s == "on"}}),
        ("<ch:ch1|ch2|ch3> set <V:number 0..32 V> <A:number 0..3.2 A>", "Setpoints",
         lambda ch, v, a: {ch[2:]: {"voltage": v, "current": a}}),
        ("update", "Read the supply now", {"update": True}),
    ]
    VARIABLES = [("PSU1_CH1_V", "V"), ...]      # (name, unit) of every value it publishes

Either list may be a function returning it, when it depends on the bench (rLACO
reads its zones from `tvac_bench.json`). Importing a routine must not touch
hardware, start threads or write files: the console imports it to read these.

A routine may also say what must be true before a command is sent
(`src/formslab/rscripts/rules.py`; rLACO's are in `devices/hvc3500/rules.py`):

    RULES = [Rule("hvc", {"rough": True}, (closed("vent"), closed("gate")), "why, in words")]
    def RULE_STATE(status): ...        # its CAST status block -> the names conditions use
    def RULE_EFFECTS(request): ...     # what a request leaves set (default: its on/off keys)
    RESULT_LABELS = ("hvc",)           # it reports done or refused for each request it takes

Plans are checked against them when read, and every command when it is sent
(docs/SEQUENCE.md, Prerequisites).

The cast tab turns `hvc vent open` into `{"vent": "open"}` and writes it to the
CAST `hvc` block; rLACO, running in the host, applies it. The same words in a
plan mean exactly the same. In the cast tab, `help` lists every command and a
trailing `?` (`hvc platen ?`) lists what can come next.

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
