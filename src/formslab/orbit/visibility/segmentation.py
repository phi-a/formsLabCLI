"""Visibility-state segmentation for one orbital pass."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from ..propagate.orbit import j2_raan_rate, propagate_raan, sun_beta_uc
from .observation import ObservationCheck
from .target import (
    OCCULTED,
    OBSERVABLE,
    PARTIAL,
    SUNLIT,
    Target,
    arc_margin,
    visibility_geometry,
    visibility_state,
)


@dataclass(frozen=True)
class VisibilityFrame:
    """One argument-of-latitude sample of target visibility."""

    t: float
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
class VisibilityEvent:
    """Discrete change in pass-level visibility state."""

    kind: str
    t: float
    detail: str = ""
    check_id: str | None = None
    previous_t: float | None = None


@dataclass(frozen=True)
class VisibilitySeries:
    """Ordered visibility samples plus derived state-change events."""

    frames: tuple[VisibilityFrame, ...]
    events: tuple[VisibilityEvent, ...]


def detect_visibility_events(frames: Iterable[VisibilityFrame]) -> VisibilitySeries:
    """Detect open/close, binding-check, and state-change events."""

    ordered = tuple(frames)
    events: list[VisibilityEvent] = []
    previous: VisibilityFrame | None = None
    for frame in ordered:
        if previous is None:
            previous = frame
            continue

        if frame.feasible and not previous.feasible:
            events.append(VisibilityEvent(
                kind="OPEN",
                t=frame.t,
                previous_t=previous.t,
                detail="visibility became observable",
            ))
        elif previous.feasible and not frame.feasible:
            events.append(VisibilityEvent(
                kind="CLOSE",
                t=frame.t,
                previous_t=previous.t,
                detail="visibility stopped being observable",
            ))

        if (
            frame.feasible
            and previous.feasible
            and frame.binding is not None
            and previous.binding is not None
            and frame.binding != previous.binding
        ):
            events.append(VisibilityEvent(
                kind="SWITCH",
                t=frame.t,
                previous_t=previous.t,
                check_id=frame.binding,
                detail=f"{previous.binding} -> {frame.binding}",
            ))

        old_state = previous.state.get("visibility_state")
        new_state = frame.state.get("visibility_state")
        if old_state != new_state:
            events.append(VisibilityEvent(
                kind="STATE",
                t=frame.t,
                previous_t=previous.t,
                detail=f"{old_state} -> {new_state}",
            ))

        previous = frame

    return VisibilitySeries(frames=ordered, events=tuple(events))


@dataclass(frozen=True)
class VisibilitySegmentationModel:
    """Sample target visibility states over one orbit."""

    a: float
    e: float
    i: float
    omega0: float
    epoch: datetime
    target: Target
    fov_half: float

    def _geometry(self, timestamp: datetime) -> dict[str, float]:
        dt_s = (timestamp - self.epoch).total_seconds()
        omega = propagate_raan(
            self.omega0,
            j2_raan_rate(self.a, self.e, self.i),
            dt_s,
        )
        beta_sun, uc_sun = sun_beta_uc(self.i, omega, timestamp)
        geom = visibility_geometry(
            self.a,
            self.i,
            omega,
            self.target,
            self.fov_half,
            beta_sun,
            uc_sun,
        )
        return geom.__dict__

    def evaluate_u(self, timestamp: datetime, u: float) -> VisibilityFrame:
        """Evaluate one argument-of-latitude sample."""

        geom = self._geometry(timestamp)
        state = visibility_state(
            u,
            geom["umbra_center_rad"],
            geom["umbra_half_rad"],
            geom["uc_target_rad"],
            geom["target_visible_half_rad"],
            geom["target_clear_half_rad"],
        )
        umbra_margin = arc_margin(u, geom["umbra_center_rad"], geom["umbra_half_rad"])
        visible_margin = arc_margin(u, geom["uc_target_rad"], geom["target_visible_half_rad"])
        clear_margin = arc_margin(u, geom["uc_target_rad"], geom["target_clear_half_rad"])
        checks = (
            ObservationCheck(
                check_id="visibility.umbra",
                name="Inside umbra",
                passed=umbra_margin > 0.0,
                margin=umbra_margin,
                detail="spacecraft must be inside Earth's umbra",
            ),
            ObservationCheck(
                check_id="visibility.target_center_visible",
                name="Target center visible",
                passed=visible_margin > 0.0,
                margin=visible_margin,
                detail="target center must clear the Earth limb",
            ),
            ObservationCheck(
                check_id="visibility.fov_clear",
                name="Full FOV clear",
                passed=clear_margin > 0.0,
                margin=clear_margin,
                detail="full instrument FOV must clear the Earth limb",
            ),
        )
        return VisibilityFrame(
            t=u / geom["mean_motion_rad_s"],
            checks=checks,
            state={
                **geom,
                "u_rad": u,
                "visibility_state": state,
            },
            measures={
                "u_deg": math.degrees(u) % 360.0,
                "umbra_margin_rad": umbra_margin,
                "target_visible_margin_rad": visible_margin,
                "target_clear_margin_rad": clear_margin,
            },
        )

    def sweep(self, timestamp: datetime, *, n: int = 360) -> VisibilitySeries:
        """Sweep one orbit and return visibility frames plus events."""

        frames = tuple(
            self.evaluate_u(timestamp, 2.0 * math.pi * k / n)
            for k in range(max(1, int(n)))
        )
        return detect_visibility_events(frames)


def summarize_visibility(series: VisibilitySeries) -> dict[str, float | int]:
    """Return pass-level measures from a visibility series."""

    state_names = (SUNLIT, OCCULTED, PARTIAL, OBSERVABLE)
    if not series.frames:
        empty: dict[str, float | int] = {}
        for state in state_names:
            empty[f"{state}_samples"] = 0
            empty[f"{state}_fraction"] = 0.0
            empty[f"{state}_duration_s"] = 0.0
        return empty
    dt = 0.0
    if len(series.frames) > 1:
        dt = float(series.frames[1].t) - float(series.frames[0].t)
    summary: dict[str, float | int] = {}
    for state in state_names:
        frames = [frame for frame in series.frames if frame.state["visibility_state"] == state]
        summary[f"{state}_samples"] = len(frames)
        summary[f"{state}_fraction"] = len(frames) / len(series.frames)
        summary[f"{state}_duration_s"] = len(frames) * dt
    return summary


__all__ = [
    "OBSERVABLE",
    "OCCULTED",
    "PARTIAL",
    "SUNLIT",
    "VisibilityEvent",
    "VisibilityFrame",
    "VisibilitySegmentationModel",
    "VisibilitySeries",
    "detect_visibility_events",
    "summarize_visibility",
]
