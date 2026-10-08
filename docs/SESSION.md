# Learning the GUI: a practice session

A session of about two hours in which each student learns to look at the chamber,
send a command, read a test, run it and write their own, on a simulated chamber.
Nothing a student does in it can reach the bench. It ends with what to do first on
the real one.

## Before the session

Each student, on their own computer (the setup is in SETUP.md):

```
python scripts/gui_demo.py
```

Then open `http://localhost:8088/` and log in as `demo`, password `demo`. The page
has an orange DEMO bar. The chamber is simulated, starts at atmosphere (760 Torr)
with both zones near 22 °C, and its files are deleted when the student presses
Ctrl+C. Each student has their own, so nobody waits for anybody else.

The bench's tests are listed beside the demo's own: `tvac`, `rest_from_ambient`,
`vent_to_ambient`, `warm_soak`, `thermal_cycle`, and the blocks they share
(`begin`, `pumpdown`, `vent`, `warm`, `cool`). They are read-only, marked *shipped*.
The demo's chamber heats in seconds, so a test that takes an hour on the bench
takes minutes here, apart from its holds.

## The session

| | Minutes | You learn |
|---|---|---|
| 1. Look | 10 | What the screens show, and when a number is old |
| 2. Command | 15 | How to send a command, and why one is refused |
| 3. Read a test | 15 | How a test is written, and what each word needs |
| 4. Run a test | 15 | Start, watch, end, and read the plot |
| 5. Write a test | 25 | Building one from blocks, and what a red step means |
| 6. Change a test | 20 | Making your own copy of a shipped test |
| 7. The bench | 10 | What changes with real hardware |

### 1. Look

On **Status**, choose `tvac` in the Start list and press *Start run*. It runs until
you end it. Then:

- Find the chamber's card. Its readings are grouped: Valves, Pumps, Zones. What is
  the Chamber pressure? Which valves are open?
- Open **Chamber**. It is the controller's own Manual screen, live. Green is open or
  on, red is closed or off. Switch the temperatures to Kelvin and back.
- End the run with *End run* in the header, then look at **Chamber** again. It is
  greyed, with the reason. A greyed number is the last one seen, not a reading.

You can say what *live* means and why a greyed number is not to be trusted.

### 2. Command

Start `tvac` again. In the command box on **Status**:

1. `hvc pump on`. Wait 10 seconds: the controller wants the Vacuum pump running that
   long before the Vacuum valve may open.
2. `hvc rough open`. Watch the Chamber pressure fall.
3. `hvc vent open`. It is refused. Read why in the help card: the Vent valve may open
   only with the Vacuum valve closed. A refusal is the chamber protecting itself.
4. `hvc stop`. It closes the Vacuum valve, then stops the Vacuum pump.

Try one wrong word (`hvc pump onn`) and read what it suggests. End the run.

You can send a command, read a refusal, and put the chamber back.

### 3. Read a test

Open **Plans** and choose `rest_from_ambient`. It is shown line by line.

- The comments at the top say where the test starts, where it ends and what makes it
  fail. Every shipped test has them. Read them before running anything.
- The legend above the steps shows the four shapes: the instrument, a keyword, a
  value, free text. Find one of each.
- Click `begin at ambient`. The help card lists the steps it runs, and what each needs
  first. Do the same for `pumpdown to 3 Torr`.
- Open the block `pumpdown`. It is the same steps, with `{pressure}` where the number
  goes.

You can say what the test does, and what it needs the chamber to be like before it
starts.

### 4. Run a test

On **Status**, choose `rest_from_ambient` and *Start run*. Watch **Chamber**: the
Vacuum pump starts, 15 seconds later the Vacuum valve opens, the pressure falls, and
the test seals the chamber. Press *End run* only if something looks wrong.

When it finishes, open **Plots**, choose the run, and select the Chamber pressure.
Zoom with the wheel, reset with a double-click.

Then start `vent_to_ambient` and watch the chamber return to air.

You can start a test, follow it, and read what it recorded.

### 5. Write a test

On **Plans**, press *New plan* and build, one step at a time from the dropdowns:

```
load rLACO
record every 5 s

begin at ambient
pumpdown to 5 Torr
hold 1 min
vent within 60 min
```

Save it as `my_first_test`. The step help card shows each prerequisite as ✓ (the
plan establishes it), ✗ (the plan breaks it) or ? (checked when the step runs).
Start it from **Status**.

Now break it on purpose. Add `hvc rough open` and then `hvc vent open` before the
`vent` step. The second is red, and says the Vacuum valve must be closed and that
the line before opened it. A red test cannot start: the mistake is caught before
anything moves.

You can build a test from blocks and read what a red or amber step is telling you.

### 6. Change a test

Open `warm_soak`. It is read-only. Press *Save as...* and name the copy
`my_warm_soak`; the original stays as it is. Change `hold 30 min` to
`hold 1 min` and the `40` of `warm to 40 C within 60 min` to `30`. Run it and plot the platen and
shroud temperatures, `platenT` and `shroudT`.

Open `thermal_cycle` and find `repeat 3 times` and its `end`. Everything between them
runs three times. Save a copy, change `3` to `2` and the holds to a minute, and run
it.

You can change a test without touching the bench's own.

### 7. The bench

The same screens run the real chamber, with differences that matter:

- It takes the time the test says. `warm_soak` takes about an hour and 40 minutes.
- A refusal is the real controller's, or a real interlock's.
- `cool` and `thermal_cycle` use liquid nitrogen, whose supply must be on and whose
  exhaust must be vented outside the building. Cooling has not yet been run on this
  chamber through formsLabCLI.
- The turbo pump, the Foreline valve and the Gate valve are never used by these tests.
  That path has not been run on this chamber.
- Before a test: the door is closed, the chamber is at atmosphere, and `labcli gui`
  shows the chamber *live* with no run going.
- **An instructor starts and ends every run on the bench.** A student reads the test
  aloud first: where it starts, where it ends, what makes it fail.

*End run* is always safe: each instrument's shutdown runs, so a pump this run started
is stopped and the Vacuum valve closed.

## For the instructor

- Run a student's `my_first_test` yourself the first time and read the log
  (**Status**, the end of the host log) with them.
- A student who is stuck on a red step should read the sentence under it before
  asking. It names the part, the state it needs and the line that changed it.
- The shipped tests are the bench's. Students use *Save as...* to change one, never
  *Edit* or *Ship*: those move the file out of, or into, the checkout's `plans/`.
- Whether students may start runs on the bench is still open (Notebook/progress.md).
  This guide assumes they may not.
