# The LACO TVAC chamber

What the chamber is, as described on 2026-10-03, with the controller's own screen.
Protocol and installed-controller facts are in `docs/HVC3500.md`; this note is the
physical picture the GUI's TVAC viewer follows.

![HMI Manual screen](img/hmi-manual-screen.png)

*The controller's Manual screen (captured at 82.26 Torr, everything off).*

## What it is

LACO built it for the University of Illinois (model FCT3048ELSSSE-1P35531,
delivered February 2026): "a 2 zone thermal vacuum environment for testing
satellites with deployable features." A floor-standing cabinet, about 38" wide,
79" deep and 76" tall (roughly 1 x 2 x 1.9 m), 1,450 lb.

Utilities: 208-240 V single-phase at 40 A; compressed air at 80-120 psi; a
low-pressure process gas supply (under 5 psi); liquid nitrogen at 20-40 psi. It is
rated only to 0.5 psig above atmosphere: a vacuum vessel, not a pressure vessel.

## Thermal side

A sealed vessel with a door or lid, and two controlled thermal surfaces:

| Zone | HMI name | Surface | Rated range |
|---|---|---|---|
| 1 | Cntrl P | **Platen**: the plate the test article sits on | -150 to +150 C |
| 2 | Cntrl S | **Shroud**: the thermal walls around the article | -150 to +120 C |
| 3 | t2 | a third heater block, monitor only | -- |

Firmware zones 4-7 exist but none is controlled. Cooling is liquid nitrogen through
a manifold on the system; its exhaust must be vented out of the building
(suffocation risk). Heating is electric, and a Watlow controller sets the hard
over-temperature trip.

## Vacuum side

Two pumps working as a pair:

- **Roughing pump**, through the HMI's "Vacuum Valve" (our `rough`): atmosphere down
  to about 1 Torr.
- **Turbo pump**, through the gate valve: takes over below the 0.010 Torr crossover
  for high vacuum. It is backed by the roughing pump through the **foreline valve**,
  and starts only once the foreline is at or below 0.2 Torr. This path has not been
  run yet.

## Gas side

A **vent valve** admits air and a **fill valve** admits process gas, both to return
the chamber to atmosphere. The PLC checks temperatures first (vent window 10-60 C).

## Controller

An **HVC-3500** (Unitronics PLC with a touchscreen) runs everything and enforces
the interlocks (for example, no gate valve unless the turbo is running). It has
temperature inputs T0-T15 (T16-T20 unused), a remote I/O network and a separate
high-vacuum enclosure.
