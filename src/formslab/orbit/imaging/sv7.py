"""SV-7 systems measures, computed from the canonical pass model.

Every value in the matrix is derived from :func:`formslab.orbit.imaging.pass_case.build_pass_case`
sweeps -- the same model the poster figures draw -- so the table and the
figures cannot drift apart.

Threshold and objective columns come from two places, and the distinction
matters when someone asks where a number is from:

* ``SV7-MEASURE-MIN-EXPOSURE`` (300 s) and ``SV7-MEASURE-N-MAX`` (20) are from
  the ``SV7-DARKNESS-MEASURES`` artifact.
* The remaining objective values are design targets carried over from the
  retired systems-measures matrix. They are recorded here as data, not
  re-derived.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .optimize import Instrument
from .season import SeasonSpec, envelope_summary, sso_noon_spec

#: Provenance tags rendered in the matrix footnote.
SOURCE_ARTIFACT = "SV7-DARKNESS-MEASURES"
SOURCE_DESIGN = "design target"


@dataclass(frozen=True)
class SV7Row:
    """One row of the systems measures matrix."""

    function: str
    measure: str
    units: str
    threshold: str
    objective: str
    values: tuple[str, ...]     # one per case column
    met: tuple[bool | None, ...]  # objective met per case column; None = n/a


def _fmt_range(lo: float, hi: float, *, digits: int = 0) -> str:
    if lo == hi:
        return f"{lo:.{digits}f}"
    return f"{lo:.{digits}f}-{hi:.{digits}f}"


def _fmt_int_range(lo: float, hi: float) -> str:
    return f"{int(round(lo))}" if lo == hi else f"{int(round(lo))}-{int(round(hi))}"


def build_sv7_rows(
    columns: list[tuple[str, dict]],
    *,
    inst: Instrument | None = None,
    min_exposure_s: float = 300.0,
    n_max: int = 20,
) -> list[SV7Row]:
    """Assemble matrix rows from ``(label, envelope_summary)`` columns."""
    inst = inst or Instrument()
    env = [e for _label, e in columns]

    def col(fn):
        return tuple(fn(e) for e in env)

    def met(fn):
        return tuple(fn(e) for e in env)

    rows = [
        SV7Row(
            "Seasonal coverage", "Viable passes", "% of season",
            ">= 30", ">= 50",
            col(lambda e: _fmt_range(*e["coverage_pct"])),
            met(lambda e: e["coverage_pct"][0] >= 50.0),
        ),
        SV7Row(
            "Eclipse budget", "Open-sky budget $T_{open}$", "s",
            ">= 200", ">= 1000",
            col(lambda e: _fmt_range(*e["open_sky_s"])),
            met(lambda e: e["open_sky_s"][0] >= 1000.0),
        ),
        SV7Row(
            "Payload startup", "Boot time $t_{su}$", "s",
            "<= 60", "<= 60",
            col(lambda e: f"{inst.startup_s:.0f}"),
            met(lambda e: inst.startup_s <= 60.0),
        ),
        SV7Row(
            "CCD readout", "Readout/sample $C$", "s",
            "fixed", "fixed",
            col(lambda e: f"{inst.readout_per_sample:.1f}"),
            met(lambda e: None),
        ),
        SV7Row(
            "CCD readout", "Sample count $N$", "samples",
            f">= 1  (<= {n_max})", ">= 8",
            col(lambda e: _fmt_int_range(*e["n_samples"])),
            met(lambda e: e["n_samples"][0] >= 8),
        ),
        SV7Row(
            "Science exposure", "Exposure time $B$", "s",
            f">= {min_exposure_s:.0f}", ">= 600",
            col(lambda e: _fmt_range(*e["exposure_s"])),
            met(lambda e: e["exposure_s"][0] >= 600.0),
        ),
        SV7Row(
            "SNR objective", "$B \\cdot N$ single-pass", "s",
            ">= 300", ">= 5000",
            col(lambda e: _fmt_range(*e["objective_s"])),
            met(lambda e: e["objective_s"][0] >= 5000.0),
        ),
        SV7Row(
            "SNR objective", "$B \\cdot N$ cumulative", "s",
            "TBD [OV-3]", "TBD [OV-3]",
            col(lambda e: _fmt_range(*e["objective_cumulative_s"])),
            met(lambda e: None),
        ),
    ]
    return rows


def build_sv7_matrix(
    spec: SeasonSpec, *, raan_step_deg: float = 15.0, include_sso: bool = True,
) -> tuple[list[tuple[str, dict]], list[SV7Row], dict]:
    """Compute the matrix for the reference orbit and the SSO comparison.

    Returns ``(columns, rows, meta)``.
    """
    columns: list[tuple[str, dict]] = [
        (f"i = {math.degrees(spec.i):.1f}° (ISS-like)",
         envelope_summary(spec, step_deg=raan_step_deg)),
    ]
    meta = {
        "raan_step_deg": raan_step_deg,
        "season_days": spec.days,
        "step_hours": spec.step_hours,
        "min_exposure_s": spec.min_exposure_s,
        "n_max": spec.n_max,
    }

    if include_sso:
        sso = sso_noon_spec(spec)
        # A sun-synchronous plane is fixed relative to the Sun, so there is no
        # RAAN envelope to take -- one sweep is the answer.
        from .season import season_summary, sweep_season

        s = season_summary(sweep_season(sso), sso)
        columns.append((
            f"SSO-noon (i = {math.degrees(sso.i):.1f}°)",
            {
                "coverage_pct": (s["feasible_fraction"] * 100.0,) * 2,
                "open_sky_s": (s["open_sky_min_s"], s["open_sky_max_s"]),
                "n_samples": (s["n_min"], s["n_max_observed"]),
                "exposure_s": (s["exposure_min_s"], s["exposure_max_s"]),
                "objective_s": (s["objective_min_s"], s["objective_max_s"]),
                "objective_cumulative_s": (s["objective_cumulative_s"],) * 2,
            },
        ))
        meta["sso_inclination_deg"] = math.degrees(sso.i)
        meta["sso_raan_deg"] = math.degrees(sso.omega0) % 360.0

    rows = build_sv7_rows(
        columns, inst=spec.inst, min_exposure_s=spec.min_exposure_s,
        n_max=spec.n_max,
    )
    return columns, rows, meta


__all__ = ["SOURCE_ARTIFACT", "SOURCE_DESIGN", "SV7Row", "build_sv7_matrix",
           "build_sv7_rows"]
