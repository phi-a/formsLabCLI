"""formslab.orbit: geometry, view-factor and thermal model behaviour."""

import math
import tempfile
import unittest
import warnings
from datetime import datetime
from pathlib import Path

import importlib.util

import numpy as np

# The transient solver needs scipy and the plots matplotlib (the `orbit` extra);
# a base install skips just those tests.
SCIPY = importlib.util.find_spec("scipy") is not None
MATPLOTLIB = importlib.util.find_spec("matplotlib") is not None
needs_scipy = unittest.skipUnless(SCIPY, "the transient solver needs scipy (the orbit extra)")
needs_matplotlib = unittest.skipUnless(MATPLOTLIB, "plots need matplotlib (the orbit extra)")
if MATPLOTLIB:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from formslab.orbit.thermal.plots import plot_temperature_heatmap, plot_temperature_trace

from formslab.orbit.geometry import (LVLHFixed, SlewModeSwitch, SO3,
                             SunTracking, TargetTracking, build_6u_double_deployable,
                             mount)
from formslab.orbit.propagate import Orbit
from formslab.orbit.geometry.cubesat import (CubeSatGeometry, RealizedGeometry, RectFacet,
                                     FacetNode, flip_facet, facet_role,
                                     surface_by_normal)
from formslab.orbit.geometry.sampling import propagation_grid
from formslab.orbit.viewfactor import (EarthDiskQuadrature, RectangularPanel, panel_loading_propagate,
                               spacecraft_occlusion_mask, integrate_surface_response,
                               hemisphere_group_view, Loading,
                               facet_loading_propagate)
import formslab.orbit.viewfactor.occlusion as occlusion_impl
from formslab.orbit.thermal import (SIGMA_SB, Background, Thermal,
                            environment, background,
                            steady,
                            steady_two_sided,
                            transient)


SGR_A = (math.radians(266.4168), math.radians(-29.0078))


def _sample_orbit():
    return Orbit.from_epoch(
        a=6771e3,
        i=math.radians(51.6),
        omega=math.radians(30.0),
        epoch=datetime(2025, 6, 21, 12, 0, 0),
    )


def _simple_geometry(*, blocker_tags=(), include_blocker=True):
    nodes = [
        FacetNode(
            RectFacet(
                name='source',
                center=np.array([0.0, 0.0, 0.0]),
                normal=np.array([0.0, 0.0, 1.0]),
                u_axis=np.array([1.0, 0.0, 0.0]),
                width=1.0,
                height=1.0,
                patch_shape=(1, 1),
            )
        )
    ]
    if include_blocker:
        nodes.append(
            FacetNode(
                RectFacet(
                    name='blocker',
                    center=np.array([0.0, 0.0, 1.0]),
                    normal=np.array([0.0, 0.0, -1.0]),
                    u_axis=np.array([1.0, 0.0, 0.0]),
                    width=1.0,
                    height=1.0,
                    tags=blocker_tags,
                )
            )
        )
    return CubeSatGeometry(tuple(nodes))


def _resolve_source(realized, source):
    if isinstance(source, str):
        return realized.by_name(source)
    return source


def _scalar_occlusion_mask(realized, source, dirs_body, *, exclude=(), eps=1e-9):
    dirs_body = np.asarray(dirs_body, dtype=float)
    surface = _resolve_source(realized, source)
    centers = surface.patch_centers().reshape(-1, 3)
    normals = np.broadcast_to(surface.normal, centers.shape)
    visible = np.zeros((centers.shape[0], dirs_body.shape[0]), dtype=bool)

    ignored = set(exclude)
    ignored.add(surface.name)
    ignored_tuple = tuple(ignored)

    for patch_index, (center, normal) in enumerate(zip(centers, normals)):
        dots = dirs_body @ normal
        for ray_index, dot in enumerate(dots):
            origin = occlusion_impl._ray_origin(
                center,
                normal,
                dot,
                two_sided=surface.two_sided,
                eps=eps,
            )
            if origin is None:
                continue
            hit = realized.first_intersection(
                origin,
                dirs_body[ray_index],
                exclude=ignored_tuple,
            )
            visible[patch_index, ray_index] = hit is None

    return visible


def _scalar_integrate_surface_response(realized, source, dirs_body, solid_angle_weights, *,
                                       sample_weight=None, exclude=(), eps=1e-9):
    dirs_body = np.asarray(dirs_body, dtype=float)
    solid_angle_weights = np.asarray(solid_angle_weights, dtype=float)
    surface = _resolve_source(realized, source)
    visibility = _scalar_occlusion_mask(
        realized,
        surface,
        dirs_body,
        exclude=exclude,
        eps=eps,
    ).astype(float)

    cosine = dirs_body @ surface.normal
    if surface.two_sided:
        cosine = np.abs(cosine)
    else:
        cosine = np.clip(cosine, 0.0, None)

    kernel = visibility * cosine[None, :]
    if sample_weight is not None:
        kernel = kernel * np.asarray(sample_weight, dtype=float)[None, :]

    values = np.sum(kernel * solid_angle_weights[None, :], axis=1) / math.pi
    ny, nx = surface.patch_centers().shape[:2]
    return values.reshape(ny, nx)


