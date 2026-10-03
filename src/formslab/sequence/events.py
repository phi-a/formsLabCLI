"""The lab sequence's event stream.

Written to ``<output>/.run/sequence.events.jsonl``: ``sequence_started``
(carrying the manifest), ``segment_started``, ``progress``, ``segment_finished``,
``sequence_finished`` (with ``error`` when the plan stopped). Times are
wall-clock seconds.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class SequenceEvent:
    @property
    def kind(self) -> str:
        name = type(self).__name__
        return name[0].lower() + "".join("_" + c.lower() if c.isupper() else c for c in name[1:])

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **asdict(self)}


@dataclass(frozen=True)
class SequenceStarted(SequenceEvent):
    name: str
    segment_count: int
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentStarted(SequenceEvent):
    index: int
    verb: str
    label: str


@dataclass(frozen=True)
class Progress(SequenceEvent):
    index: int
    verb: str
    step: int               # loops across the whole run
    segment_step: int       # loops within this segment
    elapsed_s: float        # active (unpaused) seconds since the run started
    fraction: Optional[float] = None   # of this segment, when it has a known length


@dataclass(frozen=True)
class SegmentFinished(SequenceEvent):
    index: int
    verb: str
    steps: int


@dataclass(frozen=True)
class SequenceFinished(SequenceEvent):
    name: str
    steps: int
    error: Optional[str] = None      # the plan stopped on a failure
    ended: Optional[str] = None      # "operator": stopped by ctrl `end` (or a signal)


class NullSink:
    def emit(self, event: SequenceEvent) -> None:
        pass


class ListSink:
    def __init__(self) -> None:
        self.events: list[SequenceEvent] = []

    def emit(self, event: SequenceEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e.kind for e in self.events]


class JsonlEventSink:
    """One JSON line per event. `Progress` is throttled; telemetry never
    stops a run, so write errors are swallowed."""

    def __init__(self, path, *, progress_min_interval: float = 0.5) -> None:
        self._path = str(path)
        self._interval = progress_min_interval
        self._last_progress = 0.0

    def emit(self, event: SequenceEvent) -> None:
        if isinstance(event, Progress):
            now = time.monotonic()
            if now - self._last_progress < self._interval:
                return
            self._last_progress = now
        try:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict()) + "\n")
        except Exception:
            pass
