"""Earth-disk quadrature and directional loading helpers.

This module resolves the visible Earth as a spherical cap of viewing
directions about nadir and evaluates directional kernels of the form

    G = (1 / pi) * integral_{Earth disk}
        w(s_hat) * m(s_hat) * max(0, n_face . s_hat) dOmega

where:
    w(s_hat)  sample-dependent radiance / weighting term
    m(s_hat)  face transmission mask in face-local coordinates

For a uniform Lambertian Earth and m == 1, G reduces to the
cosine-weighted solid-angle Earth factor for a differential face.
"""

import math
from dataclasses import dataclass

import numpy as np

from ..propagate.orbit import R_E


def _hat(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def _wrap_pi(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _orthobasis(z_axis):
    """Return a right-handed orthonormal basis with +Z along *z_axis*."""
    z = _hat(z_axis)
    ref = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = np.cross(ref, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    return x, y, z


def _frame(x_axis, y_axis, z_axis):
    """Columns are face-local axes expressed in body coordinates."""
    return np.column_stack([x_axis, y_axis, z_axis])


# Face-local frames: +Z_face is the outward normal for that body face.
# Local +X/+Y span the face plane and provide stable azimuth references.
FACE_LOCAL_FRAMES = {
    '+X': _frame(np.array([0.0,  1.0, 0.0]), np.array([0.0, 0.0, 1.0]), np.array([ 1.0, 0.0, 0.0])),
    '-X': _frame(np.array([0.0, -1.0, 0.0]), np.array([0.0, 0.0, 1.0]), np.array([-1.0, 0.0, 0.0])),
    '+Y': _frame(np.array([-1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), np.array([0.0,  1.0, 0.0])),
    '-Y': _frame(np.array([ 1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), np.array([0.0, -1.0, 0.0])),
    '+Z': _frame(np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0,  1.0])),
    '-Z': _frame(np.array([1.0, 0.0, 0.0]), np.array([0.0, -1.0, 0.0]), np.array([0.0, 0.0, -1.0])),
}


@dataclass(frozen=True)
class AzimuthElevationMask:
    """Simple face-local transmission window.

    Parameters
    ----------
    azimuth_center : float
        Centre azimuth [rad], measured in the face plane from local +X.
    azimuth_half_width : float
        Symmetric azimuth half-width [rad].
    elevation_min, elevation_max : float
        Accepted elevation range [rad]. Elevation is measured above the
        face plane, so the face normal is +pi/2 and the face horizon is 0.
    transmission : float
        Scalar transmission factor applied inside the accepted window.
    """
    azimuth_center: float = 0.0
    azimuth_half_width: float = math.pi
    elevation_min: float = 0.0
    elevation_max: float = math.pi / 2.0
    transmission: float = 1.0

    def __call__(self, dirs_face, azimuth, elevation):
        del dirs_face
        da = _wrap_pi(np.asarray(azimuth, dtype=float) - self.azimuth_center)
        keep = (
            (np.abs(da) <= self.azimuth_half_width)
            & (np.asarray(elevation, dtype=float) >= self.elevation_min)
            & (np.asarray(elevation, dtype=float) <= self.elevation_max)
        )
        return self.transmission * keep.astype(float)


@dataclass(frozen=True)
class EarthDiskSamples:
    """Resolved Earth-disk rays for a single spacecraft state."""
    dirs_eci: np.ndarray
    weights: np.ndarray
    surface_normals_eci: np.ndarray

    def surface_solar_cosine(self, sun_eci):
        """cos(zenith) of the Sun at each sampled Earth surface point."""
        sun = _hat(sun_eci)
        return np.clip(self.surface_normals_eci @ sun, 0.0, None)


@dataclass(frozen=True)
class EarthDiskQuadrature:
    """Midpoint quadrature over the visible Earth disk."""
    rho: float
    local_dirs: np.ndarray
    weights: np.ndarray
    n_mu: int
    n_az: int

    @staticmethod
    def build(rho, n_mu=24, n_az=72):
        """Construct a solid-angle quadrature for a cap of half-angle *rho*."""
        if not (0.0 < rho < math.pi):
            raise ValueError(f"rho must be in (0, pi), got {rho}")
        if n_mu <= 0 or n_az <= 0:
            raise ValueError("n_mu and n_az must be positive")

        mu_min = math.cos(rho)
        dmu = (1.0 - mu_min) / n_mu
        dphi = 2.0 * math.pi / n_az

        mu = mu_min + (np.arange(n_mu, dtype=float) + 0.5) * dmu
        phi = (np.arange(n_az, dtype=float) + 0.5) * dphi
        mu_grid, phi_grid = np.meshgrid(mu, phi, indexing='ij')

        sin_theta = np.sqrt(np.maximum(0.0, 1.0 - mu_grid * mu_grid))
        local_dirs = np.column_stack([
            (sin_theta * np.cos(phi_grid)).reshape(-1),
            (sin_theta * np.sin(phi_grid)).reshape(-1),
            mu_grid.reshape(-1),
        ])
        weights = np.full(local_dirs.shape[0], dmu * dphi, dtype=float)
        return EarthDiskQuadrature(rho=float(rho), local_dirs=local_dirs,
                                   weights=weights, n_mu=int(n_mu),
                                   n_az=int(n_az))

    def sample(self, nadir_eci, radius):
        """Rotate the quadrature onto *nadir_eci* and intersect rays with Earth."""
        x_axis, y_axis, z_axis = _orthobasis(nadir_eci)
        basis = np.column_stack([x_axis, y_axis, z_axis])
        dirs_eci = self.local_dirs @ basis.T

        r_sc = -float(radius) * z_axis
        proj = dirs_eci @ r_sc
        disc = np.maximum(proj * proj - (radius * radius - R_E * R_E), 0.0)
        lam = -proj - np.sqrt(disc)
        pts = r_sc + lam[:, None] * dirs_eci
        surf = pts / R_E
        return EarthDiskSamples(dirs_eci=dirs_eci, weights=self.weights,
                                surface_normals_eci=surf)


def face_coordinates(dirs_body, face):
    """Project body-frame directions into the local coordinates of *face*."""
    if face not in FACE_LOCAL_FRAMES:
        raise KeyError(f"Unknown face label {face!r}")
    dirs_body = np.asarray(dirs_body, dtype=float)
    frame = FACE_LOCAL_FRAMES[face]
    dirs_face = dirs_body @ frame
    azimuth = np.arctan2(dirs_face[:, 1], dirs_face[:, 0])
    elevation = np.arcsin(np.clip(dirs_face[:, 2], -1.0, 1.0))
    return dirs_face, azimuth, elevation


def integrate_face_response(dirs_face, solid_angle_weights,
                            mask=None, sample_weight=None):
    """Evaluate (1/pi) * integral mask * weight * cos_incidence dOmega."""
    dirs_face = np.asarray(dirs_face, dtype=float)
    kernel = np.clip(dirs_face[:, 2], 0.0, None)

    if mask is not None:
        azimuth = np.arctan2(dirs_face[:, 1], dirs_face[:, 0])
        elevation = np.arcsin(np.clip(dirs_face[:, 2], -1.0, 1.0))
        kernel = kernel * np.asarray(mask(dirs_face, azimuth, elevation), dtype=float)

    if sample_weight is not None:
        kernel = kernel * np.asarray(sample_weight, dtype=float)

    return float(np.sum(np.asarray(solid_angle_weights, dtype=float) * kernel) / math.pi)
