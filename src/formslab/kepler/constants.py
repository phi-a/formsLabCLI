"""Physical constants for orbit, Sun and radiative-environment models.

All constants live here. No module should hardcode these values.
Grouped by domain for readability and C/C++ transcription.
"""

import math

# ── WGS-84 / Earth ──────────────────────────────────────────────
MU = 3.986004418e14       # m^3/s^2  gravitational parameter
R_E = 6.3781e6            # m        equatorial radius
J2 = 1.08263e-3           #          second zonal harmonic

# ── Solar / Astronomical ────────────────────────────────────────
R_SUN = 6.957e8           # m        solar radius
AU = 1.496e11             # m        astronomical unit
J2000 = 2451545.0         #          Julian Date of J2000.0 epoch

# Solar ephemeris (Astronomical Almanac, low-precision)
SUN_L0 = 280.460          # deg      mean longitude at J2000
SUN_L_RATE = 0.9856474    # deg/day  mean longitude rate
SUN_G0 = 357.528          # deg      mean anomaly at J2000
SUN_G_RATE = 0.9856003    # deg/day  mean anomaly rate
SUN_EQ1 = 1.915           # deg      equation of center, 1st harmonic
SUN_EQ2 = 0.020           # deg      equation of center, 2nd harmonic
OBLIQUITY = 23.439        # deg      mean obliquity at J2000
OBLIQUITY_RATE = 4e-7     # deg/day  obliquity precession rate

# Earth-Sun distance coefficients (AU units)
DIST_A0 = 1.00014         #          baseline distance / AU
DIST_E1 = 0.01671         #          eccentricity coefficient, 1st
DIST_E2 = 0.00014         #          eccentricity coefficient, 2nd

# ── Radiative environment ───────────────────────────────────────
S0 = 1361.0               # W/m^2    solar constant (AM0)
J_IR = 240.0              # W/m^2    Earth mean outgoing longwave radiation
A_ALB = 0.30              #          Earth Bond albedo

# ── Time ─────────────────────────────────────────────────────────
SECONDS_PER_DAY = 86400.0
HOURS_PER_DAY = 24.0
