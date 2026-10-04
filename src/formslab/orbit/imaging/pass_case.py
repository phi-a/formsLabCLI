"""One example imaging pass: geometry -> open-sky window -> schedule.

Bundles everything a single umbral pass needs into one immutable object, so
that figures and reports read numbers rather than recomputing geometry. There
is no matplotlib here on purpose -- the poster numbers stay testable without a
plotting backend.

Time convention
---------------
``t = 0`` at umbra entry. Every span returned by this module is seconds from
that instant, so the orbit-clock angles and the unrolled timeline are two views
of the same numbers.

One geometry, one truth
-----------------------
``eclipse_s`` is derived from ``geom.umbra_half_rad`` rather than from
:func:`orbit.propagate.orbit.eclipse_duration`. ``visibility_geometry`` takes no
Sun-distance argument while ``eclipse_duration`` does, so mixing the two makes
the drawn arcs and the drawn timeline disagree by ~0.07 s. Small, but it would
show up as a visible seam where the readout blocks are supposed to end flush
with sunrise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from ..propagate.constants import R_E
from ..propagate.orbit import j2_raan_rate, propagate_raan, sun_beta_uc
from ..visibility.arcs import ArcWindow, CircularArc, intersect_arcs
from ..visibility.target import (
    GALACTIC_CENTER,
    OCCULTED,
    OBSERVABLE,
    PARTIAL,
    Target,
    VisibilityGeometry,
    visibility_geometry,
)

from .optimize import DEFAULT_FOV_HALF_DEG, DEFAULT_N_MAX, Instrument, Solution, optimize

# ---------------------------------------------------------------------------
# Reference case
# ---------------------------------------------------------------------------
# 2027-03-29 is the first date in the 2027 Galactic-Center season where both
# constraints bind (the window opens on FOV limb clearance and closes on
# sunrise) *and* every SV-7 objective column is met. Dates near mid-season put
# the umbra arc entirely inside the target-clear arc, so T_open == T_eclipse and
# the obstruction constraint never bites -- a misleading figure.

DEFAULT_ALTITUDE_M = 500_000.0
DEFAULT_ECCENTRICITY = 0.0
DEFAULT_INCLINATION_RAD = math.radians(51.6)
DEFAULT_RAAN0_RAD = math.radians(45.0)
DEFAULT_EPOCH = datetime(2027, 3, 21, tzinfo=timezone.utc)
DEFAULT_PASS_DATE = datetime(2027, 3, 29, tzinfo=timezone.utc)
DEFAULT_FOV_HALF_RAD = math.radians(DEFAULT_FOV_HALF_DEG)

#: ``SV7-MEASURE-MIN-EXPOSURE`` -- exposures shorter than this are rejected.
DEFAULT_MIN_EXPOSURE_S = 300.0

TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class PassCase:
    """A single umbral pass, fully resolved."""

    # inputs
    a: float
    e: float
    i: float
    omega0: float
    epoch: datetime
    date: datetime
    target: Target
    fov_half: float
    inst: Instrument
    n_max: int
    min_exposure_s: float

    # derived
    omega_rad: float
    geom: VisibilityGeometry
    open_window: ArcWindow      # umbra n target-clear
    visible_window: ArcWindow   # umbra n target-visible
    solution: Solution | None

    # -- angle/time frame --------------------------------------------------

    @property
    def mean_motion_rad_s(self) -> float:
        return self.geom.mean_motion_rad_s

    @property
    def u_entry_rad(self) -> float:
        """Argument of latitude at umbra entry (t = 0)."""
        return self.geom.umbra_center_rad - self.geom.umbra_half_rad

    @property
    def u_exit_rad(self) -> float:
        """Argument of latitude at umbra exit (sunrise)."""
        return self.geom.umbra_center_rad + self.geom.umbra_half_rad

    def time_of_u(self, u_rad: float) -> float:
        """Seconds after umbra entry at which the orbit reaches ``u_rad``."""
        return ((u_rad - self.u_entry_rad) % TWO_PI) / self.mean_motion_rad_s

    def u_of_time(self, t_s: float) -> float:
        """Argument of latitude ``t_s`` seconds after umbra entry."""
        return self.u_entry_rad + t_s * self.mean_motion_rad_s

    # -- durations ---------------------------------------------------------

    @property
    def orbit_period_s(self) -> float:
        return TWO_PI / self.mean_motion_rad_s

    @property
    def eclipse_s(self) -> float:
        """Umbra duration, from the same geometry the arcs are drawn from."""
        return 2.0 * self.geom.umbra_half_rad / self.mean_motion_rad_s

    @property
    def open_sky_s(self) -> float:
        """``T_open`` -- the umbra/target-clear intersection, in seconds."""
        return self.open_window.duration_s

    @property
    def target_visible_s(self) -> float:
        """Umbra time with the target centre above the limb (FOV may clip)."""
        return self.visible_window.duration_s

    @property
    def open_fraction(self) -> float:
        """``T_open`` as a fraction of the eclipse."""
        ecl = self.eclipse_s
        return self.open_sky_s / ecl if ecl > 0.0 else 0.0

    # -- spans -------------------------------------------------------------

    @property
    def state_spans_s(self) -> tuple[tuple[str, float, float], ...]:
        """Visibility states within the eclipse as ``(state, t0, t1)`` spans.

        Ordered by time from umbra entry and guaranteed to partition
        ``[0, eclipse_s]``. Zero-length spans are dropped.
        """
        ecl = self.eclipse_s
        open_start, open_end = self._window_span(self.open_window)
        vis_start, vis_end = self._window_span(self.visible_window)

        # Clamp into the eclipse and keep the observable span inside the
        # visible span, which the geometry guarantees but floating point may
        # violate by an ulp at the boundaries.
        vis_start = min(max(vis_start, 0.0), ecl)
        vis_end = min(max(vis_end, vis_start), ecl)
        open_start = min(max(open_start, vis_start), vis_end)
        open_end = min(max(open_end, open_start), vis_end)

        raw = (
            (OCCULTED, 0.0, vis_start),
            (PARTIAL, vis_start, open_start),
            (OBSERVABLE, open_start, open_end),
            (PARTIAL, open_end, vis_end),
            (OCCULTED, vis_end, ecl),
        )
        return tuple((s, t0, t1) for s, t0, t1 in raw if t1 - t0 > 1e-9)

    @property
    def operational_spans_s(self) -> tuple[tuple[str, float, float], ...]:
        """Instrument phases as ``(phase, t0, t1)`` spans from umbra entry.

        Skipper timing: one startup, then *one* contiguous exposure, then
        ``N`` non-destructive full-frame reads. Not one exposure per sample.
        The last readout ends flush with the close of the open-sky window.
        """
        sol = self.solution
        if sol is None:
            return ()

        t = self.time_of_u(self.open_window.start_rad)
        spans: list[tuple[str, float, float]] = []

        spans.append(("startup", t, t + self.inst.startup_s))
        t += self.inst.startup_s

        spans.append(("exposure", t, t + sol.exposure_s))
        t += sol.exposure_s

        readout = self.inst.readout_per_sample
        for _ in range(sol.n_samples):
            spans.append(("readout", t, t + readout))
            t += readout

        return tuple(spans)

    # -- optimization ------------------------------------------------------

    @property
    def objective_curve(self) -> tuple[tuple[int, float, float], ...]:
        """``(N, B(N), B*N)`` for every feasible N, ignoring the exposure floor.

        The floor is reported separately so the figure can show *where* it cuts
        rather than silently omitting the truncated tail.
        """
        usable = self.open_sky_s - self.inst.startup_s
        readout = self.inst.readout_per_sample
        rows = []
        for n in range(1, self.n_max + 1):
            b = usable - readout * n
            if b <= 0.0:
                break
            rows.append((n, b, b * n))
        return tuple(rows)

    @property
    def min_exposure_cut_n(self) -> int | None:
        """Smallest N whose exposure falls below the SV-7 minimum, if any."""
        if self.min_exposure_s <= 0.0:
            return None
        for n, b, _obj in self.objective_curve:
            if b < self.min_exposure_s:
                return n
        return None

    @property
    def measures(self) -> dict[str, float | int | str]:
        """Flat dict of every number a caption or table might quote."""
        sol = self.solution
        return {
            "date_utc": self.date.isoformat(),
            "epoch_utc": self.epoch.isoformat(),
            "altitude_km": (self.a - R_E) / 1000.0,
            "inclination_deg": math.degrees(self.i),
            "raan0_deg": math.degrees(self.omega0) % 360.0,
            "raan_deg": math.degrees(self.omega_rad) % 360.0,
            "target": self.target.name,
            "fov_half_deg": math.degrees(self.fov_half),
            "beta_sun_deg": math.degrees(self.geom.beta_sun_rad),
            "beta_target_deg": math.degrees(self.geom.beta_target_rad),
            "umbra_half_deg": math.degrees(self.geom.umbra_half_rad),
            "target_clear_half_deg": math.degrees(self.geom.target_clear_half_rad),
            "target_visible_half_deg": math.degrees(self.geom.target_visible_half_rad),
            "orbit_period_s": self.orbit_period_s,
            "eclipse_s": self.eclipse_s,
            "open_sky_s": self.open_sky_s,
            "target_visible_s": self.target_visible_s,
            "open_fraction": self.open_fraction,
            "startup_s": self.inst.startup_s,
            "readout_per_sample_s": self.inst.readout_per_sample,
            "n_samples": sol.n_samples if sol else 0,
            "exposure_s": sol.exposure_s if sol else 0.0,
            "readout_total_s": (
                sol.n_samples * self.inst.readout_per_sample if sol else 0.0
            ),
            "objective_s": sol.objective if sol else 0.0,
            "n_max": self.n_max,
            "min_exposure_s": self.min_exposure_s,
        }

    # -- internals ---------------------------------------------------------

    def _window_span(self, window: ArcWindow) -> tuple[float, float]:
        """``(t_start, t_end)`` of an arc window, seconds from umbra entry.

        Clamped into the eclipse. A window that intersects the umbra can still
        report a start an ulp *before* umbra entry, and the wrap in
        :meth:`time_of_u` would turn that into nearly a full orbit -- which
        renders as a full-height spike in a season landscape.
        """
        if not window.feasible or window.start_rad is None:
            return (0.0, 0.0)

        ecl = self.eclipse_s
        offset = math.remainder(window.start_rad - self.u_entry_rad, TWO_PI)
        start = min(max(offset / self.mean_motion_rad_s, 0.0), ecl)
        return (start, min(start + window.duration_s, ecl))

    def window_span_min(self, window: ArcWindow) -> tuple[float, float]:
        """``(t_start, t_end)`` of an arc window, minutes from umbra entry."""
        start, end = self._window_span(window)
        return (start / 60.0, end / 60.0)


def build_pass_case(
    *,
    date: datetime = DEFAULT_PASS_DATE,
    altitude_m: float = DEFAULT_ALTITUDE_M,
    e: float = DEFAULT_ECCENTRICITY,
    i: float = DEFAULT_INCLINATION_RAD,
    omega0: float = DEFAULT_RAAN0_RAD,
    epoch: datetime = DEFAULT_EPOCH,
    target: Target = GALACTIC_CENTER,
    fov_half: float = DEFAULT_FOV_HALF_RAD,
    inst: Instrument | None = None,
    n_max: int = DEFAULT_N_MAX,
    min_exposure_s: float = DEFAULT_MIN_EXPOSURE_S,
    strict: bool = True,
) -> PassCase | None:
    """Resolve one umbral pass into geometry plus an imaging schedule.

    This is the **only** place visibility spans are derived. Anything that
    draws or reports visibility -- a single pass, a season landscape, an SV-7
    envelope -- goes through here, so there is one model to be right or wrong
    about. Two independent implementations is how the retired landscape ended
    up double-counting the limb margin without anyone noticing.

    Parameters
    ----------
    strict
        ``True`` raises when the pass has no open-sky window or no feasible
        ``(N, B)``; a figure of an infeasible pass is a bug, not a fallback.
        ``False`` is for sweeps: returns ``None`` when there is no eclipse at
        all, otherwise a case whose ``open_window``/``solution`` may be empty.
    """
    if inst is None:
        inst = Instrument()

    a = R_E + altitude_m
    omega = propagate_raan(omega0, j2_raan_rate(a, e, i), (date - epoch).total_seconds())
    beta_sun, uc_sun = sun_beta_uc(i, omega, date)
    geom = visibility_geometry(a, i, omega, target, fov_half, beta_sun, uc_sun)

    umbra = CircularArc(geom.umbra_center_rad, geom.umbra_half_rad, "umbra")
    open_window = intersect_arcs(
        umbra,
        CircularArc(geom.uc_target_rad, geom.target_clear_half_rad, "target-clear"),
        mean_motion_rad_s=geom.mean_motion_rad_s,
    )
    visible_window = intersect_arcs(
        umbra,
        CircularArc(geom.uc_target_rad, geom.target_visible_half_rad, "target-visible"),
        mean_motion_rad_s=geom.mean_motion_rad_s,
    )

    if not strict and geom.umbra_half_rad <= 0.0:
        return None  # no eclipse: there is no pass to describe

    if strict and not open_window.feasible:
        raise ValueError(
            f"no open-sky window for {target.name} on {date.isoformat()}: "
            f"umbra half-angle {math.degrees(geom.umbra_half_rad):.2f} deg, "
            f"target-clear half-angle {math.degrees(geom.target_clear_half_rad):.2f} deg"
        )

    solution = optimize(
        open_window.duration_s, inst, n_max=n_max, min_exposure_s=min_exposure_s,
    ) if open_window.feasible else None

    if strict and solution is None:
        raise ValueError(
            f"no feasible schedule in a {open_window.duration_s:.1f} s window "
            f"(startup {inst.startup_s:.0f} s, readout "
            f"{inst.readout_per_sample:.1f} s/sample, minimum exposure "
            f"{min_exposure_s:.0f} s)"
        )

    return PassCase(
        a=a,
        e=e,
        i=i,
        omega0=omega0,
        epoch=epoch,
        date=date,
        target=target,
        fov_half=fov_half,
        inst=inst,
        n_max=n_max,
        min_exposure_s=min_exposure_s,
        omega_rad=omega,
        geom=geom,
        open_window=open_window,
        visible_window=visible_window,
        solution=solution,
    )


__all__ = [
    "DEFAULT_ALTITUDE_M",
    "DEFAULT_EPOCH",
    "DEFAULT_FOV_HALF_RAD",
    "DEFAULT_INCLINATION_RAD",
    "DEFAULT_MIN_EXPOSURE_S",
    "DEFAULT_PASS_DATE",
    "DEFAULT_RAAN0_RAD",
    "PassCase",
    "build_pass_case",
]
