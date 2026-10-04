"""Matplotlib visualizations for thermal profile results."""

from collections.abc import Sequence

import matplotlib.pyplot as plt
import numpy as np

from .background import Background
from .solver import Thermal


def _coerce_profiles(profiles):
    if isinstance(profiles, Thermal):
        return [profiles]
    if isinstance(profiles, Sequence):
        coerced = list(profiles)
    else:
        raise TypeError("profiles must be a Thermal or a sequence of them")
    if not coerced:
        raise ValueError("profiles must not be empty")
    if not all(isinstance(profile, Thermal) for profile in coerced):
        raise TypeError("all items in profiles must be Thermal instances")
    return coerced


def _validate_shared_orbit_grid(profiles):
    u_ref = np.asarray(profiles[0].u, dtype=float)
    eclipse_ref = np.asarray(profiles[0].eclipse, dtype=bool)
    for profile in profiles[1:]:
        u = np.asarray(profile.u, dtype=float)
        eclipse = np.asarray(profile.eclipse, dtype=bool)
        if u.shape != u_ref.shape or not np.allclose(u, u_ref):
            raise ValueError("all profiles must share the same u samples")
        if eclipse.shape != eclipse_ref.shape or not np.array_equal(eclipse, eclipse_ref):
            raise ValueError("all profiles must share the same eclipse mask")
    return u_ref, eclipse_ref


def _sample_edges(x):
    x = np.asarray(x, dtype=float)
    if x.ndim != 1 or x.size == 0:
        raise ValueError("x must be a non-empty 1-D array")

    if x.size == 1:
        return np.array([x[0] - 0.5, x[0] + 0.5], dtype=float)

    edges = np.empty(x.size + 1, dtype=float)
    edges[1:-1] = 0.5 * (x[:-1] + x[1:])
    edges[0] = x[0] - 0.5 * (x[1] - x[0])
    edges[-1] = x[-1] + 0.5 * (x[-1] - x[-2])
    return edges


def _shade_eclipse(ax, u_deg, eclipse):
    edges = _sample_edges(u_deg)
    eclipse = np.asarray(eclipse, dtype=bool)

    run_start = None
    for index, in_eclipse in enumerate(eclipse):
        if in_eclipse and run_start is None:
            run_start = index
        elif not in_eclipse and run_start is not None:
            ax.axvspan(edges[run_start], edges[index], color='0.92', zorder=0)
            run_start = None

    if run_start is not None:
        ax.axvspan(edges[run_start], edges[-1], color='0.92', zorder=0)


def _select_timestep(profile, *, k=None, selector='peak'):
    n_time = profile.temperature.shape[0]

    if k is not None:
        index = int(k)
    elif callable(selector):
        index = int(selector(profile.average_temperature()))
    elif selector == 'peak':
        index = int(np.argmax(profile.peak_temperature()))
    elif selector == 'mean':
        index = int(np.argmax(profile.average_temperature()))
    else:
        raise ValueError("selector must be 'peak', 'mean', or a callable")

    if index < 0 or index >= n_time:
        raise ValueError(f"k must be in [0, {n_time - 1}]")
    return index


def _surface_extent_mm(profile):
    if profile.width is None or profile.height is None:
        raise ValueError("profile.width and profile.height are required for heatmap plots")

    width = float(profile.width)
    height = float(profile.height)
    if width <= 0.0 or height <= 0.0:
        raise ValueError("profile.width and profile.height must be positive")

    return 0.5 * width * 1e3, 0.5 * height * 1e3


