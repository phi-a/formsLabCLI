"""Matplotlib visualizations for realized CubeSat body geometry."""

import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


def _set_axes_equal(ax):
    """Force equal aspect ratio on a 3-D axes."""
    x_limits = np.array(ax.get_xlim3d())
    y_limits = np.array(ax.get_ylim3d())
    z_limits = np.array(ax.get_zlim3d())

    spans = np.array([
        x_limits[1] - x_limits[0],
        y_limits[1] - y_limits[0],
        z_limits[1] - z_limits[0],
    ])
    centers = np.array([x_limits.mean(), y_limits.mean(), z_limits.mean()])
    radius = 0.5 * max(spans.max(), 1e-6)

    ax.set_xlim3d([centers[0] - radius, centers[0] + radius])
    ax.set_ylim3d([centers[1] - radius, centers[1] + radius])
    ax.set_zlim3d([centers[2] - radius, centers[2] + radius])


def _default_body_axis_scale(realized):
    corners = np.concatenate([surface.corners() for surface in realized.facets], axis=0)
    spans = np.ptp(corners, axis=0)
    return 0.35 * max(spans.max(), 1e-6)


def _draw_body_axes(ax, scale):
    origin = np.zeros(3, dtype=float)
    axes = (
        ('+X', np.array([1.0, 0.0, 0.0]), 'tab:red'),
        ('+Y', np.array([0.0, 1.0, 0.0]), 'tab:green'),
        ('+Z', np.array([0.0, 0.0, 1.0]), 'tab:blue'),
    )
    for label, direction, color in axes:
        ax.quiver(
            origin[0], origin[1], origin[2],
            direction[0], direction[1], direction[2],
            length=scale,
            color=color,
            linewidth=2.0,
            arrow_length_ratio=0.14,
        )
        tip = 1.08 * scale * direction
        ax.text(tip[0], tip[1], tip[2], label, color=color, fontsize=10, weight='bold')


def plot_realized_geometry(realized, *, title='Spacecraft geometry',
                           normal_scale=0.04,
                           show_body_axes=True,
                           body_axis_scale=None):
    """Render all surfaces in *realized* as shaded rectangles with normal arrows.

    Returns
    -------
    fig, ax
    """
    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection='3d')

    for surface in realized.facets:
        if 'solar_panel' in surface.tags:
            color = '#5B8FF9'
        elif 'bus' in surface.tags:
            color = '#C7C7C7'
        else:
            color = '#8C8C8C'

        corners = surface.corners()
        poly = Poly3DCollection([corners], facecolors=color, edgecolors='black', alpha=0.70)
        ax.add_collection3d(poly)

        center = surface.center
        normal = surface.normal
        ax.quiver(
            center[0], center[1], center[2],
            normal[0], normal[1], normal[2],
            length=normal_scale,
            color='crimson',
            linewidth=1.2,
        )
        ax.text(center[0], center[1], center[2], surface.name, fontsize=8)

    if show_body_axes:
        scale = _default_body_axis_scale(realized) if body_axis_scale is None else body_axis_scale
        _draw_body_axes(ax, scale)

    ax.set_title(title)
    ax.set_xlabel('body x [m]')
    ax.set_ylabel('body y [m]')
    ax.set_zlabel('body z [m]')
    ax.view_init(elev=22, azim=-58)
    _set_axes_equal(ax)
    plt.tight_layout()
    return fig, ax
