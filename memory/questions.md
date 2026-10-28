# Questions

Questions that come up at the bench, answered briefly, with where the full answer
lives. When a question is asked twice, add it here.

## Refusals

**Why was `hvc vent open` refused?**
Air may come in only with the vacuum and gate valves closed and both zones between
10 and 60 °C. Close the valves; after a cold or hot soak, wait for the zones.
([tvac](rules/tvac.md))

**Why was `hvc rough open` refused at high vacuum?**
Below the 0.01 Torr crossover, opening the roughing line can let pump oil flow back
into the chamber. Only formsLAB holds this rule; the controller would allow it.
([safety](rules/safety.md), Vacuum)

**Why was `hvc stop` refused?**
The turbo is running, or the foreline is open: the vacuum pump is the turbo's
backing. Turn the turbo off and let it stop first; End leaves this for the HMI.
([end](design/end.md), D6)

**Why is every command refused except closing things?**
The controller has a fault (severity F). Only closing valves, `stop`, zones off,
`closeall`, `reset` and `abort` are allowed. Find the fault on the HMI, fix it, then
reset there. ([safety](rules/safety.md), Faults)

**Why was my plan refused for commanding psu1 CH1?**
psu1 CH1 feeds the cryocooler board and belongs to rCryoBoard while it runs.
([cryocooler](instruments/cryocooler.md), Supply)

## End

**Why does End not let air back in?**
A sealed chamber is safe whatever came before, and air onto a cold platen condenses
moisture. Venting is the operator's step afterwards (`rest_to_ambient`).
([end](design/end.md), D3)

**Why does End not send `hvc abort`?**
Abort faults the controller and runs the recipe's home steps, which may open
valves; the fault then refuses the setpoints and `turbo off`. ([end](design/end.md), D9)

**End is slow. Can I skip the waiting?**
*Fast End* sends the same steps in the same order without confirming them, also
while an End is going. Check the chamber at the HMI afterwards. ([end](design/end.md), D11)

**The host was killed. What now?**
The GUI and `labcli status` say the run stopped without its end. Press *Run End now*
(or `labcli end`). It never runs by itself, because someone may have stopped the host
on purpose. ([end](design/end.md), A host that was killed)

**What does "Chamber left: sealed, 3.2 Torr, pump off ..." mean?**
How the end left the chamber, read from the controller. It is kept in
`~/.formslab/.run/ended.json` and shown in the GUI until dismissed.

## The controller

**Why does `?Z1` not show the setpoint I just wrote?**
While the zone is off, `?Zn` returns the effective setpoint, which follows the
zone's own sensor. The written one is confirmed by the controller's echo.
([hvc](rules/hvc.md))

**Why can `scripts/pumpdown.py` not connect?**
The controller takes one client. During a run rLACO holds it; between runs the GUI's
monitor does. Start the GUI with `--no-monitor`. ([hvc](rules/hvc.md))

**Can I change the pressure or temperature unit?**
Only on the HMI; it cannot be read over ASCII. If it changes, change
`tvac_bench.json` to match. ([hvc](rules/hvc.md))

**Why can I see the HMI over VNC but not touch it?**
The installed VNC session is view-only, probably a view-only password in LACO's
application. Commands over ASCII are not affected. ([hvc3500](instruments/hvc3500.md))

**Which thermocouple is which?**
Platen control T2, shroud control T3, the HMI's "t2" is T4, over-temperature
channels T0 and T1, T5 to T13 live and unnamed. T14 and T15 read about 3 °C warm;
T16 to T20 are unused. ([hvc3500](instruments/hvc3500.md), The installed chamber)

## Vacuum and temperature

**Has the turbo pump been run?**
No. The foreline, turbo and gate path has never been run on this chamber. Run it
first on its own, watched at the HMI. ([tvac](rules/tvac.md), High vacuum)

**Can a plan wait for the turbo to reach speed?**
No: `TurboPump` is its on/off, not its speed, which is not read. A plan waits a fixed
time (`highvac` holds 5 minutes). It can wait for the turbo to **stop**: `until
TurboSpinning = false`, which formsLAB reports from `turbo_spindown_s` after the
turbo was last on, and which the foreline and pump rules require. ([tvac](rules/tvac.md))

**Why does the pumpdown hold 15 s after the pump starts?**
The controller opens the vacuum valve only after the pump has run 10 s.

**Pumpdown is slow, or stops short. Leak or outgassing?**
Pump down, close every valve, plot the pressure up past 1 Torr. A straight rise is a
leak; a rise that slows is outgassing. ([tvac](rules/tvac.md), Practice)

**Has cooling been run?**
Not through formsLabCLI. A cold test needs liquid nitrogen supplied and its exhaust
vented outside the building. ([safety](rules/safety.md), Liquid nitrogen)

## The cryocooler

**The GUI said "cryo is not live".**
The board's card is written every 5 s; the GUI now waits 17 s before calling it not
live (fixed 2026-10-09). ([progress](progress.md))

**`cryo on` did nothing after a voltage change.**
A change on a running output tripped the converter. A change now turns the output
off, sets it, and turns it on again; `cryo on` re-arms a tripped output, and
`CRYO_FAULT` records a trip. Open: why the cooler then drew 0.10 A instead of 0.47 A.
([progress](progress.md), [cryocooler](instruments/cryocooler.md))

## Setup and files

**Where is my run's data?**
`outputs/<UTC start>.csv` (`20261028T143000Z.csv`), one row per `record every`, the
plan's name in its `plan` column. The host's log is
`~/.formslab/.run/host.log`, with the run before as `host.log.1`.

**Where do I set COM ports and addresses?**
In the live hardware map, `~/.formslab/usbmap.json` (on Windows,
`%USERPROFILE%\.formslab\usbmap.json`). Never in `src/formslab/defaults/`, which is
the shared template. ([setup](use/setup.md))

**The log says psu2 is "not initialized".**
psu2 is disabled in the shipped hardware map. Enable it in the live map before a run
that powers the camera. ([architecture](design/architecture.md), Known conflicts)

**Why can I not change a shipped plan?**
They are the bench's tests, read-only in the GUI. *Save as...* makes your own copy;
*Edit* and *Ship* are for changing the bench's. ([recipes](rules/recipes.md))

**May students start runs on the bench?**
Not yet decided. Until it is, an instructor starts and ends every bench run.
([progress](progress.md), Waiting on a decision)
