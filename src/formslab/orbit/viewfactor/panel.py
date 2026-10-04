"""Panel-resolved radiator geometry on top of the Earth-disk integrator."""

import math
from dataclasses import dataclass

import numpy as np

from ..geometry.cubesat import rect_patch_grid


@dataclass(frozen=True)
class RectangularPanel:
    """Rectangular panel discretized into equal-area patches.

    Parameters
    ----------
    width : float
        Panel width in local face-x [m].
    height : float
        Panel height in local face-y [m].
    nx, ny : int
        Number of patches along width and height.
    wall_height : float, optional
        Recess depth / vertical sidewall height [m]. When zero, the panel is
        fully open. When positive, a ray from a patch is accepted only if it
        clears the rectangular opening at ``z = wall_height``.
    """
    width: float
    height: float
    nx: int
    ny: int
    wall_height: float = 0.0

    def __post_init__(self):
        if self.width <= 0.0 or self.height <= 0.0:
            raise ValueError("Panel width and height must be positive")
        if self.nx <= 0 or self.ny <= 0:
            raise ValueError("Panel nx and ny must be positive")
        if self.wall_height < 0.0:
            raise ValueError("wall_height must be non-negative")

    def patch_centers(self):
        """Return patch-center coordinates in the local face plane."""
        return rect_patch_grid(self.width, self.height, self.nx, self.ny)

    def patch_visibility(self, dirs_face):
        """Return patch-by-ray transmission through the recessed opening."""
        dirs_face = np.asarray(dirs_face, dtype=float)
        if dirs_face.ndim != 2 or dirs_face.shape[1] != 3:
            raise ValueError("dirs_face must have shape (n_rays, 3)")

        n_rays = dirs_face.shape[0]
        dz = dirs_face[:, 2]
        if self.wall_height == 0.0:
            return np.ones((self.nx * self.ny, n_rays), dtype=float)

        shift_x = np.zeros(n_rays, dtype=float)
        shift_y = np.zeros(n_rays, dtype=float)
        np.divide(self.wall_height * dirs_face[:, 0], dz, out=shift_x, where=dz > 0.0)
        np.divide(self.wall_height * dirs_face[:, 1], dz, out=shift_y, where=dz > 0.0)

        xx, yy = self.patch_centers()
        x0 = xx.reshape(-1, 1)
        y0 = yy.reshape(-1, 1)

        visible = (
            (dz > 0.0)[None, :]
            & (np.abs(x0 + shift_x[None, :]) <= 0.5 * self.width + 1e-15)
            & (np.abs(y0 + shift_y[None, :]) <= 0.5 * self.height + 1e-15)
        )
        return visible.astype(float)

    def integrate(self, dirs_face, solid_angle_weights, sample_weight=None):
        """Integrate patch-resolved Earth factor / loading over the disk."""
        dirs_face = np.asarray(dirs_face, dtype=float)
        solid_angle_weights = np.asarray(solid_angle_weights, dtype=float)
        kernel = np.clip(dirs_face[:, 2], 0.0, None)[None, :]
        kernel = kernel * self.patch_visibility(dirs_face)

        if sample_weight is not None:
            kernel = kernel * np.asarray(sample_weight, dtype=float)[None, :]

        values = np.sum(kernel * solid_angle_weights[None, :], axis=1) / math.pi
        return values.reshape(self.ny, self.nx)


@dataclass(frozen=True)
class PanelLoadingProfile:
    """Panel-resolved Earth-origin loading over one orbit."""
    face: str
    panel: RectangularPanel
    u: np.ndarray
    view: np.ndarray
    ir: np.ndarray
    albedo: np.ndarray
    eclipse: np.ndarray

    def total(self):
        return self.ir + self.albedo

    def average_view(self):
        return self.view.mean(axis=(1, 2))

    def min_view(self):
        return self.view.min(axis=(1, 2))

    def max_view(self):
        return self.view.max(axis=(1, 2))

    def average_total(self):
        return self.total().mean(axis=(1, 2))

    def min_total(self):
        return self.total().min(axis=(1, 2))

    def max_total(self):
        return self.total().max(axis=(1, 2))
