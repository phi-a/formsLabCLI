# Orbit files

An orbit file describes one satellite orbit in a `.orbit` file, one classical
element per line. The GUI's Plans tab opens it beside the plans and shows the
satellite where it is now, propagated on the wall clock. It is the first part of
the environment tool, which will go on to the satellite's view factors and
background temperatures (Notebook/gui.md); nothing runs an orbit file as a plan.

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
umbra is the cone tangent to the Sun and the Earth, the model `formslab.orbit`
uses. Altitude is above a spherical Earth of radius 6378.1 km.

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

Shipped: `leo_dawn_dusk` (the plane faces the Sun, no eclipse: the hot case) and
`leo_noon` (the Sun in the plane, 35 minutes of umbra every orbit: the cold case).
Names are shared with plans: an orbit cannot take a plan's name.

The code is `formslab.kepler`: standard library only, so it needs no extra.
