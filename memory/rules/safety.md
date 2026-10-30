# Safety

The bench's safety rules in one place. Read this before a first run on the real
chamber, and again when anything here changes.

Each rule says who holds it:

- **Controller**: the HVC-3500's PLC refuses, whatever sends the command (the HMI,
  formsLAB, a script).
- **formsLAB**: refused before it is sent, when a plan is read and again when a
  step runs or a command is typed (`src/formslab/devices/hvc3500/rules.py`,
  [plans](../use/plans.md), Prerequisites).
- **Operator**: nothing else holds it. These are the rules people must carry.

Page numbers are the manuals' own ([sources](../sources/README.md)): *LACO* is the
chamber's system manual, *HVC* the controller's.

## People

| Rule | Held by | Source |
|---|---|---|
| Only trained people operate the chamber. Someone in training works under the supervision of someone trained. | Operator | LACO p. 5, 21 |
| At this bench an instructor starts and ends every run on the real chamber. Students write plans and watch. Whether students may start runs is still open ([progress](../progress.md), Waiting on a decision). | Operator | [session](../use/session.md) |
| Never override or bypass an interlock, in hardware or software. In formsLAB that means: do not edit `rules.py` or the bench profile to get a plan through. | Operator | LACO p. 6 |
| Keep hands and objects away from the vacuum inlet: its suction is strong. | Operator | LACO p. 7 |
| Do not touch the platen, shelves or inner walls unless they are at room temperature. The outside of the chamber can also get hot. | Operator | LACO p. 8 |
| Touch the inside of the chamber, and anything going into it, only with clean gloved hands. | Operator | LACO p. 26 |
| Service only with power and gases locked out: the main disconnect at the back, the compressed-air 3-way valve, gas bottles off and their lines vented. The interlock circuit does not remove hazardous voltage. | Operator | LACO p. 5, 10-11 |

## Liquid nitrogen

| Rule | Held by | Source |
|---|---|---|
| The nitrogen exhaust is vented outside the building, always. Not venting it can suffocate people in the room. | Operator | LACO p. 21 |
| A test that cools needs nitrogen supplied, at 20 to 40 psi, for the whole run (`cool`, `thermal_cycle` and any plan that goes cold). formsLAB cannot see the supply. | Operator | LACO p. 18 |
| Cooling has not yet been run on this chamber through formsLabCLI. Watch the first cold run at the HMI. | Operator | [progress](../progress.md) |

## Pressure

| Rule | Held by | Source |
|---|---|---|
| The chamber is a vacuum vessel, not a pressure vessel: at most 0.5 psig. Never pressurize it; the door clamp can fail or the chamber rupture. | Operator | LACO p. 8, 18 |
| Process gas (the fill valve) at less than 5 psi. A relief valve, 0.2 psi, keeps the chamber from pressurizing while it fills. Never adjust or remove a relief valve. | Operator | LACO p. 9, 18, 31 |
| Compressed air at 80 to 120 psi. Inspect the tubing before use; replace anything damaged. Low control air is fault 16. | Operator, Controller | LACO p. 7, 18 |
| No tracer gas with more than 5 % hydrogen. | Operator | LACO p. 8 |

## Heat

| Rule | Held by | Source |
|---|---|---|
| Heat only under vacuum. The controller limits the heaters above 700 Torr. | Controller | LACO p. 7; [hvc3500](../instruments/hvc3500.md) |
| Let air in only when both zones are between 10 and 60 °C (the vent window). Hotter burns people and, above 200 °C, oxidizes the steel; colder frosts the chamber and condenses moisture on it. | Controller, formsLAB | HVC p. 17; LACO p. 7 |
| A heater hotter than its source limit loses power and raises a soft over-temperature warning (5). The platen's source limit is 215 °C, the shroud's 125 °C. | Controller | HVC p. 17; [hvc3500](../instruments/hvc3500.md) |
| A hard over-temperature (fault 17) is the Watlow controller's trip. It does not clear by itself: someone resets it at the Watlow. | Controller, Operator | [hvc3500](../instruments/hvc3500.md) |
| Keep setpoints within the chamber's rating: platen -150 to 150 °C, shroud -150 to 120 °C. formsLAB refuses only beyond the controller's setpoint limits, which are wider (platen -180 to 200 °C, shroud -180 to 120 °C, `tvac_bench.json`). | Operator; formsLAB beyond the limits | LACO p. 18 |

