"""CubeSat-scoped modeling container with layered analysis.

Thin convenience facade over ``geometry``, ``viewfactor``, and ``thermal``:

    from formslab.orbit.file import load
    from formslab.orbit.thermal.pipeline import catalog, CubeSat, view, flux, transient, env

    orbit = load("test/fixtures/plans/leo_noon.orbit")
    sat = CubeSat(catalog("6u_double_deployable"))
    vl = view(sat.geometry, orbit, law)

Layers
------
0  geometry   RealizedGeometry from catalog + mount
1  mesh       patch centers per facet      {name: (ny, nx, 3)}
2  view       geometric view factors       {name: (n, ny, nx)}
3  flux       incident W/m^2               {name: (n, ny, nx)}
4  thermal    temperature K                {name: (n, ny, nx)}
5  env        Tenv K                       {name: (n, ny, nx)}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..geometry.cubesat import (
    CubeSatGeometry,
    RealizedGeometry,
    build_6u_double_deployable,
    mount,
)
from ..propagate.orbit import Orbit
from ..viewfactor import Loading, facet_loading_propagate
from . import (
    Background,
    Env,
    Thermal,
    background as _background_solver,
    environment as _environment_solver,
    steady as _steady_solver,
    transient as _transient_solver,
)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

_CATALOG = {
    '6u_double_deployable': build_6u_double_deployable,
}


def catalog(name: str, **kw) -> CubeSatGeometry:
    """Return a CubeSatGeometry from the named template."""
    if name not in _CATALOG:
        raise KeyError(f"unknown template {name!r}; available: {sorted(_CATALOG)}")
    return _CATALOG[name](**kw)


# ---------------------------------------------------------------------------
# Layer dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Layer:
    """Base contract: dict of {facet_name: array} with shared orbit grid."""
    u: np.ndarray
    data: dict[str, np.ndarray]
    eclipse: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def facets(self) -> tuple[str, ...]:
        return tuple(self.data.keys())

    def get(self, name: str) -> np.ndarray:
        return self.data[name]

    def mean(self, name: str, axis=(1, 2)):
        return self.data[name].mean(axis=axis)


@dataclass(frozen=True)
class ViewLayer(Layer):
    """Geometric view factors per source channel."""
    earth: dict[str, np.ndarray] = field(default_factory=dict)
    albedo: dict[str, np.ndarray] = field(default_factory=dict)
    solar: dict[str, np.ndarray] = field(default_factory=dict)
    panel: dict[str, np.ndarray] = field(default_factory=dict)
    structure: dict[str, np.ndarray] = field(default_factory=dict)
    space: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def profiles(self) -> dict[str, Loading]:
        return self.meta.get('profiles', {})


@dataclass(frozen=True)
class FluxLayer(Layer):
    """Incident W/m^2 per facet."""
    backgrounds: dict[str, Background] = field(default_factory=dict)


@dataclass(frozen=True)
class ThermalLayer(Layer):
    """Temperature [K] per facet."""
    results: dict[str, Thermal] = field(default_factory=dict)


@dataclass(frozen=True)
class EnvLayer(Layer):
    """Radiative environment temperature [K] per facet."""
    results: dict[str, Env] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# One-word factory functions
# ---------------------------------------------------------------------------

def mesh(realized: RealizedGeometry) -> dict[str, np.ndarray]:
    """Return {name: (ny, nx, 3)} patch-center arrays for every facet."""
    return {f.name: f.patch_centers() for f in realized.facets}


def view(realized: RealizedGeometry,
         orbit: Orbit,
         law: Callable,
         facets: list[str] | None = None,
         **kw) -> ViewLayer:
    """Propagate geometric view factors for selected (or all) facets.

    Extra **kw forwarded to ``facet_loading_propagate``
    (n, n_mu, n_az, hemi_n_az, hemi_n_el, ...).
    """
    names = facets if facets is not None else [f.name for f in realized.facets]
    profiles: dict[str, Loading] = {}
    earth, albedo, solar = {}, {}, {}
    panel, structure, space = {}, {}, {}
    data = {}

    for name in names:
        p = facet_loading_propagate(realized, name, orbit, law, **kw)
        profiles[name] = p
        earth[name] = p.earth
        albedo[name] = p.albedo
        solar[name] = p.solar
        panel[name] = p.panel
        structure[name] = p.structure
        space[name] = p.space
        data[name] = p.earth        # default accessor = earth view

    ref = next(iter(profiles.values()))
    return ViewLayer(
        u=ref.u,
        data=data,
        eclipse=ref.eclipse,
        meta={'profiles': profiles},
        earth=earth,
        albedo=albedo,
        solar=solar,
        panel=panel,
        structure=structure,
        space=space,
    )


def flux(vl: ViewLayer,
         facets: list[str] | None = None,
         **kw) -> FluxLayer:
    """Convert view factors to incident W/m^2.

    facets : subset of facet names to process (default: all in vl).
    **kw forwarded to ``thermal.background``
    (solar_panel_temperature_K, solar_panel_emittance, body_temperature, ...).
    """
    names = facets if facets is not None else list(vl.profiles.keys())
    backgrounds: dict[str, Background] = {}
    data = {}

    for name in names:
        bg = _background_solver(vl.profiles[name], **kw)
        backgrounds[name] = bg
        data[name] = bg.total

    return FluxLayer(
        u=vl.u,
        data=data,
        eclipse=vl.eclipse,
        meta={},
        backgrounds=backgrounds,
    )


def steady(fl: FluxLayer,
           *,
           alpha_solar: float,
           epsilon: float,
           facets: list[str] | None = None) -> ThermalLayer:
    """Single-sided steady-state temperature for selected facets."""
    names = facets if facets is not None else list(fl.backgrounds.keys())
    results: dict[str, Thermal] = {}
    data = {}

    for name in names:
        t = _steady_solver(fl.backgrounds[name],
                           alpha_solar=alpha_solar,
                           epsilon=epsilon)
        results[name] = t
        data[name] = t.temperature

    return ThermalLayer(
        u=fl.u,
        data=data,
        eclipse=fl.eclipse,
        meta={'mode': 'steady', 'alpha_solar': alpha_solar, 'epsilon': epsilon},
        results=results,
    )


def transient(fl: FluxLayer,
              *,
              pairs: list[tuple[str, str]],
              alpha_front: float,
              epsilon_front: float,
              alpha_back: float,
              epsilon_back: float,
              capacitance: float,
              period: float,
              **kw) -> ThermalLayer:
    """Two-sided transient temperature for explicit front/back facet pairs.

    pairs : list of (front_name, back_name) tuples; result keyed by front_name.
    fl    : FluxLayer containing backgrounds for both front and back names.
    Extra **kw forwarded to ``thermal.solver.transient`` (n_orbits, tol).
    """
    results: dict[str, Thermal] = {}
    data = {}

    for front_name, back_name in pairs:
        t = _transient_solver(
            fl.backgrounds[front_name],
            fl.backgrounds[back_name],
            alpha_front=alpha_front,
            epsilon_front=epsilon_front,
            alpha_back=alpha_back,
            epsilon_back=epsilon_back,
            thermal_capacitance=capacitance,
            orbit_period=period,
            **kw,
        )
        results[front_name] = t
        data[front_name] = t.temperature

    return ThermalLayer(
        u=fl.u,
        data=data,
        eclipse=fl.eclipse,
        meta={'mode': 'transient'},
        results=results,
    )


def env(fl: FluxLayer,
        facets: list[str] | None = None) -> EnvLayer:
    """Radiative environment temperature Tenv for selected facets."""
    names = facets if facets is not None else list(fl.backgrounds.keys())
    results: dict[str, Env] = {}
    data = {}

    for name in names:
        e = _environment_solver(fl.backgrounds[name])
        results[name] = e
        data[name] = e.Tenv

    return EnvLayer(
        u=fl.u,
        data=data,
        eclipse=fl.eclipse,
        meta={},
        results=results,
    )


# ---------------------------------------------------------------------------
# CubeSat container
# ---------------------------------------------------------------------------

class CubeSat:
    """Realized CubeSat geometry with registered analysis layers.

    Usage
    -----
    sat = CubeSat(catalog('6u_double_deployable'), mount_rotation=mount(...))
    vl = view(sat.geometry, orbit, law)
    sat.register('view', vl)
    sat.layer('view').earth['bus_+Y']   # (n, ny, nx)
    """

    def __init__(self,
                 geometry_or_template,
                 *,
                 state: dict | None = None,
                 mount_rotation=None,
                 mount_offset=None):
        if isinstance(geometry_or_template, CubeSatGeometry):
            self._realized = geometry_or_template.realize(
                state,
                mount_rotation=mount_rotation,
                mount_offset=mount_offset,
            )
        elif isinstance(geometry_or_template, RealizedGeometry):
            self._realized = geometry_or_template
        else:
            raise TypeError("pass a CubeSatGeometry or RealizedGeometry")

        self._layers: dict[str, Layer] = {}

    # -- geometry access ---------------------------------------------------

    @property
    def geometry(self) -> RealizedGeometry:
        return self._realized

    @property
    def facets(self) -> tuple[str, ...]:
        return self._realized.names()

    @property
    def mesh(self) -> dict[str, np.ndarray]:
        return mesh(self._realized)

    # -- layer management --------------------------------------------------

    def register(self, name: str, layer: Layer):
        """Attach a named analysis layer."""
        if not isinstance(layer, Layer):
            raise TypeError("layer must be a Layer instance")
        self._layers[name] = layer

    def layer(self, name: str) -> Layer:
        """Retrieve a registered layer by name."""
        if name not in self._layers:
            raise KeyError(f"no layer {name!r}; registered: {sorted(self._layers)}")
        return self._layers[name]

    def layers(self) -> tuple[str, ...]:
        return tuple(self._layers.keys())

    # -- persistence -------------------------------------------------------

    def to_json(self, path):
        return self._realized.to_json(path)

    @classmethod
    def from_json(cls, path):
        return cls(RealizedGeometry.from_json(path))

    def __repr__(self):
        n = len(self._realized.facets)
        l = ', '.join(self._layers) if self._layers else 'none'
        return f"CubeSat({n} facets, layers=[{l}])"
