"""Orbit geometry configuration.

Single source of truth: orbital elements + epoch -> all derived geometry
(beta angles, eclipse model, LVLH/ECI frames). Sun ephemeris and constants
come from ``sun`` and ``constants``.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from .constants import AU, J2, MU, R_E, R_SUN  # noqa: F401  (re-exported)
from .sun import sun_ra_dec

# -- LVLH convention -----------------------------------------------------
#   index 0 = along-track  (T, velocity)
#   index 1 = cross-track  (W, orbit normal)
#   index 2 = radial       (R, zenith)
NADIR = np.array([0.0, 0.0, -1.0])


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


# -- Direction in LVLH ---------------------------------------------------

def direction(beta, uc, u):
    """Unit vector in LVLH (T, W, R) at argument of latitude *u*.

    Parameters
    ----------
    beta : float  beta angle of the direction [rad]
    uc   : float  argument of latitude of culmination [rad]
    u    : float  spacecraft argument of latitude [rad]
    """
    cb = math.cos(beta)
    du = u - uc
    return np.array([-cb * math.sin(du), math.sin(beta), cb * math.cos(du)])


# -- Orbit dataclass ------------------------------------------------------

@dataclass(frozen=True)
class Orbit:
    """Immutable orbit geometry.  Single configuration point.

    Construct via ``Orbit.from_epoch(...)`` for automatic Sun/target
    geometry, or directly for precomputed values.
    """
    a:        float          # semi-major axis [m]
    i:        float          # inclination [rad]
    omega:    float          # RAAN [rad]
    H:        float          # normalised altitude  a / R_E
    rho:      float          # Earth angular radius from S/C [rad]
    n:        float          # mean motion [rad/s]
    beta_sun: float          # Sun beta angle [rad]
    uc_sun:   float          # Sun culmination arg-of-lat [rad]
    nu:       float          # eclipse half-angle [rad]
    beta_tgt: float = None   # target beta angle [rad]
    uc_tgt:   float = None   # target culmination [rad]
    epoch:    object = None  # reference epoch (informational)

    # -- factory -----------------------------------------------------------

    @staticmethod
    def from_epoch(a, i, omega, epoch, target_radec=None):
        """Construct from orbital elements and UTC epoch.

        Parameters
        ----------
        a     : float     semi-major axis [m]
        i     : float     inclination [rad]
        omega : float     RAAN [rad]
        epoch : datetime  UTC epoch for Sun ephemeris
        target_radec : tuple (ra_rad, dec_rad), optional
        """
        ra_s, dec_s = sun_ra_dec(epoch)
        bs, us = beta_uc(i, omega, ra_s, dec_s)
        nu = eclipse_half_angle(a, bs)

        bt, ut = None, None
        if target_radec is not None:
            bt, ut = beta_uc(i, omega, *target_radec)

        return Orbit(
            a=a, i=i, omega=omega,
            H=a / R_E,
            rho=math.asin(R_E / a),
            n=math.sqrt(MU / a**3),
            beta_sun=bs, uc_sun=us, nu=nu,
            beta_tgt=bt, uc_tgt=ut,
            epoch=epoch,
        )

    # -- direction queries -------------------------------------------------

    def sun_dir(self, u):
        """Sun direction in LVLH at argument of latitude *u*."""
        return direction(self.beta_sun, self.uc_sun, u)

    def target_dir(self, u):
        """Target direction in LVLH at argument of latitude *u*."""
        return direction(self.beta_tgt, self.uc_tgt, u)

    def in_eclipse(self, u):
        """True if *u* falls within the eclipse (umbra) arc."""
        if self.nu <= 0:
            return False
        return abs(math.remainder(u - self.uc_sun - math.pi, 2 * math.pi)) < self.nu

    @property
    def period(self):
        """Orbital period [s]."""
        return 2 * math.pi / self.n

    def utc_at(self, u):
        """UTC datetime(s) at argument of latitude u [rad].

        Convention: u = 0 at ``self.epoch``. The orbit propagates uniformly
        at mean motion ``self.n`` (rad/s), so ``t = epoch + u / n``.

        Parameters
        ----------
        u : float or array-like
            Argument of latitude [rad]. Need not be wrapped to [0, 2π);
            negative values map to times before the epoch and values
            beyond 2π map to subsequent orbits.

        Returns
        -------
        datetime  if ``u`` is a scalar
        list[datetime]  if ``u`` is array-like
        """
        if self.epoch is None:
            raise ValueError("Orbit has no epoch — cannot compute utc_at()")
        is_scalar = np.ndim(u) == 0
        arr = np.atleast_1d(np.asarray(u, dtype=float))
        out = [self.epoch + timedelta(seconds=float(s / self.n)) for s in arr]
        return out[0] if is_scalar else out

    # -- ECI vector queries ------------------------------------------------

    def r_hat_eci(self, u):
        """Radial (zenith) unit vector in ECI at argument of latitude *u*."""
        cu, su = math.cos(u), math.sin(u)
        cO, sO = math.cos(self.omega), math.sin(self.omega)
        ci, si = math.cos(self.i),     math.sin(self.i)
        return np.array([
            cu * cO - su * ci * sO,
            cu * sO + su * ci * cO,
            su * si,
        ])

    def nadir_eci(self, u):
        """Nadir (toward Earth centre) unit vector in ECI."""
        return -self.r_hat_eci(u)

    def v_hat_eci(self, u):
        """Along-track (velocity) unit vector in ECI at argument *u*."""
        cu, su = math.cos(u), math.sin(u)
        cO, sO = math.cos(self.omega), math.sin(self.omega)
        ci, si = math.cos(self.i),     math.sin(self.i)
        return np.array([
            -su * cO - cu * ci * sO,
            -su * sO + cu * ci * cO,
             cu * si,
        ])

    @property
    def h_hat_eci(self):
        """Orbit-normal (angular momentum) unit vector in ECI."""
        cO, sO = math.cos(self.omega), math.sin(self.omega)
        si, ci = math.sin(self.i),     math.cos(self.i)
        return np.array([si * sO, -si * cO, ci])

    def sun_eci(self):
        """Sun direction unit vector in ECI (constant over one orbit)."""
        if self.epoch is None:
            raise ValueError("Orbit has no epoch — cannot compute sun_eci()")
        ra, dec = sun_ra_dec(self.epoch)
        cd = math.cos(dec)
        return np.array([math.cos(ra) * cd, math.sin(ra) * cd, math.sin(dec)])

    def eci_from_lvlh(self, u):
        """3×3 rotation matrix mapping LVLH vectors to ECI at argument *u*.

        Columns: [v_hat, h_hat, r_hat] — the LVLH (T, W, R) axes in ECI.
        """
        return np.column_stack([self.v_hat_eci(u), self.h_hat_eci, self.r_hat_eci(u)])
