"""Circular-arc primitives used by visibility and thermal routines."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CircularArc:
    """Closed arc on the argument-of-latitude circle."""

    center_rad: float
    half_width_rad: float
    label: str = ""

    @property
    def length_rad(self) -> float:
        return 2.0 * max(0.0, min(math.pi, self.half_width_rad))

    def contains(self, angle_rad: float) -> bool:
        if self.half_width_rad >= math.pi:
            return True
        return abs(math.remainder(angle_rad - self.center_rad, 2.0 * math.pi)) <= self.half_width_rad


@dataclass(frozen=True)
class ArcWindow:
    """Intersection result for two circular arcs."""

    overlap_rad: float
    duration_s: float
    center_rad: float | None = None
    start_rad: float | None = None
    end_rad: float | None = None

    @property
    def feasible(self) -> bool:
        return self.overlap_rad > 0.0


def intersect_arc_range(
    first_center_rad: float,
    first_half_width_rad: float,
    second_center_rad: float,
    second_half_width_rad: float,
) -> tuple[float, float] | None:
    """Return the ``(center, half_width)`` of two wrapped arc ranges."""

    h1 = max(0.0, min(math.pi, first_half_width_rad))
    h2 = max(0.0, min(math.pi, second_half_width_rad))
    if h1 == 0.0 or h2 == 0.0:
        return None
    if h1 >= math.pi:
        return second_center_rad, h2
    if h2 >= math.pi:
        return first_center_rad, h1

    delta = math.remainder(second_center_rad - first_center_rad, 2.0 * math.pi)
    second_center_unwrapped = first_center_rad + delta
    start = max(first_center_rad - h1, second_center_unwrapped - h2)
    end = min(first_center_rad + h1, second_center_unwrapped + h2)
    if end <= start:
        return None
    return math.remainder((start + end) / 2.0, 2.0 * math.pi), (end - start) / 2.0


def intersect_arcs(
    first: CircularArc,
    second: CircularArc,
    *,
    mean_motion_rad_s: float,
) -> ArcWindow:
    """Return the angular and time overlap of two wrapped circular arcs."""

    overlap = intersect_arc_range(
        first.center_rad,
        first.half_width_rad,
        second.center_rad,
        second.half_width_rad,
    )
    if overlap is None:
        return ArcWindow(overlap_rad=0.0, duration_s=0.0)

    center, half_width = overlap
    overlap_rad = 2.0 * half_width
    return ArcWindow(
        overlap_rad=overlap_rad,
        duration_s=overlap_rad / mean_motion_rad_s if mean_motion_rad_s > 0.0 else 0.0,
        center_rad=center,
        start_rad=math.remainder(center - half_width, 2.0 * math.pi),
        end_rad=math.remainder(center + half_width, 2.0 * math.pi),
    )


__all__ = [
    "ArcWindow",
    "CircularArc",
    "intersect_arc_range",
    "intersect_arcs",
]
