# Progress

Where the work stands, what is next, and what is waiting on a decision. Update it
when a pull request merges or a decision is made: change *Now*, add a line to
*Done*, and move settled questions out of *Waiting on a decision*. Intent and
architecture are in [project](project.md); the GUI's plan is in [gui-design](design/gui-design.md).

## Now (2026-10-05)

- **The GUI's first tab is Control (2026-10-30):** the chamber drawing, and under it, as
  wide as it, the run and the command box, the host log folded beneath. Status is the
  instruments' tiles alone. Decided so a command is sent with the picture it changes
  in view ([gui](use/gui.md), [gui-design](design/gui-design.md)). Before it, a slip in
  the page's script had left Status blank with nothing in the console; the page is now
  driven under Node in `test/test_gui_page_js.py`.
- **On main:** everything through PR #12, merged 2026-10-15 (the plans renamed and
  sorted, the cryocooler by hand, End for a killed host, supply protection read
  back). **Next:** the End bench checklist ([end](design/end.md), section 9), on the bench
  computer with someone at the HMI; End was rehearsed on the GUI demo 2026-10-15.
- **Focus (2026-10-09): plans and blocks for darkness thermal testing.** First step
  done: the two plans tested on the bench renamed `ambient_to_rest` (was
  `rest_from_ambient`) and `rest_to_ambient` (was `vent_to_ambient`), and `plans/`
  sorted into folders (`vacuum/`, `thermal/`, `darkness/`, `blocks/`, `orbits/`).
  Decided: folders are only for sorting, so files can be dragged between them; a name
  is still found in any folder and must stay unique.
- **Cryocooler by hand (2026-10-09).** Commands sent from the GUI after `Cryocooler`
  were refused at times as "cryo is not live": the GUI expected its card every 1 s,
  rCryoBoard writes it every 5 s; the GUI now waits 17 s. `cryo ccv 17` on a running
  cooler tripped the converter with its enable bit still set, so `cryo on` did
  nothing; now a change on a running output drops and restores it (decided: off, set,
  on, not a ramp), `cryo on` re-arms a tripped output, and `CRYO_FAULT` records a
  trip. host.log keeps the last run's as host.log.1. **Open:** after the trip the
  cooler drew a steady 0.10 A instead of 0.47 A, also in the next run: at its fixed
  point, or its controller holding off? To check at the bench ([cryocooler](instruments/cryocooler.md)).
- **In progress:** the bench's official tests, and a practice session for students.
  `plans/` holds the blocks (`begin`, `pumpdown`, `vent`, `warm`, `cool`, `k508n`,
  `detector`) and the plans (`tvac`, `ambient_to_rest`, `rest_to_ambient`, `warm_soak`,
  `thermal_cycle`, `Cryocooler`), each with
  a header: start, end, what fails it. All run end to end against the simulated chamber
  (`test/test_shipped_plans.py`). `ambient_to_rest` and `rest_to_ambient` have run on the
  bench and work; the others have not. Warming and cooling have
  not been run through formsLabCLI. The session is [session](use/session.md), on the GUI demo. The old
  plans, blocks and orbits are the suite's fixtures (`test/fixtures/plans/`). Tests that run
  with an orbit come next.
- **Also in progress:** holding a zone at a thermocouple, `hvc platen 40 at TC01`
  ([plans](use/plans.md)). rLACO moves the zone's setpoint once a minute by a gain times
  the difference, within a band; the controller's own loop is unchanged. Tested against
  the simulator (T5 stands in for an article); the gain (0.05 per minute) and band (15 °C)
  are guesses until it is run on the bench.
- **Also in progress:** rules beside the steps, `when <condition> then <command>`
  ([plans](use/plans.md), Rules), because a linear plan cannot guard the chamber or act on
  the orbit while it waits on something else. Decided 2026-10-07: a rule watches to the
  end of the run, also while paused; acts at once if its condition already holds, then
  on each change to true; a refused command stops the run. Stage 1 (engine, checks,
  editor colours) is built; next: `when` in blocks, the orbit examples as rules, guards
  in the shipped tests and a session exercise.
- **Also in progress:** preparing for darkness (2026-10-07). Two blocks: `k508n at <V> V
  with <R> ohm` (the K508N cryocooler on, through rCryoBoard, which now answers each
  request and publishes CRYO_* values; a plan then waits `until TC01 <= -40 C`, as long
  as it takes) and `darkness imaging` (camera supply and umbra captures on). The
  cooler's supply is psu1 CH1 at 24 V, 1.0 A, OCP 1.25 A; its input 8.5 to 20 V. Open:
  which resistance gives which fixed-point temperature; to be measured on the bench.
  With them, the grammar says what every number is: a block call writes each input's
  unit (`vent within 60 min`), a condition may carry its own (`chamberP < 5 Torr`), a
  block can take a thermocouple as an input, and `within` is optional, since a
  cooldown takes as long as it takes. Not run on the bench; psu2 must be enabled in
  the live hardware map for the camera.
