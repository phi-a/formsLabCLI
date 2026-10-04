"""Base attitude laws: u -> SO3 (body-to-ECI rotation).

Each law is a callable ``(u, orbit) -> SO3`` where the returned matrix maps
body-frame vectors to ECI vectors.

Body-frame convention (identity rotation = LVLH-aligned):
    +X = velocity
    +Y = orbit normal
    +Z = zenith
"""

import math

import numpy as np

from .so3 import SO3


class LVLHFixed:
    """Body frame locked to LVLH, expressed in ECI."""
    def __init__(self, R_lvlh=None):
        self._R = R_lvlh if R_lvlh is not None else SO3.identity()

    def __call__(self, u, orbit):
        return SO3(orbit.eci_from_lvlh(u)) @ self._R


class TargetTracking:
    """Boresight (+X) tracks an inertial target, +Z toward nadir.

    Primary  : +X body -> target (exact, in ECI).
    Secondary: +Z body -> nadir_eci(u), best-effort Gram-Schmidt.
    +/-Y normals stay perpendicular to the target-nadir plane. Under the
    scalar Earth-view model this keeps their nadir angle fixed.
    """
    def __init__(self, ra, dec):
        cd = math.cos(dec)
        self._tgt_eci = np.array([
            math.cos(ra) * cd,
            math.sin(ra) * cd,
            math.sin(dec),
        ])

    def __call__(self, u, orbit):
        return SO3.align(self._tgt_eci, orbit.nadir_eci(u))


class SunTracking:
    """Solar panel (+Z) tracks Sun, +X toward zenith as secondary."""
    _swap = SO3.Ry(math.pi / 2)

    def __call__(self, u, orbit):
        return SO3.align(orbit.sun_eci(), orbit.nadir_eci(u)) @ self._swap


class TargetTrackingNadirRoll:
    """Boresight (+X) tracks target, +Y toward nadir."""
    def __init__(self, ra, dec):
        cd = math.cos(dec)
        self._tgt_eci = np.array([
            math.cos(ra) * cd,
            math.sin(ra) * cd,
            math.sin(dec),
        ])

    def __call__(self, u, orbit):
        return SO3.align_y(self._tgt_eci, orbit.nadir_eci(u))


class ModeSwitch:
    """Delegates to different laws based on eclipse state."""
    def __init__(self, eclipse_law, sunlit_law):
        self.eclipse_law = eclipse_law
        self.sunlit_law = sunlit_law

    def __call__(self, u, orbit):
        law = self.eclipse_law if orbit.in_eclipse(u) else self.sunlit_law
        return law(u, orbit)


class InertialDrift:
    """Inertially fixed attitude (constant body-to-ECI rotation)."""
    def __init__(self, R_eci):
        self.R_eci = R_eci

    def __call__(self, u, orbit):
        return self.R_eci
