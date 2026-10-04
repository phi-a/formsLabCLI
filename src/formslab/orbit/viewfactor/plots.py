"""Matplotlib visualizations for view-factor and occlusion results."""

import matplotlib.pyplot as plt
import numpy as np

from ..geometry.cubesat.inspect import facet_labels, opposite_axis_label


def plot_occlusion_heatmap(ax, visible, azimuth_deg, elevation_deg, *, title):
    """Plot a hemisphere occlusion map as a 2-D azimuth/elevation image.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    visible : np.ndarray, shape (n_patches, n_rays)
        Occlusion mask from ``spacecraft_occlusion_mask``.
    azimuth_deg : np.ndarray, shape (n_az,)
    elevation_deg : np.ndarray, shape (n_el,)
    title : str

    Returns
    -------
    image : AxesImage
    blocked : np.ndarray, shape (n_el, n_az)
        Mean blocked fraction per direction bin.
    """
    blocked = 1.0 - visible.mean(axis=0).reshape(len(elevation_deg), len(azimuth_deg))
    image = ax.imshow(
        blocked,
        extent=[azimuth_deg[0], azimuth_deg[-1], elevation_deg[0], elevation_deg[-1]],
        origin='lower',
        aspect='auto',
        vmin=0.0,
        vmax=1.0,
        cmap='magma',
    )
    ax.set_title(title)
    ax.set_xlabel('local azimuth [deg]')
    ax.set_ylabel('local elevation [deg]')
    return image, blocked


def plot_patch_occlusion_map(ax, visible, surface, *, title):
    """Plot patch-resolved blocked fraction in surface (u, v) space.

    Parameters
    ----------
    ax : matplotlib.axes.Axes
    visible : np.ndarray, shape (n_patches, n_rays)
        Occlusion mask from ``spacecraft_occlusion_mask``.
    surface : RectFacet
        The source surface. Used for physical extent and axis labels.
    title : str

    Returns
    -------
    image : AxesImage
    blocked : np.ndarray, shape (ny, nx)
        Mean blocked fraction per patch.
    """
    ny, nx, _ = surface.patch_centers().shape
    blocked = 1.0 - visible.mean(axis=1).reshape(ny, nx)

    x_mm = 0.5 * surface.width * 1e3
    y_mm = 0.5 * surface.height * 1e3
    image = ax.imshow(
        blocked,
        origin='lower',
        extent=[-x_mm, x_mm, -y_mm, y_mm],
        aspect='equal',
        vmin=0.0,
        vmax=1.0,
        cmap='viridis',
    )

    x_edges = np.linspace(-x_mm, x_mm, nx + 1)
    y_edges = np.linspace(-y_mm, y_mm, ny + 1)
    ax.set_xticks(x_edges, minor=True)
    ax.set_yticks(y_edges, minor=True)
    ax.grid(which='minor', color='white', lw=0.8, alpha=0.55)
    ax.tick_params(which='minor', bottom=False, left=False)
    ax.set_title(title)
    ax.set_xlabel('surface u [mm]')
    ax.set_ylabel('surface v [mm]')

    u_label, v_label, n_label = facet_labels(surface)
    ax.text(0.50,  1.03, f'+v = {v_label}',
            transform=ax.transAxes, ha='center', va='bottom', fontsize=9)
    ax.text(0.50, -0.15, f'-v = {opposite_axis_label(v_label)}',
            transform=ax.transAxes, ha='center', va='top', fontsize=9)
    ax.text(1.03,  0.50, f'+u = {u_label}',
            transform=ax.transAxes, ha='left', va='center', rotation=-90, fontsize=9)
    ax.text(-0.03, 0.50, f'-u = {opposite_axis_label(u_label)}',
            transform=ax.transAxes, ha='right', va='center', rotation=90, fontsize=9)
    ax.text(
        0.02, 0.98, f'normal = {n_label}',
        transform=ax.transAxes, ha='left', va='top', fontsize=9,
        bbox=dict(facecolor='white', edgecolor='none', alpha=0.75),
    )
    return image, blocked
