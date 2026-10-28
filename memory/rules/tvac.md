# Operating the chamber

How the LACO chamber is run, as rules: the interlocks, the order of each
procedure, and the practice that keeps it working. What the chamber is:
[laco-chamber](../instruments/laco-chamber.md). How formsLAB talks to its
controller: [hvc](hvc.md). The rules that protect people: [safety](safety.md).

## The interlocks, both sets

The controller enforces its own interlocks (HVC p. 29-30). formsLAB checks the same
things, and a few more, before sending (`src/formslab/devices/hvc3500/rules.py`), so
a plan that would be refused is caught while it is written, and the refusal says why
in words. Part names are the HMI's ([writing](../design/writing.md)).

| Command | Controller needs | formsLAB also needs |
|---|---|---|
| `hvc rough open` (Vacuum valve) | vacuum pump on 10 s; vent, fill, foreline and gate valves closed | turbo stopped; chamber at or above the 0.01 Torr crossover |
| `hvc vent open`, `hvc fill open` | gate and vacuum valves closed; the vent window | platen and shroud each 10 to 60 °C |
| `hvc foreline open` | vacuum pump on 10 s; vacuum valve closed | |
| `hvc foreline close` | | turbo stopped |
| `hvc turbo on` | foreline open; foreline at or below 0.2 Torr (lost above 0.5) | |
| `hvc gate open` | chamber below the crossover; turbo on; foreline open | |
| `hvc pump off` | vacuum and foreline valves closed | turbo stopped |
| anything during a fault | | only what makes it safer (see [safety](safety.md), Faults) |

The two rules only formsLAB holds are the ones that protect against oil and a
starved turbo: no roughing below the crossover, and no foreline closed under a
spinning turbo. Neither is in the controller's list.

**Turbo stopped** means off, and `turbo_spindown_s` past since it was last on
(`tvac_bench.json` `limits`; 30 minutes until the turbo's manual says otherwise).
The controller reports the turbo on or off, not its speed, and the rotor coasts for
minutes after `turbo off`, needing its backing all the while. So formsLAB keeps the
time it last saw the turbo on (`.run/turbo_on.json`, written by rLACO during a run
and by the monitor between runs) and publishes `TurboSpinning`. `hvc turbo off`
alone settles nothing; a plan waits `until TurboSpinning = false` before it closes
the foreline, as `begin`, `highvac` and `seal` do. The status page shows *Turbo
pump turning*.

The vacuum pump must run 10 s before the controller lets the vacuum valve open.
The `pumpdown` block waits 15 s.

## Roughing down

The path every shipped test uses (`ambient_to_rest`, `pumpdown`):

1. Door closed, chamber at atmosphere, turbo off.
2. Vent, fill and gate valves closed.
3. Vacuum pump on; wait 15 s.
4. Check the chamber is still above 0.01 Torr; open the vacuum valve.
5. Wait for the pressure, with a time limit (20 minutes to 3 or 5 Torr).
6. `hvc stop`: vacuum valve closed, then the pump off. The chamber is sealed.

## High vacuum: the turbo path

**Never run on this chamber.** Run it first on its own, watched at the HMI. The
order is LACO's (LACO p. 23-24):

1. The vacuum pump's power setting must be *Always On* for a system with a turbo
   (HVC p. 16). Check it in Advanced Settings before the first turbo run.
2. Start the turbo with the foreline valve open and the gate closed; let it reach
   full speed.
3. To rough the chamber, close the foreline and open the vacuum valve. The pump is
   shared, so the turbo runs a few minutes without backing; LACO advises a separate
   backing pump if roughing takes several minutes or more.
4. At the crossover (0.010 Torr on this chamber): close the vacuum valve, open the
   foreline, then open the gate, in that order.
5. Let the turbo pump the chamber down. Keep the foreline open while the turbo is on.
6. Before venting, close the gate. The turbo may keep running behind it.
7. To stop it: gate closed, `turbo off`, wait for it to stop turning, then the
   foreline closed and `hvc stop` (`seal chamber`). LACO's own procedure leaves the
   turbo running between tests, behind the closed gate; whether this bench does is
   open ([progress](../progress.md), Waiting on a decision).

formsLAB's rules hold step 3 differently: they refuse the vacuum valve while the
turbo runs and the foreline while the vacuum valve is open, so a formsLAB plan
roughs first and starts the turbo after. A turbo stored more than 12 months needs a
bearing run-in before use (LACO p. 15). Its speed is not read, so a plan cannot wait
for it to spin up or down; it can only wait a fixed time.

## Thermal

- Two controlled zones: the **platen** (zone 1, the plate the article sits on) and
  the **shroud** (zone 2, the walls around it). Rated -150 to 150 °C and -150 to
  120 °C (LACO p. 18).
- Heating is electric; cooling is liquid nitrogen. A test that goes cold needs the
  supply on and the exhaust vented outside ([safety](safety.md)).
- A zone controls only while its thermal control is on (`hvc platen on`). Off, its
  effective setpoint follows its own temperature.
- A setpoint is confirmed by the controller's echo, not by reading it back
  ([hvc](hvc.md)).
- **Rate control** must be set and turned on *before* thermal control: the HMI does
  not allow it while thermal control is on. It works best for heating and can
  struggle while cooling (HVC p. 31). Whether `hvc platen rate 1` limits the ramp
  from formsLAB is not yet confirmed at the bench.
- After a recipe cycle completes or is reset with the zones outside the vent
  window, the controller starts its own cycle to bring them back into it (HVC
  p. 17).
- **Holding a thermocouple** (`hvc platen 40 at TC01`): formsLAB moves the zone's
  setpoint once a minute, 0.05 times the difference, within 15 °C of the target.
  Both numbers are guesses until run on the bench ([plans](../use/plans.md)).

## Back to air

- Close the gate and vacuum valves; wait for both zones to be 10 to 60 °C; then
  open the vent valve (the `vent` block). After a cold soak, warm the zones first:
  the wait can be long.
- Air onto a cold platen condenses moisture; that is why End never vents and why the
  window has a lower edge.
- LACO advises venting with dry nitrogen (the fill valve), so water does not settle
  on clean walls (LACO p. 26).

## Practice that keeps the chamber working

From LACO's do's and don'ts (LACO p. 25-27):

- Keep the chamber under vacuum when it is not in use. `ambient_to_rest` leaves it
  sealed under rough vacuum for this.
- Nothing plastic, porous, oily or dirty goes in. Clean parts first. No blind
  tapped holes with screws in them: they are virtual leaks.
- Water vapour is the main load. Below 1e-6 Torr the chamber needs a bake-out.
- **Leak or outgassing?** Pump down as far as practical, close every valve, and plot
  the pressure up past 1 Torr. A straight rise is a leak; a rise that slows is
  outgassing. Check what was last changed first.
- The chamber gasket is replaced yearly and cleaned with water only, never solvent;
  gauges are calibrated yearly (LACO p. 30-31).
