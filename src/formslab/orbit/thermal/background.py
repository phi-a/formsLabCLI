"""Thermal background conversion from geometric facet loading profiles."""

from dataclasses import dataclass

import numpy as np

from ..propagate.constants import A_ALB, J_IR, S0
from ..viewfactor import Loading

from .materials import SIGMA_SB


@dataclass(frozen=True)
class Background:
    """Incident radiative background components for one named facet."""
    name: str
    u: np.ndarray
    width: float
    height: float
    earth: np.ndarray
    albedo: np.ndarray
    solar: np.ndarray
    panel: np.ndarray
    structure: np.ndarray
    total: np.ndarray
    eclipse: np.ndarray

    def average_total(self):
        return self.total.mean(axis=(1, 2))


def _broadcast_temperature(u, temperature, view):
    """Validate and broadcast a temperature input against a view-factor field."""
    t = np.asarray(temperature, dtype=float)
    if np.any(t < 0.0):
        raise ValueError("temperature must be non-negative")
    if t.ndim == 0:
        return t
    if t.ndim == 1:
        if t.shape[0] != u.shape[0]:
            raise ValueError(
                "1-D temperature must match the orbit grid length"
            )
        t = t[:, None, None]
    try:
        np.broadcast_shapes(t.shape, view.shape)
    except ValueError as exc:
        raise ValueError(
            "temperature must be scalar, 1-D orbit trace, "
            "or broadcastable to the view-factor field"
        ) from exc
    return t


def background(profile, *,
               s0=S0,
               j_ir=J_IR,
               a_earth=A_ALB,
               solar_panel_temperature_K=None,
               solar_panel_emittance=1.0,
               body_temperature=None,
               body_emittance=1.0):
    """Convert geometric loading factors into incident radiative background."""
    if not isinstance(profile, Loading):
        raise TypeError("profile must be a Loading instance")
    if solar_panel_emittance < 0.0 or solar_panel_emittance > 1.0:
        raise ValueError("solar_panel_emittance must lie in [0, 1]")
    if body_emittance < 0.0 or body_emittance > 1.0:
        raise ValueError("body_emittance must lie in [0, 1]")

    earth = j_ir * profile.earth
    albedo = a_earth * s0 * profile.albedo
    solar = s0 * profile.solar

    warm_view_present = bool(np.any(profile.panel > 1e-15))
    if solar_panel_temperature_K is None:
        if warm_view_present:
            raise ValueError(
                "solar_panel_temperature_K is required when panel view is non-zero"
            )
        panel = np.zeros_like(profile.panel)
    else:
        panel_temperature = _broadcast_temperature(
            profile.u, solar_panel_temperature_K, profile.panel,
        )
        panel = (
            solar_panel_emittance
            * SIGMA_SB
            * panel_temperature ** 4
            * profile.panel
        )

    if body_temperature is None:
        structure = np.zeros_like(profile.structure)
    else:
        body_temp = _broadcast_temperature(
            profile.u, body_temperature, profile.structure,
        )
        structure = (
            body_emittance * SIGMA_SB * body_temp ** 4
            * profile.structure
        )

    total = earth + albedo + solar + panel + structure
    return Background(
        name=profile.name,
        u=profile.u,
        width=profile.width,
        height=profile.height,
        earth=earth,
        albedo=albedo,
        solar=solar,
        panel=panel,
        structure=structure,
        total=total,
        eclipse=profile.eclipse,
    )