- **Also in progress:** End made safe and readable (2026-10-08). A run cut short runs
  the shipped end script, `plans/end.plan`, with every routine live, before the
  routines' shutdowns: cooler and camera supplies off at once; zones off, confirmed,
  setpoints to 20 °C; every valve closed and confirmed, before the pumps; turbo off,
  `hvc stop`, confirmed; every step best effort; 120 s cap; in-flight request dropped.
  Decided: zones off and valves closed on End, never opened; psu1 CH1 and psu2 CH1 off
  even if the run did not switch them on; a turbo spinning down keeps its backing until
  the HMI finishes it; a controller recipe or cycle is out of scope. The outcome
  (`ended.json`) shows in the GUI until dismissed; the End dialog lists the steps.
  Routine shutdowns run by dependency (`SHUTDOWN_BEFORE`); rSLTA got a shutdown. A plan
  that completes leaves what it said. Tested against the simulated chamber
  (`test/test_end.py`); not yet on the bench. **2026-10-13:** a run whose host was
  killed is found (its lock left behind) and its end runs from a fresh host when
  the operator asks (`labcli end`, *Run End now*); decided: never by itself. The
  GUI resends End for 150 s. rPSU reads OVP/OCP back and warns when not as set.
  Decided: End does not send `hvc abort`; the manual says it faults the controller
  and runs the recipe's home steps, which may open valves. Next for recipes: find
  at the bench what the controller reports while one runs ([end](design/end.md), Open items).
  **2026-10-15:** rPSU keeps the channels it switched on in `.run/psu_on.json`, so a
  killed run's channels go off at the next host's end.
  **2026-10-20:** Fast End ([end](design/end.md), D11), asked for because there was no way to
  stop waiting on a slow End: the same steps, in order, each command sent once
  taken, the confirmations skipped; also while an End is going. GUI *Fast End*,
  `end fast` in the console and `labcli end fast`. Decided: it is not a "freeze"
  (stop the plan, leave the chamber as it is); Pause is the nearest to that. The
  GUI is now called formsLAB (desktop shortcut `scripts/labgui.cmd`); formsLabCLI
  stays the name of the project and the console.
  **2026-10-22:** the chamber stays live between runs, asked for to watch it after
  End. The formsLAB server reads it, read-only, and hands the controller to a
  starting host (`formslab.host.monitor`, [gui](use/gui.md), Between runs). Decided:
  the monitor lives in the GUI server, so it reads only while formsLAB is open;
  only the chamber, not the thermocouples or supplies; `--no-monitor` frees the
  controller for `scripts/`. Not yet run against the real controller.
  **2026-10-28:** a NASA GEVS thermal vacuum test, asked for as a standard recipe
  for the chamber: `gevs_unit` ([gevs](use/gevs.md)), GSFC-STD-7000B at the unit level,
  protoflight. New blocks `highvac`, `seal chamber` and `plateau`; a block input
  can be moved by a number (`{temperature-2}`), for GEVS's 2 °C plateau start.
  Decided: true GEVS, so the turbo path (1e-5 Torr), though it has never been run;
  the article powered from psu1 CH2, switched by the plan for the cold and hot
  turn-ons. Found: `TurboPump` is the pump's on/off, not its speed, and the
  controller lets the foreline close under a coasting turbo; so did formsLAB's rule
  (it asked for `turbo off`, which the toggle makes true at once). Fixed as a rule,
  not a hold: the turbo counts as turning for `turbo_spindown_s` (30 min, a guess
  until the turbo's manual is read) after it was last seen on, kept in
  `.run/turbo_on.json` across hosts and by the monitor; rLACO publishes
  `TurboSpinning`; the foreline, pump and roughing rules ask for it stopped;
  `begin`, `highvac` and `seal chamber` wait `until TurboSpinning = false`
  ([tvac](rules/tvac.md), Turbo stopped). Found in LACO's manual: its own procedure
  leaves the turbo running between tests (Waiting on a decision, 11). Next at the
  bench: run `highvac` then `seal chamber` alone, watched at the HMI (the checklist
  in [gevs](use/gevs.md)).
  **2026-10-28, later: `shutdown`**, asked for as the proper system shutdown:
  one plan from any state (turbo on, a rough rest, at air, cold or hot, an End
  that left the backing) to at air with every pump, zone and supply off. Supplies
  off first, zones to 25 °C and on (they move while the turbo spins down), `seal
  chamber`, `vent within 240 min`, zones off. It is what End's "finish at the HMI"
  meant: the chamber line now ends "run shutdown to finish" and the GUI's ended
  banner has *Run shutdown*. Decided: `rest_to_ambient` stays, for a rough rest
  only. Open at the bench: whether the controller accepts `zone on` at atmosphere
  ([safety](rules/safety.md), Open).
- **2026-10-30: labcli rebuilt as formsLAB's companion over SSH.** Asked for
  because the console was a tab REPL that repainted only after a command, and
  the GUI and the console each had their own run control, with two End policies
  (the console killed a host after 15 s; the GUI never did). Now: one backend,
  `formslab.lab`, for both front ends; the host publishes where its run is
  (`.run/run.json`: the step, paused, ending), which both show; a bare `labcli`
  is one watch screen, repainted every second, with `E F P R Q` hotkeys and a
  command line that takes the `labcli <command>` words; the one-shot commands
  rewritten over the backend (status with every card and the ended or lost
  banner, check for blocks and orbits, end that waits and says how the chamber
  was left); the off switches below End as `labcli kill`, `supplies off` and
  `power` (the PowerSwitch), each asked first and refused while a run is going
  ([safety](rules/safety.md), When something must go off); every action from
  either front end in `.run/actions.log` (was gui.log); `labcli --demo` and the
  GUI demo on one simulated bench (`formslab.demo`). Decided: End never kills
  the host from anywhere, `kill` is the operator's own act; the hotkeys are
  capital letters, so every command can still be typed; the watch screen reads
  the chamber between runs when formsLAB does not, one reader at a time. The
  tab console (ctrl, cast, log, psu) is gone. Not run on the bench: `kill`,
  `supplies off`, `power`; which outlet feeds which supply is still to be
  written in the map.
- **Next:** the environment profile, the first stage after orbit files
  ([gui-design](design/gui-design.md), section 4): orbit file + spacecraft model → per-face background
  temperature over an orbit → a run in the recorder's format, in the Plots tab. It needs a way to
  describe the spacecraft first.
- **2026-10-28: the memory vault.** Asked for because the Notebook had stopped
  being the second memory: `Notebook/` and `docs/` are now one Obsidian vault,
  `memory/` ([README](README.md)), the canon for what formsLabCLI is, how to use
  it, why, and where it stands. New: the rule sets in `rules/` ([safety](rules/safety.md),
  [tvac](rules/tvac.md), [hvc](rules/hvc.md), [recipes](rules/recipes.md)), each
  rule saying who holds it (controller, formsLAB, operator) and its source, from
  the LACO and HVC manuals and the code; and [questions](questions.md). Found while
  writing them: the setpoint limits are wider than the chamber's rating (Waiting,
  9). Decided: plain relative markdown links, so it reads the same on GitHub;
  code comments point into `memory/`. The GEVS note joined it as [gevs](use/gevs.md)
  when the two branches merged.
