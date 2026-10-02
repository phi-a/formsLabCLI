# Lab plans

A lab plan is a hardware test sequence: the rScripts that own the instruments,
and an ordered list of steps. It is a `.forms` file that formsLabCLI reads
statically (literal assignments only, nothing executed).

```python
mission.name = "psu1_smtc08_first"
rscripts.load = ["rPSU", "rSMTC08"]      # the routines that own the instruments
recording.interval = 2                   # CSV of every variable: outputs/<name>_<UTC>.csv
recording.unit = "seconds"

sequence.operations = [
    {"command": "psu1", "request": {"1": {"voltage": 1.0, "current": 0.1}}},
    {"command": "psu1", "request": {"1": {"on": True}}},
    {"hold": 60, "units": "seconds"},
    {"until": "TC01", "above": 30.0, "unit": "C", "timeout_s": 600},
    {"command": "psu1", "request": {"1": {"on": False}}},
    {"log": "done"},
]
```

| step | does | fails the plan when |
|---|---|---|
| `hold` | runs the routines for a duration (`units`: seconds, minutes, hours) | — |
| `command` | writes a CAST request to an instrument label and waits until the routine that owns it has taken it | not taken within `timeout_s` (default 10) |
| `cast` | the same, as the cast tab's words: `{"cast": "hvc pump on"}` -- checked against the routine's grammar when the plan is read | as `command` |
| `until` | runs until a variable is `above` / `below` a value; `unit` converts C/K | not met within `timeout_s` (required: a wait on hardware always has a limit) |
| `log` | one line in the run log | — |

The request grammar of each label is its routine's; `cast` steps use the cast
tab's words instead, which is usually easier to read. `help` in the cast tab
lists them all; `LACO.apply` documents the `hvc` dict grammar.

Shipped plans: `psu1_smtc08_first` (PSU1 + thermocouples), `laco_pumpdown`
(pump on, rough open, until below 5 Torr, stop) and `laco_vent` (temperature
guards, vent valve open, until atmosphere).

Orbit content (`orbit.*`, `propagate`, `@procedure`) is refused: that is FORMS'
part, done offline (see ARCHITECTURE.md).

## Running

```
python -m formslab.sequence <plan>              # check: scripts found, steps listed
labcli --ctrl  ->  run <plan>                    # or:
python -m formslab.host.sequence --plan <plan>
```

Plans are found by path, or by name in `$FORMSLAB_PLANS_DIR`, `<cwd>/plans`,
then the checkout's `plans/`. A plan whose rScripts do not all load does not
start.

Each loop (10 Hz): poll ctrl, pausing here while paused -> each routine once ->
CSV row when due. Holds and limits count active time, so a pause does not use
up a hold.

## Ending

However a run ends -- last step, ctrl `end`, a failed step, a crash -- each
loaded routine's `rShutdown` runs before the host exits. rPSU turns off the
channels the run switched on; rLACO ends pumping the run started (rough valve
closed, pump off) and releases the controller; rSMTC08 and rCryoBoard release
their ports.

The host writes `outputs/.run/sequence.events.jsonl`: `sequence_started` (with
the plan's manifest), `segment_started`, `progress`, `segment_finished`,
`sequence_finished` (with `error` when the plan stopped).
