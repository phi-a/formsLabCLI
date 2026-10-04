"""Default CubeSat geometry builders."""

import math

import numpy as np

from .surfaces import CubeSatGeometry, RectFacet, FacetNode, flip_facet


def build_6u_double_deployable(*,
                               bus_x=0.100,
                               bus_y=0.2263,
                               bus_z=0.3405,
                               leaf_y=None,
                               leaf_z=None,
                               wing_span=None,
                               wing_length=None,
                               bus_patch_shape=None,
                               wing_patch_shape=None):
    """Build a 6U CubeSat with two double-leaf deployable solar-panel wings.

    The default example is a 6U bus with one two-leaf wing on each side,
    attached along the top-edge 3U rail and deployed into a common plane.
    Each leaf is sized explicitly in body-frame `y x z` dimensions.
    """
    if leaf_y is None:
        leaf_y = bus_y if wing_span is None else wing_span
    elif wing_span is not None and not math.isclose(leaf_y, wing_span, abs_tol=1e-12):
        raise ValueError("leaf_y and wing_span disagree")

    if leaf_z is None:
        leaf_z = bus_z if wing_length is None else wing_length
    elif wing_length is not None and not math.isclose(leaf_z, wing_length, abs_tol=1e-12):
        raise ValueError("leaf_z and wing_length disagree")

    nodes = []

    def add_root(name, center, normal, u_axis, width, height, tags=(), patch_shape=None):
        nodes.append(FacetNode(
            facet=RectFacet(
                name=name,
                center=np.asarray(center, dtype=float),
                normal=np.asarray(normal, dtype=float),
                u_axis=np.asarray(u_axis, dtype=float),
                width=width,
                height=height,
                patch_shape=patch_shape,
                tags=tags,
            )
        ))

    add_root(
        'bus_+X',
        [0.5 * bus_x, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        bus_z,
        bus_y,
        tags=('bus', '+X'),
        patch_shape=bus_patch_shape,
    )
    add_root(
        'bus_-X',
        [-0.5 * bus_x, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        bus_z,
        bus_y,
        tags=('bus', '-X'),
        patch_shape=bus_patch_shape,
    )
    add_root(
        'bus_+Y',
        [0.0, 0.5 * bus_y, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        bus_z,
        bus_x,
        tags=('bus', '+Y'),
        patch_shape=bus_patch_shape,
    )
    add_root(
        'bus_-Y',
        [0.0, -0.5 * bus_y, 0.0],
        [0.0, -1.0, 0.0],
        [0.0, 0.0, 1.0],
        bus_z,
        bus_x,
        tags=('bus', '-Y'),
        patch_shape=bus_patch_shape,
    )
    add_root(
        'bus_+Z',
        [0.0, 0.0, 0.5 * bus_z],
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
        bus_x,
        bus_y,
        tags=('bus', '+Z'),
        patch_shape=bus_patch_shape,
    )
    add_root(
        'bus_-Z',
        [0.0, 0.0, -0.5 * bus_z],
        [0.0, 0.0, -1.0],
        [1.0, 0.0, 0.0],
        bus_x,
        bus_y,
        tags=('bus', '-Z'),
        patch_shape=bus_patch_shape,
    )

    def add_deployable_pair(facet, *,
                            parent=None,
                            hinge_origin=None,
                            hinge_axis=None,
                            state_key=None,
                            default_angle=0.0):
        nodes.append(FacetNode(
            facet=facet,
            parent=parent,
            hinge_origin=hinge_origin,
            hinge_axis=hinge_axis,
            state_key=state_key,
            default_angle=default_angle,
        ))
        nodes.append(FacetNode(
            facet=flip_facet(
                facet,
                tags=(
                    'deployable',
                    'solar_panel_back',
                    'solar_array',
                    *tuple(
                        tag for tag in facet.tags
                        if tag not in {'deployable', 'solar_panel', 'solar_array'}
                    ),
                ),
            ),
            parent=parent,
            hinge_origin=hinge_origin,
            hinge_axis=hinge_axis,
            state_key=state_key,
            default_angle=default_angle,
        ))

    # Port-side inner wing, stowed against +X and deployed +90 deg into +Y.
    add_deployable_pair(
        RectFacet(
            name='wing_port_inner',
            center=np.array([0.5 * bus_x, 0.5 * bus_y - 0.5 * leaf_y, 0.0]),
            normal=np.array([1.0, 0.0, 0.0]),
            u_axis=np.array([0.0, 0.0, 1.0]),
            width=leaf_z,
            height=leaf_y,
            patch_shape=wing_patch_shape,
            tags=('deployable', 'solar_panel', 'solar_array', 'port', 'inner'),
        ),
        hinge_origin=np.array([0.5 * bus_x, 0.5 * bus_y, 0.0]),
        hinge_axis=np.array([0.0, 0.0, 1.0]),
        state_key='wing_port_inner_angle',
        default_angle=math.pi / 2.0,
    )

    # Outer leaf is defined in the realized frame of the inner leaf.
    add_deployable_pair(
        RectFacet(
            name='wing_port_outer',
            center=np.array([0.0, 0.0, 0.0]),
            normal=np.array([0.0, 0.0, -1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=leaf_z,
            height=leaf_y,
            patch_shape=wing_patch_shape,
            tags=('deployable', 'solar_panel', 'solar_array', 'port', 'outer'),
        ),
        parent='wing_port_inner',
        hinge_origin=np.array([0.0, 0.5 * leaf_y, 0.0]),
        hinge_axis=np.array([1.0, 0.0, 0.0]),
        state_key='wing_port_outer_angle',
        default_angle=math.pi,
    )

    # Starboard-side inner wing, deployed -90 deg so both wings face +Y.
    add_deployable_pair(
        RectFacet(
            name='wing_starboard_inner',
            center=np.array([-0.5 * bus_x, 0.5 * bus_y - 0.5 * leaf_y, 0.0]),
            normal=np.array([-1.0, 0.0, 0.0]),
            u_axis=np.array([0.0, 0.0, 1.0]),
            width=leaf_z,
            height=leaf_y,
            patch_shape=wing_patch_shape,
            tags=('deployable', 'solar_panel', 'solar_array', 'starboard', 'inner'),
        ),
        hinge_origin=np.array([-0.5 * bus_x, 0.5 * bus_y, 0.0]),
        hinge_axis=np.array([0.0, 0.0, 1.0]),
        state_key='wing_starboard_inner_angle',
        default_angle=-math.pi / 2.0,
    )

    add_deployable_pair(
        RectFacet(
            name='wing_starboard_outer',
            center=np.array([0.0, 0.0, 0.0]),
            normal=np.array([0.0, 0.0, -1.0]),
            u_axis=np.array([1.0, 0.0, 0.0]),
            width=leaf_z,
            height=leaf_y,
            patch_shape=wing_patch_shape,
            tags=('deployable', 'solar_panel', 'solar_array', 'starboard', 'outer'),
        ),
        parent='wing_starboard_inner',
        hinge_origin=np.array([0.0, -0.5 * leaf_y, 0.0]),
        hinge_axis=np.array([1.0, 0.0, 0.0]),
        state_key='wing_starboard_outer_angle',
        default_angle=-math.pi,
    )

    return CubeSatGeometry(
        nodes=tuple(nodes),
        metadata={
            'example': '6U_double_deployable',
            'bus_dimensions_m': (bus_x, bus_y, bus_z),
            'leaf_y_m': leaf_y,
            'leaf_z_m': leaf_z,
            'leaf_dimensions_m': (leaf_y, leaf_z),
            'wing_span_m': leaf_y,
            'wing_length_m': leaf_z,
        },
    )