def plot_temperature_trace(ax, profiles, *, labels=None, title=None, shade_eclipse=True):
    """Plot min/mean/max patch temperature envelopes over one orbit."""
    profiles = _coerce_profiles(profiles)
    u_ref, eclipse_ref = _validate_shared_orbit_grid(profiles)

    if labels is None:
        labels = [profile.name for profile in profiles]
    else:
        labels = list(labels)
        if len(labels) != len(profiles):
            raise ValueError("labels must have the same length as profiles")

    u_deg = np.degrees(u_ref)
    if shade_eclipse:
        _shade_eclipse(ax, u_deg, eclipse_ref)

    for profile, label in zip(profiles, labels):
        mean_temperature = profile.average_temperature()
        min_temperature = profile.min_temperature()
        max_temperature = profile.peak_temperature()

        line = ax.plot(u_deg, mean_temperature, linewidth=2.0, label=label)[0]
        ax.fill_between(
            u_deg,
            min_temperature,
            max_temperature,
            color=line.get_color(),
            alpha=0.2,
            linewidth=0.0,
        )

    ax.set_xlabel('argument of latitude u [deg]')
    ax.set_ylabel('temperature [K]')
    ax.set_title('Orbit Temperature Envelope' if title is None else title)
    ax.grid(True, alpha=0.3)
    return ax


def plot_temperature_heatmap(ax, profile, *, k=None, selector='peak', title=None, cmap='inferno'):
    """Plot a patch temperature map for a selected timestep."""
    if not isinstance(profile, Thermal):
        raise TypeError("profile must be a Thermal instance")

    k = _select_timestep(profile, k=k, selector=selector)
    ny, nx = profile.temperature.shape[1:]
    x_mm, y_mm = _surface_extent_mm(profile)

    image = ax.imshow(
        profile.temperature[k],
        origin='lower',
        extent=[-x_mm, x_mm, -y_mm, y_mm],
        aspect='equal',
        cmap=cmap,
    )

    x_edges = np.linspace(-x_mm, x_mm, nx + 1)
    y_edges = np.linspace(-y_mm, y_mm, ny + 1)
    ax.set_xticks(x_edges, minor=True)
    ax.set_yticks(y_edges, minor=True)
    ax.grid(which='minor', color='white', lw=0.8, alpha=0.55)
    ax.tick_params(which='minor', bottom=False, left=False)
    ax.set_xlabel('surface u [mm]')
    ax.set_ylabel('surface v [mm]')

    if title is None:
        ax.set_title(
            f"{profile.name} temperature at u = {np.degrees(profile.u[k]):.2f} deg"
        )
    else:
        ax.set_title(title)

    return image, k


_FLUX_COMPONENTS = {
    'earth':  ('Earth IR',     '#2ecc71'),
    'albedo': ('Albedo',       '#3498db'),
    'solar':  ('Direct Solar', '#f39c12'),
}


def plot_flux_trace(ax, bg, *,
                    components=('earth', 'albedo', 'solar'),
                    title=None, shade_eclipse=True):
    """Plot mean ± min/max patch flux envelopes for selected components.

    Parameters
    ----------
    ax : matplotlib Axes
    bg : Background
    components : sequence of str — any of 'earth', 'albedo', 'solar'
    title : str, optional
    shade_eclipse : bool
    """
    if not isinstance(bg, Background):
        raise TypeError("bg must be a Background instance")

    u_deg = np.degrees(np.asarray(bg.u, dtype=float))
    eclipse = np.asarray(bg.eclipse, dtype=bool)

    if shade_eclipse:
        _shade_eclipse(ax, u_deg, eclipse)

    for comp in components:
        if comp not in _FLUX_COMPONENTS:
            raise ValueError(f"unknown component {comp!r}; choose from {list(_FLUX_COMPONENTS)}")
        label, color = _FLUX_COMPONENTS[comp]
        data = getattr(bg, comp)
        mean = data.mean(axis=(1, 2))
        lo   = data.min(axis=(1, 2))
        hi   = data.max(axis=(1, 2))
        ax.fill_between(u_deg, lo, hi, color=color, alpha=0.18, linewidth=0)
        ax.plot(u_deg, mean, lw=1.8, color=color, label=label)

    ax.set_xlim(u_deg[0], u_deg[-1])
    ax.set_xlabel('argument of latitude u [deg]')
    ax.set_ylabel('incident flux [W m⁻²]')
    ax.set_title(
        f'Incident flux — {bg.name}' if title is None else title
    )
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    return ax


__all__ = [
    'plot_temperature_trace',
    'plot_temperature_heatmap',
    'plot_flux_trace',
]
