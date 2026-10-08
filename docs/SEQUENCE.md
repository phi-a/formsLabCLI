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
until TC01 > 30 C within 10 min
psu1 ch1 off
log done
```

| step | does | fails the plan when |
|---|---|---|
| `<label> <words>` | a command to the routine that owns the label (`hvc vent open`, `psu1 ch1 on`, `cryo ccv 14`) -- the cast tab's words; waits until the routine has taken it, and for the chamber (rLACO) until it is done | a prerequisite is not met (below); not taken within 10 s; the chamber refuses it |
| `hold <time> s\|min\|h` | runs the routines for a while | — |
| `hold until end` | runs until ctrl `end` (`tvac.plan`: manual operation) | — |
| `until <condition>` | runs until the condition holds, however long that takes, or until the run is ended (Conditions, below) | — |
| `until <condition> within <time> s\|min\|h` | the same, with a limit | the condition does not hold within the time |
| `until <condition> within <time> s\|min\|h or go on` | the same, but goes on at the limit | — |
| `log <text>` | one line in the run log | — |
| `repeat …` … `end` | the steps between them again: n times, until a condition holds, or until `end` (Loops, below) | a `repeat until` whose condition does not hold within its time |
| `when <condition> then <command>` | a rule beside the steps, to the end of the run: the command each time the condition comes to hold (Rules, below) | the command is refused |

`#` starts a comment, on a line of its own (a `#` after a step is an error,
since `log` text may contain one). Words and value names ignore case.

### Conditions

A condition compares a value a loaded routine publishes:

| value | written | examples |
|---|---|---|
| a number | `<value> < \| <= \| > \| >= <limit> [unit]` | `chamberP < 5 Torr`, `platenT >= 10 C`, `PSU1_CH1_V >= 23 V` |
| on or off | `<value> = \| != true \| false` | `InUmbra = true`, `PSU1_CH1_ON != true` |

- **Spaces** go between the value, the comparison and the limit: `platenT < 60`,
  not `platenT<60`.
- **The unit** goes after the limit, so the line says what the number is. It is the
  value's own unit (`Torr` for `chamberP`, `V` for a supply's voltage), or `C` or `K`
  for a temperature, which converts from the value's own. Another unit is refused
  (`chamberP is in Torr, not V`). A limit without a unit is read in the value's own.
- **`=` is for on/off values only.** A number is compared with `<`, `<=`, `>` or `>=`,
  since two readings are almost never exactly equal. On/off values are declared with
  the unit `bool` (rOrbit's `InUmbra`, a supply channel's `_ON`); one is true when it
  is not 0.
- **Without `within`**, a wait takes as long as it takes: `until TC01 <= -40 C` goes on
  when TC01 reaches -40 °C, or when someone ends the run. Use it where the time is not
  known; a rule (`when … then …`, below) can watch for what would go wrong meanwhile.
- **`within`** gives the wait a limit. If the condition does not hold by then, the run
  stops and each routine's shutdown runs. Add **`or go on`** at the end to carry on
  instead: `until InUmbra = true within 2 h or go on`.
- **Old plans:** `above`, `below` and `timeout` are no longer words of a condition. A
  plan that uses them is refused with what to write instead: `>` (or `>=`), `<` (or
  `<=`), `within`.

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

### Holding at a thermocouple

The controller holds the platen and shroud at their setpoints with its own sensors.
To hold a test article instead, name the thermocouple on it:

```
load rLACO rSMTC08
record every 30 s

hvc platen 40 at TC01
hvc platen on
until TC01 >= 39.5 C within 3 h
hold 2 h
hvc platen off
```

- **What it does:** once a minute, rLACO moves the platen's setpoint by the gain
  times the difference between 40 °C and TC01's reading. The setpoint settles where
  the article reads 40 °C.
- **Its limits:** the setpoint never goes more than the band from the temperature,
  whatever the thermocouple reads. The gain and band are `hold_at_gain_per_min`
  (0.05) and `hold_at_band_c` (15 °C) in `tvac_bench.json` `limits`; tune them on
  the bench.
- **When it pauses:** while thermal control is off, or while the thermocouple has
  no reading less than two minutes old. The status page shows `Platen held at` and
  why it is waiting.
- **How it ends:** a plain `hvc platen <temperature>`, `hvc platen off` or the end
  of the run. The platen keeps the last setpoint, always inside the band.
