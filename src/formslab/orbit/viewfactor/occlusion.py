"""Spacecraft self-occlusion helpers for realized body geometry.

This module bridges the realized rectangular-surface spacecraft model with
directional view-factor integration. The core operation is:

    for each source patch center and sampled direction,
    cast a ray against the realized spacecraft surfaces and decide whether
    that direction is blocked by local structure.

The result is a patch-by-ray visibility mask that can be multiplied with the
existing cosine-weighted directional kernels in the view-factor layer.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ..geometry.cubesat.surfaces import RealizedGeometry, RectFacet


_RAY_INTERSECTION_EPS = 1e-12


@dataclass(frozen=True)
class _PreparedOccluders:
    names: tuple[str, ...]
    tags: tuple[tuple[str, ...], ...]
    centers: np.ndarray
    normals: np.ndarray
    u_axes: np.ndarray
    v_axes: np.ndarray
    half_widths: np.ndarray
    half_heights: np.ndarray


def _resolve_source_surface(realized: RealizedGeometry,
                            source: str | RectFacet) -> RectFacet:
    if isinstance(source, str):
        return realized.by_name(source)
    if isinstance(source, RectFacet):
        return source
    raise TypeError("source must be a surface name or a RectFacet")


def _validate_dirs_body(dirs_body) -> np.ndarray:
    dirs_body = np.asarray(dirs_body, dtype=float)
    if dirs_body.ndim != 2 or dirs_body.shape[1] != 3:
        raise ValueError("dirs_body must have shape (n_rays, 3)")
    return dirs_body


def _validate_realized(realized, *, eps: float) -> RealizedGeometry:
    if not isinstance(realized, RealizedGeometry):
        raise TypeError("realized must be a RealizedGeometry instance")
    if eps <= 0.0:
        raise ValueError("eps must be positive")
    return realized


def _ray_origin(center, normal, dot, *, two_sided: bool, eps: float):
    if two_sided:
        if abs(dot) <= 1e-15:
            return None
        side = 1.0 if dot > 0.0 else -1.0
        return center + side * eps * normal
    if dot <= 0.0:
        return None
    return center + eps * normal


def _surface_cosine(surface: RectFacet, dirs_body: np.ndarray) -> np.ndarray:
    cosine = dirs_body @ surface.normal
    if surface.two_sided:
        return np.abs(cosine)
    return np.clip(cosine, 0.0, None)


def _patch_arrays(surface: RectFacet) -> tuple[np.ndarray, np.ndarray, int, int]:
    patch_centers = surface.patch_centers()
    ny, nx = patch_centers.shape[:2]
    centers = patch_centers.reshape(-1, 3)
    normals = np.broadcast_to(surface.normal, centers.shape)
    return centers, normals, ny, nx


def _prepare_occluders(realized: RealizedGeometry,
                       source_surface: RectFacet,
                       exclude=()) -> _PreparedOccluders:
    ignored = set(exclude)
    ignored.add(source_surface.name)
    targets = tuple(
        surface for surface in realized.facets
        if surface.name not in ignored
    )
    if not targets:
        empty_vec3 = np.empty((0, 3), dtype=float)
        empty_scalar = np.empty(0, dtype=float)
        return _PreparedOccluders(
            names=(),
            tags=(),
            centers=empty_vec3,
            normals=empty_vec3,
            u_axes=empty_vec3,
            v_axes=empty_vec3,
            half_widths=empty_scalar,
            half_heights=empty_scalar,
        )

    centers = np.stack([surface.center for surface in targets], axis=0)
    normals = np.stack([surface.normal for surface in targets], axis=0)
    u_axes = np.stack([surface.u_axis for surface in targets], axis=0)
    v_axes = np.cross(normals, u_axes)
    half_widths = np.array([0.5 * surface.width for surface in targets], dtype=float)
    half_heights = np.array([0.5 * surface.height for surface in targets], dtype=float)
    return _PreparedOccluders(
        names=tuple(surface.name for surface in targets),
        tags=tuple(surface.tags for surface in targets),
        centers=centers,
        normals=normals,
        u_axes=u_axes,
        v_axes=v_axes,
        half_widths=half_widths,
        half_heights=half_heights,
    )


def _hemisphere_quadrature(surface: RectFacet, *,
                           n_az: int,
                           n_el: int,
                           elevation_min_deg: float,
                           elevation_max_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if n_az <= 0 or n_el <= 0:
        raise ValueError("n_az and n_el must be positive")
    if not (0.0 <= elevation_min_deg < elevation_max_deg <= 90.0):
        raise ValueError("elevation bounds must satisfy 0 <= min < max <= 90 deg")

    az_edges = np.linspace(-math.pi, math.pi, n_az + 1)
    az_centers = 0.5 * (az_edges[:-1] + az_edges[1:])

    el_centers_deg = np.linspace(elevation_min_deg, elevation_max_deg, n_el)
    el_centers = np.radians(el_centers_deg)
    if n_el == 1:
        el_edges = np.array([0.0, 0.5 * math.pi], dtype=float)
    else:
        el_edges = np.empty(n_el + 1, dtype=float)
        el_edges[0] = 0.0
        el_edges[-1] = 0.5 * math.pi
        el_edges[1:-1] = 0.5 * (el_centers[:-1] + el_centers[1:])

    az_widths = np.diff(az_edges)
    el_solid_angle = np.sin(el_edges[1:]) - np.sin(el_edges[:-1])
    solid_angle_weights = (el_solid_angle[:, None] * az_widths[None, :]).reshape(-1)
    el_cosine_solid_angle = 0.5 * (
        np.sin(el_edges[1:]) ** 2 - np.sin(el_edges[:-1]) ** 2
    )
    cosine_solid_angle_weights = (
        el_cosine_solid_angle[:, None] * az_widths[None, :]
    ).reshape(-1)

    frame = surface.frame_matrix
    el_grid, az_grid = np.meshgrid(el_centers, az_centers, indexing='ij')
    ce = np.cos(el_grid)
    se = np.sin(el_grid)
    local_dirs = np.stack([
        ce * np.cos(az_grid),
        ce * np.sin(az_grid),
        se,
    ], axis=-1).reshape(-1, 3)
    dirs_body = local_dirs @ frame.T

    return dirs_body, solid_angle_weights, cosine_solid_angle_weights


def _first_hits_for_patch(prepared: _PreparedOccluders,
                          patch_center,
                          patch_normal,
                          dirs_body,
                          *,
                          source_two_sided: bool,
                          eps: float) -> tuple[np.ndarray, np.ndarray]:
    """Return nearest-hit occluder indices and valid-ray mask for one patch."""
    n_rays = dirs_body.shape[0]
    hit_indices = np.full(n_rays, -1, dtype=int)

    dots = dirs_body @ patch_normal
    if source_two_sided:
        valid = np.abs(dots) > 1e-15
    else:
        valid = dots > 0.0

    valid_indices = np.flatnonzero(valid)
    if valid_indices.size == 0 or prepared.centers.shape[0] == 0:
        return hit_indices, valid

    valid_dirs = dirs_body[valid]
    if source_two_sided:
        side = np.where(dots[valid] > 0.0, 1.0, -1.0)[:, None]
        origins = patch_center[None, :] + side * eps * patch_normal[None, :]
    else:
        origins = np.broadcast_to(
            patch_center[None, :] + eps * patch_normal[None, :],
            (valid_indices.size, 3),
        )

    denom = valid_dirs @ prepared.normals.T
    non_parallel = np.abs(denom) > _RAY_INTERSECTION_EPS

    center_delta = prepared.centers[None, :, :] - origins[:, None, :]
    numer = np.sum(center_delta * prepared.normals[None, :, :], axis=2)
    t_hit = np.full_like(numer, np.inf)
    np.divide(numer, denom, out=t_hit, where=non_parallel)

    valid_hit = non_parallel & (t_hit > _RAY_INTERSECTION_EPS)
    if not np.any(valid_hit):
        return hit_indices, valid

    t_for_hit = np.where(valid_hit, t_hit, 0.0)
    hit_points = origins[:, None, :] + t_for_hit[:, :, None] * valid_dirs[:, None, :]
    rel = hit_points - prepared.centers[None, :, :]
    u = np.sum(rel * prepared.u_axes[None, :, :], axis=2)
    v = np.sum(rel * prepared.v_axes[None, :, :], axis=2)

    valid_hit &= (
        np.abs(u) <= prepared.half_widths[None, :] + _RAY_INTERSECTION_EPS
    )
    valid_hit &= (
        np.abs(v) <= prepared.half_heights[None, :] + _RAY_INTERSECTION_EPS
    )

    t_hit[~valid_hit] = np.inf
    nearest_surface = np.argmin(t_hit, axis=1)
    nearest_t = t_hit[np.arange(valid_indices.size), nearest_surface]
    has_hit = np.isfinite(nearest_t)
    hit_indices[valid_indices[has_hit]] = nearest_surface[has_hit]
    return hit_indices, valid


def _first_hit_grid(prepared: _PreparedOccluders,
                    surface: RectFacet,
                    dirs_body,
                    *,
                    eps: float,
                    centers=None,
                    normals=None) -> tuple[np.ndarray, np.ndarray]:
    if centers is None or normals is None:
        centers, normals, _, _ = _patch_arrays(surface)

    n_patches = centers.shape[0]
    n_rays = dirs_body.shape[0]
    hit_indices = np.full((n_patches, n_rays), -1, dtype=int)
    valid_rays = np.zeros((n_patches, n_rays), dtype=bool)

    for patch_index, (center, normal) in enumerate(zip(centers, normals)):
        patch_hits, patch_valid = _first_hits_for_patch(
            prepared,
            center,
            normal,
            dirs_body,
            source_two_sided=surface.two_sided,
            eps=eps,
        )
        hit_indices[patch_index] = patch_hits
        valid_rays[patch_index] = patch_valid

    return hit_indices, valid_rays


def _visibility_from_hits(hit_indices: np.ndarray,
                          valid_rays: np.ndarray) -> np.ndarray:
    return (valid_rays & (hit_indices < 0)).astype(float)


def _integrate_visibility_kernel(surface: RectFacet,
                                 dirs_body,
                                 solid_angle_weights,
                                 visibility,
                                 *,
                                 sample_weight=None) -> np.ndarray:
    """Integrate a precomputed float visibility array for one source surface."""
    dirs_body = _validate_dirs_body(dirs_body)
    solid_angle_weights = np.asarray(solid_angle_weights, dtype=float)
    if solid_angle_weights.ndim != 1 or solid_angle_weights.shape[0] != dirs_body.shape[0]:
        raise ValueError("solid_angle_weights must have shape (n_rays,)")

    visibility = np.asarray(visibility, dtype=float)
    nx, ny = surface.patch_shape if surface.patch_shape is not None else (1, 1)
    if visibility.shape != (ny * nx, dirs_body.shape[0]):
        raise ValueError("visibility must have shape (n_patches, n_rays)")

    kernel = visibility * _surface_cosine(surface, dirs_body)[None, :]

    if sample_weight is not None:
        sample_weight = np.asarray(sample_weight, dtype=float)
        if sample_weight.ndim != 1 or sample_weight.shape[0] != dirs_body.shape[0]:
            raise ValueError("sample_weight must have shape (n_rays,)")
        kernel = kernel * sample_weight[None, :]

    values = np.sum(kernel * solid_angle_weights[None, :], axis=1) / math.pi
    return values.reshape(ny, nx)


def _group_view_from_prepared(surface: RectFacet,
                              prepared: _PreparedOccluders,
                              group_tags: Sequence[tuple[str, str]],
                              dirs_body,
                              cosine_solid_angle_weights,
                              *,
                              eps: float,
                              centers=None,
                              normals=None) -> dict[str, np.ndarray]:
    if centers is None or normals is None:
        centers, normals, ny, nx = _patch_arrays(surface)
    else:
        nx, ny = surface.patch_shape if surface.patch_shape is not None else (1, 1)

    group_names: list[str] = []
    normalized_group_tags: list[tuple[str, str]] = []
    reserved = {'structure', 'space'}
    for entry in group_tags:
        if len(entry) != 2:
            raise ValueError("group_tags entries must be (tag, group_name) pairs")
        tag, group_name = entry
        if not isinstance(tag, str) or not isinstance(group_name, str):
            raise TypeError("group_tags entries must contain strings")
        if group_name in reserved:
            raise ValueError(f"{group_name!r} is reserved")
        normalized_group_tags.append((tag, group_name))
        if group_name not in group_names:
            group_names.append(group_name)

    hit_indices, valid_rays = _first_hit_grid(
        prepared,
        surface,
        dirs_body,
        eps=eps,
        centers=centers,
        normals=normals,
    )

    weighted_kernel = cosine_solid_angle_weights / math.pi
    group_lookup = np.full(len(prepared.names), -1, dtype=int)
    for surface_index, tags in enumerate(prepared.tags):
        for group_index, (tag, _) in enumerate(normalized_group_tags):
            if tag in tags:
                group_lookup[surface_index] = group_index
                break

    n_patches = centers.shape[0]
    grouped = {name: np.zeros(n_patches, dtype=float) for name in group_names}
    structure = np.zeros(n_patches, dtype=float)
    space = np.zeros(n_patches, dtype=float)

    for patch_index in range(n_patches):
        patch_valid = valid_rays[patch_index]
        patch_hits = hit_indices[patch_index]

        patch_space = patch_valid & (patch_hits < 0)
        if np.any(patch_space):
            space[patch_index] = weighted_kernel[patch_space].sum()

        patch_hit = patch_valid & (patch_hits >= 0)
        if not np.any(patch_hit):
            continue

        hit_groups = group_lookup[patch_hits[patch_hit]]
        patch_weights = weighted_kernel[patch_hit]

        other_mask = hit_groups < 0
        if np.any(other_mask):
            structure[patch_index] = patch_weights[other_mask].sum()

        for group_index, group_name in enumerate(group_names):
            group_mask = hit_groups == group_index
            if np.any(group_mask):
                grouped[group_name][patch_index] = patch_weights[group_mask].sum()

    result = {
        name: values.reshape(ny, nx)
        for name, values in grouped.items()
    }
    result['structure'] = structure.reshape(ny, nx)
    result['space'] = space.reshape(ny, nx)
    return result


def spacecraft_occlusion_mask(realized: RealizedGeometry,
                              source: str | RectFacet,
                              dirs_body,
                              *,
                              exclude=(),
                              eps: float = 1e-9) -> np.ndarray:
    """Return a patch-by-ray self-occlusion mask for a realized surface.

    Parameters
    ----------
    realized : RealizedGeometry
        Realized spacecraft geometry in the body frame.
    source : str | RectFacet
        Source surface, either by name or as the realized surface object.
    dirs_body : array-like, shape (n_rays, 3)
        Sampled ray directions expressed in the body frame.
    exclude : iterable[str], optional
        Additional surface names to ignore during ray intersection.
        The source surface itself is always excluded.
    eps : float, optional
        Small origin offset along the emitting side of the source patch to
        avoid self-hits against the source plane.

    Returns
    -------
    np.ndarray
        Boolean array of shape (n_patches, n_rays). ``True`` means the ray is
        unobstructed by spacecraft structure for that patch.
    """
    _validate_realized(realized, eps=eps)
    dirs_body = _validate_dirs_body(dirs_body)
    surface = _resolve_source_surface(realized, source)
    centers, normals, _, _ = _patch_arrays(surface)
    prepared = _prepare_occluders(realized, surface, exclude=exclude)
    hit_indices, valid_rays = _first_hit_grid(
        prepared,
        surface,
        dirs_body,
        eps=eps,
        centers=centers,
        normals=normals,
    )
    return valid_rays & (hit_indices < 0)


def integrate_surface_response(realized: RealizedGeometry,
                               source: str | RectFacet,
                               dirs_body,
                               solid_angle_weights,
                               *,
                               sample_weight=None,
                               exclude=(),
                               eps: float = 1e-9) -> np.ndarray:
    """Integrate a patch-resolved surface response with self-occlusion.

    This is the rectangular-surface analogue of the existing panel-level
    directional integration, but with a global spacecraft blockage mask.
    """
    _validate_realized(realized, eps=eps)
    dirs_body = _validate_dirs_body(dirs_body)
    surface = _resolve_source_surface(realized, source)
    centers, normals, _, _ = _patch_arrays(surface)
    prepared = _prepare_occluders(realized, surface, exclude=exclude)
    hit_indices, valid_rays = _first_hit_grid(
        prepared,
        surface,
        dirs_body,
        eps=eps,
        centers=centers,
        normals=normals,
    )
    visibility = _visibility_from_hits(hit_indices, valid_rays)
    return _integrate_visibility_kernel(
        surface,
        dirs_body,
        solid_angle_weights,
        visibility,
        sample_weight=sample_weight,
    )


def hemisphere_group_view(realized: RealizedGeometry,
                          source: str | RectFacet,
                          group_tags: Sequence[tuple[str, str]],
                          *,
                          n_az: int = 73,
                          n_el: int = 33,
                          elevation_min_deg: float = 5.0,
                          elevation_max_deg: float = 85.0,
                          exclude=(),
                          eps: float = 1e-9) -> dict[str, np.ndarray]:
    """Integrate first-hit spacecraft view factors over the source hemisphere.

    Parameters
    ----------
    realized : RealizedGeometry
        Realized spacecraft geometry in the body frame.
    source : str | RectFacet
        Source surface, either by name or as the realized surface object.
    group_tags : sequence[tuple[str, str]]
        Ordered ``(tag, group_name)`` pairs. For each blocking first-hit
        surface, the first matching tag determines the output group.
    n_az, n_el : int, optional
        Hemisphere quadrature resolution.
    elevation_min_deg, elevation_max_deg : float, optional
        Approximate center range for elevation samples above the source plane.
        The quadrature still spans the full outward hemisphere in v1.
    exclude : iterable[str], optional
        Additional surface names to ignore during ray intersection.
    eps : float, optional
        Small origin offset along the emitting side of the source patch.

    Returns
    -------
    dict[str, np.ndarray]
        Patch-resolved view factors keyed by group name, plus
        ``structure`` and ``space``. ``space`` means directions unobstructed
        by spacecraft structure and may include Earth-facing rays.
    """
    _validate_realized(realized, eps=eps)
    surface = _resolve_source_surface(realized, source)
    dirs_body, _, cosine_solid_angle_weights = _hemisphere_quadrature(
        surface,
        n_az=n_az,
        n_el=n_el,
        elevation_min_deg=elevation_min_deg,
        elevation_max_deg=elevation_max_deg,
    )
    prepared = _prepare_occluders(realized, surface, exclude=exclude)
    centers, normals, _, _ = _patch_arrays(surface)
    return _group_view_from_prepared(
        surface,
        prepared,
        group_tags,
        dirs_body,
        cosine_solid_angle_weights,
        eps=eps,
        centers=centers,
        normals=normals,
    )
