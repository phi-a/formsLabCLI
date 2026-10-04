"""Spacecraft geometry: attitude laws and body shape.

    so3          SO(3) rotation tools
    laws         steady-state attitude laws
    transitions  finite-rate wrappers around base laws
    faces        body-frame face normals
    cubesat      body-fixed CubeSat geometry builders and realizations

Orbit geometry lives in ``propagate``; view factors in ``viewfactor``.
"""

from .so3 import SO3                                                   # noqa: F401
from .laws import (LVLHFixed, TargetTracking, TargetTrackingNadirRoll,  # noqa: F401
                   SunTracking, InertialDrift, ModeSwitch)              # noqa: F401
from .transitions import SlewModeSwitch                                # noqa: F401
from .faces import FACES                                               # noqa: F401
from .cubesat import (CubeSatGeometry, RealizedGeometry, RectFacet,    # noqa: F401
                      FacetNode, build_6u_double_deployable, mount)
