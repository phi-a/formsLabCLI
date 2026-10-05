# Orbit files

An orbit file describes one satellite orbit in a `.orbit` file, one classical
element per line. The GUI's Plans tab opens it beside the plans and shows the
satellite where it is now, propagated on the wall clock. A plan can follow it
during a run (In a run, below). It is the first part of the environment tool,
which will go on to the satellite's view factors and background temperatures
(Notebook/gui.md).

```
# orbit: leo_noon -- sun-synchronous, 550 km, noon to midnight
epoch 2026-10-05T12:00:00Z
a 6928 km
e 0.001
i 97.6 deg
raan 191.3 deg
argp 0 deg
nu 0 deg
```

| line | element | range |
|---|---|---|
| `epoch <utc>` | the moment the other six describe, in UTC, ending in `Z` | any |
| `a <semimajor> km` | semi-major axis, from the Earth's centre | at least 6480 km |
| `e <eccentricity>` | eccentricity: 0 is a circle | 0 to 0.99 |
| `i <inclination> deg` | inclination: the plane's tilt from the equator | 0 to 180 |
| `raan <node> deg` | right ascension of the ascending node | 0 to 360 |
| `argp <perigee> deg` | argument of perigee, from the node | 0 to 360 |
| `nu <anomaly> deg` | true anomaly at the epoch, from the perigee | 0 to 360 |

Each element appears once, in any order. `#` starts a comment, on a line of its
own. Words ignore case. The perigee, `a(1 - e)`, must be at least 100 km above
the surface.

A mistake is reported with its line number, as a plan's is:

```
3: expected <semimajor> (>= 6480) after 'a', got '6900km': not a number (write `6900 km`, with a space)
4: e: 1.2 is outside 0..0.99
0: the orbit has no `argp`; every element is needed: epoch, a, e, i, raan, argp, nu
```

## How it moves

The satellite follows Kepler's two-body motion: the Earth is a point mass, so the
orbit is a fixed ellipse. Left out:

- **J2.** The Earth's flattening turns a real orbit's plane. A sun-synchronous orbit
  relies on it to keep its local time; here the plane stays put, so over weeks the
  Sun moves away from it. Move the epoch forward to start it again.
- **Drag** and the Moon and Sun's pull.

The Sun's direction is a low-precision ephemeris (the Astronomical Almanac's). The
umbra is the cone tangent to the Sun and the Earth. Altitude is above a spherical
Earth of radius 6378.1 km.

## In the GUI

On the Plans tab, an orbit file is listed with an ellipse before its name, and
*New orbit* starts one now at the perigee of a 550 km sun-synchronous orbit. Its
lines are drawn in the orbit's colour, each element with a symbol for what it
describes:

| symbol | describes | elements |
|---|---|---|
| ellipse | the size and shape of the orbit | `a`, `e` |
| tilted ellipse | the orbit's plane | `i`, `raan` |
| dot on an ellipse | the place on the orbit | `argp`, `nu` |
| clock | time | `epoch` |

Beside the lines, a panel shows the orbit now, once a second: in sunlight or in
umbra, when that changes, the beta angle, altitude, speed, true anomaly,
argument of latitude, period and time since the epoch. Under them, a strip shows
the coming orbit from now, sunlit or in umbra. The panel follows the text as it
is, unsaved changes included, and says why it shows nothing while the file has a
mistake.

Under them, the panel says how to use the orbit in a plan, and *Use in a plan*
makes one: it follows the orbit and waits for its umbra.

Shipped: `leo_dawn_dusk` (the plane faces the Sun, no eclipse: the hot case) and
`leo_noon` (the Sun in the plane, 35 minutes of umbra every orbit: the cold case).
Names are shared with plans: an orbit cannot take a plan's name.

## In a run

The rScript `rOrbit` follows an orbit file during a run and publishes, once a
second, where the satellite is:

```
# Capture images in the umbra of leo_noon
load rOrbit rSLTA
record every 10 s

orbit follow leo_noon
slta run on
eclipse within 120
log umbra began
sunrise within 60
```

| step | does |
|---|---|
| `orbit follow <orbit>` | puts the satellite where the wall clock does, as the live panel shows it |
| `orbit replay <orbit>` | starts the satellite at the file's epoch at this step, so a test sees the same orbit every time |
| `eclipse within <minutes>` | a block: waits for the satellite to enter the umbra |
| `sunrise within <minutes>` | a block: waits for it to leave the umbra |

The orbit names offered are the orbit files on the plan path. A file that does not
read is refused when the step runs, and the run stops there.

| value | means |
|---|---|
| `InUmbra` | 1 in the umbra, 0 in sunlight |
| `UmbraDuration` | s: in the umbra, its whole length; in sunlight, the next one's; 0 if there is none |
| `UmbraTimeRemaining` | s left in this umbra; 0 in sunlight |
| `NextUmbra` | s until the next umbra begins |
| `OrbitBeta`, `OrbitAltitude` | the beta angle (deg) and altitude (km) |

They are recorded with the run, an `until` can wait on any of them, and rSLTA's
umbra captures (`slta run on`) follow the first three. Until a `follow` or
`replay`, nothing is published.

## In the models

The same file drives the view-factor and environment models:

```python
from formslab.orbit.file import load
from formslab.orbit.geometry import LVLHFixed
from formslab.orbit.thermal.pipeline import CubeSat, catalog, view

orbit = load("plans/leo_noon.orbit")
sat = CubeSat(catalog("6u_double_deployable"))
vl = view(sat.geometry, orbit, LVLHFixed(), facets=["bus_-Z"])
```

The models sweep one orbit by its mean argument of latitude, `argp + M`, which
advances uniformly in time, so their samples are evenly spaced in time. Position,
radius, the Earth's size seen from the satellite, and the umbra at each sample
come from Kepler's equation, so an eccentric orbit sees more of the Earth at
perigee. For the sweep the Sun is held where it is at the epoch (it moves about a
degree a day); the live panel moves it.

The code is `formslab.orbit`: `file` for the file, `propagate.kepler` for the
motion, `propagate.orbit.Orbit` for the sweep.
