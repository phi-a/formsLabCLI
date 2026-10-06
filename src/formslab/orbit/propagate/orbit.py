"""Orbit geometry: the `Orbit` the attitude, view-factor and thermal models
sweep, and the circular-orbit formulas (beta, eclipse arc, J2 RAAN drift, LTAN)
that visibility and imaging use.

`Orbit` is built from Keplerian elements (``kepler.Elements``); the Sun ephemeris
and constants come from ``sun`` and ``constants``.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property

import numpy as np

from .constants import AU, J2, MU, R_E, R_SUN  # noqa: F401  (re-exported)
from .kepler import Elements, in_shadow_cone, mean_from_true, true_from_mean
from .sun import sun_dist, sun_ra_dec

# -- LVLH convention -----------------------------------------------------
#   index 0 = along-track  (T, in the plane, perpendicular to the radius)
#   index 1 = cross-track  (W, orbit normal)
#   index 2 = radial       (R, zenith)


def beta_uc(i, omega, ra, dec):
    """Beta angle and culmination argument of latitude for any inertial direction.

    Parameters
    ----------
    i     : float  orbit inclination [rad]
    omega : float  RAAN [rad]
    ra    : float  right ascension of direction [rad]
    dec   : float  declination of direction [rad]

    Returns
    -------
    beta : float  beta angle [rad]
    uc   : float  culmination argument of latitude [rad]
    """
    sd, cd = math.sin(dec), math.cos(dec)
    si, ci = math.sin(i),   math.cos(i)
    sin_beta = si * cd * math.sin(omega - ra) + ci * sd
    beta = math.asin(max(-1.0, min(1.0, sin_beta)))
    gx = cd * math.cos(ra - omega)
    gy = ci * cd * math.sin(ra - omega) + si * sd
    uc = math.atan2(gy, gx)
    return beta, uc


def mean_motion(a: float) -> float:
    """Mean motion n (rad/s) for semi-major axis a (m)."""
    return math.sqrt(MU / a**3)


def j2_raan_rate(a: float, e: float, i: float) -> float:
    """Secular J2 RAAN drift rate (rad/s)."""
    n = mean_motion(a)
    return -1.5 * J2 * n * (R_E / a) ** 2 * math.cos(i) / (1 - e**2) ** 2


def propagate_raan(omega0: float, omega_dot: float, dt_seconds: float) -> float:
    """Propagate RAAN (rad) over dt_seconds."""
    return omega0 + omega_dot * dt_seconds


def raan_for_ltan(dt: datetime, ltan_hours: float) -> float:
    """Return the RAAN (rad) that yields the requested LTAN at *dt*."""
    ra_sun, _ = sun_ra_dec(dt)
    return (ra_sun + math.radians(15.0 * (ltan_hours - 12.0))) % (2 * math.pi)


def ltan_for_raan(dt: datetime, omega: float) -> float:
    """Return the LTAN (hours) corresponding to RAAN *omega* at *dt*."""
    ra_sun, _ = sun_ra_dec(dt)
    delta_deg = math.degrees((omega - ra_sun) % (2 * math.pi))
    return (12.0 + delta_deg / 15.0) % 24.0


def beta_angle(i: float, omega: float, dt: datetime) -> float:
    """Compute Sun beta angle (rad) for orbit plane normal vs Sun direction."""
    ra_sun, dec_sun = sun_ra_dec(dt)
    beta, _ = beta_uc(i, omega, ra_sun, dec_sun)
    return beta


def eclipse_half_angle(a: float, beta: float, D: float = AU) -> float:
    """Eclipse half-angle (rad), conical umbra model. Returns 0 if no eclipse."""
    k, eps = R_E / a, (R_SUN - R_E) / D
    cos_beta = abs(math.cos(beta))
    if cos_beta < 1e-15:
        return 0.0
    arg = (k * eps + math.sqrt((1 - k**2) * (1 - eps**2))) / cos_beta
    return 0.0 if arg >= 1.0 else math.acos(arg)


def eclipse_duration(a: float, beta: float, D: float = AU) -> float:
    """Eclipse duration (seconds) for circular orbit. Returns 0 if no eclipse."""
    nu = eclipse_half_angle(a, beta, D)
    if nu == 0.0:
        return 0.0
    return 2.0 * nu / mean_motion(a)


def sun_beta_uc(i: float, omega: float, dt: datetime) -> tuple[float, float]:
    """Sun beta angle and argument-of-latitude of culmination."""
    ra_sun, dec_sun = sun_ra_dec(dt)
    return beta_uc(i, omega, ra_sun, dec_sun)


# -- Orbit ---------------------------------------------------------------

@dataclass(frozen=True)
class Orbit:
    """One orbit, swept once, for the attitude, view-factor and thermal models.

    It is built from Keplerian elements (``propagate.kepler.Elements``, as an
    orbit file gives them) and is swept by one parameter, ``u``: the *mean*
    argument of latitude, ``argp + M``. It advances uniformly in time, so a grid
    in u is a grid in time, ``t = epoch + (u - u0) / n``. For a circular orbit it
    is the argument of latitude itself. Position, radius, the Earth's size and the
    umbra at each u come from Kepler's equation.

    The sweep is one orbit of two-body motion from the epoch: in an orbit J2
    turns the plane, and the Sun moves, about 0.07 degrees, so both are held where
    they are at the epoch; eclipse is the umbra cone at that Sun distance. For
    another date, sweep ``Orbit(elements.at(t))``: the elements J2 has carried
    there. Hashable, so laws can cache per orbit.
    """
    elements: Elements

    # -- construction -------------------------------------------------------

    @staticmethod
    def from_epoch(a, i, omega, epoch):
        """A circular orbit at the ascending node at `epoch` (u = 0 there).

        a [m], i and omega (RAAN) [rad], epoch a UTC datetime.
        """
        return Orbit(Elements(a=a, e=0.0, i=i, raan=omega, argp=0.0, nu=0.0, epoch=epoch))

    # -- the elements -------------------------------------------------------

    @property
    def a(self):
        return self.elements.a

    @property
    def e(self):
        return self.elements.e

    @property
    def i(self):
        return self.elements.i

    @property
    def raan(self):
        return self.elements.raan

    @property
    def epoch(self):
        return self.elements.epoch

    @property
    def n(self):
        """Mean motion [rad/s]."""
        return self.elements.n

    @property
    def period(self):
        """Orbital period [s]."""
        return self.elements.period

    @cached_property
    def u0(self):
        """The mean argument of latitude at the epoch [rad]."""
        return self.elements.argp + mean_from_true(self.elements.nu, self.e)

    # -- along the sweep ----------------------------------------------------

    def true_latitude(self, u):
        """Argument of latitude (argp + true anomaly) [rad] at sweep parameter `u`."""
        if self.e == 0.0:
            return u
        return self.elements.argp + true_from_mean(u - self.elements.argp, self.e)

    def radius(self, u):
        """Distance from the Earth's centre [m] at `u`."""
        if self.e == 0.0:
            return self.a
        nu = self.true_latitude(u) - self.elements.argp
        return self.a * (1 - self.e ** 2) / (1 + self.e * math.cos(nu))

    def rho(self, u):
        """The Earth's angular radius seen from the spacecraft [rad] at `u`."""
        return math.asin(R_E / self.radius(u))

    def utc_at(self, u):
        """UTC datetime(s) at `u`: ``epoch + (u - u0) / n``.

        Parameters
        ----------
        u : float or array-like
            Need not be wrapped to [0, 2π); values before u0 map to times before
            the epoch and values beyond 2π to later orbits.

        Returns
        -------
        datetime  if ``u`` is a scalar
        list[datetime]  if ``u`` is array-like
        """
        if self.epoch is None:
            raise ValueError("Orbit has no epoch — cannot compute utc_at()")
        is_scalar = np.ndim(u) == 0
        arr = np.atleast_1d(np.asarray(u, dtype=float))
        out = [self.epoch + timedelta(seconds=float((s - self.u0) / self.n)) for s in arr]
        return out[0] if is_scalar else out

    # -- the Sun, frozen at the epoch ----------------------------------------

    @cached_property
    def _sun(self):
        return sun_ra_dec(self.epoch)

    @property
    def beta_sun(self):
        """Sun beta angle [rad]."""
        return beta_uc(self.i, self.raan, *self._sun)[0]

    @property
    def uc_sun(self):
        """Argument of latitude at which the Sun culminates [rad]."""
        return beta_uc(self.i, self.raan, *self._sun)[1]

    @cached_property
    def sun_distance(self):
        """Earth-Sun distance at the epoch [m]."""
        return sun_dist(self.epoch)

    def sun_eci(self):
        """Sun direction unit vector in ECI (constant over the sweep)."""
        if self.epoch is None:
            raise ValueError("Orbit has no epoch — cannot compute sun_eci()")
        ra, dec = self._sun
        cd = math.cos(dec)
        return np.array([math.cos(ra) * cd, math.sin(ra) * cd, math.sin(dec)])

    def in_eclipse(self, u):
        """True if the spacecraft is in the Earth's umbra at `u`."""
        return in_shadow_cone(self.position_eci(u), self.sun_eci(), self.sun_distance)

    @cached_property
    def eclipse_arcs(self):
        """The umbra over one sweep, as ((entry, exit), ...) in u [rad]: entry in
        [0, 2π), exit after it (past 2π when the arc wraps). Found by sampling,
        then bisection."""
        steps = 720
        grid = [2 * math.pi * k / steps for k in range(steps + 1)]
        shade = [self.in_eclipse(u) for u in grid]

        def edge(lo, hi, lo_shade):
            while hi - lo > 1e-12:
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if self.in_eclipse(mid) == lo_shade else (lo, mid)
            return hi

        entries = [edge(grid[k], grid[k + 1], False) for k in range(steps) if not shade[k] and shade[k + 1]]
        exits = [edge(grid[k], grid[k + 1], True) for k in range(steps) if shade[k] and not shade[k + 1]]
        arcs = []
        for start in entries:
            after = [x for x in exits if x > start] or [x + 2 * math.pi for x in exits]
            if after:
                arcs.append((start % (2 * math.pi), min(after)))
        return tuple(sorted(arcs))

    # -- ECI vector queries ----------------------------------------------------

    def r_hat_eci(self, u):
        """Radial (zenith) unit vector in ECI at `u`."""
        lat = self.true_latitude(u)
        cu, su = math.cos(lat), math.sin(lat)
        cO, sO = math.cos(self.raan), math.sin(self.raan)
        ci, si = math.cos(self.i),     math.sin(self.i)
        return np.array([
            cu * cO - su * ci * sO,
            cu * sO + su * ci * cO,
            su * si,
        ])

    def position_eci(self, u):
        """Position [m] in ECI at `u`."""
        return self.radius(u) * self.r_hat_eci(u)

    def nadir_eci(self, u):
        """Nadir (toward Earth centre) unit vector in ECI."""
        return -self.r_hat_eci(u)

    def v_hat_eci(self, u):
        """Along-track unit vector in ECI at `u`: in the plane, perpendicular to
        the radius (the velocity direction on a circular orbit)."""
        lat = self.true_latitude(u)
        cu, su = math.cos(lat), math.sin(lat)
        cO, sO = math.cos(self.raan), math.sin(self.raan)
        ci, si = math.cos(self.i),     math.sin(self.i)
        return np.array([
            -su * cO - cu * ci * sO,
            -su * sO + cu * ci * cO,
             cu * si,
        ])

    @property
    def h_hat_eci(self):
        """Orbit-normal (angular momentum) unit vector in ECI."""
        cO, sO = math.cos(self.raan), math.sin(self.raan)
        si, ci = math.sin(self.i),     math.cos(self.i)
        return np.array([si * sO, -si * cO, ci])

    def eci_from_lvlh(self, u):
        """3×3 rotation matrix mapping LVLH vectors to ECI at `u`.

        Columns: [v_hat, h_hat, r_hat] — the LVLH (T, W, R) axes in ECI.
        """
        return np.column_stack([self.v_hat_eci(u), self.h_hat_eci, self.r_hat_eci(u)])
