"""Season sweeps and RAAN envelopes, built from the canonical pass model.

Everything here is a list of :class:`~orbit.imaging.pass_case.PassCase` objects.
There is deliberately no second geometry implementation: a season landscape and
a single-pass timeline must agree by construction, not by two routines
happening to compute the same thing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..propagate.constants import R_E
from ..visibility.target import GALACTIC_CENTER, Target

from .optimize import DEFAULT_N_MAX, Instrument
from .pass_case import (
    DEFAULT_ALTITUDE_M,
    DEFAULT_ECCENTRICITY,
    DEFAULT_EPOCH,
    DEFAULT_FOV_HALF_RAD,
    DEFAULT_INCLINATION_RAD,
    DEFAULT_MIN_EXPOSURE_S,
    DEFAULT_RAAN0_RAD,
    PassCase,
    build_pass_case,
)

#: 2027 vernal equinox to just past the autumnal one -- the Galactic Center
#: primary observing season.
DEFAULT_SEASON_START = datetime(2027, 3, 21, tzinfo=timezone.utc)
DEFAULT_SEASON_DAYS = 198.0
DEFAULT_STEP_HOURS = 24.0


@dataclass(frozen=True)
class SeasonSpec:
    """Everything needed to reproduce a season sweep."""

    start: datetime = DEFAULT_SEASON_START
    days: float = DEFAULT_SEASON_DAYS
    step_hours: float = DEFAULT_STEP_HOURS
    altitude_m: float = DEFAULT_ALTITUDE_M
    e: float = DEFAULT_ECCENTRICITY
    i: float = DEFAULT_INCLINATION_RAD
    omega0: float = DEFAULT_RAAN0_RAD
    epoch: datetime = DEFAULT_EPOCH
    target: Target = GALACTIC_CENTER
    fov_half: float = DEFAULT_FOV_HALF_RAD
    n_max: int = DEFAULT_N_MAX
    min_exposure_s: float = DEFAULT_MIN_EXPOSURE_S
    inst: Instrument = field(default_factory=Instrument)

    @property
    def a(self) -> float:
        return R_E + self.altitude_m

    @property
    def dates(self) -> list[datetime]:
        n = max(1, math.ceil(self.days * 24.0 / self.step_hours))
        return [self.start + timedelta(hours=k * self.step_hours) for k in range(n)]

    def with_raan(self, omega0_rad: float) -> "SeasonSpec":
        return SeasonSpec(**{**self.__dict__, "omega0": omega0_rad})

    @property
    def subtitle(self) -> str:
        # mathtext for the subscript: Arial has no U+2080 SUBSCRIPT ZERO
        return (
            f"{self.target.name} · {self.altitude_m / 1000:.0f} km · "
            f"i = {math.degrees(self.i):.1f}° · "
            f"$\\Omega_0$ = {math.degrees(self.omega0):.0f}° · "
            f"FOV half-cone {math.degrees(self.fov_half):.0f}° · "
            f"from {self.start:%Y-%m-%d}"
        )


def pass_at(spec: SeasonSpec, when: datetime, *, strict: bool = False) -> PassCase | None:
    """Resolve one date under *spec* through the canonical pass model."""
    return build_pass_case(
        date=when, altitude_m=spec.altitude_m, e=spec.e, i=spec.i,
        omega0=spec.omega0, epoch=spec.epoch, target=spec.target,
        fov_half=spec.fov_half, inst=spec.inst, n_max=spec.n_max,
        min_exposure_s=spec.min_exposure_s, strict=strict,
    )


def sweep_season(spec: SeasonSpec) -> list[PassCase | None]:
    """One entry per sampled date. ``None`` where there is no eclipse."""
    return [pass_at(spec, when) for when in spec.dates]


def feasible(cases: list[PassCase | None]) -> list[PassCase]:
    """Cases that produced a schedule."""
    return [c for c in cases if c is not None and c.solution is not None]


def season_summary(cases: list[PassCase | None], spec: SeasonSpec) -> dict:
    """Season-wide numbers for captions and the SV-7 matrix."""
    ok = feasible(cases)
    ns = [c.solution.n_samples for c in ok]
    bs = [c.solution.exposure_s for c in ok]
    objs = [c.solution.objective for c in ok]
    opens = [c.open_sky_s for c in cases if c is not None]
    return {
        "epochs": len(cases),
        "feasible_epochs": len(ok),
        "feasible_fraction": len(ok) / len(cases) if cases else 0.0,
        "no_eclipse_epochs": sum(1 for c in cases if c is None),
        "rejected_min_exposure": sum(
            1 for c in cases
            if c is not None and c.solution is None and c.open_window.feasible
        ),
        "open_sky_min_s": min((v for v in opens if v > 0.0), default=0.0),
        "open_sky_max_s": max(opens, default=0.0),
        "n_min": min(ns, default=0),
        "n_max_observed": max(ns, default=0),
        "exposure_min_s": min(bs, default=0.0),
        "exposure_max_s": max(bs, default=0.0),
        "objective_min_s": min(objs, default=0.0),
        "objective_max_s": max(objs, default=0.0),
        "objective_cumulative_s": sum(objs),
        "step_hours": spec.step_hours,
        "days": spec.days,
    }


# ---------------------------------------------------------------------------
# RAAN envelope
# ---------------------------------------------------------------------------

def raan_envelope(
    spec: SeasonSpec, *, step_deg: float = 15.0,
) -> list[tuple[float, list[PassCase | None]]]:
    """Sweep the season once per RAAN, in ``step_deg`` increments.

    A single RAAN is one slice through a mission whose node is not yet fixed;
    the envelope is what the measure has to hold across.
    """
    out = []
    for k in range(int(round(360.0 / step_deg))):
        omega0 = math.radians(k * step_deg)
        out.append((omega0, sweep_season(spec.with_raan(omega0))))
    return out


def envelope_summary(
    spec: SeasonSpec, *, step_deg: float = 15.0,
) -> dict:
    """Aggregate :func:`raan_envelope` into min/max bounds per measure."""
    per_raan = raan_envelope(spec, step_deg=step_deg)
    summaries = [season_summary(cases, spec) for _omega, cases in per_raan]
    scored = [s for s in summaries if s["feasible_epochs"]]

    def bounds(low_key: str, high_key: str) -> tuple[float, float]:
        """Widest span of a measure across every RAAN that produced a pass."""
        if not scored:
            return (0.0, 0.0)
        return (min(s[low_key] for s in scored), max(s[high_key] for s in scored))

    coverage = [s["feasible_fraction"] * 100.0 for s in summaries]
    return {
        "raan_step_deg": step_deg,
        "raan_count": len(per_raan),
        "coverage_pct": (min(coverage, default=0.0), max(coverage, default=0.0)),
        "open_sky_s": bounds("open_sky_min_s", "open_sky_max_s"),
        "n_samples": bounds("n_min", "n_max_observed"),
        "exposure_s": bounds("exposure_min_s", "exposure_max_s"),
        "objective_s": bounds("objective_min_s", "objective_max_s"),
        "objective_cumulative_s": bounds(
            "objective_cumulative_s", "objective_cumulative_s"
        ),
    }


# ---------------------------------------------------------------------------
# Sun-synchronous comparison case
# ---------------------------------------------------------------------------

def sso_inclination(a: float, e: float = 0.0) -> float:
    """Inclination (rad) whose J2 nodal drift matches Earth's mean motion.

    Solves ``Omega_dot_J2 = 360 deg/yr`` for cos i.
    """
    from ..propagate.constants import J2, MU

    n = math.sqrt(MU / a**3)
    target_rate = 2.0 * math.pi / 365.2422 / 86400.0  # rad/s
    cos_i = -target_rate * 2.0 * (1.0 - e**2) ** 2 / (3.0 * n * J2 * (R_E / a) ** 2)
    return math.acos(max(-1.0, min(1.0, cos_i)))


def sso_noon_spec(spec: SeasonSpec) -> SeasonSpec:
    """The same season flown sun-synchronous with a noon-midnight node.

    LTAN 12:00 puts the ascending node under the Sun, so the orbit plane holds
    the Sun direction and the solar beta angle stays near zero -- the deepest
    available eclipse, and the usual comparison case for an eclipse-limited
    payload.
    """
    from ..propagate.sun import sun_ra_dec

    ra_sun, _dec = sun_ra_dec(spec.epoch)
    return SeasonSpec(**{
        **spec.__dict__,
        "i": sso_inclination(spec.a, spec.e),
        "omega0": ra_sun,
    })


__all__ = [
    "DEFAULT_SEASON_DAYS",
    "DEFAULT_SEASON_START",
    "DEFAULT_STEP_HOURS",
    "SeasonSpec",
    "envelope_summary",
    "feasible",
    "pass_at",
    "raan_envelope",
    "season_summary",
    "sso_inclination",
    "sso_noon_spec",
    "sweep_season",
]
