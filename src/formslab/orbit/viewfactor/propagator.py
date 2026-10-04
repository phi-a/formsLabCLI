"""Disk-integrated and panel-resolved geometry propagators.

The actively used geometry path resolves the Earth disk in solid angle and
supports both face-level and panel-level loading models.
"""

from dataclasses import dataclass

import numpy as np

from ..geometry.cubesat import RealizedGeometry
from ..geometry.faces import FACES
from ..propagate.constants import A_ALB, J_IR, S0
from ..geometry.sampling import propagation_grid
from .earthdisk import (EarthDiskQuadrature, face_coordinates,
                        integrate_face_response)
from .occlusion import (_first_hit_grid, _group_view_from_prepared,
                        _hemisphere_quadrature, _integrate_visibility_kernel,
                        _patch_arrays, _prepare_occluders,
                        _visibility_from_hits)
from .panel import PanelLoadingProfile, RectangularPanel


@dataclass(frozen=True)
class _OrbitSweepSample:
    u: float
    rotation: object
    eclipse: bool
    samples: object | None
    dirs_body: np.ndarray | None
    albedo_weight: np.ndarray | None
    sun_body: np.ndarray | None


@dataclass(frozen=True)
class EarthLoadingProfile:
    """Disk-integrated Earth loading over one orbit."""
    u: np.ndarray
    view: dict
    ir: dict
    albedo: dict
    eclipse: np.ndarray

    def total(self, face):
        """Earth IR + albedo loading for ``face`` in W/m^2."""
        return self.ir[face] + self.albedo[face]


@dataclass(frozen=True)
class Loading:
    """Patch-resolved named-facet geometric loading over one orbit."""
    name: str
    u: np.ndarray
    width: float
    height: float
    earth: np.ndarray
    albedo: np.ndarray
    solar: np.ndarray
    panel: np.ndarray
    structure: np.ndarray
    space: np.ndarray
    eclipse: np.ndarray

    def average_earth(self):
        return self.earth.mean(axis=(1, 2))

    def average_solar(self):
        return self.solar.mean(axis=(1, 2))

    def average_panel(self):
        return self.panel.mean(axis=(1, 2))


def _orbit_sweep(orbit, law, n, *,
                 n_mu=24,
                 n_az=72,
                 need_earth_samples=True,
                 need_sun=False):
    """Return sampled orbit state shared by the public propagators."""
    u_arr = propagation_grid(orbit, law, n)
    quad = (
        EarthDiskQuadrature.build(orbit.rho, n_mu=n_mu, n_az=n_az)
        if need_earth_samples else None
    )
    sun_eci = orbit.sun_eci() if need_sun else None

    samples = []
    for uk in u_arr:
        rotation = law(uk, orbit)
        eclipse = orbit.in_eclipse(uk)

        earth_samples = None
        dirs_body = None
        albedo_weight = None
        if quad is not None:
            earth_samples = quad.sample(orbit.nadir_eci(uk), orbit.a)
            dirs_body = earth_samples.dirs_eci @ rotation.m
            if sun_eci is not None:
                albedo_weight = earth_samples.surface_solar_cosine(sun_eci)

        sun_body = None
        if sun_eci is not None:
            sun_body = rotation.T.apply(sun_eci)

        samples.append(_OrbitSweepSample(
            u=float(uk),
            rotation=rotation,
            eclipse=bool(eclipse),
            samples=earth_samples,
            dirs_body=dirs_body,
            albedo_weight=albedo_weight,
            sun_body=sun_body,
        ))

    return u_arr, samples


def earth_loading_propagate(orbit, law, n=360, *,
                            face_masks=None,
                            n_mu=24, n_az=72,
                            s0=S0, j_ir=J_IR, a_earth=A_ALB):
    """Sweep argument of latitude and integrate the visible Earth disk."""
    if face_masks is None:
        face_masks = {}
    else:
        unknown = sorted(set(face_masks) - set(FACES))
        if unknown:
            raise ValueError(f"Unknown face mask labels: {unknown}")

    u_arr, sweep = _orbit_sweep(
        orbit,
        law,
        n,
        n_mu=n_mu,
        n_az=n_az,
        need_earth_samples=True,
        need_sun=(a_earth != 0.0),
    )
    n_samp = u_arr.size
    view = {face: np.zeros(n_samp) for face in FACES}
    ir = {face: np.zeros(n_samp) for face in FACES}
    albedo = {face: np.zeros(n_samp) for face in FACES}
    eclipse = np.empty(n_samp, dtype=bool)

    for k, sample in enumerate(sweep):
        eclipse[k] = sample.eclipse

        for face in FACES:
            dirs_face, _, _ = face_coordinates(sample.dirs_body, face)
            mask = face_masks.get(face)

            geom = integrate_face_response(dirs_face, sample.samples.weights, mask=mask)
            view[face][k] = geom
            ir[face][k] = j_ir * geom

            if sample.albedo_weight is not None:
                alb = integrate_face_response(
                    dirs_face,
                    sample.samples.weights,
                    mask=mask,
                    sample_weight=sample.albedo_weight,
                )
                albedo[face][k] = a_earth * s0 * alb

    return EarthLoadingProfile(
        u=u_arr,
        view=view,
        ir=ir,
        albedo=albedo,
        eclipse=eclipse,
    )