- **The thermocouple** is named as a condition names it: `TC01` to `TC16` (rSMTC08)
  or the controller's own, `HVC_T5` and so on. A plan that holds at one must load
  the routine that publishes it. The reading is only as right as the thermocouple's
  type: an SMTC08 board converts with the type set on it.

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
with an `until` just before the step (`until chamberP >= 0.01 ...` before
`hvc rough open`, as the `pumpdown` block does). A wait that may go on without it
(`... or go on`) proves nothing. Every rule is then checked again, live, when the step runs, against what the chamber last reported:
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
until chamberP < {pressure} within 20 min
hvc stop
```

- The leading comments are its help: the first line the summary, the rest the
  details (docs/WRITING.md).
- The `block` line is its name and the words a plan writes to call it. Each input
  is a number, declared as the grammar declares one, or a thermocouple,
  `<name:temperature>`, and written `{name}` where a step uses it.
- **A call writes each number with its unit**, the one the input declares:
  `pumpdown to 3 Torr`, `vent within 60 min`, `warm to 40 C within 60 min`. A
  thermocouple input is named in the call (`hold article to -40 C at TC01`),
  chosen from every temperature the routines publish; the plan must load the one
  that publishes it.
- `load` names the rScripts it needs; a plan that calls it must load them too. A
  block has no `record` (the plan's) and no `hold until end`.

A plan calls it like any step (`pumpdown to 5 Torr`). Reading the plan puts the
block's steps in place of the call, with the inputs filled in, and checks each
as a step of the plan; the log shows `pumpdown > hvc rough open`. So the rules
see inside: what a block sets holds after it, and a finding from inside names
where (`In pumpdown line 16: ...`). Blocks may call blocks, eight deep; a block
that calls itself is an error. A block's steps tolerate many starting states,
since every valve and pump command reads first and an `until` already met ends at
once, but not every one: where its steps do not establish a prerequisite, the
live check refuses and the run stops there.

Shipped blocks, which the bench's tests share:

| call | does |
|---|---|
| `begin at ambient` | turns the zones and the turbo pump off, closes every valve, and stops the plan unless the chamber is above 700 Torr |
| `pumpdown to <pressure> Torr` | roughs the chamber down to the pressure, 0.01 to 760 Torr, then seals it |
| `vent within <minutes> min` | waits for the platen and shroud to be between 10 and 60 °C, then lets in air |
| `warm to <temperature> C within <minutes> min` | sets both zones, 0 to 100 °C, turns them on and waits for both |
| `cool to <temperature> C within <minutes> min` | the same, -150 to 30 °C; needs liquid nitrogen |
| `k508n at <volts> V with <resistance> ohm` | sets the cryocooler board's supply (24 V, 1.0 A, protected at 24.5 V and 1.25 A, a line in the block), starts the board, sets the variable resistor, which sets the K508N's fixed-point temperature control, and the cooler voltage, 8.5 to 20 V, and turns it on; each step is answered, so a refusal stops the run (docs/CRYOCOOLER.md) |
| `detector imaging` | turns on the camera's supply (psu2 CH1) and umbra captures, 600 s exposures of 10 samples; the camera then images each umbra of the orbit the run follows |

Preparing for darkness, a plan that follows an orbit, runs the cryocooler and waits,
however long it takes, for the detector to reach -40 °C, and images every umbra until
it is ended:

```
load rLACO rCryoBoard rPSU rSMTC08 rSLTA rOrbit
record every 10 s

orbit follow LEO
k508n at 17 V with 266 ohm
until TC01 <= -40 C
detector imaging
when TC01 > -30 C then log detector warming
hold until end
```

`labcli plans` lists the blocks too.

### Loops

The steps between `repeat` and `end` run again:

```
# Image every umbra for ten orbits
load rOrbit rPSU rSLTA
record every 10 s

orbit replay leo_noon
psu1 ch1 set 5.0 0.5
repeat 10 times
  until InUmbra = true within 2 h
  psu1 ch1 on
  slta image
  until InUmbra = false within 1 h
  psu1 ch1 off
end
```

| line | does |
|---|---|
| `repeat <n> times` | the steps up to `end`, n times (1 to 10000) |
| `repeat until <condition> within <time> s\|min\|h [or go on]` | the steps again until the condition holds (Conditions, above); if it does not within the time, the run stops, or with `or go on` the plan goes on after `end` |
| `repeat until end` | the steps again until the run is ended: a chamber held in a cycle for days |
| `end` | closes the nearest open `repeat` |

- A condition is read **before each pass**: a loop whose condition already holds runs
  no pass.
- A pass is **never cut short**. A condition that comes to hold during a pass is seen
  when that pass ends, so keep the passes short when the timing matters.
- **In the editor**, choosing `repeat` adds its `end`, with an empty step between them
  to fill in. `end` is offered only inside an open loop, and deleting a `repeat` or its
  `end` deletes both and keeps the steps between them.
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
  until platenT < -15 C within 2 h
  log platen cold
  hold 30 min
end
```

### Rules

A plan's steps run one after another. A **rule** runs beside them: from its line to
the end of the run, it watches a condition and sends a command each time the
condition comes to hold.

```
load rLACO rSMTC08
record every 10 s

when platenT > 90 C then hvc platen off
when chamberP > 1 then log vacuum lost

hvc shroud 40 at TC02
hvc shroud on
hvc platen 80 at TC01
hvc platen on
until TC01 >= 79.5 C within 30 min
hold 2 h
```