def _scalar_hemisphere_group_view(realized, source, group_tags, *,
                                  n_az=73, n_el=33,
                                  elevation_min_deg=5.0,
                                  elevation_max_deg=85.0,
                                  exclude=(), eps=1e-9):
    surface = _resolve_source(realized, source)
    dirs_body, _, cosine_weights = occlusion_impl._hemisphere_quadrature(
        surface,
        n_az=n_az,
        n_el=n_el,
        elevation_min_deg=elevation_min_deg,
        elevation_max_deg=elevation_max_deg,
    )
    weighted_kernel = cosine_weights / math.pi

    group_names = []
    for _, group_name in group_tags:
        if group_name not in group_names:
            group_names.append(group_name)

    centers = surface.patch_centers().reshape(-1, 3)
    normals = np.broadcast_to(surface.normal, centers.shape)
    grouped = {name: np.zeros(centers.shape[0], dtype=float) for name in group_names}
    other_structure = np.zeros(centers.shape[0], dtype=float)
    space = np.zeros(centers.shape[0], dtype=float)

    ignored = set(exclude)
    ignored.add(surface.name)
    ignored_tuple = tuple(ignored)

    for patch_index, (center, normal) in enumerate(zip(centers, normals)):
        dots = dirs_body @ normal
        for ray_index, dot in enumerate(dots):
            origin = occlusion_impl._ray_origin(
                center,
                normal,
                dot,
                two_sided=surface.two_sided,
                eps=eps,
            )
            if origin is None:
                continue
            hit = realized.first_intersection(
                origin,
                dirs_body[ray_index],
                exclude=ignored_tuple,
            )
            if hit is None:
                space[patch_index] += weighted_kernel[ray_index]
                continue

            hit_surface, _ = hit
            group_name = None
            for tag, candidate_name in group_tags:
                if tag in hit_surface.tags:
                    group_name = candidate_name
                    break
            if group_name is None:
                other_structure[patch_index] += weighted_kernel[ray_index]
            else:
                grouped[group_name][patch_index] += weighted_kernel[ray_index]

    ny, nx = surface.patch_centers().shape[:2]
    result = {name: values.reshape(ny, nx) for name, values in grouped.items()}
    result['structure'] = other_structure.reshape(ny, nx)
    result['space'] = space.reshape(ny, nx)
    return result


def _scalar_surface_loading_reference(realized, name, orbit, law, *,
                                      n=180, n_mu=24, n_az=72,
                                      hemi_n_az=73, hemi_n_el=33,
                                      hemi_elevation_min_deg=5.0,
                                      hemi_elevation_max_deg=85.0):
    surface = realized.by_name(name)
    u_arr = propagation_grid(orbit, law, n)
    quad = EarthDiskQuadrature.build(orbit.rho(0.0), n_mu=n_mu, n_az=n_az)   # circular: one size
    sun_eci = orbit.sun_eci()
    static_views = _scalar_hemisphere_group_view(
        realized,
        surface,
        [('solar_array', 'panel')],
        n_az=hemi_n_az,
        n_el=hemi_n_el,
        elevation_min_deg=hemi_elevation_min_deg,
        elevation_max_deg=hemi_elevation_max_deg,
    )

    ny, nx = surface.patch_centers().shape[:2]
    n_samp = u_arr.size
    earth = np.zeros((n_samp, ny, nx), dtype=float)
    albedo = np.zeros_like(earth)
    solar = np.zeros_like(earth)
    eclipse = np.empty(n_samp, dtype=bool)
    sun_weight = np.array([math.pi], dtype=float)

    for k, uk in enumerate(u_arr):
        rotation = law(uk, orbit)
        eclipse[k] = orbit.in_eclipse(uk)
        samples = quad.sample(orbit.nadir_eci(uk), orbit.radius(uk))
        dirs_body = samples.dirs_eci @ rotation.m

        earth[k] = _scalar_integrate_surface_response(
            realized,
            surface,
            dirs_body,
            samples.weights,
        )
        albedo[k] = _scalar_integrate_surface_response(
            realized,
            surface,
            dirs_body,
            samples.weights,
            sample_weight=samples.surface_solar_cosine(sun_eci),
        )
        if not eclipse[k]:
            sun_body = rotation.T.apply(sun_eci)
            solar[k] = _scalar_integrate_surface_response(
                realized,
                surface,
                sun_body[None, :],
                sun_weight,
            )

    return Loading(
        name=name,
        u=u_arr,
        width=surface.width,
        height=surface.height,
        earth=earth,
        albedo=albedo,
        solar=solar,
        panel=np.repeat(static_views['panel'][None, :, :], n_samp, axis=0),
        structure=np.repeat(static_views['structure'][None, :, :], n_samp, axis=0),
        space=np.repeat(static_views['space'][None, :, :], n_samp, axis=0),
        eclipse=eclipse,
    )


def _sample_thermal_profile(*, name='source', width=0.4, height=0.2,
                            u=None, eclipse=None, temperature=None):
    if u is None:
        u = np.array([0.0, 1.0, 2.0], dtype=float)
    if eclipse is None:
        eclipse = np.array([False, True, False], dtype=bool)
    if temperature is None:
        temperature = np.array([
            [[280.0, 282.0], [281.0, 283.0]],
            [[290.0, 295.0], [292.0, 294.0]],
            [[285.0, 287.0], [286.0, 288.0]],
        ])

    q_absorbed = np.maximum(temperature, 0.0)
    return Thermal(
        name=name,
        u=np.asarray(u, dtype=float),
        width=width,
        height=height,
        temperature=np.asarray(temperature, dtype=float),
        q_absorbed=np.asarray(q_absorbed, dtype=float),
        eclipse=np.asarray(eclipse, dtype=bool),
        alpha_solar=0.8,
        epsilon=0.9,
    )


