# Recipes

Two things go by this name at the bench. A **controller recipe** is stored in the
HVC-3500 and runs the chamber by itself. A **test recipe** is a formsLAB plan or
block: the steps of a test, in a file. They follow different rules.

## Controller recipes

What the controller offers (HVC p. 31-34):

- Up to 20 recipes of up to 50 steps, edited on the HMI in technician or advanced
  mode.
- Each step has a vacuum step (*None* closes every valve; *Setpoint*; *Vent2Atm*
  opens the vent valve and leaves it open until the next step; *Fill2Atm*; *ASCII*
  hands pressure and temperature to a computer; *ATM Purge*; *Cycle* repeats
  earlier steps), setpoints and a hold timer.
- A hold timer pauses while the process is outside its control band and resumes
  when it is back. A *hold step* waits, once its setpoints are met, until someone
  presses Start (or sends `!CS`).
- Abort sends the recipe to its home step and back to the ready state. Its faults
  stop everything.

**formsLAB's rules for them:**

1. **No plan starts one.** formsLAB drives the chamber in Manual mode, valve by
   valve, so its interlock checks and End can reason about every state. The
   commands exist (`hvc recipe <n>`, `hvc recipe start|stop`, `hvc start`), but no
   shipped plan uses them.
2. **End does not stop one** ([end](../design/end.md), D9). If a recipe is running,
   stop it at the HMI.
3. **Never `hvc abort` to stop one** from a plan: it faults the controller and runs
   the recipe's home steps, which may open valves.
4. **After a recipe or cycle command a plan knows nothing.** The plan checker
   treats the valves and pumps as unknown from there on.
5. *Enable ASCII in Recipe* is off on this chamber. An externally driven recipe
   (an *ASCII* step with a hold trigger) would need it on, and a decision first.

**Open:** what the controller reports while a recipe runs (`?TC`, `?MC`, `?RS`).
Until that is found at the bench, formsLAB cannot tell that one is running. When
it can, the plan is to publish it (`CycleRunning`) and let End stop a recipe with
`hvc recipe stop`, if that leaves the valves alone ([end](../design/end.md), Open
items).

## Test recipes: plans and blocks

A plan is a test; a block is a named group of steps that plans call. How to
write them: [plans](../use/plans.md). The rules for one the bench relies on:

### Every shipped test

1. **A header** says the state it starts from, the state it leaves, and what fails
   it: *Start*, *End*, *Fails*. A student reads it aloud before a bench run.
2. **It checks clean**: no error and no warning in `labcli check`.
3. **It runs end to end against the simulated chamber**
   (`test/test_shipped_plans.py`). A new shipped plan is added there.
4. **Its name is unique** across `plans/` and its folders. Folders only sort.
5. **It leaves the chamber as its header says.** A plan that runs to its last step
   gets no end script ([end](../design/end.md), D8), so its last steps are its end.
   `rest_to_ambient` ends vented, on purpose.
6. **Every wait has a limit** (`within`) unless taking as long as it takes is the
   point, as for a cooldown, and then the header says so.
7. **Says what it has not been proven on.** A step never run on the real chamber
   (cooling, the turbo path) is named in the header.

### Writing steps that are safe

- **Establish a state before you need it.** Close the valves a command needs closed,
  or wait for the value it needs (`until chamberP >= 0.01 ...` before
  `hvc rough open`). A step whose prerequisites the plan does not establish is a
  warning, checked live when it runs.
- **Guard with rules.** `when platenT > 90 C then hvc platen off` watches for the
  rest of the run, paused or not. A rule whose command is refused stops the run.
- **Start from ambient with `begin at ambient`**: everything off and closed, and the
  chamber checked at air before any pump starts.
- **Do not command a channel another routine owns** (psu1 CH1 while rCryoBoard runs).
  The plan is refused.
- **Keep setpoints within the chamber's rating**, not only within what formsLAB
  accepts ([safety](safety.md), Heat).
- **Cold tests say so**: liquid nitrogen supplied, exhaust vented outside.

### Who may change them

- The shipped tests are the bench's. Change one with *Edit* (it leaves the checkout's
  `plans/`), check it, then *Ship*.
- Students use *Save as...*, never *Edit* or *Ship* ([session](../use/session.md)).
- `plans/end.plan` is the end script. Every step in it must be safe to try in any
  state; prefer closing and turning off ([end](../design/end.md), Changing the end
  script).

### The bench's recipes

| Plan | Does | Run on the bench |
|---|---|---|
| `tvac` | manual operation until End | the everyday way to run the chamber by hand |
| `ambient_to_rest` | rough to 3 Torr, seal, rest | run, works |
| `rest_to_ambient` | let air into a sealed chamber | run, works |
| `warm_soak` | pump down, 40 °C for 30 minutes, back to air | not run |
| `thermal_cycle` | three cycles, 60 to -20 °C, under rough vacuum | not run; cooling never run |
| `Cryocooler` | the K508N cryocooler by hand | used 2026-10-09; questions open in [cryocooler](../instruments/cryocooler.md) |

Which have run is kept current in [progress](../progress.md).