- **The condition** is written as for `until` (Conditions, above), without `within`.
- **The action** is one command of a loaded routine, or `log` and a message. A rule
  does not wait and cannot call a block.
- **When it acts:** at once, if its condition holds when the plan reaches its line;
  after that, each time the condition comes to hold again, not on every reading. It
  keeps watching while the plan is paused, so a guard does not stop with the steps.
- **A refusal stops the run.** The command goes through the same prerequisites as a
  step, when it fires. If they are not met, or the chamber refuses it, the run stops
  as a failed step does, and the log names the rule.
- **One at a time per routine.** A rule's command and a step's command to the same
  routine are sent one after the other, never merged into one request.
- **What it may change is not known after it.** When the plan is read, a state a
  rule's command may change (`then hvc rough open`: the vacuum valve) no longer
  counts as settled after the rule's line, so a step that needs it is a warning,
  checked when it runs. A rule that sets only what the plan sets changes nothing.
- **It ends with the run.** A rule in a loop is armed once, by its first pass.
  Blocks cannot hold a rule yet.

Shipped plans, the bench's tests, each built from the blocks above:

| plan | does | takes about |
|---|---|---|
| `tvac` | manual operation from the cast tab, until `end` | until `end` |
| `rest_from_ambient` | pumps down from ambient to 3 Torr, seals the chamber and leaves it to rest | 10 minutes |
| `vent_to_ambient` | lets air into a sealed chamber | 5 minutes |
| `warm_soak` | pumps down, holds both zones at 40 °C for 30 minutes, returns to air | 1 h 40 min |
| `thermal_cycle` | pumps down, takes both zones between 60 and -20 °C three times, returns to air | 2 h 30 min or more |

Each starts with a header that says the state it starts from, the state it leaves,
and what fails it. Every shipped plan must check with no error and no warning, and
each runs end to end against the simulated chamber (`test/test_shipped_plans.py`).
The rest of the suite reads its own, frozen set, in `test/fixtures/plans/`.

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

A run cut short -- End in the GUI, `labcli end`, ctrl `end`, Ctrl+C, a failed
step, a crash -- first runs the **end script**, `plans/end.plan`, with every
routine still live. It is a plan like any other, read in the editor and by
`labcli check`, and never started on its own. What it does, in order:

1. `psu1 ch1 off`, `psu2 ch1 off`: the cryocooler and the camera lose power at
   once, whether or not this run switched them on.
2. The chamber stops heating and cooling: both zones off, confirmed from the
   controller's flag (`until HoldingTemperature = false within 15 s`), then both
   setpoints to 20 °C so nothing extreme stays armed.
3. The chamber is sealed before anything is done to the pumps: vent, fill, gate
   and vacuum valves closed, each confirmed (`until VentValve = false ...`). No air
   is let in; venting is the operator's deliberate step afterwards.
4. The pumps: turbo off, then `hvc stop` (vacuum valve closed, roughing pump off),
   each confirmed. While the turbo runs, `hvc stop` is refused and the roughing
   pump keeps backing it through the open foreline valve; the log says to finish
   at the HMI once the turbo has stopped.

Every step is tried: one that is refused, not answered, or not confirmed in its
time is logged and the next runs; a step for a routine the plan did not load is
skipped; a confirmation of a value never read fails at once. A request the plan
had in flight is dropped first, so no last command is applied after End. Pause
is ignored. The script has 120 s; a normal end takes about 10. During a
controller fault every chamber step is one the fault allows, except `hvc turbo
off` and the 20 °C setpoints, which are refused and logged.

Then each loaded routine's `rShutdown` runs, the backstop when the script could
not act: rLACO ends pumping the run started and releases the controller; rPSU
turns off the channels the run switched on; rCryoBoard, rSLTA (a capture in
progress stopped) and rSMTC08 release their boards. The order is by dependency
(`SHUTDOWN_BEFORE`: a routine that uses another's supply goes first), not the
`load` line's.

The host then logs the chamber as it left it (`Chamber left: sealed, 3.2 Torr,
pump off, turbo off, zones off`, or `Chamber not reached: check it at the HMI`)
and writes `~/.formslab/.run/ended.json` (plan, when, how, that line, and what
the script could not do), which the GUI shows until dismissed. A plan that runs
to its last step leaves the chamber as its steps said; only the routines'
shutdowns follow.

The chamber's states the end script confirms are published for any plan:
`VentValve`, `FillValve`, `GateValve`, `RoughValve`, `ForelineValve` (open),
`VacuumPump`, `TurboPump` (on) and `HoldingTemperature` (either zone on), each
`= true` or `= false`.

The host writes `~/.formslab/.run/sequence.events.jsonl` (one per machine,
beside its lock and `host.log`): `sequence_started` (with
the plan's manifest), `segment_started`, `progress`, `segment_finished`,
`sequence_finished` (with `error` when the plan stopped).
