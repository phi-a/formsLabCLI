"""Body-frame face normals shared by the geometry and view-factor models."""

import numpy as np

# -- Body face normals in body frame --------------------------------------
FACES = {
    '+X': np.array([1.0, 0.0, 0.0]),    # velocity / ram
    '-X': np.array([-1.0, 0.0, 0.0]),   # wake
    '+Y': np.array([0.0, 1.0, 0.0]),    # port / orbit normal
    '-Y': np.array([0.0, -1.0, 0.0]),   # starboard
    '+Z': np.array([0.0, 0.0, 1.0]),    # zenith
    '-Z': np.array([0.0, 0.0, -1.0]),   # nadir
}
