"""Shared argument-of-latitude sweep helpers."""

import math

import numpy as np


def sorted_unique_angles(u_arr, tol=1e-12):
    """Normalize, sort, and deduplicate wrapped angles in ``[0, 2*pi)``."""
    u_arr = np.mod(np.asarray(u_arr, dtype=float).ravel(), 2 * math.pi)
    if u_arr.size == 0:
        return u_arr
    u_arr.sort()
    keep = np.ones(u_arr.size, dtype=bool)
    keep[1:] = np.diff(u_arr) > tol
    u_arr = u_arr[keep]
    if u_arr.size > 1 and (2 * math.pi - u_arr[-1] + u_arr[0]) <= tol:
        u_arr = u_arr[:-1]
    return u_arr


def propagation_grid(orbit, law, n):
    """Build the orbit sample grid, including optional law-driven refinement."""
    u_arr = np.linspace(0, 2 * math.pi, n, endpoint=False)
    refine = getattr(law, 'refine_u_samples', None)
    if callable(refine):
        u_arr = sorted_unique_angles(refine(u_arr, orbit))
    return u_arr
