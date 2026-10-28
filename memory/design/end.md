# The End button: the end script and everything around it

What happens when a run is ended, why each step is there, where each piece lives in
the code, how it is tested, and what is still open. It is the reference for
changing how End behaves, or for building it again elsewhere. How to run plans is
in [plans](../use/plans.md); the cryocooler board in [cryocooler](../instruments/cryocooler.md).

Status (2026-10-13): built and tested against the simulated chamber and a
stand-in cryocooler board (PR #11). A run whose host was killed is now found and
ended on request (D10). Fast End added 2026-10-20 (D11). Not yet run against
the real chamber: see [Bench checklist](#9-bench-checklist).

## Contents

1. [What End must achieve](#1-what-end-must-achieve)
2. [Decisions](#2-decisions)
3. [The end script](#3-the-end-script)
4. [The sequence of an end](#4-the-sequence-of-an-end)
5. [Where each piece lives](#5-where-each-piece-lives)
6. [Edge cases](#6-edge-cases)
7. [The GUI](#7-the-gui)
8. [Tests](#8-tests)
9. [Bench checklist](#9-bench-checklist)
10. [Open items](#10-open-items)
11. [Changing the end script](#11-changing-the-end-script)

## 1. What End must achieve

Whatever a run was doing, End leaves the bench in one known, safe state, and says
plainly what it did and what it could not do.

| Goal | How |
|---|---|
| The cryocooler and the camera lose power at once | their supply channels are the end script's first two steps |
| Nothing heats or cools after End | both zones off, confirmed from the controller |
| Nothing extreme stays armed | both setpoints to 20 °C |
| The chamber is sealed, never vented | every valve into it closed and confirmed, before any pump is touched |
| The pumps stop, without starving a turbo | turbo off behind a closed gate; the roughing pump keeps backing a spinning turbo |
| A refused or unconfirmed step does not stop the end | every step is tried; each problem is one line |
| No last command slips through | a request still waiting in CAST is dropped before anything else |
| Nothing powers up again during the end | while a run ends, no supply channel is switched on |
| The order does not depend on how a plan was written | routine shutdowns run by dependency, not by the `load` line |
| The operator knows what was done | the End dialog lists the steps; afterwards a banner and the log say how the chamber was left |
| A killed host is not silent | the GUI and `labcli status` say the run stopped without its end; End runs it from a fresh host |
| Students can read it | the steps are a plan file, opened in the editor like any other |

## 2. Decisions

Settled with the bench (2026-10-08). Change them here first, then in the script.

| # | Decision | Why |
|---|---|---|
| D1 | End turns both zones **off**; it does not hold the last setpoint | someone pressing End expects heating and nitrogen to stop. A run that should keep its temperatures says so in its own last steps |
| D2 | The setpoints go to **20 °C** after the zones are off | a zone turned on later by hand goes to room temperature, not to the last test's −150 or 150 °C |
| D3 | Vent and fill are **closed**, never opened | a sealed chamber is safe whatever came before; venting onto a cold platen condenses moisture. Venting is the operator's deliberate step afterwards |
| D4 | The chamber is **sealed before** the pumps are touched | if the pump steps are refused, the chamber is still sealed |
| D5 | psu1 CH1 (cooler) and psu2 CH1 (camera) go **off even if this run did not switch them on** | those channels feed the cooler and the camera; End means off |
| D6 | A **turbo** spinning down keeps its backing: gate closed, turbo off, foreline and roughing pump left until the HMI finishes it | the turbo's spin-down cannot be read; stopping its backing while it spins damages it |
| D7 | Every step is **best effort**; the script has **120 s** | an end must not fail or hang |
| D8 | A plan that **completes** does not run the end script | it leaves the chamber as its steps said (`rest_to_ambient` ends vented) |
| D9 | A controller **recipe or cycle** (`hvc recipe start`, `hvc start`) is out of scope; End does **not** send `hvc abort` | no plan starts one. The manual (sections 4.3.8 and 7) says an abort raises fault 0 and runs the recipe's "home steps", which the recipe defines and may open valves: that would undo D3 and D4, and the fault would refuse the setpoints and `turbo off` |
| D10 | The end of a run whose host was **killed** runs only when the operator asks (End in the GUI, `labcli end`), never by itself | the host may have been stopped on purpose by someone at the HMI; commanding the chamber unasked could undo what they are doing |
| D11 | **Fast End** sends the same steps in the same order, without waiting to confirm them: each command goes once its routine has taken it, and the `until` steps are skipped. It can be asked for while an End is going, and the rest of that End is fast | when an End is waiting on something slow (a pump that does not report off, a valve that does not confirm), the operator can choose not to wait. The order still holds, since a routine carries out its requests one at a time in the order taken; what is lost is the check that each step worked, so the banner says to check the chamber at the HMI |

## 3. The end script

`plans/end.plan`, shipped, read-only in the GUI, never in the Start list. It is
read by the same parser as any plan, so `labcli check end` checks it.

```
load rPSU rLACO
record every 5 s

# The cooler and the camera: power off, at once
psu1 ch1 off
psu2 ch1 off

# 1. Stop heating and cooling, confirmed; setpoints to room temperature
hvc platen off
hvc shroud off
until HoldingTemperature = false within 15 s
hvc platen 20
hvc shroud 20

# 2. Seal the chamber, each valve confirmed, before the pumps
hvc vent close
hvc fill close
hvc gate close
hvc rough close
until VentValve = false within 15 s
until FillValve = false within 15 s
until GateValve = false within 15 s
until RoughValve = false within 15 s

# 3. The pumps: turbo off behind the closed gate; then the roughing pump
hvc turbo off
hvc stop
until TurboPump = false within 15 s
until VacuumPump = false within 30 s
```

Each step, what it does and why:

| Step | Does | Why / what can go wrong |
|---|---|---|
| `psu1 ch1 off` | cuts the cryocooler board's supply | D5. Allowed although rCryoBoard owns the channel (the ownership check lets End turn a channel off) |
| `psu2 ch1 off` | cuts the camera's supply | D5. If psu2 is disabled in the hardware map, rPSU logs "not initialized" and the step goes on |
| `hvc platen off`, `hvc shroud off` | thermal control off on both zones | D1. Allowed during a controller fault |
| `until HoldingTemperature = false within 15 s` | confirms from the controller's one flag for both zones | not confirmed → one warning line |
| `hvc platen 20`, `hvc shroud 20` | setpoints to 20 °C | D2. Refused during a fault (a setpoint is not a fault-time command): logged, the end goes on |
| `hvc vent close`, `fill close`, `gate close`, `rough close` | every valve into the chamber closed | D3, D4. Closing a valve is always allowed, even during a fault |
| `until VentValve = false …` ×4 | confirms each valve from the controller's report | not confirmed → "check the HMI" |
| `hvc turbo off` | the turbo spins down behind the closed gate | D6. Refused during a fault |
| `hvc stop` | vacuum valve closed (again), then the roughing pump off, each verified | refused while the turbo runs or the foreline is open: the pump keeps backing the turbo, and the warning says to finish at the HMI |
| `until TurboPump = false …`, `until VacuumPump = false …` | confirms both pumps off | the second is the warning when the turbo case kept the pump on |

Not touched on purpose: the foreline valve, the pressure setpoint, ramp rates,
supply channels no routine owns, and any valve opening.

## 4. The sequence of an end

Everything below runs in the host process, in `channel()`'s `finally:`, so it
runs however the plan stopped (End, `labcli end`, ctrl `end`, Ctrl+C or SIGTERM,
a failed step, an error), but not if the process is killed: see
[A host that was killed](#a-host-that-was-killed).

```
  plan stops (GracefulExit / SequenceError / exception / last step)
        │  how = ended | failed | error | complete
        ▼
  _end(run, plan, how)                                  never raises
    1. paused = False; run.ending = True                (routines read it)
    2. if how != "complete":
         ClearPending()                                 a waiting request is dropped
         _end_script(run)                               the steps for the loaded routines
        ▼
  workers.stop()                                        each routine thread finishes its call (15 s)
        ▼
  _say_chamber(run, out)                                "Chamber left: sealed, 3.2 Torr, ..."
        ▼
  rscripts.shutdown(run)                                each rShutdown, by dependency
        ▼
  _write_ended(out)                                     ~/.formslab/.run/ended.json
        ▼
  "Host stopped."; the lock is released
```

**While the end script runs, every routine is still live.** That is the point: the
script talks to instruments through the same owners as the plan did, with the
same checks, and the routines' threads keep reading, so each `until` sees fresh
values.

**`run.ending = True`** changes two routines' behaviour from that moment on:

- **rPSU** ignores any request that switches a channel **on** ("CH1 ON ignored: the
  run is ending"). Without it, rCryoBoard, still live, would see its supply off and
  ask for it back.
- **rCryoBoard** does not try to bring the board up again.

**Which steps run.** For each step, `_needs` works out which routine it needs: the
owner of a command's label, or the publisher of an `until`'s value. A step whose
routine the plan did not load is skipped with one line (`end: hvc platen off:
skipped, rLACO not loaded`). So `tvac` without the chamber runs only the supply
steps.

**How each step runs** (`LabSequenceRunner.attempt_each`):

- one step after another, each on its own; a `SequenceError` (refused, not taken,
  not confirmed in time) becomes one warning line and the next step runs;
- an `until` on a value never published (the instrument was never reached) fails
  at once, instead of waiting its whole limit;
- after 120 s of wall clock, the remaining steps are logged as not run;
- commands go through the normal live prerequisite check, with one exception: a
  request that only turns supply channels **off** is allowed on a channel another
  routine owns (`assess(..., ending=True)`).

**Routine shutdowns** (`rscripts.shutdown`) run after the script, as the
backstop for when it could not act, in **dependency order**: a routine that
declares `SHUTDOWN_BEFORE = ("rPSU",)` (rCryoBoard, rSLTA) shuts down before
rPSU, whatever the `load` line's order. What each does:

| Routine | Its shutdown |
|---|---|
| rCryoBoard | releases the Pico link; if its supply is still on, output off and supply off; if the end script already cut the supply, only the link (the board cannot answer) |
| rSLTA | stops a capture in progress |
| rLACO | if this run started pumping and the script could not stop it, vacuum valve closed and pump off; ends a thermocouple hold; releases the controller |
| rPSU | turns off every channel this run switched on, and any a killed run left on (`psu_on.json`); hands the front panels back |
| rSMTC08 | closes its boards |

### A host that was killed

A killed host (a crash, Task Manager, a power cut) runs no end and leaves its
lock behind. A lock whose process is not a host is a **lost run** (`lost_run`):

- `labcli status` and the ctrl tab say "the last run ... ended without cleanup;
  `end` runs its end now"; the GUI shows a red banner, *Run End now*.
- **Ending it** (D10): `labcli end`, ctrl `end` or *Run End now* starts
  `python -m formslab.host.sequence --end`. That host takes the lock, loads the
  routines of the lost run's plan (the end script's own, `rPSU rLACO`, if that
  plan is gone), and sets `run.ending` **before** any routine runs, so nothing is
  switched on and the board is not brought up. It runs none of the plan's steps.
  It waits up to 20 s (`END_SETTLE_S`) for each value the end script confirms to
  be read once, then runs `_end` as above with `how = "lost"`.
- rPSU keeps the channels it has switched on and not turned off in
  `~/.formslab/.run/psu_on.json`, written at each change. A fresh rPSU takes them
  over ("PSU1 CH2 was switched on by a run that did not end") and its shutdown
  turns them off. A channel it cannot turn off stays in the file for the next host.
- No `end` is sent to that host: it stops by itself, and an `end` would cut short
  its wait for first readings.

### Fast End

The same end, without waiting to confirm it (D11). Asked for with *Fast End* in
the GUI's header, `end fast` in the ctrl tab or `labcli end fast`: ctrl `end` with
the key `fast`. For a lost run, *Fast End now* or `labcli end fast` starts the
fresh host with `--end --fast`, which also skips the wait for first readings.

- Each command goes once its routine has **taken** it, not once it reports it
  done. Requests to one routine are still carried out one at a time, in order:
  zones off, valves closed, then the pumps.
- Each `until` is **skipped**; one already waiting stops waiting. While the end
  script runs, the host reads ctrl at every step (`_watch_for_fast`), so a Fast
  End sent during an End makes the rest of it fast.
- The chamber line is read after the routines' threads stop, so it shows what
  their last requests did. The record has `"fast": true` and one warning:
  `Fast End: 7 confirmations skipped; check the chamber at the HMI`.

On the simulator, from the request to "Host stopped.": End mid-pumpdown 14.4 s,
Fast End 11.4 s; the rest is the controller carrying the commands out. With the
turbo running, End waits 30 s for the vacuum pump to stop; a Fast End then ends
the wait at once.

**The record of the end**, `~/.formslab/.run/ended.json`:

```json
{
 "plan": "tvac",
 "when": "2026-10-08T14:47:59+00:00",
 "how": "ended",
 "fast": false,
 "chamber": "Chamber left: sealed, 1e-05 Torr, pump off, turbo off, zones off",
 "warnings": []
}
```

## 5. Where each piece lives

| Piece | File | Name |
|---|---|---|
| The script | `plans/end.plan` | — |
| Running it | `src/formslab/host/sequence.py` | `_end`, `_end_script`, `_needs`, `_chamber_left`, `_write_ended`, `END_CAP_S`, `END_SCRIPT`, `ended_path` |
| Best-effort steps | `src/formslab/sequence/runner.py` | `LabSequenceRunner.attempt_each`; `_command` passes `ending` to `assess` |
| Dropping a waiting request | `src/formslab/console/cast/castutils.py` | `ClearPending` |
| Turning off an owned channel | `src/formslab/rscripts/rules.py` | `assess(..., ending=)`, `_only_off` |
| Shutdown order | `src/formslab/rscripts/loader.py` | `shutdown`, `_ordered`, `SHUTDOWN_BEFORE` |
| Nothing switched on while ending | `rScripts/rPSU.py` | the `"on"` branch of a channel request |
| No board bring-up while ending | `rScripts/rCryoBoard.py` | the `ending` check in `rScript` |
| The states the script confirms | `rScripts/rLACO.py` | `STATES`, `_publish_states` |
| The camera's shutdown | `rScripts/rSLTA.py` | `rShutdown`, `SHUTDOWN_BEFORE` |
| The board's shutdown | `src/formslab/devices/cryocooler/owner.py` | `_shutdown_cryo_subsystem` (skips an unpowered board) |
| The End dialog's list | `src/formslab/gui/api.py`, `server.py` | `end_steps`, `GET /api/end/steps` |
| Sending End | `src/formslab/gui/api.py` | `end_run` (writes ctrl `end` every 3 s for up to 120 s) |
| The outcome | `src/formslab/gui/api.py` | `last_ended`; `status()["ended"]` |
| The page | `src/formslab/gui/static/app.js`, `index.html` | the `#end` handler, `renderEnded`, `#ended` |
| Not in the Start list | `app.js` `loadPlans`; `api.list_plans` | `"end": true` |
| A lost run, found | `src/formslab/host/sequence.py` | `lost_run` |
| Its end, from a fresh host | `src/formslab/host/sequence.py` | `channel(..., end_only=True)`, `_settle`, `END_SETTLE_S`, `main`'s `--end` |
| Its end, asked for | `src/formslab/console/ctrl/ctrlcli.py`; `src/formslab/gui/api.py` | `end_sequence` → `_end_lost`; `end_run` → `_end_lost`; `status()["lost"]` |
| Its banner | `src/formslab/gui/static/app.js` | `renderLost` |
| Fast End, in the host | `src/formslab/host/sequence.py`; `src/formslab/sequence/runner.py` | `fast`, `_fast_if_asked`, `_watch_for_fast`, `--fast`; `attempt_each(..., skip_waits=)`, `_until`, `_command` |
| Fast End, asked for | `ctrlcli.py`, `cli.py`; `api.py`, `server.py`, `app.js` | `end_sequence(fast=)`, `end fast`; `end_run(fast)`, `POST /api/end {"fast": true}`, `#end-fast` |

The chamber's states the script confirms are published by rLACO for any plan, each
`= true` or `= false`:

| Value | True when |
|---|---|
| `VentValve`, `FillValve`, `GateValve`, `RoughValve`, `ForelineValve` | the valve is open |
| `VacuumPump`, `TurboPump` | the pump is on |
| `HoldingTemperature` | either zone's thermal control is on (the controller reports one flag for both) |

## 6. Edge cases

| Case | What happens |
|---|---|
| End mid-pumpdown | the request in flight is dropped; zones off; valves closed; pump off; sealed at the pressure reached |
| End with a zone hot or cold | zones off, setpoints 20 °C; the chamber then drifts toward room temperature |
| End mid-vent | the vent valve closes: the chamber is sealed at whatever pressure it reached (D3) |
| End with the turbo running | gate closed, turbo off; `hvc stop` refused, so the roughing pump and foreline keep backing it; the banner says "vacuum pump still on: finish at the HMI" |
| End during a controller fault | valves close, zones off, `stop` runs; the setpoints and `turbo off` are refused and logged |
| End while paused | the same as not paused: pause is ignored while ending |
| End twice | the second `end` arrives after the plan has stopped; it changes nothing |
| Chamber unreachable | each chamber command is refused at once, each confirmation fails at once ("never read"); the end takes about 25 s; "Chamber not reached: check it at the HMI" |
| Cryocooler board unpowered or unreachable | `psu1 ch1 off` still cuts its supply; its shutdown only releases the link |
| psu2 disabled in the hardware map | `psu2 ch1 off` logs and goes on |
| The plan did not load rLACO | only the supply steps run; no chamber line |
| A plan step failed | the same end as End; `how` is `failed` and the banner says so |
| The plan ran to its last step | no end script (D8); the routines' shutdowns only |
| A bug in the end code | caught in `_end`; one warning; the routine shutdowns still run |
| The host process killed | nothing runs then. The GUI and `labcli status` say so; End runs that run's end from a fresh host ([A host that was killed](#a-host-that-was-killed)) |
| A controller recipe or cycle running | End does not stop it (D9): stop it at the HMI |

## 7. The GUI

- **The button.** *End run* is in the header on every tab while a run is going,
  and *Fast End* beside it ([Fast End](#fast-end)). *Fast End* stays enabled while
  an End is going, to make the rest of it fast; its dialog lists the same steps,
  "without waiting to confirm each step", and says to check the chamber at the
  HMI afterwards. Its banner says the run "was ended with Fast End".
- **The dialog** lists the end script's steps for the routines the running plan
  loaded (`GET /api/end/steps`), then "then each instrument's shutdown". Without
  the list (an old server), it falls back to "Each instrument's shutdown runs".
- **Sending.** `POST /api/end` writes ctrl `end`, and again every 3 s while the host
  lives, for up to 150 s (`END_WAIT_S`: the script's 120 s, then the routines'
  stop and shutdowns). The GUI never kills the host.
- **A lost run.** With no run going but a lost run's lock, a red banner says the
  run stopped without its end, with *Run End now*: the same dialog, then
  `POST /api/end` starts the `--end` host. It stays until that end has run or
  another run starts. The Status note says
  "Ending: the end script is running, then each instrument's shutdown...".
- **The outcome.** When no run is going, `/api/status` carries `ended` (the host's
  `ended.json`). The page shows it as a banner under the run controls:
  - green: `tvac ended at 14:47. Chamber left: sealed, 1e-05 Torr, pump off, turbo
    off, zones off.`
  - red, when the script could not do something or the chamber was not reached:
    the same, then `Not done: <each warning>`.

  *Dismiss* hides it in that browser (localStorage, by the end's time); a new end
  shows again.
- **The log.** Each step is a line: `[end 6/17] hvc vent close`, then the routine's
  own line (`vent verified closed`), then `Chamber left: ...`.

## 8. Tests

`test/test_end.py`, against the simulated HVC-3500, with the shipped end script:

| Test | Checks |
|---|---|
| `test_end_mid_pumpdown_leaves_the_chamber_sealed_off_and_at_20_c` | every valve closed, both pumps off, zones off, setpoints 20 °C, no warnings, the steps in the log |
| `test_end_with_the_turbo_running_keeps_its_backing_and_says_so` | gate closed, turbo off, pump and foreline still on, a warning naming `hvc stop` |
| `test_end_during_a_fault_closes_valves_and_zones_but_not_setpoints` | valves and zones done; the 20 °C setpoint refused and listed |
| `test_end_with_the_chamber_unreachable_still_ends_and_says_to_check_the_hmi` | the chamber line, the warnings, under 60 s |
| `test_a_plan_that_runs_to_its_last_step_leaves_what_it_said` | a completed plan keeps its vent open; no end script |
| `test_the_chamber_publishes_its_states_for_plans_to_confirm` | `VentValve`, `VacuumPump`, `HoldingTemperature` follow the chamber |
| `test_a_request_still_waiting_is_dropped_before_its_owner_takes_it` | `ClearPending` |
| `test_shutdown_runs_a_script_before_the_one_it_depends_on` | `SHUTDOWN_BEFORE` beats the load order |
| `test_the_end_script_checks_clean_and_turns_off_the_channels_the_map_owns` | the script reads clean; its `psu` lines are exactly the hardware map's owned channels |
| `test_the_end_script_runs_only_the_steps_of_the_routines_loaded` | `_needs` |
| `test_a_run_whose_host_was_killed_is_found_and_ended_from_a_fresh_host` | `lost_run`; `end_only`: valves closed, zones off, `how` is `lost`, the supplies warning, the lock gone |
| `test_fast_end_sends_every_step_in_order_without_confirming_them` | everything closed and off, `fast` true, the one warning, the chamber line read after the routines stop |
| `test_fast_end_asked_while_an_end_waits_stops_the_wait` | with the turbo running, the 30 s wait for the pump cut short |
| `test_a_lost_run_can_be_fast_ended` | `end_only` with `fast_end` |
| `test_end_on_its_own_needs_a_lost_run` | `--end` with nothing lost refuses |

Elsewhere:

- `test/test_k508n.py::test_end_may_cut_the_boards_channel_and_nothing_switches_it_back_on`:
  the ownership exception, and rCryoBoard not asking for its supply again.
- `test/test_lab_sequence.py::test_rpsu_switches_nothing_on_while_the_run_is_ending`.
- `test/test_shipped_plans.py`: `end` is in the shipped set and reads clean.
- `test/test_gui_control.py::test_end_on_a_lost_run_starts_a_host_for_its_end_and_sends_no_end`
  and `::test_end_keeps_asking_for_as_long_as_the_end_script_may_take`;
  `::test_fast_end_asks_for_it_also_of_a_run_already_ending` and
  `::test_fast_end_on_a_lost_run_starts_its_end_fast`.
- `test/test_ctrl_host.py::End::test_end_fast_asks_for_a_fast_end`.

Driven by hand on the GUI demo (2026-10-08) through the page's own API: End
mid-pumpdown with a zone on, and End while paused. Both took 12 to 16 s and left
the chamber sealed, pumps and zones off, no warnings.

Again on 2026-10-15, with the killed-host end added:

| Scenario | End took | Left |
|---|---|---|
| End mid-pumpdown, platen on at 60 °C | 16 s | sealed, pump off, zones off, no warnings |
| End while paused, pump on | 11 s | the same |
| The host killed (pump and platen on), then *Run End now* | the lost run shown within 1 s; its end 19 s | the same; `how` `lost`, the one warning to check the supplies (since gone: `psu_on.json`); a second End refused, nothing lost |

Run them: `pytest test/test_end.py` (about 4 minutes; it runs real plans against
the simulator).

## 9. Bench checklist

In order, each on the real chamber, with someone at the HMI. Each step: press End,
read the banner, compare with the HMI's Manual screen.

1. **Idle `tvac`**, nothing on. Expect green, everything closed and off. Proves the
   commands reach the controller.
2. **Chamber off** (controller unreachable). Expect about 25 s, red, "Chamber not
   reached: check it at the HMI".
3. **Mid-pumpdown** (`ambient_to_rest`, around 100 Torr). Expect the vacuum valve
   closed, the pump off, sealed near that pressure.
4. **A zone on** (`tvac`, `hvc platen 40`, `hvc platen on`). Expect the zone off and
   its setpoint 20 °C on the HMI.
5. **The cryocooler running** (`Cryocooler`). Before ending, the psu1 card shows
   CH1's OVP 24.5 V, OCP 1.25 A, protection on, and the log has no "protection
   ... not as set". Expect psu1 CH1 off within a second of confirming, before the
   chamber steps.
6. **The camera powered** (psu2 enabled). Expect psu2 CH1 off, a capture stopped.
7. **A killed host.** `tvac`, `hvc pump on`, then end the host's python process in
   Task Manager. Expect the red banner within a few seconds; *Run End now*; expect
   the pump off and everything closed. With a supply channel switched on in that
   run (bench computer only), expect it off too.
8. **Fast End mid-pumpdown** (`ambient_to_rest`, around 100 Torr). Expect the same
   as item 3, sooner, and the banner saying it was a Fast End, to check at the HMI;
   then the HMI agreeing.
9. **Later, with the turbo path commissioned:** End with the turbo running. Expect
   gate closed, turbo off, roughing pump and foreline still on, the banner saying
   to finish at the HMI.

Record each result in [progress](../progress.md).

## 10. Open items

| Item | Why it matters | A way to close it |
|---|---|---|
| **A controller recipe or cycle** started by a plan | End does not stop it (D9), and `hvc abort` is not safe to add blindly: it faults the controller and runs the recipe's home steps | first find what the controller reports while a recipe runs (`?TC`, `?MC`, `?RS` at the bench), then publish it (`CycleRunning`) and stop a recipe only when one runs, by `hvc recipe stop` if that leaves the valves alone |
| **The turbo path** has never been run on the chamber | step 3's handling is right on paper and in the simulator only | bench checklist item 9 |
| **The protection command** rPSU sends (`:VOLT:PROT:STAT CH1,ON`) differs from the DP800 guide's form (no channel; the channel selected first) | if the supply ignores it, protection stays off; the read-back now says so ("not as set") | bench checklist item 5; if it warns, send the guide's form |
| **Confirmation times** (15 s per state, 30 s for the pump) are estimates | a slow valve or pump on the real chamber would be logged as not confirmed | adjust from bench checklist items 3 and 4 |

Closed 2026-10-13: a killed host is found and its end run on request (D10); the
GUI resends End for 150 s, longer than an end can take; rPSU reads each channel's
OVP, OCP and protection back from the supply at the start and after each
`protect`, shows them on its card, and warns when they are not as set.
Closed 2026-10-15: a killed run's supply channels go off at the next host's end
(`psu_on.json`).

## 11. Changing the end script

- Edit `plans/end.plan` in the editor: *Edit*, change, *Ship*. It is a plan; the
  editor's checks apply.
- Keep every step **safe to try in any state**: an end may start from anything.
  Prefer closing and turning off to opening and turning on; `rPSU` refuses any
  channel **on** while ending anyway.
- Confirm a state with `until <value> = false within <time>` right after the
  commands that set it, so a refusal or a slow part is one clear warning.
- A new instrument whose power must go at once: its supply channel's `off` at the
  top. If its routine owns the channel in the hardware map, the ownership check
  already lets End turn it off. `test_the_end_script_checks_clean_and_turns_off_the_channels_the_map_owns`
  will fail until the line is added: on purpose.
- A new routine whose shutdown needs another's: declare `SHUTDOWN_BEFORE`.
- Update this document's [step table](#3-the-end-script), [edge cases](#6-edge-cases)
  and [tests](#8-tests).