- **2026-10-29: the bench network.** The PowerSwitch and the chamber now share a
  Netgear switch on one cable to the computer, on two subnets, so `Ethernet 2`
  holds `192.168.0.50` and `10.1.2.50`; both checked ([network](instruments/network.md)).
  Fixed: the PowerSwitch's `--setup` replaced every address on the adapter, which
  would have cut off the chamber; it now adds its own and keeps the rest.

## Done

| Date | PR | What |
|---|---|---|
| 2026-09-01 | | The lab console extracted from FORMS; config and state moved out of the package; the sequence host brought over |
| 2026-09-10 | #1, #2 | New-computer setup simplified; updating without losing local settings documented |
| 2026-09-17 | #3 | Rigol DP832A over USB on the Raspberry Pi |
| 2026-09-24 to 29 | #4 | LACO chamber support (HVC-3500 driver, `rTVAC_LACO`); the fixed-frame console; the rScripts runtime moved in, so LACO runs without FORMS |
| 2026-10-01 | #5 | The lab-only architecture: plans, ported rScripts, no FORMS runtime; vent and pumpdown scripts |
| 2026-10-02 | | One `tvac` mode, which is a plan; rScripts declare their cast commands; one thread per rScript; faster PSU reads |
| 2026-10-03 | #6 | `devices/` one folder per instrument; `.plan` files, one step per line; rScripts get a `Run`; the command grammar as data; `labcli <command>`; `formslab.orbit` added (orbit, view-factor and environment models); the GUI planned here |
| 2026-10-04 | | Shared files safe between processes; the GUI: login, status, plots, run control, the plan editor, the chamber view, the demo; commands declare their prerequisites (`RULES`), checked in plans and when sent; help cards |
| 2026-10-05 | #7 | One writing standard ([writing](design/writing.md)); part symbols in commands, cards and the chamber view; orbit files in the Plans tab with a live panel; one orbit stack (orbit files drive the models, numpy in base); roughing refused below the crossover; blocks; Edit and Ship; CI fixed on Windows |
| 2026-10-05 | #8 | rOrbit: a plan follows an orbit file and waits for its umbra; *Use in a plan* |
| 2026-10-05 | #9 | J2 drift, so sun-synchronous orbits keep their local time; one umbra reading for the panel and rOrbit; a wait on an orbit value before an orbit is chosen is refused; loops (`repeat … end`); conditions (`< <= > >=`, `= true`, `within`, `or go on`; `above`, `below`, `timeout` retired); rename; list symbols; CI runs the orbit extra |
| 2026-10-06 | #10 | The Notebook: the project's intent, architecture and progress |
| 2026-10-09 | #11 | Plans for the bench and a student session; rules (`when … then`), units, holds at a thermocouple; the K508N; a readable End (the end script); the cryocooler board as it really is |
| 2026-10-15 | #12 | The two tested plans renamed, `plans/` in folders; the cryocooler by hand (no false "not live", a tripped output turns on again); End for a killed host (`--end`, *Run End now*); End resent for 150 s; supply protection read back; [end](design/end.md) |
| 2026-10-30 | | labcli rebuilt: one backend for both front ends (`formslab.lab`), the host's published run state, the watch screen, the off switches (`kill`, `supplies off`, `power`), `actions.log`, the demo bench; the tab console removed |

