"""Inertial target visibility: FOV clear-sky budget within eclipse."""

import math
from dataclasses import dataclass

from ..propagate.constants import R_E
from ..propagate.orbit import eclipse_half_angle, mean_motion

# Named targets (J2000 equatorial)
SGR_A_RA_DEG = 266.4168   # deg      Sgr A* right ascension
SGR_A_DEC_DEG = -29.0078  # deg      Sgr A* declination
CYG_X1_RA_DEG = 299.5903  # deg      Cygnus X-1 right ascension
CYG_X1_DEC_DEG = 35.2016  # deg      Cygnus X-1 declination

# ---------------------------------------------------------------------------
# Geometric visibility states
# ---------------------------------------------------------------------------
# Four mutually exclusive conditions based purely on arc membership:
#
#   SUNLIT     — satellite outside the umbra arc (no eclipse)
#   OCCULTED   — in eclipse; target centre behind the Earth limb
#   PARTIAL    — in eclipse; target visible but FOV intersects the Earth limb
#   OBSERVABLE — in eclipse; entire FOV clears the Earth limb
#
SUNLIT     = "sunlit"
OCCULTED   = "occulted"
PARTIAL    = "partial"
OBSERVABLE = "observable"


@dataclass(frozen=True)
class Target:
    """Inertial celestial target in J2000 equatorial coordinates."""
    ra_rad: float
    dec_rad: float
    name: str = ""


@dataclass(frozen=True)
class VisibilityGeometry:
    """Orbit-phase visibility arcs for one target and one orbit geometry."""

    omega_rad: float
    mean_motion_rad_s: float
    beta_sun_rad: float
    uc_sun_rad: float
    umbra_center_rad: float
    umbra_half_rad: float
    beta_target_rad: float
    uc_target_rad: float
    target_visible_half_rad: float
    target_clear_half_rad: float


# Galactic center (Sgr A*) J2000
GALACTIC_CENTER = Target(
    ra_rad=math.radians(SGR_A_RA_DEG),
    dec_rad=math.radians(SGR_A_DEC_DEG),
    name="Galactic Center",
)

# Cygnus X-1 J2000
CYGNUSX1 = Target(
    ra_rad=math.radians(CYG_X1_RA_DEG),
    dec_rad=math.radians(CYG_X1_DEC_DEG),
    name="Cygnus X-1",
)


def target_beta_uc(
    i: float, omega: float, target: Target
) -> tuple[float, float]:
    """Beta angle and culmination argument-of-latitude for an inertial target.

    Parameters
    ----------
    i : float
        Orbit inclination (rad).
    omega : float
        RAAN (rad).
    target : Target
        Celestial target coordinates.

    Returns
    -------
    beta : float
        Target beta angle (rad), in [-π/2, π/2].
    uc : float
        Argument of latitude of target culmination (rad).
    """
    sd, cd = math.sin(target.dec_rad), math.cos(target.dec_rad)
    si, ci = math.sin(i), math.cos(i)
    d_ra = omega - target.ra_rad

    sin_beta = si * cd * math.sin(d_ra) + ci * sd
    beta = math.asin(max(-1.0, min(1.0, sin_beta)))

    g_x = cd * math.cos(target.ra_rad - omega)
    g_y = ci * cd * math.sin(target.ra_rad - omega) + si * sd
    uc = math.atan2(g_y, g_x)

    return beta, uc


def clear_half_angle(
    a: float, beta_target: float, fov_half: float
) -> float:
    """Half-angle of the arc where the target FOV clears the Earth limb.

    Parameters
    ----------
    a : float
        Semi-major axis (m).
    beta_target : float
        Target beta angle (rad).
    fov_half : float
        Half-cone angle of the instrument FOV (rad).

    Returns
    -------
    lam : float
        Half-angle (rad) of the clear-sky arc, in [0, π].
    """
    # Minimum elevation for the FOV edge to clear the limb
    e_limb = -math.acos(R_E / a)
    e_req = e_limb + fov_half

    cos_beta = math.cos(beta_target)
    if cos_beta == 0.0:
        # Target exactly in orbit plane — degenerate
        return 0.0

    ratio = math.sin(e_req) / cos_beta
    if ratio <= -1.0:
        return math.pi  # always clear
    if ratio >= 1.0:
        return 0.0       # never clear
    return math.acos(ratio)


def arc_margin(u: float, center: float, half_width: float) -> float:
    """Signed angular margin to a wrapped S1 arc boundary."""

    if half_width <= 0.0:
        return -math.inf
    if half_width >= math.pi:
        return math.pi
    return half_width - abs(math.remainder(u - center, 2.0 * math.pi))


