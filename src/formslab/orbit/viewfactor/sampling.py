"""Direction sampling utilities for view-factor computation.

These helpers generate discrete ray sets to use as inputs to occlusion
and integration functions.
"""

import math

import numpy as np


def hemisphere_directions(surface, *, n_az=73, n_el=33,
                          elevation_min_deg=5.0, elevation_max_deg=85.0):
    """Sample ray directions uniformly over the outward hemisphere of *surface*.

    Directions are returned in body coordinates, using the surface's local
    frame (u, v, normal) as the hemisphere basis.

    Parameters
    ----------
    surface : RectFacet
        Source surface. Its ``frame_matrix`` defines the local hemisphere.
    n_az : int
        Number of azimuth samples in [-180, +180] deg.
    n_el : int
        Number of elevation samples.
    elevation_min_deg, elevation_max_deg : float
        Elevation range [deg], measured above the surface plane.

    Returns
    -------
    dirs_body : np.ndarray, shape (n_az * n_el, 3)
        Unit ray directions in the body frame.
    azimuth_deg : np.ndarray, shape (n_az,)
        Azimuth sample values [deg].
    elevation_deg : np.ndarray, shape (n_el,)
        Elevation sample values [deg].
    """
    azimuth_deg = np.linspace(-180.0, 180.0, n_az)
    elevation_deg = np.linspace(elevation_min_deg, elevation_max_deg, n_el)
    frame = surface.frame_matrix
    dirs_body = []

    for elevation in np.radians(elevation_deg):
        ce = math.cos(elevation)
        se = math.sin(elevation)
        for azimuth in np.radians(azimuth_deg):
            local_dir = np.array([
                ce * math.cos(azimuth),
                ce * math.sin(azimuth),
                se,
            ])
            dirs_body.append(frame @ local_dir)

    return np.asarray(dirs_body), azimuth_deg, elevation_deg