def panel_loading_propagate(orbit, law, panel, *, face='+Y', n=180,
                            n_mu=24, n_az=72,
                            s0=S0, j_ir=J_IR, a_earth=A_ALB):
    """Sweep argument of latitude and integrate a patch-resolved panel model."""
    if face not in FACES:
        raise ValueError(f"Unknown face label {face!r}")
    if not isinstance(panel, RectangularPanel):
        raise TypeError("panel must be a RectangularPanel instance")

    u_arr, sweep = _orbit_sweep(
        orbit,
        law,
        n,
        n_mu=n_mu,
        n_az=n_az,
        need_earth_samples=True,
        need_sun=(a_earth != 0.0),
    )
    n_samp = u_arr.size
    view = np.zeros((n_samp, panel.ny, panel.nx), dtype=float)
    ir = np.zeros_like(view)
    albedo = np.zeros_like(view)
    eclipse = np.empty(n_samp, dtype=bool)

    for k, sample in enumerate(sweep):
        eclipse[k] = sample.eclipse
        dirs_face, _, _ = face_coordinates(sample.dirs_body, face)

        geom = panel.integrate(dirs_face, sample.samples.weights)
        view[k] = geom
        ir[k] = j_ir * geom

        if sample.albedo_weight is not None:
            alb = panel.integrate(
                dirs_face,
                sample.samples.weights,
                sample_weight=sample.albedo_weight,
            )
            albedo[k] = a_earth * s0 * alb

    return PanelLoadingProfile(
        face=face,
        panel=panel,
        u=u_arr,
        view=view,
        ir=ir,
        albedo=albedo,
        eclipse=eclipse,
    )


def facet_loading_propagate(realized, name, orbit, law, *,
                            n=180, n_mu=24, n_az=72,
                            hemi_n_az=73, hemi_n_el=33,
                            hemi_elevation_min_deg=5.0,
                            hemi_elevation_max_deg=85.0,
                            s0=S0, j_ir=J_IR, a_earth=A_ALB):
    """Sweep one realized CubeSat facet through the orbit geometry."""
    if not isinstance(realized, RealizedGeometry):
        raise TypeError("realized must be a RealizedGeometry instance")

    facet = realized.by_name(name)
    source_centers, source_normals, ny, nx = _patch_arrays(facet)
    prepared = _prepare_occluders(realized, facet)

    hemi_dirs_body, _, hemi_cosine_weights = _hemisphere_quadrature(
        facet,
        n_az=hemi_n_az,
        n_el=hemi_n_el,
        elevation_min_deg=hemi_elevation_min_deg,
        elevation_max_deg=hemi_elevation_max_deg,
    )
    static_views = _group_view_from_prepared(
        facet,
        prepared,
        [('solar_array', 'panel')],
        hemi_dirs_body,
        hemi_cosine_weights,
        eps=1e-9,
        centers=source_centers,
        normals=source_normals,
    )

    u_arr, sweep = _orbit_sweep(
        orbit,
        law,
        n,
        n_mu=n_mu,
        n_az=n_az,
        need_earth_samples=True,
        need_sun=True,
    )
    n_samp = u_arr.size
    earth = np.zeros((n_samp, ny, nx), dtype=float)
    albedo = np.zeros_like(earth)
    solar = np.zeros_like(earth)
    eclipse = np.empty(n_samp, dtype=bool)

    panel = np.repeat(static_views['panel'][None, :, :], n_samp, axis=0)
    structure = np.repeat(static_views['structure'][None, :, :], n_samp, axis=0)
    space = np.repeat(static_views['space'][None, :, :], n_samp, axis=0)
    # integrate_surface_response divides by π internally, so weighting the
    # single sun ray by π leaves solar = cos(θ_sun) * visibility.
    sun_weight = np.array([np.pi], dtype=float)

    for k, sample in enumerate(sweep):
        eclipse[k] = sample.eclipse
        earth_hits, earth_valid = _first_hit_grid(
            prepared,
            facet,
            sample.dirs_body,
            eps=1e-9,
            centers=source_centers,
            normals=source_normals,
        )
        earth_visibility = _visibility_from_hits(earth_hits, earth_valid)
        earth[k] = _integrate_visibility_kernel(
            facet,
            sample.dirs_body,
            sample.samples.weights,
            earth_visibility,
        )
        if sample.albedo_weight is not None:
            albedo[k] = _integrate_visibility_kernel(
                facet,
                sample.dirs_body,
                sample.samples.weights,
                earth_visibility,
                sample_weight=sample.albedo_weight,
            )
        if not sample.eclipse:
            sun_hits, sun_valid = _first_hit_grid(
                prepared,
                facet,
                sample.sun_body[None, :],
                eps=1e-9,
                centers=source_centers,
                normals=source_normals,
            )
            solar_visibility = _visibility_from_hits(sun_hits, sun_valid)
            solar[k] = _integrate_visibility_kernel(
                facet,
                sample.sun_body[None, :],
                sun_weight,
                solar_visibility,
            )

    return Loading(
        name=name,
        u=u_arr,
        width=float(facet.width),
        height=float(facet.height),
        earth=earth,
        albedo=albedo,
        solar=solar,
        panel=panel,
        structure=structure,
        space=space,
        eclipse=eclipse,
    )
