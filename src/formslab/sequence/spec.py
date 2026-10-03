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

    def to_manifest(self) -> dict[str, Any]:
        return {"verb": self.verb, "label": self.label or self.verb, "params": dict(self.params)}


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
