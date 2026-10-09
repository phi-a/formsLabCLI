# The GUI: intent, design and where it stands

Updated 2026-10-05. Started 2026-10-03 as the plan for the GUI; Stages 0-4 are now
built and in use, and the space-environment view has its first part (orbit files).
How to use it is in docs/GUI.md; this note records what it is for, why it is built
the way it is, and what is still to decide. Progress across the project is in
[progress.md](progress.md).

## What it is for

The bench is run by people who should not need to remember the console's words: a
student starting a pumpdown, an engineer writing a soak, someone checking last
night's run from another room. The GUI gives them the same lab as the console, on
one web page:

- **Plans:** write and check a test without knowing the grammar by heart.
- **Status and control:** start, pause and end runs; send a command; see each
  instrument's state and whether it is current.
- **Chamber:** the chamber as its controller's own screen draws it, live.
- **Plots:** what a run recorded, while it runs or after.
- **Space environment:** the orbit a test is meant to reproduce, and in time the
  satellite's background temperatures from it (section 4).

It is a *client* of what already exists. It never opens an instrument, so it adds no
new way for the bench to go wrong.

## How it is meant to be used

1. **Write the test in Plans.** Each step is chosen from dropdowns that offer only
   what fits; a step that breaks a prerequisite is red before anything runs. Repeated
   steps become a block; a cycle becomes a loop; an orbit file says which orbit the
   test follows.
2. **Start it from Status.** A plan with problems is in the Start list, greyed, with
   the reason; only one that can run can be started. The run is
   the same sequence host the console starts, in its own process.
3. **Watch it in Chamber and Status.** Every value says whether it is current. A
   command sent from the box goes through the same checks as a plan step and says
   what became of it.
4. **Read it in Plots**, live or afterwards, from the CSV the recorder wrote.
5. **End it from any tab.** End asks the host to stop, so each instrument's shutdown
   runs.

The console and SSH (`labcli <command>`) stay full equals: a run started in one is
seen and ended from the others.

## Decisions

| | | Why |
|---|---|---|
| Form | A web page, not a desktop window | Windows, Linux, the Pi and Mac all have a browser. |
| Server | Python standard library (`ThreadingHTTPServer`) | No framework, no build step, no internet needed at the bench. |
| Page | Plain HTML, CSS and JavaScript, served as they are | Readable and changeable by the next student, with no toolchain. |
| Charts | A hand-written canvas chart (`chart.js`, about 200 lines) | The vendored uPlot considered in the first draft was not needed. |
| Access | One login; listens on `127.0.0.1`; SSH tunnel from elsewhere | The lab is locked. `--listen` opens it to the network on purpose only. |
| Login | A salted hash in `~/.formslab/gui.json`, set with `labcli gui --set-login` | Not in the source or the repo. The initial values were given in conversation and are deliberately not recorded here. |
| Live data | Polled about once a second; no websockets | Simple, and fast enough for a chamber that changes over minutes. |

## Principles

1. **One owner per instrument.** The HVC-3500 takes exactly one TCP client, and a
   supply or board should have one opener. The server therefore never opens a
   driver: it reads CAST status blocks and files, and sends commands the way the
   console does. Tests hold that ports, VISA, sockets and new processes stay shut
   while every screen's data is fetched.
2. **No grammar of its own.** Dropdowns, checks, help cards and error text come from
   what the rScripts declare (`COMMANDS`, `VARIABLES`, `RULES`) and the plan parser.
   A command typed in the console, a plan line and a GUI click are the same thing,
   so they cannot disagree.
3. **The files are the truth.** Plans, blocks and orbits are text files; runs are
   CSVs. The GUI reads and writes those and keeps no database, so a page reload loses
   nothing and git can review every change.
4. **Works with nothing running.** No host, no chamber: each screen says what is
   missing and stays usable where it can. `scripts/gui_demo.py` runs the whole GUI
   against a simulated chamber.