def visibility_geometry(
    a: float,
    i: float,
    omega: float,
    target: Target,
    fov_half: float,
    beta_sun: float,
    uc_sun: float,
) -> VisibilityGeometry:
    """Build the visibility arcs used by image and thermal routines."""

    beta_tgt, uc_tgt = target_beta_uc(i, omega, target)
    return VisibilityGeometry(
        omega_rad=omega,
        mean_motion_rad_s=mean_motion(a),
        beta_sun_rad=beta_sun,
        uc_sun_rad=uc_sun,
        umbra_center_rad=uc_sun + math.pi,
        umbra_half_rad=eclipse_half_angle(a, beta_sun),
        beta_target_rad=beta_tgt,
        uc_target_rad=uc_tgt,
        target_visible_half_rad=clear_half_angle(a, beta_tgt, 0.0),
        target_clear_half_rad=clear_half_angle(a, beta_tgt, fov_half),
    )


def arc_intersection(
    c1: float, h1: float, c2: float, h2: float
) -> float:
    """Angular overlap (rad) of two arcs on a circle.

    Each arc is defined by a center and a half-width.  The arcs wrap
    at 2π.  Returns the overlap angle in [0, 2π].

    Parameters
    ----------
    c1, h1 : float
        Center and half-width of arc 1 (rad).
    c2, h2 : float
        Center and half-width of arc 2 (rad).
    """
    if h1 >= math.pi:
        return 2.0 * h2
    if h2 >= math.pi:
        return 2.0 * h1

    # Angular distance between centers, wrapped to [0, π]
    d = abs(math.remainder(c1 - c2, 2 * math.pi))

    if d >= h1 + h2:
        return 0.0            # disjoint
    if d <= abs(h1 - h2):
        return 2.0 * min(h1, h2)  # one contained in the other
    return h1 + h2 - d        # partial overlap


def open_sky_budget(
    a: float,
    i: float,
    omega: float,
    target: Target,
    fov_half: float,
    beta_sun: float,
    uc_sun: float,
) -> float:
    """Usable science time (s): intersection of umbra and target-clear arcs.

    Parameters
    ----------
    a : float
        Semi-major axis (m).
    i : float
        Inclination (rad).
    omega : float
        RAAN (rad).
    target : Target
        Celestial target.
    fov_half : float
        Instrument half-cone FOV (rad).
    beta_sun : float
        Solar beta angle (rad), pre-computed.
    uc_sun : float
        Solar culmination argument of latitude (rad), pre-computed.

    Returns
    -------
    float
        Open-sky budget in seconds.
    """
    geom = visibility_geometry(a, i, omega, target, fov_half, beta_sun, uc_sun)
    if geom.umbra_half_rad == 0.0:
        return 0.0

    if geom.target_clear_half_rad == 0.0:
        return 0.0

    overlap = arc_intersection(
        geom.umbra_center_rad,
        geom.umbra_half_rad,
        geom.uc_target_rad,
        geom.target_clear_half_rad,
    )
    return overlap / geom.mean_motion_rad_s


def visibility_state(
    u: float,
    umbra_c: float,
    nu: float,
    uc_tgt: float,
    lam_vis: float,
    lam_clear: float,
) -> str:
    """Classify one orbit position into a geometric visibility state.

    Parameters
    ----------
    u : float
        Current argument of latitude (rad).
    umbra_c : float
        Centre of umbra arc = uc_sun + π (rad), the anti-solar direction.
    nu : float
        Umbra half-angle (rad).
    uc_tgt : float
        Target culmination argument of latitude (rad).
    lam_vis : float
        Half-angle (rad) where the target centre clears the Earth limb
        (= ``clear_half_angle(a, beta_tgt, fov_half=0)``).
    lam_clear : float
        Half-angle (rad) where the entire FOV clears the Earth limb
        (= ``clear_half_angle(a, beta_tgt, fov_half)``).

    Returns
    -------
    str
        One of SUNLIT, OCCULTED, PARTIAL, OBSERVABLE.
    """
    in_umbra = nu > 0 and abs(math.remainder(u - umbra_c, 2 * math.pi)) < nu
    if not in_umbra:
        return SUNLIT

    in_vis = lam_vis > 0 and abs(math.remainder(u - uc_tgt, 2 * math.pi)) < lam_vis
    if not in_vis:
        return OCCULTED

    in_clear = lam_clear > 0 and abs(math.remainder(u - uc_tgt, 2 * math.pi)) < lam_clear
    if not in_clear:
        return PARTIAL

    return OBSERVABLE
