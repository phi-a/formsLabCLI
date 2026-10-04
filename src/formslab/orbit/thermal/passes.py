"""Pass-level thermal feasibility evaluation.

``evaluate_pass`` runs the full geometry → view-factor → flux → transient panel
→ radiator environment pipeline (via ``pipeline``'s factories) for a single
orbit and reduces the result to per-pass measures.

The function is intentionally pure: it takes an Orbit, an attitude law, a
realized geometry, and a list of observable arcs (u_start, u_end), and returns
a structured result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from .pipeline import env, flux, transient, view
from ..geometry.cubesat import RealizedGeometry
from ..propagate.orbit import Orbit


# Default view-factor quadrature: matches main.ipynb production settings.
# Honest but slow: tens of seconds per call. Override via ``vf_kw=`` for fast
# development runs.
_VF_DEFAULTS = dict(n=60, n_mu=24, n_az=72, hemi_n_az=73, hemi_n_el=33)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RadiatorMeasures:
    """Per-radiator pass-level temperature figures, one bracket."""
    name: str
    Tenv_observable_start_K:   float           # value at first u-sample inside the observable arc
    Tenv_observable_peak_K:    float
    Tenv_observable_trough_K:  float
    Tenv_observable_mean_K:    float
    Tenv_full_orbit_peak_K:    float
    Tenv_full_orbit_trough_K:  float
    Tenv_full_orbit_mean_K:    float
    time_above_threshold_s:    float | None    # None if T_env_max is None
    Tenv_trace:                np.ndarray      # (n_u,) — radiative mean over patches


@dataclass(frozen=True)
class BracketResult:
    """All radiator measures + driving panel temperature for one coating bracket."""
    bracket: str                                # "HOT" or "COLD"
    panel_temperature_K: np.ndarray             # area-weighted T_panel(u), shape (n_u,)
    radiators: dict[str, RadiatorMeasures]


@dataclass(frozen=True)
class ThermalPassResult:
    """End-to-end output of ``evaluate_pass``."""
    u_grid:           np.ndarray                # (n_u,) rad
    utc_grid:         list                      # [datetime] of length n_u
    eclipse_mask:     np.ndarray                # boolean (n_u,)
    observable_mask:  np.ndarray                # boolean (n_u,)
    brackets:         dict[str, BracketResult]  # "HOT" / "COLD"
    radiator_facets:  tuple[str, ...]
    observable_arcs:  tuple[tuple[float, float], ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _radiative_mean(Tenv_patches: np.ndarray) -> np.ndarray:
    """(n_u, ny, nx) -> (n_u,) via fourth-power patch average then ^¼."""
    return np.power(np.nanmean(Tenv_patches**4, axis=(1, 2)), 0.25)


def _wrap_pi(a: np.ndarray) -> np.ndarray:
    return (a + np.pi) % (2 * np.pi) - np.pi


def _arc_mask(u_grid: np.ndarray, arcs: Sequence[tuple[float, float]]) -> np.ndarray:
    """Boolean mask over u_grid for membership in any of the (u_start, u_end) arcs.

    Wrap-aware: an arc with u_start > u_end is treated as wrapping past 2π.
    """
    out = np.zeros_like(u_grid, dtype=bool)
    for u_start, u_end in arcs:
        # Translate to u_grid - u_start mod 2π and check ≤ width.
        width = (u_end - u_start) % (2 * np.pi)
        offset = (u_grid - u_start) % (2 * np.pi)
        out |= offset <= width
    return out


def _eclipse_mask(orbit: Orbit, u_grid: np.ndarray) -> np.ndarray:
    if orbit.nu <= 0:
        return np.zeros_like(u_grid, dtype=bool)
    delta = _wrap_pi(u_grid - orbit.uc_sun - np.pi)
    return np.abs(delta) < orbit.nu


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate_pass(
    orbit: Orbit,
    law: Callable,
    geometry: RealizedGeometry,
    observable_arcs: Sequence[tuple[float, float]],
    *,
    radiator_facets: Sequence[str],
    panel_pairs: Sequence[tuple[str, str]],
    panel_front: dict,                          # {"alpha": float, "epsilon": float}
    panel_back_brackets: dict[str, dict],       # {"HOT": {"alpha": ..., "epsilon": ...}, "COLD": {...}}
    capacitance: float,
    T_env_max: float | None = None,
    vf_kw: dict | None = None,
    n_orbits: int = 10,
    tol_K: float = 0.5,
) -> ThermalPassResult:
    """Run the full thermal pipeline for one orbit and reduce to pass measures.

    Parameters
    ----------
    orbit
        Orbit configuration (``Orbit.from_epoch(...)``); supplies u→UTC mapping.
    law
        Attitude law (any callable accepted by ``cubesat.view``).
    geometry
        Realized CubeSat geometry. Must contain all radiator and panel facets.
    observable_arcs
        List of (u_start, u_end) tuples in radians. Typically the eclipse ∩
        target-clear intersection produced by the image scheduler.
        Wrap-aware: u_start > u_end is treated as wrapping past 2π.
    radiator_facets
        Facet names of the radiator faces to evaluate (e.g. ['bus_+X', 'bus_-X']).
    panel_pairs
        Solar-panel front/back facet name pairs. Front names are area-weighted
        to produce the driving T_panel(u) trace.
    panel_front
        ``{"alpha": float, "epsilon": float}`` for the panel front (cell side).
    panel_back_brackets
        ``{"HOT": {"alpha", "epsilon"}, "COLD": {...}}`` — the two coating
        brackets to evaluate. Names become the keys of ``result.brackets``.
    capacitance
        Areal thermal capacitance for the panel substrate [J/(m²·K)].
    T_env_max
        Optional Tenv threshold [K]. If provided, ``time_above_threshold_s`` is
        populated; otherwise it is None.
    vf_kw
        View-factor quadrature kwargs forwarded to ``cubesat.view``. Defaults to
        the production settings (n=60, n_mu=24, n_az=72, hemi_n_az=73, hemi_n_el=33)
        — tens of seconds per call. Use a coarser grid for development.
    n_orbits, tol_K
        Forwarded to ``cubesat.transient`` (panel transient solver convergence).
    """
    vf_kw = {**_VF_DEFAULTS, **(vf_kw or {})}

    # Layer 0–2: geometry mesh → view factors per facet, per u-sample.
    panel_fronts = [front for front, _ in panel_pairs]
    panel_backs  = [back  for _, back  in panel_pairs]
    needed = list(set(list(radiator_facets) + panel_fronts + panel_backs))
    vl = view(geometry, orbit, law, facets=needed, **vf_kw)

    u_grid = vl.u
    eclipse_mask    = _eclipse_mask(orbit, u_grid)
    observable_mask = _arc_mask(u_grid, observable_arcs)
    utc_grid        = orbit.utc_at(u_grid)

    # Time per u-sample (uniform grid spanning [0, 2π) at orbit.n).
    dt_per_sample_s = (2 * np.pi / orbit.n) / u_grid.size

    # Panel area weights for T_panel(u) reduction.
    front_areas = np.array([geometry.by_name(n).area for n in panel_fronts], dtype=float)

    brackets: dict[str, BracketResult] = {}

    for bracket_name, bracket in panel_back_brackets.items():
        # Layer 3: incident flux on panel front+back, no panel-IR coupling yet.
        fl_panel = flux(vl, facets=panel_fronts + panel_backs, solar_panel_temperature_K=0.0)

        # Layer 4: transient two-sided panel temperature.
        tl_panel = transient(
            fl_panel,
            pairs=list(panel_pairs),
            alpha_front=panel_front["alpha"],
            epsilon_front=panel_front["epsilon"],
            alpha_back=bracket["alpha"],
            epsilon_back=bracket["epsilon"],
            capacitance=capacitance,
            period=orbit.period,
            n_orbits=n_orbits,
            tol=tol_K,
        )

        # Area-weighted panel temperature trace driving the radiator environment.
        T_panel = np.average(
            np.stack([tl_panel.mean(n) for n in panel_fronts], axis=0),
            axis=0, weights=front_areas,
        )

        # Layer 3' + 5: radiator flux with real T_panel(u), then env.
        fl_rad = flux(vl, facets=list(radiator_facets), solar_panel_temperature_K=T_panel)
        el_rad = env(fl_rad)

        # Reduce per radiator face.
        radiators: dict[str, RadiatorMeasures] = {}
        for name in radiator_facets:
            Tenv_face = _radiative_mean(el_rad.results[name].Tenv)        # (n_u,)

            obs = Tenv_face[observable_mask]
            full = Tenv_face

            if obs.size == 0:
                # No observable samples on this orbit — fall back to NaNs so
                # downstream code can detect the absence cleanly.
                obs_start = obs_peak = obs_trough = obs_mean = float("nan")
            else:
                obs_start  = float(Tenv_face[int(np.argmax(observable_mask))])
                obs_peak   = float(np.nanmax(obs))
                obs_trough = float(np.nanmin(obs))
                obs_mean   = float(np.power(np.nanmean(obs**4), 0.25))

            full_peak   = float(np.nanmax(full))
            full_trough = float(np.nanmin(full))
            full_mean   = float(np.power(np.nanmean(full**4), 0.25))

            t_above = None
            if T_env_max is not None:
                above = observable_mask & (Tenv_face > T_env_max)
                t_above = float(np.count_nonzero(above) * dt_per_sample_s)

            radiators[name] = RadiatorMeasures(
                name=name,
                Tenv_observable_start_K=obs_start,
                Tenv_observable_peak_K=obs_peak,
                Tenv_observable_trough_K=obs_trough,
                Tenv_observable_mean_K=obs_mean,
                Tenv_full_orbit_peak_K=full_peak,
                Tenv_full_orbit_trough_K=full_trough,
                Tenv_full_orbit_mean_K=full_mean,
                time_above_threshold_s=t_above,
                Tenv_trace=Tenv_face,
            )

        brackets[bracket_name] = BracketResult(
            bracket=bracket_name,
            panel_temperature_K=T_panel,
            radiators=radiators,
        )

    return ThermalPassResult(
        u_grid=u_grid,
        utc_grid=utc_grid,
        eclipse_mask=eclipse_mask,
        observable_mask=observable_mask,
        brackets=brackets,
        radiator_facets=tuple(radiator_facets),
        observable_arcs=tuple((float(s), float(e)) for s, e in observable_arcs),
    )