5. **Say what is old.** A value that is not live is greyed with its age and the reason
   (no run, owner not loaded, chamber not connected). The GUI never shows a stale
   number as current.
6. **Changes go through commands.** The viewers only read. Valves and pumps change on
   the Status tab, through the same commands and prerequisite checks as the console,
   so a refusal reads the same everywhere.
7. **The grammar is visible.** Every step is drawn in four shapes (instrument block,
   keyword pill, value box, free text) in its instrument's colour, so a plan can be
   read at a glance and a wrong word stands out in red.

## Architecture

```
 browser (any machine)                    bench machine
 ┌──────────────────┐   HTTP + login     ┌───────────────────────────────┐
 │ Status / control │ ◀──── JSON ──────▶ │ gui server (stdlib)           │
 │ Chamber          │   polls ~1 s       │   api.py: thin wrappers       │
 │ Plans (+ orbits) │                    └───┬───────────┬───────────────┘
 │ Plots            │                        │ reads     │ writes
 └──────────────────┘            CAST status │           │ CAST commands, ctrl,
                                 run lock,   │           │ plan/block/orbit files
                                 CSVs, logs  ▼           ▼
                                      ┌──────────────────────────────┐
                                      │ sequence host (one per run)  │──▶ instruments
                                      └──────────────────────────────┘
```

- The server is a separate process from the host, so a GUI crash cannot stop a run
  and a run ending does not close the page.
- Every endpoint calls an existing function and returns JSON; `api.py` is testable
  without a server.
- The shared files (`castfile.json`, `ctrlfile.json`) are written under a
  cross-process lock (`console/safefile.py`), since the console, the host and the GUI
  all write them.

### Code

| File | Holds |
|---|---|
| `gui/server.py` | Routes, login, static files, the request checks (Host, a header other sites cannot set, body size) |
| `gui/api.py` | One function per endpoint, over existing code |
| `gui/auth.py` | The one login (PBKDF2) and in-memory sessions |
| `gui/plans.py` | Plan, block and orbit files: one list, one set of names, save, trash, Edit and Ship |
| `gui/runs.py` | The recorder's CSVs, parts stitched into one run |
| `static/app.js` | Tabs, status and control, plots |
| `static/editor.js` | The plan editor and the orbit's live panel |
| `static/plantext.js` | Plan text as lines: header lines, loops, missing orbit elements (pure, tested under Node) |
| `static/info.js` | The help card for a command or step |
| `static/tvac.js` | The chamber view (`viewModel` pure and tested; `render` draws the SVG) |
| `static/chart.js` | The canvas chart |

Tests: `test/test_gui_*.py`, including the JavaScript's pure parts run under Node.

### Endpoints

| Group | Endpoints |
|---|---|
| Login | `/api/login`, `/api/logout`, `/api/me` |
| State | `/api/status`, `/api/info`, `/api/rscripts` |
| Control | `/api/run`, `/api/end`, `/api/pause`, `/api/resume`, `/api/cast`, `/api/complete` |
| Plans | `/api/plans`, `/api/plans/<name>`, `/api/plan/check`, `/api/plan/line`, `/api/plan/tokens`, `/api/plan/needs`, `/api/describe`, `/api/plan/save`, `/api/plan/delete`, `/api/plan/rename`, `/api/plan/edit`, `/api/plan/ship` |
| Runs | `/api/runs`, `/api/runs/<id>` |
| Orbit | `/api/orbit/live` |

## The screens

### 1. Plans

The editor is the GUI's main work, because writing a correct test is where people get
stuck. A plan is shown line by line: `load` as checkboxes, `record` as a number and
unit, each step as a chain of dropdowns whose choices come from the server. Built and
in use:

- live checking against the real parser, with the prerequisites each step needs
  marked as the plan leaves them at that line (✓, ✗, ?, •);
- blocks (named groups of steps, called by name, with `{input}` values) and loops
  (`repeat … end`, building and deleting themselves as a pair);
- conditions written `chamberP < 5`, `InUmbra = true`, with `within` and `or go on`;
- three kinds of file in one list: plans (green page), blocks (purple square), orbits
  (ellipse);
