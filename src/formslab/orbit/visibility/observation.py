"""Observation-window model for eclipse-only payload planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..propagate.orbit import (
    eclipse_half_angle,
    j2_raan_rate,
    mean_motion,
    propagate_raan,
    sun_beta_uc,
)
from .arcs import ArcWindow, CircularArc, intersect_arcs
from .target import Target, clear_half_angle, target_beta_uc


@dataclass(frozen=True)
class ObservationCheck:
    """One boolean check evaluated for an observation window."""

    check_id: str
    name: str
    passed: bool
    margin: float | None = None
    value: Any = None
    threshold: Any = None
    detail: str = ""

    @property
    def satisfied(self) -> bool:
        """Boolean result for callers that prefer rule language."""

        return self.passed


@dataclass(frozen=True)
class ObservationWindowFrame:
    """One timestamp's observation-window geometry and derived measures."""

    t: datetime
    checks: tuple[ObservationCheck, ...]
    state: dict[str, Any]
    measures: dict[str, Any]

    @property
    def feasible(self) -> bool:
        return all(item.passed for item in self.checks)

    @property
    def binding(self) -> str | None:
        with_margins = [item for item in self.checks if item.margin is not None]
        if with_margins:
            return min(with_margins, key=lambda item: item.margin or 0.0).check_id
        failed = [item for item in self.checks if not item.passed]
        return failed[0].check_id if failed else None


@dataclass(frozen=True)
class ObservationWindowModel:
    """Window model for one inertial target and a conic FOV."""

    a: float
    e: float
    i: float
    omega0: float
    epoch: datetime
    target: Target
    fov_half: float

    def evaluate(self, timestamp: datetime) -> ObservationWindowFrame:
        """Evaluate umbra, target-clear, and open-window geometry."""

        dt_s = (timestamp - self.epoch).total_seconds()
        omega = propagate_raan(
            self.omega0,
            j2_raan_rate(self.a, self.e, self.i),
            dt_s,
        )
        n = mean_motion(self.a)
        beta_sun, uc_sun = sun_beta_uc(self.i, omega, timestamp)
        nu = eclipse_half_angle(self.a, beta_sun)
        umbra_arc = CircularArc(
            center_rad=uc_sun + math.pi,
            half_width_rad=nu,
            label="umbra",
        )

        beta_target, uc_target = target_beta_uc(self.i, omega, self.target)
        target_half = clear_half_angle(self.a, beta_target, self.fov_half)
        target_arc = CircularArc(
            center_rad=uc_target,
            half_width_rad=target_half,
            label="target_clear",
        )
        window = intersect_arcs(umbra_arc, target_arc, mean_motion_rad_s=n)

        checks = (
            ObservationCheck(
                check_id="visibility.umbra_available",
                name="Umbra arc exists",
                passed=nu > 0.0,
                margin=nu,
                value=2.0 * nu / n if n > 0.0 else 0.0,
                threshold=0.0,
                detail="spacecraft must be inside Earth's umbra",
            ),
            ObservationCheck(
                check_id="visibility.target_clear",
                name="Target-clear arc exists",
                passed=target_half > 0.0,
                margin=target_half,
                value=target_half,
                threshold=0.0,
                detail="target FOV must clear the Earth limb",
            ),
            ObservationCheck(
                check_id="visibility.open_window",
                name="Umbra and target-clear arcs overlap",
                passed=window.feasible,
                margin=window.overlap_rad,
                value=window.duration_s,
                threshold=0.0,
                detail="open observation window induced by arc intersection",
            ),
        )

        return ObservationWindowFrame(
            t=timestamp,
            checks=checks,
            state={
                "raan_rad": omega,
                "raan_deg": math.degrees(omega) % 360.0,
                "beta_sun_rad": beta_sun,
                "beta_target_rad": beta_target,
                "umbra_arc": umbra_arc,
                "target_clear_arc": target_arc,
                "open_window": window,
            },
            measures={
                "t_umbra_s": checks[0].value,
                "t_open_s": window.duration_s,
                "open_angle_rad": window.overlap_rad,
            },
        )


def observation_window(
    timestamp: datetime,
    *,
    a: float,
    e: float,
    i: float,
    omega0: float,
    epoch: datetime,
    target: Target,
    fov_half: float,
) -> ArcWindow:
    """Convenience function returning only the open-window result."""

    model = ObservationWindowModel(
        a=a,
        e=e,
        i=i,
        omega0=omega0,
        epoch=epoch,
        target=target,
        fov_half=fov_half,
    )
    return model.evaluate(timestamp).state["open_window"]