def _sample_background_profile(*, name='source', width=0.4, height=0.2,
                               u=None, eclipse=None,
                               earth=None, albedo=None, solar=None, panel=None):
    if u is None:
        u = np.array([0.0, 1.0, 2.0], dtype=float)
    if eclipse is None:
        eclipse = np.array([False, True, False], dtype=bool)
    shape = (len(u), 2, 3)
    earth = np.zeros(shape) if earth is None else np.asarray(earth, dtype=float)
    albedo = np.zeros(shape) if albedo is None else np.asarray(albedo, dtype=float)
    solar = np.zeros(shape) if solar is None else np.asarray(solar, dtype=float)
    panel = (
        np.zeros(shape) if panel is None else np.asarray(panel, dtype=float)
    )
    structure = np.zeros(shape)
    total = earth + albedo + solar + panel + structure
    return Background(
        name=name,
        u=np.asarray(u, dtype=float),
        width=width,
        height=height,
        earth=earth,
        albedo=albedo,
        solar=solar,
        panel=panel,
        structure=structure,
        total=total,
        eclipse=np.asarray(eclipse, dtype=bool),
    )


class GeometryPackageTests(unittest.TestCase):
    def test_orbit_utc_at_zero_returns_epoch(self):
        orbit = _sample_orbit()
        self.assertEqual(orbit.utc_at(0.0), orbit.epoch)

    def test_orbit_utc_at_two_pi_advances_one_period(self):
        from datetime import timedelta
        orbit = _sample_orbit()
        self.assertEqual(
            orbit.utc_at(2 * math.pi),
            orbit.epoch + timedelta(seconds=orbit.period),
        )

    def test_orbit_utc_at_array_returns_list_with_correct_length(self):
        orbit = _sample_orbit()
        u = np.linspace(0, 2 * math.pi, 8)
        out = orbit.utc_at(u)
        self.assertIsInstance(out, list)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[0], orbit.epoch)

    def test_open_panel_is_uniform(self):
        orbit = _sample_orbit()
        panel = RectangularPanel(width=20e-3, height=30e-3, nx=4, ny=6, wall_height=0.0)
        profile = panel_loading_propagate(
            orbit,
            LVLHFixed(),
            panel,
            face='+Y',
            n=12,
            n_mu=6,
            n_az=16,
        )
        snapshot = profile.view[0]
        self.assertTrue(np.allclose(snapshot, snapshot[0, 0]))

    def test_slew_begins_at_boundary(self):
        orbit = _sample_orbit()
        eclipse_law = TargetTracking(*SGR_A)
        sunlit_law = SunTracking()
        law = SlewModeSwitch(eclipse_law, sunlit_law, slew_rate_deg_s=0.5)

        transitions = law._transitions(orbit)
        self.assertEqual(len(transitions), 2)

        for index, transition in enumerate(transitions):
            from_law = sunlit_law if index == 0 else eclipse_law
            start_u = transition.start
            epsilon = 1e-6
            before = law(start_u - epsilon, orbit)
            at_boundary = law(start_u, orbit)
            after = law(start_u + min(epsilon, transition.span * 0.5), orbit)
            reference = from_law(start_u, orbit)

            self.assertTrue(np.allclose(before.m, reference.m, atol=1e-9))
            self.assertTrue(np.allclose(at_boundary.m, reference.m, atol=1e-9))
            self.assertFalse(np.allclose(after.m, at_boundary.m, atol=1e-9))

    def test_default_6u_builder_realizes_deployables(self):
        cubesat = build_6u_double_deployable()
        realized = cubesat.realize()

        self.assertEqual(len(realized.facets), 14)
        self.assertIn('wing_port_inner', realized.names())
        self.assertIn('wing_starboard_outer', realized.names())
        self.assertIn('wing_port_inner_back', realized.names())
        self.assertIn('wing_starboard_outer_back', realized.names())

        port_inner = realized.by_name('wing_port_inner')
        port_outer = realized.by_name('wing_port_outer')
        star_inner = realized.by_name('wing_starboard_inner')
        star_outer = realized.by_name('wing_starboard_outer')
        port_inner_back = realized.by_name('wing_port_inner_back')

        self.assertTrue(np.allclose(port_inner.normal, [0.0, 1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(port_outer.normal, [0.0, 1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(star_inner.normal, [0.0, 1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(star_outer.normal, [0.0, 1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(port_inner_back.normal, [0.0, -1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(port_inner_back.center, port_inner.center, atol=1e-9))
        self.assertTrue(np.allclose(port_inner_back.u_axis, port_inner.u_axis, atol=1e-9))

        self.assertAlmostEqual(cubesat.metadata['leaf_y_m'], 0.2263)
        self.assertAlmostEqual(cubesat.metadata['leaf_z_m'], 0.3405)
        self.assertAlmostEqual(port_inner.height, 0.2263)
        self.assertAlmostEqual(port_inner.width, 0.3405)

        self.assertEqual(len(realized.by_tag('solar_panel')), 4)
        self.assertEqual(len(realized.by_tag('solar_panel_back')), 4)
        self.assertEqual(len(realized.by_tag('solar_array')), 8)

        self.assertGreater(port_outer.center[0], port_inner.center[0])
        self.assertLess(star_outer.center[0], star_inner.center[0])

    def test_flip_facet_creates_back_face_with_shared_u_axis(self):
        surface = RectFacet(
            name='panel',
            center=np.array([1.0, 2.0, 3.0]),
            normal=np.array([0.0, 0.0, 1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=0.4,
            height=0.2,
            patch_shape=(3, 2),
            tags=('solar_panel', 'solar_array'),
        )

        back = flip_facet(
            surface,
            tags=('solar_panel_back', 'solar_array'),
        )

        self.assertEqual(back.name, 'panel_back')
        self.assertTrue(np.allclose(back.center, surface.center, atol=1e-12))
        self.assertTrue(np.allclose(back.normal, -surface.normal, atol=1e-12))
        self.assertTrue(np.allclose(back.u_axis, surface.u_axis, atol=1e-12))
        self.assertEqual(back.patch_shape, surface.patch_shape)
        self.assertEqual(back.tags, ('solar_panel_back', 'solar_array'))

    def test_builder_accepts_explicit_leaf_dimensions(self):
        cubesat = build_6u_double_deployable(leaf_y=0.180, leaf_z=0.300)
        realized = cubesat.realize()
        port_inner = realized.by_name('wing_port_inner')

        self.assertAlmostEqual(cubesat.metadata['leaf_y_m'], 0.180)
        self.assertAlmostEqual(cubesat.metadata['leaf_z_m'], 0.300)
        self.assertAlmostEqual(port_inner.height, 0.180)
        self.assertAlmostEqual(port_inner.width, 0.300)

    def test_realized_geometry_mount_applies_rigid_transform(self):
        source = RectFacet(
            name='source',
            center=np.array([1.0, 0.0, 0.0]),
            normal=np.array([1.0, 0.0, 0.0]),
            u_axis=np.array([0.0, 0.0, 1.0]),
            width=1.0,
            height=2.0,
        )
        realized = RealizedGeometry((source,))

        mounted = realized.mounted(
            rotation=SO3.Rz(math.pi / 2.0),
            offset=np.array([0.0, 1.0, 0.0]),
        )
        transformed = mounted.by_name('source')

        self.assertTrue(np.allclose(transformed.center, [0.0, 2.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(transformed.normal, [0.0, 1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(transformed.u_axis, [0.0, 0.0, 1.0], atol=1e-9))

    def test_builder_realize_accepts_optional_mount_transform(self):
        cubesat = build_6u_double_deployable()
        plain = cubesat.realize()
        mounted = cubesat.realize(
            mount_rotation=SO3.Rz(math.pi / 2.0),
            mount_offset=np.array([0.0, 0.1, 0.0]),
        )

        plain_face = plain.by_name('bus_+X')
        mounted_face = mounted.by_name('bus_+X')

        expected_center = np.array([0.0, 0.15, 0.0])
        expected_normal = np.array([0.0, 1.0, 0.0])
        expected_u = np.array([0.0, 0.0, 1.0])

        self.assertTrue(np.allclose(mounted_face.center, expected_center, atol=1e-9))
        self.assertTrue(np.allclose(mounted_face.normal, expected_normal, atol=1e-9))
        self.assertTrue(np.allclose(mounted_face.u_axis, expected_u, atol=1e-9))
        self.assertFalse(np.allclose(mounted_face.center, plain_face.center, atol=1e-9))

    def test_mount_identity_for_matching_axis_pair(self):
        rotation = mount('+Y', '+Y')

        self.assertTrue(np.allclose(rotation.apply([1.0, 0.0, 0.0]), [1.0, 0.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 1.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 0.0, 1.0]), [0.0, 0.0, 1.0], atol=1e-12))

    def test_mount_single_pair_uses_canonical_minimal_rotation(self):
        rotation = mount('+Y', '+Z')

        self.assertTrue(np.allclose(rotation.apply([1.0, 0.0, 0.0]), [1.0, 0.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 1.0, 0.0]), [0.0, 0.0, 1.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 0.0, 1.0]), [0.0, -1.0, 0.0], atol=1e-12))

    def test_mount_opposite_axis_is_deterministic(self):
        rotation = mount('+X', '-X')

        self.assertTrue(np.allclose(rotation.apply([1.0, 0.0, 0.0]), [-1.0, 0.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 1.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 0.0, 1.0]), [0.0, 0.0, -1.0], atol=1e-12))

    def test_mount_two_pairs_aligns_full_frame(self):
        rotation = mount('+Y', '+Z', '+Z', '+X')

        self.assertTrue(np.allclose(rotation.apply([0.0, 1.0, 0.0]), [0.0, 0.0, 1.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([0.0, 0.0, 1.0]), [1.0, 0.0, 0.0], atol=1e-12))
        self.assertTrue(np.allclose(rotation.apply([1.0, 0.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-12))

    def test_mount_rejects_invalid_secondary_axis_pair(self):
        with self.assertRaises(ValueError):
            mount('+Y', '+Z', '+Y', '+X')
        with self.assertRaises(ValueError):
            mount('+Y', '+Z', '+X')

    def test_realized_geometry_json_roundtrip_preserves_surfaces_and_metadata(self):
        cubesat = build_6u_double_deployable(bus_patch_shape=(2, 2))
        realized = cubesat.realize(
            {
                'wing_port_inner_angle': math.pi / 3.0,
                'wing_port_outer_angle': math.pi,
                'wing_starboard_inner_angle': -math.pi / 2.0,
                'wing_starboard_outer_angle': -math.pi,
            },
            mount_rotation=mount('+Y', '+Z', '+Z', '+X'),
            mount_offset=np.array([0.01, -0.02, 0.03]),
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test_realized_geometry_roundtrip.json'
            realized.to_json(path)
            loaded = RealizedGeometry.from_json(path)

        self.assertEqual(loaded.metadata, realized.metadata)
        self.assertEqual(loaded.names(), realized.names())
        for loaded_surface, realized_surface in zip(loaded.facets, realized.facets):
            self.assertEqual(loaded_surface.name, realized_surface.name)
            self.assertTrue(np.allclose(loaded_surface.center, realized_surface.center, atol=1e-12))
            self.assertTrue(np.allclose(loaded_surface.normal, realized_surface.normal, atol=1e-12))
            self.assertTrue(np.allclose(loaded_surface.u_axis, realized_surface.u_axis, atol=1e-12))
            self.assertEqual(loaded_surface.width, realized_surface.width)
            self.assertEqual(loaded_surface.height, realized_surface.height)
            self.assertEqual(loaded_surface.two_sided, realized_surface.two_sided)
            self.assertEqual(loaded_surface.patch_shape, realized_surface.patch_shape)
            self.assertEqual(loaded_surface.tags, realized_surface.tags)

    def test_facet_role_uses_realized_normals(self):
        realized = build_6u_double_deployable().realize(
            mount_rotation=mount('+Y', '+Z', '+Z', '+X'),
        )

        self.assertEqual(
            facet_role(surface_by_normal(realized, [0.0, 1.0, 0.0], tag='bus')),
            'body +Y bus face',
        )
        self.assertEqual(
            facet_role(surface_by_normal(realized, [1.0, 0.0, 0.0], tag='bus')),
            'body +X bus face',
        )
        self.assertEqual(
            facet_role(realized.by_name('wing_port_inner')),
            'body +Z solar panel',
        )

    def test_spacecraft_occlusion_mask_blocks_parallel_rectangle(self):
        source = RectFacet(
            name='source',
            center=np.array([0.0, 0.0, 0.0]),
            normal=np.array([0.0, 0.0, 1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=2.0,
            height=2.0,
            patch_shape=(2, 2),
        )
        blocker = RectFacet(
            name='blocker',
            center=np.array([0.0, 0.0, 1.0]),
            normal=np.array([0.0, 0.0, -1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=2.0,
            height=2.0,
        )
        realized = RealizedGeometry((source, blocker))
        dirs_body = np.array([[0.0, 0.0, 1.0]])

        visible = spacecraft_occlusion_mask(realized, 'source', dirs_body)

        self.assertEqual(visible.shape, (4, 1))
        self.assertTrue(np.all(~visible))

    def test_spacecraft_occlusion_mask_matches_scalar_reference(self):
        cubesat = build_6u_double_deployable(bus_patch_shape=(2, 2))
        realized = cubesat.realize()
        surface = realized.by_name('bus_+Y')
        dirs_body, _, _ = occlusion_impl._hemisphere_quadrature(
            surface,
            n_az=16,
            n_el=8,
            elevation_min_deg=5.0,
            elevation_max_deg=85.0,
        )

        visible = spacecraft_occlusion_mask(realized, surface, dirs_body)
        reference = _scalar_occlusion_mask(realized, surface, dirs_body)

        self.assertTrue(np.array_equal(visible, reference))

    def test_integrate_surface_response_respects_occlusion(self):
        source = RectFacet(
            name='source',
            center=np.array([0.0, 0.0, 0.0]),
            normal=np.array([0.0, 0.0, 1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=1.0,
            height=1.0,
            patch_shape=(1, 1),
        )
        blocker = RectFacet(
            name='blocker',
            center=np.array([0.0, 0.0, 1.0]),
            normal=np.array([0.0, 0.0, -1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=1.0,
            height=1.0,
        )
        clear = RealizedGeometry((source,))
        blocked = RealizedGeometry((source, blocker))

        dirs_body = np.array([[0.0, 0.0, 1.0]])
        solid_angle_weights = np.array([math.pi])

        clear_value = integrate_surface_response(
            clear,
            'source',
            dirs_body,
            solid_angle_weights,
        )
        blocked_value = integrate_surface_response(
            blocked,
            'source',
            dirs_body,
            solid_angle_weights,
        )

        self.assertTrue(np.allclose(clear_value, [[1.0]], atol=1e-12))
        self.assertTrue(np.allclose(blocked_value, [[0.0]], atol=1e-12))

    def test_integrate_surface_response_matches_scalar_reference(self):
        cubesat = build_6u_double_deployable(bus_patch_shape=(2, 2))
        realized = cubesat.realize()
        surface = realized.by_name('bus_+Y')
        dirs_body, _, _ = occlusion_impl._hemisphere_quadrature(
            surface,
            n_az=14,
            n_el=7,
            elevation_min_deg=5.0,
            elevation_max_deg=85.0,
        )
        solid_angle_weights = np.linspace(0.1, 0.9, dirs_body.shape[0])
        sample_weight = np.linspace(0.2, 1.0, dirs_body.shape[0])

        value = integrate_surface_response(
            realized,
            surface,
            dirs_body,
            solid_angle_weights,
            sample_weight=sample_weight,
        )
        reference = _scalar_integrate_surface_response(
            realized,
            surface,
            dirs_body,
            solid_angle_weights,
            sample_weight=sample_weight,
        )

        self.assertTrue(np.allclose(value, reference, atol=1e-12))

    def test_hemisphere_group_view_uses_first_matching_tag_order(self):
        geometry = _simple_geometry(blocker_tags=('solar_array', 'deployable'))
        realized = geometry.realize()

        grouped = hemisphere_group_view(
            realized,
            'source',
            [('deployable', 'deployable_view'), ('solar_array', 'panel')],
            n_az=120,
            n_el=60,
        )

        self.assertGreater(grouped['deployable_view'][0, 0], 0.0)
        self.assertAlmostEqual(grouped['panel'][0, 0], 0.0, places=12)

    def test_hemisphere_group_view_closure_for_open_hemisphere(self):
        geometry = _simple_geometry(include_blocker=False)
        realized = geometry.realize()

        grouped = hemisphere_group_view(
            realized,
            'source',
            [('solar_array', 'panel')],
            n_az=160,
            n_el=80,
        )
        total = (
            grouped['panel']
            + grouped['structure']
            + grouped['space']
        )

        self.assertTrue(np.allclose(total, [[1.0]], atol=2e-3))

    def test_hemisphere_group_view_matches_scalar_reference(self):
        cubesat = build_6u_double_deployable(bus_patch_shape=(2, 2))
        realized = cubesat.realize()

        grouped = hemisphere_group_view(
            realized,
            'bus_+Y',
            [('solar_array', 'panel')],
            n_az=20,
            n_el=10,
        )
        reference = _scalar_hemisphere_group_view(
            realized,
            'bus_+Y',
            [('solar_array', 'panel')],
            n_az=20,
            n_el=10,
        )

        for key in ('panel', 'structure', 'space'):
            self.assertTrue(np.allclose(grouped[key], reference[key], atol=1e-12))

    def test_solar_array_grouping_matches_coplanar_front_surface_geometry(self):
        realized = build_6u_double_deployable(bus_patch_shape=(2, 2)).realize()

        grouped_front_only = hemisphere_group_view(
            realized,
            'bus_+Y',
            [('solar_panel', 'panel')],
            n_az=20,
            n_el=10,
        )
        grouped_front_and_back = hemisphere_group_view(
            realized,
            'bus_+Y',
            [('solar_array', 'panel')],
            n_az=20,
            n_el=10,
        )

        self.assertTrue(np.allclose(
            grouped_front_and_back['panel'],
            grouped_front_only['panel'],
            atol=1e-12,
        ))

    def test_facet_loading_propagate_keeps_warm_views_static(self):
        orbit = _sample_orbit()
        realized = build_6u_double_deployable(bus_patch_shape=(2, 2)).realize()
        law = SlewModeSwitch(TargetTracking(*SGR_A), SunTracking(), slew_rate_deg_s=0.5)

        profile = facet_loading_propagate(
            realized,
            'bus_+Y',
            orbit,
            law,
            n=24,
            n_mu=6,
            n_az=16,
            hemi_n_az=72,
            hemi_n_el=36,
        )

        self.assertTrue(np.allclose(
            profile.panel,
            np.repeat(profile.panel[:1], profile.u.size, axis=0),
        ))
        self.assertGreater(np.ptp(profile.average_earth()), 1e-4)
        self.assertGreater(np.ptp(profile.average_solar()), 1e-4)

    def test_facet_loading_propagate_direct_solar_occlusion(self):
        orbit = _sample_orbit()
        blocked_geometry = _simple_geometry(blocker_tags=('solar_array',)).realize()
        clear_geometry = _simple_geometry(include_blocker=False).realize()

        blocked = facet_loading_propagate(
            blocked_geometry,
            'source',
            orbit,
            SunTracking(),
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=48,
            hemi_n_el=24,
        )
        clear = facet_loading_propagate(
            clear_geometry,
            'source',
            orbit,
            SunTracking(),
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=48,
            hemi_n_el=24,
        )

        self.assertLess(blocked.solar.mean(), clear.solar.mean())
        self.assertGreater(blocked.panel.mean(), 0.0)
        self.assertTrue(np.allclose(clear.panel, 0.0, atol=1e-12))

    def test_facet_loading_propagate_matches_scalar_reference(self):
        orbit = _sample_orbit()
        realized = build_6u_double_deployable(bus_patch_shape=(2, 2)).realize()
        law = SlewModeSwitch(TargetTracking(*SGR_A), SunTracking(), slew_rate_deg_s=0.5)

        profile = facet_loading_propagate(
            realized,
            'bus_+Y',
            orbit,
            law,
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=16,
            hemi_n_el=8,
        )
        reference = _scalar_surface_loading_reference(
            realized,
            'bus_+Y',
            orbit,
            law,
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=16,
            hemi_n_el=8,
        )

        self.assertTrue(np.allclose(profile.earth, reference.earth, atol=1e-12))
        self.assertTrue(np.allclose(profile.albedo, reference.albedo, atol=1e-12))
        self.assertTrue(np.allclose(profile.solar, reference.solar, atol=1e-12))
        self.assertTrue(np.allclose(profile.panel, reference.panel, atol=1e-12))
        self.assertTrue(np.allclose(profile.structure, reference.structure, atol=1e-12))
        self.assertTrue(np.allclose(profile.space, reference.space, atol=1e-12))

    def test_facet_loading_propagate_loaded_json_matches_in_memory_realized(self):
        orbit = _sample_orbit()
        realized = build_6u_double_deployable(bus_patch_shape=(2, 2)).realize(
            mount_rotation=mount('+Y', '+Z', '+Z', '+X'),
        )
        law = SlewModeSwitch(TargetTracking(*SGR_A), SunTracking(), slew_rate_deg_s=0.5)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test_surface_loading_roundtrip.json'
            realized.to_json(path)
            loaded = RealizedGeometry.from_json(path)

        profile = facet_loading_propagate(
            realized,
            'bus_+Y',
            orbit,
            law,
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=16,
            hemi_n_el=8,
        )
        loaded_profile = facet_loading_propagate(
            loaded,
            'bus_+Y',
            orbit,
            law,
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=16,
            hemi_n_el=8,
        )

        self.assertTrue(np.allclose(profile.earth, loaded_profile.earth, atol=1e-12))
        self.assertTrue(np.allclose(profile.albedo, loaded_profile.albedo, atol=1e-12))
        self.assertTrue(np.allclose(profile.solar, loaded_profile.solar, atol=1e-12))
        self.assertTrue(np.allclose(profile.panel, loaded_profile.panel, atol=1e-12))
        self.assertTrue(np.allclose(profile.structure, loaded_profile.structure, atol=1e-12))
        self.assertTrue(np.allclose(profile.space, loaded_profile.space, atol=1e-12))

    def test_background_matches_component_formulas(self):
        profile = Loading(
            name='source',
            u=np.array([0.0, 1.0]),
            width=1.0,
            height=1.0,
            earth=np.array([[[0.5]], [[0.25]]]),
            albedo=np.array([[[0.2]], [[0.1]]]),
            solar=np.array([[[0.3]], [[0.0]]]),
            panel=np.array([[[0.1]], [[0.1]]]),
            structure=np.zeros((2, 1, 1)),
            space=np.ones((2, 1, 1)),
            eclipse=np.array([False, True]),
        )

        bg = background(
            profile,
            solar_panel_temperature_K=300.0,
            solar_panel_emittance=0.8,
        )

        expected_panel = 0.8 * SIGMA_SB * 300.0 ** 4 * profile.panel
        self.assertTrue(np.allclose(bg.earth, 240.0 * profile.earth))
        self.assertTrue(np.allclose(bg.albedo, 0.30 * 1361.0 * profile.albedo))
        self.assertTrue(np.allclose(bg.solar, 1361.0 * profile.solar))
        self.assertTrue(np.allclose(bg.panel, expected_panel))
        self.assertTrue(np.allclose(
            bg.total,
            bg.earth + bg.albedo + bg.solar + bg.panel,
        ))

    def test_background_temperature_is_optional_only_for_zero_warm_view(self):
        cold_profile = Loading(
            name='source',
            u=np.array([0.0]),
            width=1.0,
            height=1.0,
            earth=np.zeros((1, 1, 1)),
            albedo=np.zeros((1, 1, 1)),
            solar=np.zeros((1, 1, 1)),
            panel=np.zeros((1, 1, 1)),
            structure=np.zeros((1, 1, 1)),
            space=np.ones((1, 1, 1)),
            eclipse=np.array([False]),
        )
        warm_profile = Loading(
            name='source',
            u=np.array([0.0]),
            width=1.0,
            height=1.0,
            earth=np.zeros((1, 1, 1)),
            albedo=np.zeros((1, 1, 1)),
            solar=np.zeros((1, 1, 1)),
            panel=np.ones((1, 1, 1)) * 0.1,
            structure=np.zeros((1, 1, 1)),
            space=np.ones((1, 1, 1)) * 0.9,
            eclipse=np.array([False]),
        )

        cold = background(cold_profile)
        self.assertTrue(np.allclose(cold.panel, 0.0, atol=1e-12))

        with self.assertRaises(ValueError):
            background(warm_profile)

    def test_background_accepts_time_trace_panel_temperature(self):
        profile = Loading(
            name='source',
            u=np.array([0.0, 1.0]),
            width=1.0,
            height=1.0,
            earth=np.zeros((2, 1, 1)),
            albedo=np.zeros((2, 1, 1)),
            solar=np.zeros((2, 1, 1)),
            panel=np.array([[[0.2]], [[0.4]]]),
            structure=np.zeros((2, 1, 1)),
            space=np.ones((2, 1, 1)),
            eclipse=np.array([False, True]),
        )

        scalar_background = background(
            profile,
            solar_panel_temperature_K=np.array([300.0, 320.0]),
            solar_panel_emittance=0.8,
        )
        explicit_background = background(
            profile,
            solar_panel_temperature_K=np.array([[[300.0]], [[320.0]]]),
            solar_panel_emittance=0.8,
        )

        self.assertTrue(np.allclose(scalar_background.panel, explicit_background.panel))

        with self.assertRaises(ValueError):
            background(profile, solar_panel_temperature_K=np.array([300.0]))
        with self.assertRaises(ValueError):
            background(profile, solar_panel_temperature_K=np.array([-1.0, 300.0]))

    def test_facet_loading_propagate_records_surface_extents(self):
        orbit = _sample_orbit()
        realized = build_6u_double_deployable(bus_patch_shape=(2, 2)).realize()
        law = SlewModeSwitch(TargetTracking(*SGR_A), SunTracking(), slew_rate_deg_s=0.5)

        profile = facet_loading_propagate(
            realized,
            'bus_+Y',
            orbit,
            law,
            n=8,
            n_mu=4,
            n_az=12,
            hemi_n_az=16,
            hemi_n_el=8,
        )

        surface = realized.by_name('bus_+Y')
        self.assertEqual(profile.width, surface.width)
        self.assertEqual(profile.height, surface.height)

    def test_thermal_profiles_preserve_surface_extents(self):
        profile = Loading(
            name='source',
            u=np.array([0.0, 1.0]),
            width=0.4,
            height=0.2,
            earth=np.array([[[0.5]], [[0.25]]]),
            albedo=np.array([[[0.2]], [[0.1]]]),
            solar=np.array([[[0.3]], [[0.0]]]),
            panel=np.array([[[0.1]], [[0.1]]]),
            structure=np.zeros((2, 1, 1)),
            space=np.ones((2, 1, 1)),
            eclipse=np.array([False, True]),
        )

        bg = background(
            profile,
            solar_panel_temperature_K=300.0,
            solar_panel_emittance=0.8,
        )
        thermal_profile = steady(
            bg,
            alpha_solar=0.8,
            epsilon=0.9,
        )
        sink_profile = environment(bg)

        self.assertEqual(bg.width, profile.width)
        self.assertEqual(bg.height, profile.height)
        self.assertEqual(thermal_profile.width, profile.width)
        self.assertEqual(thermal_profile.height, profile.height)
        self.assertEqual(sink_profile.width, profile.width)
        self.assertEqual(sink_profile.height, profile.height)

    def test_two_sided_steady_state_aligns_back_face_patch_rows(self):
        front = _sample_background_profile(
            solar=np.array([
                [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
                [[6.0, 5.0, 4.0], [3.0, 2.0, 1.0]],
            ]),
            u=np.array([0.0, 1.0]),
            eclipse=np.array([False, True]),
        )
        back = _sample_background_profile(
            solar=np.array([
                [[10.0, 20.0, 30.0], [40.0, 50.0, 60.0]],
                [[60.0, 50.0, 40.0], [30.0, 20.0, 10.0]],
            ]),
            u=np.array([0.0, 1.0]),
            eclipse=np.array([False, True]),
        )

        profile = steady_two_sided(
            front,
            back,
            alpha_front=1.0,
            epsilon_front=0.5,
            alpha_back=1.0,
            epsilon_back=0.5,
        )
        expected_q = front.solar + np.flip(back.solar, axis=1)

        self.assertTrue(np.allclose(profile.q_absorbed, expected_q))

    def test_two_sided_steady_state_rejects_mismatched_backgrounds(self):
        front = _sample_background_profile()
        back = _sample_background_profile(u=np.array([0.0, 1.1, 2.0]))

        with self.assertRaises(ValueError):
            steady_two_sided(
                front,
                back,
                alpha_front=0.6,
                epsilon_front=0.8,
                alpha_back=0.2,
                epsilon_back=0.9,
            )

    @needs_scipy
    def test_transient_matches_constant_steady_state(self):
        front = _sample_background_profile(solar=np.ones((3, 2, 3)) * 100.0)
        back = _sample_background_profile(solar=np.ones((3, 2, 3)) * 50.0)

        steady = steady_two_sided(
            front,
            back,
            alpha_front=1.0,
            epsilon_front=0.5,
            alpha_back=1.0,
            epsilon_back=0.5,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            transient_result = transient(
                front,
                back,
                alpha_front=1.0,
                epsilon_front=0.5,
                alpha_back=1.0,
                epsilon_back=0.5,
                thermal_capacitance=3600.0,
                orbit_period=5400.0,
                n_orbits=3,
                tol=1e-6,
            )

        self.assertFalse(caught)
        self.assertTrue(np.allclose(transient_result.temperature, steady.temperature, atol=1e-6))

    @needs_scipy
    def test_transient_warns_when_convergence_limit_is_hit(self):
        front = _sample_background_profile(
            solar=np.array([
                [[100.0, 120.0, 140.0], [160.0, 180.0, 200.0]],
                [[0.0, 10.0, 20.0], [30.0, 40.0, 50.0]],
                [[80.0, 90.0, 100.0], [110.0, 120.0, 130.0]],
            ])
        )
        back = _sample_background_profile(
            solar=np.array([
                [[20.0, 30.0, 40.0], [50.0, 60.0, 70.0]],
                [[10.0, 5.0, 0.0], [15.0, 10.0, 5.0]],
                [[25.0, 35.0, 45.0], [55.0, 65.0, 75.0]],
            ])
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            profile = transient(
                front,
                back,
                alpha_front=1.0,
                epsilon_front=0.5,
                alpha_back=1.0,
                epsilon_back=0.5,
                thermal_capacitance=1.0e6,
                orbit_period=5400.0,
                n_orbits=1,
                tol=1e-6,
            )

        self.assertEqual(profile.temperature.shape, front.total.shape)
        self.assertTrue(any(issubclass(item.category, RuntimeWarning) for item in caught))

    @needs_matplotlib
    def test_plot_temperature_trace_accepts_single_profile(self):
        profile = _sample_thermal_profile()
        fig, ax = plt.subplots()
        try:
            returned_ax = plot_temperature_trace(ax, profile)
            self.assertIs(returned_ax, ax)
            self.assertEqual(ax.get_ylabel(), 'temperature [K]')
            self.assertEqual(len(ax.lines), 1)
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_trace_accepts_multiple_profiles(self):
        hot = _sample_thermal_profile(name='hot')
        cold = _sample_thermal_profile(
            name='cold',
            temperature=np.array([
                [[260.0, 262.0], [261.0, 263.0]],
                [[270.0, 275.0], [272.0, 274.0]],
                [[265.0, 267.0], [266.0, 268.0]],
            ]),
        )
        fig, ax = plt.subplots()
        try:
            plot_temperature_trace(ax, [hot, cold])
            self.assertEqual(len(ax.lines), 2)
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_trace_rejects_mismatched_u(self):
        first = _sample_thermal_profile()
        second = _sample_thermal_profile(u=np.array([0.0, 1.0, 2.1]))
        fig, ax = plt.subplots()
        try:
            with self.assertRaises(ValueError):
                plot_temperature_trace(ax, [first, second])
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_trace_rejects_mismatched_eclipse(self):
        first = _sample_thermal_profile()
        second = _sample_thermal_profile(eclipse=np.array([False, False, False]))
        fig, ax = plt.subplots()
        try:
            with self.assertRaises(ValueError):
                plot_temperature_trace(ax, [first, second])
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_heatmap_honors_explicit_k(self):
        profile = _sample_thermal_profile()
        fig, ax = plt.subplots()
        try:
            image, k = plot_temperature_heatmap(ax, profile, k=2)
            self.assertEqual(k, 2)
            self.assertTrue(np.allclose(np.asarray(image.get_array()), profile.temperature[2]))
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_heatmap_selectors_choose_expected_timestep(self):
        profile = _sample_thermal_profile(
            temperature=np.array([
                [[270.0, 271.0], [272.0, 273.0]],
                [[290.0, 291.0], [292.0, 293.0]],
                [[280.0, 281.0], [282.0, 283.0]],
            ]),
        )
        fig, axs = plt.subplots(1, 3)
        try:
            _, peak_k = plot_temperature_heatmap(axs[0], profile, selector='peak')
            _, mean_k = plot_temperature_heatmap(axs[1], profile, selector='mean')
            _, cold_k = plot_temperature_heatmap(axs[2], profile, selector=np.argmin)
            self.assertEqual(peak_k, 1)
            self.assertEqual(mean_k, 1)
            self.assertEqual(cold_k, 0)
        finally:
            plt.close(fig)

    @needs_matplotlib
    def test_plot_temperature_heatmap_requires_surface_extents(self):
        profile = _sample_thermal_profile(width=None)
        fig, ax = plt.subplots()
        try:
            with self.assertRaises(ValueError):
                plot_temperature_heatmap(ax, profile)
        finally:
            plt.close(fig)


if __name__ == '__main__':
    unittest.main()