- shipped files read-only; *Edit* and *Ship* move a file between the shipped plans and
  your own; *Rename* renames what refers to it too; delete goes to a trash folder.

A draft with mistakes can be saved, but it is listed as "cannot run" until it is fixed.

### 2. Status and control

Run state, the Start list, Pause and Resume, End in the header of every tab, a command
box with the instruments' own completion and a help card, and one card per instrument
with its readings grouped by part (Valves, Pumps, Zones, ...).

### 3. Chamber

The controller's Manual screen redrawn live, so operators see the picture they already
know ([tvac-chamber.md](tvac-chamber.md) has the screenshot). What each item is in our
software:

| On the screen | Our name / source | Read? |
|---|---|---|
| Chamber pressure | `pressure`, `?VP` | yes |
| Vent, Fill, Foreline, Gate valves | `vent`, `fill`, `foreline`, `gate` (`!OV/OF/O4/OG`) | yes, open or closed |
| "Vacuum Valve" | `rough` (`!OR`) | yes |
| Vacuum Pump, Turbo Pump | `pump`, `turbo` (`!OP/OT`) | yes, on or off |
| Zone temperatures | `<zone> C`, thermocouples T2, T3, T4 | yes |
| Zone setpoint | `<zone> setpoint C` | yes |
| Zone ON/OFF | `thermal_control` | one chamber-wide "holding temperature" flag, not per zone; drawn as such |
| Side readouts (Cntrl P, ot1-ptn, ...) | named thermocouples `HVC_...` | yes. The HMI labels both lower rows "ot1-ptn"; the profile maps T0 and T1 to `ot1_ptn` and `ot2_shd`, so the second is presumably `ot2-shd` |
| Fault banner | `faults`, `fault_severity` | yes |
| Heater output %, turbo speed %, foreline pressure | no documented query | **not read**: drawn "n/a" |
| LED Light, Manual Heat, Trend | HMI-only controls | no; Trend is the Plots tab |

Finding where the controller exposes the three unread values is a commissioning task,
not GUI work. The HMI's icon states (red X against pale pink) still need decoding from
live observation.

### 4. Space environment

**Goal:** simulate the satellite's background temperatures in orbit, and in time drive
the chamber with them. This is the work still ahead, in stages, each to get its own plan.

**Built (2026-10-05): orbit files.** A `.orbit` file (docs/ORBIT.md) holds one Keplerian
element per line in its own grammar and colour (deep blue). It opens in the Plans
editor with a live panel beside it: sunlit or in umbra and when that changes, beta
angle, altitude, speed, and the coming orbit as a strip. Motion is Kepler with J2
drift, so a sun-synchronous orbit keeps its local time. During a run, the rScript
rOrbit follows an orbit (`orbit follow`, `orbit replay`) and publishes `InUmbra` and
the umbra timings, which plans wait on (`until InUmbra = true ...`) and rSLTA
follows. The same file builds the `Orbit` the environment models in `formslab.orbit`
sweep, so one orbit feeds both the bench and the models.

**Next, in order:**

1. **Environment profile.** Orbit file + spacecraft model → `orbit.thermal.pipeline` →
   per-face environment temperature over one orbit, written as a recorder-format CSV,
   so the Plots tab shows it with no new code. View, flux and environment temperature
   run on a base install (numpy); the transient solver needs scipy. Open: how the
   spacecraft is described (a file with its own grammar, like the orbit?).
2. **3D view.** Orbit, Earth, Sun direction, the spacecraft's attitude. Open: plotly
   (`scene3d`) is a multi-MB script; a small vendored WebGL library or a hand-drawn
   canvas fits "no internet, no build step" better.
3. **The satellite in the chamber.** A Chamber-view-style drawing with per-face
   thermal overlays from the profile, live or replayed.
4. **Replay against the chamber.** A plan step that follows a profile: shroud targets
   from it, `InUmbra` for rSLTA as now. Then the orbit screen can show where the run is
   in the profile.

