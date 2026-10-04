"""Approximate Sun ephemeris in ECI (right ascension, declination)."""

import math
from datetime import datetime, timezone

from .constants import (AU, J2000, SUN_L0, SUN_L_RATE, SUN_G0, SUN_G_RATE,
                        SUN_EQ1, SUN_EQ2, OBLIQUITY, OBLIQUITY_RATE,
                        DIST_A0, DIST_E1, DIST_E2)


def julian_date(dt: datetime) -> float:
    """Convert a UTC datetime to Julian Date."""
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    a = (14 - dt.month) // 12
    y = dt.year + 4800 - a
    m = dt.month + 12 * a - 3
    jdn = dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    frac = (dt.hour - 12 + dt.minute / 60 + dt.second / 3600) / 24
    return jdn + frac


def sun_ra_dec(dt: datetime) -> tuple[float, float]:
    """Return Sun right ascension and declination in radians for a UTC datetime.

    Uses the low-precision solar coordinates from the Astronomical Almanac.
    """
    jd = julian_date(dt)
    n = jd - J2000

    # Mean longitude and mean anomaly (degrees)
    L = (SUN_L0 + SUN_L_RATE * n) % 360
    g = math.radians((SUN_G0 + SUN_G_RATE * n) % 360)

    # Ecliptic longitude and obliquity (degrees)
    lam = math.radians(L + SUN_EQ1 * math.sin(g) + SUN_EQ2 * math.sin(2 * g))
    eps = math.radians(OBLIQUITY - OBLIQUITY_RATE * n)

    # Right ascension and declination
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    return ra, dec


def sun_dist(dt: datetime) -> float:
    """Earth-Sun distance (m) for a UTC datetime."""
    jd = julian_date(dt)
    n = jd - J2000
    g = math.radians((SUN_G0 + SUN_G_RATE * n) % 360)
    return AU * (DIST_A0 - DIST_E1 * math.cos(g) - DIST_E2 * math.cos(2 * g))