## Waiting on a decision

From the GUI's open questions ([gui-design](design/gui-design.md)) and recent reviews:

1. **The spacecraft model:** how it is written (a file with its own grammar, like an
   orbit?). Blocks the environment profile.
2. **3D view:** from the start of the space-environment work, or after the profile?
   And with what, given no internet and no build step (plotly's `scene3d` is multi-MB)?
3. **rOrbit and Pause:** a paused run holds its steps, but the orbit goes on. Should a
   paused `orbit replay` hold the satellite too?
4. ~~**`labcli check` for `.orbit` files**~~ Settled 2026-10-30: `labcli check` reads a block or an orbit too.
5. **The umbra strip in dark mode:** its contrast is low.
6. **Students starting runs** from the GUI, or only writing plans and watching. The
   practice session ([session](use/session.md)) assumes an instructor starts every bench run.
7. **Default temperature unit** in the GUI (Celsius today).
8. **The controller's unread values** (heater output %, turbo speed %, foreline
   pressure): worth finding? A readable turbo speed would replace the
   `turbo_spindown_s` guess with the truth.
9. **Setpoint limits:** `tvac_bench.json` allows the platen -180 to 200 °C and the
   shroud -180 to 120 °C, the controller's own limits; the chamber is rated -150 to
   150 °C and -150 to 120 °C (LACO p. 18). Narrow the profile to the rating? Until
   then the operator holds it ([safety](rules/safety.md), Heat).
10. **The vacuum pump's power setting** must be *Always On* with a turbo (HVC p. 16).
   What is it set to now? To read on the HMI before the first turbo run.
11. **Leave the turbo running between tests?** LACO's procedure does (LACO p. 23):
   the gate valve isolates it to vent, the vacuum pump backs it through the foreline,
   and it never spins down. `gevs_unit` stops it (`seal chamber`), as its header
   promises every pump off. If the bench leaves it running, the plan ends with
   `hvc gate close` and `vent`, and End's D6 case becomes the normal one.

## Known gaps

- **The turbo path has never been run** on the chamber (crossover, foreline,
  gate valve): [laco-chamber](instruments/laco-chamber.md).
- **PSU2** is disabled in the shipped hardware map, but rSLTA powers the camera from it;
  enable it in the live map before an sLTA run ([architecture](design/architecture.md), Known conflicts).
- **No replay of a FORMS profile** yet: orbit timing reaches the bench through rOrbit,
  but no plan step follows a temperature profile.
- **The models' sweep is one orbit at its epoch**, two-body within it; J2 moves the
  orbit between dates (`Orbit(elements.at(t))`), not within one sweep.

## How work lands

- Work happens on the `cleanup` branch and reaches `main` by pull request; CI runs the
  suite on Ubuntu and Windows (Python 3.11, with the `orbit` extra).
- Two Claude Code sessions have worked in parallel, the second in a separate worktree
  (`formsLabCLI-blocks`) for blocks, loops and conditions; its commits were
  fast-forwarded onto `cleanup`.
- The vault (`memory/`) changes with the code, in the same pull request: what
  exists, how to use it, why, and what is next ([README](README.md), Keeping it).
