# GEVS thermal vacuum

`plans/thermal/gevs_unit.plan` is NASA Goddard's thermal vacuum test as the
General Environmental Verification Standard sets it for a **unit**: one component
or board, on the platen. The standard is GSFC-STD-7000B (revision of 2026-04-24),
section 2.6.3, from standards.nasa.gov. Section numbers below are its own.

GEVS tests flight hardware at **protoflight** levels: beyond the temperatures the
unit is allowed to see in flight, by a margin, so a weak part fails here and not in
orbit. Larger assemblies (an instrument, a satellite) have longer plateaus and
fewer cycles; this chamber and this plan are for units.

## What GEVS asks, and where the plan does it

| GEVS asks (section) | The plan |
|---|---|
| Pressure below 1e-5 Torr, so gas conducts no heat (2.6.2.4.4) | `highvac to 0.00001 Torr within 8 h`: the turbo pump |
| Test temperatures 10 °C beyond the allowable flight temperature (AFT) range (2.6.3.2.1.1) | -30 and 60 °C, for an AFT range of -20 to 50 °C |
| 8 cycles at the unit level, of the 12 before flight (Table 2.6-1) | the first cycle, then `repeat 7 times` |
| A plateau starts when the control temperature, the platen, is within 2 °C (2.6.3.2.3) | `plateau at <temperature> C`: waits for the platen within 2 °C, from above or below |
| Operating plateaus at least 4 hours; non-operating at least 2 (2.6.3.2.3) | `hold 4 h` with the article on; `hold 2 h` with it off |
| Transitions no faster than 1 °C/min (Table 2.6-1) | `hvc platen rate 1`, `hvc shroud rate 1` |
| Turn-on demonstrated at least once cold and once hot, after a soak off (2.6.3.3.1.5) | cycle 1: psu1 CH2 off for 2 hours at each end, then on |
| The article working through transitions, a functional test at every plateau (2.6.3.3.1.3, 2.6.3.3.1.6) | the supply stays on; the log says when to run the test |
| Back to air with the article warmer than its surroundings, above the dew point (2.6.3.3.1.7) | platen 30 °C, shroud 25 °C until the vent; the vent waits for 10 to 60 °C |
| All sensors monitored, alarmed if possible (2.6.2.3) | every value recorded each 30 s; rules turn the platen off 5 °C beyond either extreme |

## Setting it for an article

Copy the plan (*Edit* in the GUI) and change:

- **The temperatures.** Take the article's AFT range and add the margin:
  protoflight 10 °C each way (the default), qualification 15, acceptance 5. Change
  every `plateau at` line, and the two `when platenT` guards to 5 °C beyond them.
- **The supply.** The `psu1 ch2 set` and `psu1 ch2 protect` lines: volts, amps and
  their limits. psu1 CH2 is the article's in the hardware map; CH1 feeds the
  cryocooler board.
- **The plateaus.** 4 hours is the least; make them as long as the article's
  functional test needs.

Below 30 °C the zones cool with liquid nitrogen, so a test that goes cold needs
it supplied for the whole run, its exhaust vented outside the building.

## What it does not cover

- **100 failure-free hours hot and 100 cold** (2.6.3.6.2): a total across the
  whole program, not one run. The run's CSV holds the hours at each plateau.
- **Thermal balance** (2.6.4): a different test, which checks a thermal model
  against measured temperatures. It belongs to the orbit environment work.
- **The outgassing phase and the electrical discharge check** (2.6.3.3.1): they need
  contamination monitors and a powered article during pump-down that this bench
  does not have.

## Before the first run

The turbo path has never been run on this chamber. Run `highvac` and then
`seal chamber` on their own, watched at the HMI, and find:

1. Whether the roughing pump reaches the 0.01 Torr crossover, and how long it takes.
2. Whether the gate valve opens after the turbo's 5-minute spin-up, and whether the
   chamber stays at the crossover meanwhile.
3. Whether the gauge reads down to 1e-5 Torr.
4. How long the turbo takes to spin down: its speed is not read, so formsLAB
   counts it as turning for `turbo_spindown_s` after it was last on, 30 minutes
   until set from the turbo's manual (`tvac_bench.json`; [tvac](../rules/tvac.md),
   Turbo stopped). `seal chamber` waits that long before it closes the foreline.
5. Whether `hvc platen rate 1` limits the ramp to 1 °C/min on the controller.

End after a turbo run leaves the vacuum pump backing the turbo through the open
foreline, on purpose ([end](../design/end.md), D6); finish at the HMI once the status
page shows *Turbo pump turning: No*. LACO's own procedure leaves the turbo running
between tests behind the closed gate valve; whether this bench does is open
([progress](../progress.md), Waiting on a decision).
