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
| `hold <time> s\|min\|h` | runs the routines for a while | — |
| `hold until end` | runs until ctrl `end` (`tvac.plan`: manual operation) | — |
| `until <variable> above\|below <limit> [C\|K] timeout <time> s\|min\|h` | runs until a published value crosses a limit; `C`/`K` converts from the value's own unit | not met by the timeout (required: a wait on hardware always has a limit) |
| `log <text>` | one line in the run log | — |
| `repeat …` … `end` | the steps between them again: n times, until a value passes a limit, or until `end` (Loops, below) | a `repeat until` not met by its timeout |

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

Parts are named as on the chamber's screen (docs/WRITING.md): `rough` is the
vacuum valve, `pump` the vacuum pump.

| command | needs first |
|---|---|
| `hvc vent open`, `hvc fill open` | Vacuum valve and Gate valve closed; Platen and Shroud each at least 10 and at most 60 °C |
| `hvc rough open` | Turbo pump off; Vent, Fill, Foreline and Gate valves closed; Chamber pressure at least 0.01 Torr (opening the roughing line to a chamber already at high vacuum can let roughing-pump oil flow back into it) |
| `hvc pump off` | Vacuum valve and Foreline valve closed, Turbo pump off (`hvc stop` does it in order) |
| `hvc foreline open` | Vacuum valve closed |
| `hvc foreline close` | Turbo pump off: the foreline is a running turbo's only backing |
| `hvc turbo on` | Foreline valve open |
| `hvc gate open` | Turbo pump on, Foreline valve open, Chamber pressure at most 0.01 Torr (the crossover) |
| `cryo on` | Board supply at least 20 V |
| anything, during a fault (severity F) | refused, except closing valves, `stop`, zones off, `closeall`, `reset`, `abort` |
| a supply channel the hardware map gives an owner | refused while that owner runs (psu1 ch1: rCryoBoard) |

They are checked when the plan is read. A step that breaks one is an error
and the plan cannot start:

```
bad.plan:5: Needs Vacuum valve closed (line 4 changed it). Air may only come in with the chamber sealed ...
```

A step whose conditions the plan does not itself establish is a **warning**
(amber in the editor; the plan can run). It says what would settle it:

```
Checked when the step runs: Vacuum valve closed and Gate valve closed. To settle it here,
add hvc rough close and hvc gate close before this step.
```

The plan establishes a state by commanding it (`hvc rough close`), or a value
with an `until` just before the step (`until platenT below 60 C ...`, as
`laco_vent` does). Every rule is then
checked again, live, when the step runs, against what the chamber last reported:
a step it fails stops the plan, before anything is sent. The command box, the
cast tab and `labcli cast` check the same rules and refuse with the reason.

### Blocks

A block is a named group of steps a plan calls by name, kept in its own
`.block` file beside the plans (the same folders):

```
# Rough the chamber down to a pressure, then seal it
# Closes the vent, fill and gate valves, starts the vacuum pump ...
block pumpdown to <pressure:number 0.01..760 Torr>
load rLACO

hvc vent close
...
until chamberP below {pressure} timeout 20 min
hvc stop
```

- The leading comments are its help: the first line the summary, the rest the
  details (docs/WRITING.md).
- The `block` line is its name and the words a plan writes to call it. Each input
  is a number, declared as the grammar declares one, and written `{name}` where a
  step uses it.
- `load` names the rScripts it needs; a plan that calls it must load them too. A
  block has no `record` (the plan's) and no `hold until end`.

A plan calls it like any step (`pumpdown to 5`). Reading the plan puts the
block's steps in place of the call, with the inputs filled in, and checks each
as a step of the plan; the log shows `pumpdown > hvc rough open`. So the rules
see inside: what a block sets holds after it, and a finding from inside names
where (`In pumpdown line 16: ...`). Blocks may call blocks, eight deep; a block
that calls itself is an error. A block's steps tolerate many starting states,
since every valve and pump command reads first and an `until` already met ends at
once, but not every one: where its steps do not establish a prerequisite, the
live check refuses and the run stops there.

Shipped blocks: `pumpdown to <pressure>` and `vent within <minutes>`, from which
the plan `pump_soak_vent` is built, and `eclipse within <minutes>` and `sunrise
within <minutes>`, which wait for the orbit a run follows to enter or leave the
umbra (docs/ORBIT.md, In a run). `labcli plans` lists the blocks too.

### Loops

The steps between `repeat` and `end` run again:

```
# Image every umbra for ten orbits
load rOrbit rPSU rSLTA
record every 10 s

orbit replay leo_noon
psu1 ch1 set 5.0 0.5
repeat 10 times
  eclipse within 120
  psu1 ch1 on
  slta image
  sunrise within 60
  psu1 ch1 off
end
```

| line | does |
|---|---|
| `repeat <n> times` | the steps up to `end`, n times (1 to 10000) |
| `repeat until <value> above\|below <limit> [C\|K] timeout <time> s\|min\|h` | the steps again until the value passes the limit; the run stops if it has not by the timeout |
| `repeat until end` | the steps again until the run is ended: a chamber held in a cycle for days |
| `end` | closes the nearest open `repeat` |

- A condition is read **before each pass**: a loop whose condition is already met runs
  no pass.
- A pass is **never cut short**. A value that passes the limit during a pass is seen
  when that pass ends, so keep the passes short when the timing matters.
- **Nesting:** loops nest up to eight deep, and may call blocks. A block may hold a
  loop, closed inside the block, but not `repeat until end`, since nothing after its
  call would run.
- **Indentation** is for reading only. The editor writes the steps inside a loop two
  spaces in.
- **The rules see a loop as its later passes do.** A step is checked against what the
  steps before it left, both before the first pass and at the end of a pass. So
  `hvc vent open` followed by `hvc rough open` in the same loop is an error on the
  vent: on the second pass, the vacuum valve is open.
- **After a loop**, a `repeat until` proves its condition, as an `until` does.
- **The log** marks each pass: `[7/23] repeat 10 times: pass 3 of 10`.

A plan that holds the chamber at its temperatures until the run is ended:

```
# Hold the platen at -20 C until the run is ended
load rLACO rSMTC08
record every 30 s

hvc platen -20
repeat until end
  until platenT below -15 C timeout 2 h
  log platen cold
  hold 30 min
end
```

Shipped plans: `tvac` (manual operation from the cast tab, until `end`),
`psu1_smtc08_first` (PSU1 + thermocouples), `laco_pumpdown`
(pump on, rough open, until below 5 Torr, stop), `laco_vent` (temperature
guards, vent valve open, until atmosphere) and `pump_soak_vent` (the blocks).

Orbit content (`orbit.*`, `propagate`, `@procedure`) is refused: that is FORMS'
part, done offline (see ARCHITECTURE.md). An orbit is described in its own file,
beside the plans; a plan follows one with rOrbit, `orbit follow <orbit>`
(docs/ORBIT.md). A file in the old format
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
