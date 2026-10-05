# Lab plans

A lab plan is a hardware test sequence in a `.plan` file, one step per line:
which rScripts own the instruments, then what to do, in order.

```
# PSU1 CH1 on for a minute while the thermocouples record.
# load: the routines that own the instruments (first line).
load rPSU rSMTC08
# record: a CSV of every value, outputs/<plan>_<UTC>.csv (default every 10 s).
record every 2 s

# A command is the same words as the cast tab.
psu1 ch1 set 1.0 0.1
psu1 ch1 on
hold 60 s
until TC01 above 30 C timeout 10 min
psu1 ch1 off
log done
```

| step | does | fails the plan when |
|---|---|---|
| `<label> <words>` | a command to the routine that owns the label (`hvc vent open`, `psu1 ch1 on`, `cryo ccv 14`) -- the cast tab's words; waits until the routine has taken it, and for the chamber (rLACO) until it is done | a prerequisite is not met (below); not taken within 10 s; the chamber refuses it |
| `hold <n> s\|min\|h` | runs the routines for a while | — |
| `hold until end` | runs until ctrl `end` (`tvac.plan`: manual operation) | — |
| `until <value> above\|below <n> [C\|K] timeout <n> s\|min\|h` | runs until a published value crosses a limit; `C`/`K` converts from the value's own unit | not met by the timeout (required: a wait on hardware always has a limit) |
| `log <text>` | one line in the run log | — |

`#` starts a comment, on a line of its own (a `#` after a step is an error,
since `log` text may contain one). Words and value names ignore case.

The commands and value names come from what the loaded routines declare
(`COMMANDS`, `VARIABLES`; see rScripts/README.md), and the plan is checked
against them when it is read, before anything runs. A mistake is reported with
its line number and what would fit:

```
tvac.plan:7: expected on or off after 'hvc pump', got 'onn'; did you mean 'on'?
tvac.plan:9: TC01 is published by rSMTC08; add it to `load`
tvac.plan:4: expected s, min or h after 'hold 30', got 'sec'; did you mean 's'?
```

To see what can follow some words, end them with `?` in the cast tab
(`hvc platen ?`) or `labcli cast hvc platen ?`.

### Prerequisites

Some commands are only safe in some states, and the routine that owns them
says which (`RULES`, rScripts/README.md). The chamber's, with limits from
`tvac_bench.json`:

| command | needs first |
|---|---|
| `hvc vent open`, `hvc fill open` | rough and gate closed; every zone inside the vent window (10..60 C); no fault |
| `hvc rough open` | vent, fill, foreline and gate closed |
| `hvc pump off` | rough and foreline closed, turbo off (`hvc stop` does it in order) |
| `hvc foreline open` | rough closed |
| `hvc turbo on` | foreline open |
| `hvc gate open` | turbo on, foreline open, chamberP below the crossover (0.01 Torr) |
| `cryo on` | its supply at 20 V or more |
| anything, during a fault (severity F) | refused, except closing valves, `stop`, zones off, `closeall`, `reset`, `abort` |
| a supply channel the hardware map gives an owner | refused while that owner runs (psu1 ch1: rCryoBoard) |

They are checked when the plan is read. A step that breaks one is an error
and the plan cannot start:

```
bad.plan:5: needs rough closed (line 4 changed it): air may only come in with the chamber sealed ...
```

A step whose conditions the plan does not itself establish is a **warning**
(amber in the editor; the plan can run). The plan establishes a state by
commanding it (`hvc rough close`), or a value with an `until` just before the
step (`until platenT below 60 C ...`, as `laco_vent` does). Every rule is then
checked again, live, when the step runs, against what the chamber last reported:
a step it fails stops the plan, before anything is sent. The command box, the
cast tab and `labcli cast` check the same rules and refuse with the reason.

Shipped plans: `tvac` (manual operation from the cast tab, until `end`),
`psu1_smtc08_first` (PSU1 + thermocouples), `laco_pumpdown`
(pump on, rough open, until below 5 Torr, stop) and `laco_vent` (temperature
guards, vent valve open, until atmosphere).

Orbit content (`orbit.*`, `propagate`, `@procedure`) is refused: that is FORMS'
part, done offline (see ARCHITECTURE.md). A file in the old format
(`sequence.operations = [...]`) is refused with a pointer here.

## Running

```
labcli check <plan>                              # read it: steps listed, errors by line
labcli run <plan>                                # or in the console: ctrl> run <plan>
labcli status / end
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

The host writes `~/.formslab/.run/sequence.events.jsonl` (one per machine,
beside its lock and `host.log`): `sequence_started` (with
the plan's manifest), `segment_started`, `progress`, `segment_finished`,
`sequence_finished` (with `error` when the plan stopped).
