"""Kepler motion from classical orbital elements, with the Earth's J2 drift.

The satellite moves on a Kepler ellipse whose node, perigee and mean anomaly
drift at J2's secular rates: the Earth's flattening turns the orbit's plane, so
a sun-synchronous orbit keeps its local time. Left out: J2's short-period
wobble (kilometres), drag and the Moon's and Sun's pull. Altitude is above a
spherical Earth. Frames are ECI (J2000-like, the Sun ephemeris's), metres,
seconds, radians.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .constants import J2, MU, R_E, R_SUN
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
    j2: bool = True     # drift node, perigee and mean anomaly at J2's secular rates

    @property
    def n(self) -> float:
        """Mean motion of the Kepler ellipse [rad/s]."""
        return math.sqrt(MU / self.a ** 3)

    @property
    def period(self) -> float:
        """Orbital period [s]."""
        return 2 * math.pi / self.n

    @property
    def perigee_altitude(self) -> float:
        """Lowest altitude above the surface [m]."""
        return self.a * (1 - self.e) - R_E

    @property
    def rates(self) -> tuple[float, float, float]:
        """How fast the node, the perigee and the mean anomaly move [rad/s]: J2's
        first-order secular rates, or 0, 0, n without J2."""
        if not self.j2:
            return 0.0, 0.0, self.n
        k = 1.5 * J2 * (R_E / (self.a * (1 - self.e ** 2))) ** 2 * self.n
        s2 = math.sin(self.i) ** 2
        return (-k * math.cos(self.i),
                k * (2 - 2.5 * s2),
                self.n + k * math.sqrt(1 - self.e ** 2) * (1 - 1.5 * s2))

    def at(self, t: datetime) -> "Elements":
        """The same orbit with its epoch moved to `t`: node, perigee and anomaly
        carried there."""
        dt = (t - self.epoch).total_seconds()
        raan_dot, argp_dot, m_dot = self.rates
        M = mean_from_true(self.nu, self.e) + m_dot * dt
        return replace(self, raan=(self.raan + raan_dot * dt) % (2 * math.pi),
                       argp=(self.argp + argp_dot * dt) % (2 * math.pi),
                       nu=true_from_mean(M, self.e) % (2 * math.pi), epoch=t)


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
    return el.at(t).nu


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
    """Position [m] and velocity [m/s] in ECI at time `t`, as two 3-tuples: on
    the ellipse the elements describe at `t`."""
    now = el.at(t)
    nu = now.nu
    p = now.a * (1 - now.e ** 2)
    r = p / (1 + now.e * math.cos(nu))
    x, y = r * math.cos(nu), r * math.sin(nu)
    k = math.sqrt(MU / p)
    vx, vy = -k * math.sin(nu), k * (now.e + math.cos(nu))
    P, Q = _perifocal_axes(now)
    return (tuple(x * P[j] + y * Q[j] for j in range(3)),
            tuple(vx * P[j] + vy * Q[j] for j in range(3)))


def orbit_normal(el: Elements):
    """Unit vector along the angular momentum, in ECI, at the elements' epoch."""
    si = math.sin(el.i)
    return (si * math.sin(el.raan), -si * math.cos(el.raan), math.cos(el.i))


# --- the Sun -----------------------------------------------------------------------

def sun_direction(t: datetime):
    """Unit vector toward the Sun, in ECI."""
    ra, dec = sun_ra_dec(t)
    return (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))


def beta(el: Elements, t: datetime) -> float:
    """Sun beta angle [rad] at `t`: the Sun's elevation above the orbit's plane."""
    s, h = sun_direction(t), orbit_normal(el.at(t))
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


# --- where the satellite is: the live panel and rOrbit -----------------------------

def umbra_window(el: Elements, t: datetime):
    """Umbra spans from one period before `t` to three after, and the time after
    which they no longer reach far enough: an umbra under way at `t`, and the
    next one, are always whole in them. Found once, used until then."""
    period = timedelta(seconds=el.period)
    return umbra_spans(el, t - period, t + 3 * period), t + period


def situation(el: Elements, t: datetime, spans) -> dict:
    """Whether the satellite is in umbra at `t` and for how long, given the spans
    `umbra_window` found around `t`: what the live panel shows and rOrbit
    publishes. Times are seconds; `next_umbra_s` is None when there is none."""
    current = next(((a, b) for a, b in spans if a <= t < b), None)
    coming = next(((a, b) for a, b in spans if a > t), None)
    shown = current or coming
    r, _ = state(el, t)
    return {
        "in_umbra": current is not None,
        # In umbra, this umbra's whole length; in sunlight, the next one's (rSLTA
        # sets its exposure from it ahead of time); 0 when there is none.
        "umbra_duration_s": (shown[1] - shown[0]).total_seconds() if shown else 0.0,
        "umbra_left_s": (current[1] - t).total_seconds() if current else 0.0,
        "next_umbra_s": (coming[0] - t).total_seconds() if coming else None,
        "beta_deg": math.degrees(beta(el, t)),
        "altitude_km": (math.sqrt(sum(c * c for c in r)) - R_E) / 1000,
    }


def live(el: Elements, now: datetime) -> dict:
    """The orbit at `now`, for the GUI's live panel: `situation`, and the speed,
    the anomalies, and the umbra over the coming orbit (`spans`, seconds from now)."""
    spans, _ = umbra_window(el, now)
    s = situation(el, now, spans)
    here = el.at(now)
    _, v = state(el, now)
    ahead = [((a - now).total_seconds(), (b - now).total_seconds()) for a, b in spans]
    return {
        "now": now.isoformat().replace("+00:00", "Z"),
        "since_epoch_s": (now - el.epoch).total_seconds(),
        "period_s": el.period,
        "altitude_km": s["altitude_km"],
        "speed_km_s": math.sqrt(sum(c * c for c in v)) / 1000,
        "true_anomaly_deg": math.degrees(here.nu),
        "latitude_argument_deg": math.degrees((here.argp + here.nu) % (2 * math.pi)),
        "beta_deg": s["beta_deg"],
        "umbra": s["in_umbra"],
        "umbra_duration_s": s["umbra_duration_s"],
        "umbra_ends_s": s["umbra_left_s"] if s["in_umbra"] else None,
        "next_umbra_s": s["next_umbra_s"],
        "spans": [[max(0.0, a), min(el.period, b)] for a, b in ahead if b > 0 and a < el.period],
    }
