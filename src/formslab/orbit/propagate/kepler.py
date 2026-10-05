"""Two-body Kepler motion from classical orbital elements.

The satellite moves on a fixed ellipse: the Earth is a point mass, so there is
no J2 (the plane does not turn, and a sun-synchronous orbit slowly loses its
local time), no drag and no third body. Altitude is above a spherical Earth.
Frames are ECI (J2000-like, the Sun ephemeris's), metres, seconds, radians.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .constants import MU, R_E, R_SUN
from .sun import sun_dist, sun_ra_dec

UMBRA_SAMPLES = 360        # samples per orbit when looking for umbra entry and exit
UMBRA_TOLERANCE_S = 0.1


@dataclass(frozen=True)
class Elements:
    """An orbit at one moment: the six classical elements and their epoch."""
    a: float            # semi-major axis [m]
    e: float            # eccentricity, 0 <= e < 1
    i: float            # inclination [rad]
    raan: float         # right ascension of the ascending node [rad]
    argp: float         # argument of perigee [rad]
    nu: float           # true anomaly at the epoch [rad]
    epoch: datetime     # UTC, timezone-aware

    @property
    def n(self) -> float:
        """Mean motion [rad/s]."""
        return math.sqrt(MU / self.a ** 3)

    @property
    def period(self) -> float:
        """Orbital period [s]."""
        return 2 * math.pi / self.n

    @property
    def perigee_altitude(self) -> float:
        """Lowest altitude above the surface [m]."""
        return self.a * (1 - self.e) - R_E


# --- anomalies ---------------------------------------------------------------------

def mean_from_true(nu: float, e: float) -> float:
    """Mean anomaly [rad] for true anomaly `nu`."""
    E = 2 * math.atan2(math.sqrt(1 - e) * math.sin(nu / 2), math.sqrt(1 + e) * math.cos(nu / 2))
    return E - e * math.sin(E)


def true_from_mean(M: float, e: float) -> float:
    """True anomaly [rad] for mean anomaly `M`, by Newton's method on Kepler's
    equation E - e sin E = M."""
    M = math.remainder(M, 2 * math.pi)
    E = M if e < 0.8 else math.copysign(math.pi, M)
    for _ in range(50):
        step = (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
        E -= step
        if abs(step) < 1e-14:
            break
    return 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))


def true_anomaly(el: Elements, t: datetime) -> float:
    """True anomaly [rad, 0..2pi) at time `t`."""
    M = mean_from_true(el.nu, el.e) + el.n * (t - el.epoch).total_seconds()
    return true_from_mean(M, el.e) % (2 * math.pi)


# --- position and velocity ---------------------------------------------------------

def _perifocal_axes(el: Elements):
    """The perigee direction P and the in-plane normal to it Q, in ECI."""
    cO, sO = math.cos(el.raan), math.sin(el.raan)
    ci, si = math.cos(el.i), math.sin(el.i)
    cw, sw = math.cos(el.argp), math.sin(el.argp)
    P = (cO * cw - sO * sw * ci, sO * cw + cO * sw * ci, sw * si)
    Q = (-cO * sw - sO * cw * ci, -sO * sw + cO * cw * ci, cw * si)
    return P, Q


def state(el: Elements, t: datetime):
    """Position [m] and velocity [m/s] in ECI at time `t`, as two 3-tuples."""
    nu = true_anomaly(el, t)
    p = el.a * (1 - el.e ** 2)
    r = p / (1 + el.e * math.cos(nu))
    x, y = r * math.cos(nu), r * math.sin(nu)
    k = math.sqrt(MU / p)
    vx, vy = -k * math.sin(nu), k * (el.e + math.cos(nu))
    P, Q = _perifocal_axes(el)
    return (tuple(x * P[j] + y * Q[j] for j in range(3)),
            tuple(vx * P[j] + vy * Q[j] for j in range(3)))


def orbit_normal(el: Elements):
    """Unit vector along the angular momentum, in ECI."""
    si = math.sin(el.i)
    return (si * math.sin(el.raan), -si * math.cos(el.raan), math.cos(el.i))


# --- the Sun -----------------------------------------------------------------------

def sun_direction(t: datetime):
    """Unit vector toward the Sun, in ECI."""
    ra, dec = sun_ra_dec(t)
    return (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))


def beta(el: Elements, t: datetime) -> float:
    """Sun beta angle [rad]: the Sun's elevation above the orbit's plane."""
    s, h = sun_direction(t), orbit_normal(el)
    return math.asin(max(-1.0, min(1.0, sum(a * b for a, b in zip(s, h)))))


def in_shadow_cone(r, sun, distance: float) -> bool:
    """True when position `r` [m, ECI] is in the Earth's umbra, for the Sun along
    unit vector `sun` at `distance` [m]: inside the cone tangent to the Sun and
    the Earth, on the night side (the conical model of `orbit.eclipse_half_angle`)."""
    x = sum(a * b for a, b in zip(r, sun))        # along the Sun direction
    if x >= 0:
        return False
    sin_a = (R_SUN - R_E) / distance
    behind_apex = R_E / sin_a + x                  # distance from the cone's apex, toward the Earth
    if behind_apex <= 0:
        return False
    across = math.sqrt(max(0.0, sum(a * a for a in r) - x * x))
    return across < behind_apex * math.tan(math.asin(sin_a))


def in_umbra(r, t: datetime) -> bool:
    """True when position `r` [m, ECI] is in the Earth's umbra at `t`."""
    return in_shadow_cone(r, sun_direction(t), sun_dist(t))


