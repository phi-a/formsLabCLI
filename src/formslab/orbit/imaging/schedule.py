"""Date-driven imaging schedule: orbit elements + date → optimal (B, N)."""

import math
from datetime import datetime, timedelta, timezone

from ..propagate.constants import HOURS_PER_DAY
from ..propagate.orbit import (eclipse_duration, j2_raan_rate, propagate_raan,
                            sun_beta_uc)
from .optimize import DEFAULT_N_MAX, Instrument, Solution, optimize
from ..propagate.sun import sun_dist
from ..visibility import Target, open_sky_budget, target_beta_uc


def schedule_date(
    dt: datetime,
    a: float,
    e: float,
    i: float,
    omega0: float,
    epoch: datetime,
    inst: Instrument | None = None,
    n_max: int = DEFAULT_N_MAX,
    target: Target | None = None,
    fov_half: float = 0.0,
    thermal: bool = True,
    min_exposure_s: float = 0.0,
) -> dict:
    """Compute optimal imaging parameters for a given date.

    Parameters
    ----------
    dt : datetime
        Target date (UTC).
    a : float
        Semi-major axis (m).
    e : float
        Eccentricity.
    i : float
        Inclination (rad).
    omega0 : float
        RAAN at epoch (rad).
    epoch : datetime
        Epoch of initial orbit elements (UTC).
    inst : Instrument, optional
        CCD parameters. Defaults to standard instrument.
    n_max : int
        Maximum number of samples considered by the optimizer.
    min_exposure_s : float
        Minimum acceptable exposure time in seconds. Defaults to 0, preserving
        the original feasible-solution behavior.
    target : Target, optional
        Inertial celestial target for FOV constraint.
    fov_half : float
        Instrument half-cone FOV angle (rad). Only used when target is set.
    thermal : bool
        Deprecated and ignored. The view-factor coupling this once enabled was
        removed; the parameter is retained so existing callers keep working.

    Returns
    -------
    dict with keys: date, raan_deg, beta_deg, eclipse_s, solution.
    When *target* is provided, also: target_beta_deg, open_sky_s, target_vis_s.
    """
    if inst is None:
        inst = Instrument()

    dt_s = (dt - epoch).total_seconds()
    omega_dot = j2_raan_rate(a, e, i)
    omega = propagate_raan(omega0, omega_dot, dt_s)

    beta_sun, uc_sun = sun_beta_uc(i, omega, dt)
    D = sun_dist(dt)
    ecl = eclipse_duration(a, beta_sun, D)

    result = {
        "date": dt,
        "raan_deg": math.degrees(omega) % 360,
        "beta_deg": math.degrees(beta_sun),
        "eclipse_s": ecl,
    }

    if target is not None:
        osky = open_sky_budget(a, i, omega, target, fov_half, beta_sun, uc_sun)
        tvis = open_sky_budget(a, i, omega, target, 0.0, beta_sun, uc_sun)
        beta_tgt, _uc_tgt = target_beta_uc(i, omega, target)
        result["target_beta_deg"] = math.degrees(beta_tgt)
        result["open_sky_s"] = osky
        result["target_vis_s"] = tvis  # target centre visible, FOV may clip Earth
        budget = osky
    else:
        budget = ecl

    candidate = optimize(budget, inst, n_max=n_max)
    solution = candidate
    if min_exposure_s > 0.0:
        solution = optimize(
            budget, inst, n_max=n_max, min_exposure_s=min_exposure_s,
        )
        if candidate is not None:
            result["candidate_solution"] = candidate
        if candidate is not None and solution is None:
            result["rejection_reason"] = "minimum_exposure_not_met"
            result["minimum_exposure_s"] = min_exposure_s

    result["solution"] = solution
    return result


def schedule_range(
    start: datetime,
    days: float | datetime,
    a: float,
    e: float,
    i: float,
    omega0: float,
    epoch: datetime,
    inst: Instrument | None = None,
    n_max: int = DEFAULT_N_MAX,
    step_hours: float = HOURS_PER_DAY,
    target: Target | None = None,
    fov_half: float = 0.0,
    thermal: bool = True,
    min_exposure_s: float = 0.0,
) -> list[dict]:
    """Compute schedule for a range of dates.

    Parameters
    ----------
    days : float | datetime
        Either a duration in days, or an explicit UTC end epoch.
    step_hours : float
        Time resolution in hours (default 24 = one sample per day).
        Use e.g. 1.0 for hourly or 1.5 for ~one sample per orbit.
    n_max : int
        Maximum number of samples considered by the optimizer.
    target : Target, optional
        Inertial celestial target for FOV constraint.
    fov_half : float
        Instrument half-cone FOV angle (rad). Only used when target is set.
    thermal : bool
        Deprecated and ignored. See :func:`schedule_date`.
    min_exposure_s : float
        Minimum acceptable exposure time in seconds. Defaults to 0.
    """
    if isinstance(days, datetime):
        span_hours = (days - start).total_seconds() / 3600.0
        if span_hours < 0:
            raise ValueError("end epoch must be on or after the start epoch")
        n_steps = math.floor(span_hours / step_hours) + 1
    else:
        n_steps = math.ceil(days * HOURS_PER_DAY / step_hours)
    return [
        schedule_date(start + timedelta(hours=k * step_hours), a, e, i,
                      omega0, epoch, inst, n_max=n_max,
                      target=target, fov_half=fov_half,
                      thermal=thermal, min_exposure_s=min_exposure_s)
        for k in range(n_steps)
    ]


def print_schedule(results: list[dict], inst: Instrument | None = None) -> None:
    """Print a formatted schedule table."""
    if inst is None:
        inst = Instrument()
    dt_read = inst.readout_per_sample
    has_target = "open_sky_s" in results[0] if results else False

    cols = f"{'DateTime':<19} {'RAAN':>7} {'Beta':>7} {'Eclipse':>8}"
    if has_target:
        cols += f" {'TgtBeta':>7} {'OpenSky':>8}"
    cols += f" {'N':>3} {'B(s)':>8} {'C(s)':>8} {'Total':>8}"
    print(cols)
    print("-" * len(cols))

    for r in results:
        sol = r["solution"]
        line = (
            f"{r['date'].strftime('%Y-%m-%d %H:%M:%S'):<19} "
            f"{r['raan_deg']:>7.2f} "
            f"{r['beta_deg']:>7.2f} "
            f"{r['eclipse_s']:>8.1f}"
        )
        if has_target:
            line += (
                f" {r['target_beta_deg']:>7.2f}"
                f" {r['open_sky_s']:>8.1f}"
            )
        if sol:
            c = dt_read * sol.n_samples
            total = inst.startup_s + sol.exposure_s + c
            line += (
                f" {sol.n_samples:>3d}"
                f" {sol.exposure_s:>8.1f}"
                f" {c:>8.1f}"
                f" {total:>8.1f}"
            )
        else:
            line += f" {'---':>3} {'---':>8} {'---':>8} {'---':>8}"
        print(line)
