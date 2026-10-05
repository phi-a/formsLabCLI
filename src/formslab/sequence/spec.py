"""Sequence and Segment: the runtime model of a lab plan.

A Sequence is an ordered list of Segments, a Segment is a ``verb`` plus a
``params`` dict, and ``to_manifest`` is what the event stream carries. Data
only; nothing here runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Segment:
    verb: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    # Where a step from a block came from: ((block, line in its file), ...), outermost first.
    origin: tuple = ()

    def to_manifest(self) -> dict[str, Any]:
        out = {"verb": self.verb, "label": self.label or self.verb, "params": dict(self.params)}
        if self.origin:
            out["origin"] = origin_text(self.origin)
        return out


def origin_text(origin) -> str:
    """`pumpdown line 9`, or `vent line 3 > safe line 2` for a nested block."""
    return " > ".join(f"{name} line {line}" for name, line in origin)


@dataclass(frozen=True)
class Sequence:
    name: str = ""
    segments: tuple[Segment, ...] = ()
    rscripts: tuple[str, ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "clock": "wall",
            "rscripts": list(self.rscripts),
            "segment_count": len(self.segments),
            "segments": [s.to_manifest() for s in self.segments],
        }