def umbra_at(el: Elements, t: datetime) -> bool:
    return in_umbra(state(el, t)[0], t)


def umbra_spans(el: Elements, t0: datetime, t1: datetime) -> list[tuple[datetime, datetime]]:
    """The stretches of [t0, t1] spent in umbra, each (start, end), clipped to the
    window. Found by sampling, then bisecting each change to a tenth of a second."""
    total = (t1 - t0).total_seconds()
    steps = max(1, math.ceil(total / (el.period / UMBRA_SAMPLES)))
    times = [t0 + timedelta(seconds=total * k / steps) for k in range(steps + 1)]
    shade = [umbra_at(el, t) for t in times]

    def edge(lo: datetime, hi: datetime, lo_shade: bool) -> datetime:
        while (hi - lo).total_seconds() > UMBRA_TOLERANCE_S:
            mid = lo + (hi - lo) / 2
            lo, hi = (mid, hi) if umbra_at(el, mid) == lo_shade else (lo, mid)
        return hi

    spans, start = [], t0 if shade[0] else None
    for k in range(steps):
        if shade[k] != shade[k + 1]:
            at = edge(times[k], times[k + 1], shade[k])
            if shade[k + 1]:
                start = at
            else:
                spans.append((start, at))
                start = None
    if start is not None:
        spans.append((start, t1))
    return spans


# --- what the live panel shows -----------------------------------------------------

def live(el: Elements, now: datetime) -> dict:
    """The orbit at `now`, for the GUI: where the satellite is, whether it is in
    sunlight, and the umbra over the coming orbit. Times ahead are in seconds
    from `now`."""
    r, v = state(el, now)
    nu = true_anomaly(el, now)
    ahead = el.period * 2                          # far enough to find the next change
    spans = [((a - now).total_seconds(), (b - now).total_seconds())
             for a, b in umbra_spans(el, now, now + timedelta(seconds=ahead))]
    shaded = bool(spans) and spans[0][0] <= 0
    following = [s for s in spans if s[0] > 0]
    return {
        "now": now.isoformat().replace("+00:00", "Z"),
        "since_epoch_s": (now - el.epoch).total_seconds(),
        "period_s": el.period,
        "altitude_km": (math.sqrt(sum(c * c for c in r)) - R_E) / 1000,
        "speed_km_s": math.sqrt(sum(c * c for c in v)) / 1000,
        "true_anomaly_deg": math.degrees(nu),
        "latitude_argument_deg": math.degrees((el.argp + nu) % (2 * math.pi)),
        "beta_deg": math.degrees(beta(el, now)),
        "umbra": shaded,
        "umbra_ends_s": spans[0][1] if shaded and spans[0][1] < ahead else None,
        "next_umbra_s": following[0][0] if following else None,
        "spans": [[max(0.0, a), min(el.period, b)] for a, b in spans if a < el.period],
    }