The last row is a gap, not a design: see [Open](#open).

## Vacuum

The full set, with the order of a high-vacuum pumpdown, is in [tvac](tvac.md).
These are the ones that protect people, the pumps or the chamber.

| Never | Why | Held by |
|---|---|---|
| Vent or fill unless the vacuum and gate valves are closed and both zones are in the vent window | air rushes into a pump, or onto a hot or cold surface | Controller, formsLAB |
| Open the vacuum valve to a chamber already below the 0.01 Torr crossover | roughing-pump oil can flow back into the chamber | formsLAB only |
| Stop the vacuum pump with the vacuum or foreline valve open | oil is drawn into the chamber or turbo; `hvc stop` closes the valve first | Controller, formsLAB |
| Close the foreline, or stop the vacuum pump, while the turbo pump runs or coasts after it is turned off | the foreline is a spinning turbo's only backing. The controller does not report the spin-down, so formsLAB counts the turbo as turning for `turbo_spindown_s` after it was last on ([tvac](tvac.md)) | Controller (the pump, while the turbo is on), formsLAB; End (D6) |
| Expose a turbo at full speed to a pressure shock: open the vacuum or vent valve with the gate open to it | it can destroy the turbo | Controller (gate interlocks), formsLAB |

## Faults

| Rule | Held by | Source |
|---|---|---|
| A fault stops every process, turns the outputs off and ends the cycle. | Controller | HVC p. 19; LACO p. 28 |
| During a fault formsLAB allows only what makes the chamber safer: closing valves, `stop`, zones off, `closeall`, `reset`, `abort`. | formsLAB | `rules.py` |
| To recover, fix the cause, then press Reset or Abort at the HMI. The controller then runs its recovery ("home") steps, which may move valves. Decide this at the HMI; formsLAB never sends `abort` or `reset` on its own. | Operator | LACO p. 9, 28; [end](../design/end.md), D9 |

## What End does, and does not

End is always safe to press. It cuts the cryocooler's and the camera's supplies,
turns both zones off and their setpoints to 20 °C, closes every valve into the
chamber, then stops the pumps. It never vents. ([end](../design/end.md), D1 to D7.)

It does not:

- **stop a controller recipe or cycle** (D9). Stop it at the HMI.
- **stop the roughing pump behind a running turbo** (D6). It leaves the pump
  backing the turbo through the foreline and says to run `shutdown`, which waits
  for the turbo to stop, stops the pumps and lets air in ([plans](../use/plans.md)).
- **run by itself for a host that was killed** (D10). Someone may have stopped it
  on purpose; it runs when the operator asks.
- **confirm anything, if it was a Fast End** (D11). Check the chamber at the HMI.
- **kill a host that does not take it.** No front end does; `labcli kill` is the
  operator's own act, asked first, after which End runs from a fresh host.

## When something must go off

In this order; each step is for when the one before cannot do it. The refusals
are held by formsLAB; the order by the operator.

| Step | When | What it does |
|---|---|---|
| End: `E` on the watch screen, *End run*, `labcli end` | a run is going | the end script, then every routine's shutdown: supplies off, zones off, valves closed, pumps stopped |
| Fast End: `F`, *Fast End*, `labcli end fast` | End is waiting on something slow | the same steps, not waiting to confirm each |
| `labcli kill`, then End again | the host does not take End | stops the host without its end; the run is then lost, and End runs its end from a fresh host (D10) |
| `labcli supplies off` | no run is going, or its host is gone, and a supply must go off now | every output of every enabled supply off, over its own session; refused while a run is going |
| `labcli power <outlet> cycle`, then `supplies off` again | a supply does not answer | the PowerSwitch outlet it is on off and on again ([powerswitch](../instruments/powerswitch.md)); refused while a run is going |

`kill`, `supplies off` and an outlet off or cycled ask `y/n` before they act; from
a script, add the word `yes`. Every one of them, from either front end, is a line
in `~/.formslab/.run/actions.log`.

## Supplies

| Rule | Held by | Source |
|---|---|---|
| One owner per supply channel. psu1 CH1 feeds the cryocooler board and belongs to rCryoBoard while it runs; a stray setpoint from elsewhere can brown out or over-volt the board. | formsLAB | [cryocooler](../instruments/cryocooler.md) |
| The board's supply: 20 to 24.5 V, at most 2.0 A, protection at or above the setting (shipped: 24 V, 1.0 A, OVP 24.5 V, OCP 1.25 A). | formsLAB | [cryocooler](../instruments/cryocooler.md) |
| Check the psu1 card shows protection on, and the log has no "protection ... not as set", before trusting it. The command rPSU sends differs from the supply guide's form and is not yet confirmed on the bench. | Operator | [end](../design/end.md), Open items |
| While a run ends, no supply channel is switched on. | formsLAB | [end](../design/end.md) |
| Between runs a supply is turned off by `labcli supplies off`, or its outlet cycled by `labcli power`; while a run is going both are refused and End does it. | formsLAB | [When something must go off](#when-something-must-go-off) |

## The network

The controller has no authentication on its command port, and its FTP credential
is printed in its manual. Keep it on the isolated lab network, never on the
internet, and do not change its IP addresses without recording the old ones and
checking what else uses them. ([hvc3500](../instruments/hvc3500.md), Network safety.)

## Open

| Item | Until it is closed |
|---|---|
| The setpoint limits in `tvac_bench.json` are wider than the chamber's rating | the operator keeps setpoints within the rating; the shipped plans do (`thermal_cycle` -20 to 60 °C) |
| The turbo path (foreline, turbo, gate) has never been run | run it first on its own, watched at the HMI |
| `turbo_spindown_s` (30 minutes) is a guess: the parts list names no turbo model | read the model off the pump and set the time from its manual; longer only delays `seal chamber`, shorter can starve the turbo |
| Whether the controller accepts `hvc platen on` at atmosphere (it limits the heaters above 700 Torr) | if it refuses, `shutdown` run on a chamber already at air stops at that step; then move the zone steps after `vent` |
| Cooling has never been run through formsLabCLI | watch the first cold run at the HMI |
| End has not been run on the real chamber | the bench checklist, [end](../design/end.md) section 9 |
| `labcli kill` and `labcli supplies off` have not been run on the bench | try `supplies off` on a supply with nothing connected first |
| Which PowerSwitch outlet feeds which supply is not written down | read it off the switch and record `PS.outlets` in the live map ([powerswitch](../instruments/powerswitch.md)) |
| End's confirmation times (15 s, 30 s) are estimates | adjust from the bench checklist |
| What the controller reports while a recipe runs | [recipes](recipes.md) |