Heavy work stays out of requests: a profile is computed once and cached, and the page
draws it.

### 5. Plots

Pick a recorded run and its variables; one chart per unit; zoom, pan, hover; Kelvin
shown as Celsius on request; follow a run live; save PNG or CSV. Live plots lag by the
record cadence (30 s for `tvac`); faster would need the host to keep recent values in
memory, which is deferred. Older CSV layouts in `outputs/` are not listed rather than
guessed.

## Speed

Measured 2026-10-07 (thermal_cycle.plan): a plan check 42 ms, one row's choices 10 ms,
the plans list 270 ms, a status poll 4 ms. What keeps it so: the bench profile is
parsed once per change of the file; the editor asks for every row's choices in one
request (`/api/plan/lines`); one status poll serves every tab (every second on Status
and Chamber, every two elsewhere) and a tab switch retires the old loop; follow-live
and the orbit panel wait for each reply before the next. `test/test_gui_speed.py`
guards the first two. Left as they are, cheap enough today: the Status cards and the
Chamber drawing are rebuilt each second; follow-live re-reads the whole CSV every 5 s
(an incremental read when runs grow long); the plans list checks every file each time
it is asked for (on opening a view or after a save).

## Login and safety

- A session cookie (`SameSite=Strict`) after login; constant-time comparison; about a
  second's wait after a wrong password; sessions last 8 hours and end when the server
  restarts.
- Every state-changing request carries a header another site's page cannot set; bodies
  are capped; an unexpected Host header is refused.
- Plans are started by name from the server's own list, never from a path in a request.
  Writes are limited to the user's plans folder (and `plans/` through *Ship*).
- Logins and actions are noted with the user name in `~/.formslab/.run/gui.log`. Per-command
  names inside the host would need a change to the CAST request format.
- The chosen password is also the PowerSwitch fallback password: fine for a locked lab,
  worth separating later.

## Requirements

Functional, all met except F5's later stages:

- F1 Edit, check and save a plan with line-numbered errors, from dropdowns.
- F2 Start, end, pause and resume a run; send any cast command.
- F3 Live valves, pumps, pressure and temperatures with the age of each value.
- F4 Plot any recorded run's variables, with zoom.
- F5 Orbit, eclipse, view factor and environment temperature views. *Orbit and eclipse
  done; the rest is section 4.*
- F6 Log in and out; unauthenticated requests get nothing.

Non-functional:

- N1 Runs on Windows, Linux (Pi included) and Mac; any current browser.
- N2 No build step, no internet; numpy is the only base dependency the orbit work added
  (scipy and matplotlib stay in the `orbit` extra).
- N3 Never opens an instrument.
- N4 Usable with no host running.
- N5 A page reload loses nothing.
- N6 Small enough to read in a day: about 1,300 lines of Python and 2,400 of page today.
  `editor.js` (about 800) is the one file growing fastest; split it before it doubles.

## Open questions

Settled:

- ~~Chamber schematic~~: follow the HMI Manual screen (2026-10-03).
- ~~Two people saving the same plan~~: a save is refused if the file changed on disk
  since it was opened.
- ~~Replay an orbit in a plan~~: rOrbit's `orbit follow` / `orbit replay` (2026-10-05).
  Replaying a whole *thermal profile* is still stage 4 above.

Open:

1. 3D view: needed from the start of the space-environment work, or after the profile
   and its plots?
2. How the spacecraft model is written (stage 1).
3. Should students start runs from the GUI, or only write plans and watch? Today anyone
   with the one login can start one.
4. Celsius or Kelvin as the default display? Values are published in K; the Chamber and
   Plots tabs offer both and start in Celsius.
5. Heater output %, turbo speed % and foreline pressure: worth finding where the
   controller exposes them?
6. rOrbit and Pause: pausing a run holds the plan's steps, but the orbit goes on
   (`follow` is the wall clock; `replay` is the wall clock from its step). Should a
   paused `replay` hold the satellite too?
