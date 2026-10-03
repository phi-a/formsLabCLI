"""Run a lab plan's Sequence on the wall clock.

Each step of the plan is executed by a loop paced in real time:

    poll       host: ctrl, and blocks while paused
    rScripts   every loaded script once -- unless the host runs each on its
               own thread (rscripts.workers), as it does on the bench
    emit       a CSV row when one is due

Segment durations and time limits count *active* seconds: time spent paused
inside ``poll`` does not use up a hold or run down a timeout. With worker
threads, pausing pauses the plan only; the rScripts keep reading their
instruments and taking cast commands.

The runner commands hardware only through CAST, the channel the console's tabs
already use. A ``command`` segment writes a request and waits until the rScript
that owns the label has taken it, so each operation starts after the previous
one reached the bench. The rScript stays the only code that talks to the
instrument.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from formslab import rscripts
from formslab.console.cast.castutils import CommandPending, WriteCommand
from formslab.sequence.events import (
    NullSink, Progress, SegmentFinished, SegmentStarted, SequenceFinished, SequenceStarted,
)
from formslab.sequence.spec import Sequence

COMPONENT = "sequence"


class SequenceError(RuntimeError):
    """A segment could not complete: a request nobody took, a condition not met
    in time. The run stops, and the host's shutdown path still runs."""


@dataclass
class RunResult:
    name: str
    steps: int = 0
    segment_steps: list[int] = field(default_factory=list)


def _noop(*_args, **_kwargs):
    return None


class LabSequenceRunner:
    def __init__(self, forms, sequence: Sequence, *, sink=None,
                 poll: Optional[Callable[[], None]] = None,
                 write: Optional[Callable[[object], None]] = None,
                 tick: Optional[Callable[[object], None]] = None,
                 hz: float = 10.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.forms = forms
        self.sequence = sequence
        self.sink = sink or NullSink()
        self._poll = poll or _noop
        self._write = write or _noop
        # Runs the rScripts once per loop. The host passes a no-op: it runs each
        # script on its own thread (rscripts.workers).
        self._tick = tick or rscripts.tick
        self._period = 1.0 / hz
        self._clock = clock
        self._sleep = sleep
        self.index = -1
        self.total_steps = 0
        self.elapsed = 0.0

    def step(self, verb: str, segment_step: int, fraction: float | None = None) -> None:
        """One loop. Only the part after `poll` returns counts as active time."""
        self._poll()
        t0 = self._clock()
        self._tick(self.forms)
        self._write(self.forms)
        self.forms.record()
        self._sleep(max(0.0, self._period - (self._clock() - t0)))
        self.elapsed += self._clock() - t0
        self.total_steps += 1
        self.sink.emit(Progress(index=self.index, verb=verb, step=self.total_steps,
                                segment_step=segment_step, elapsed_s=round(self.elapsed, 3),
                                fraction=fraction))

    def run(self) -> RunResult:
        seq = self.sequence
        self.sink.emit(SequenceStarted(name=seq.name, segment_count=len(seq.segments),
                                       manifest=seq.to_manifest()))
        result = RunResult(name=seq.name)
        error = ended = None
        try:
            for i, segment in enumerate(seq.segments):
                self.index = i
                self.sink.emit(SegmentStarted(index=i, verb=segment.verb,
                                              label=segment.label or segment.verb))
                self.forms.log(f"[{i + 1}/{len(seq.segments)}] {segment.label or segment.verb}",
                               component=COMPONENT)
                steps = _EXECUTORS[segment.verb](self, self.forms, segment)
                result.segment_steps.append(steps)
                self.sink.emit(SegmentFinished(index=i, verb=segment.verb, steps=steps))
        except SystemExit:
            ended = "operator"        # ctrl `end` or a signal: how an open-ended plan stops
            raise
        except BaseException as exc:
            error = str(exc) or type(exc).__name__
            raise
        finally:
            result.steps = self.total_steps
            self.sink.emit(SequenceFinished(name=seq.name, steps=self.total_steps, error=error,
                                            ended=ended))
        return result


# --- executors: (runner, forms, segment) -> loops taken ------------------------

def _hold(runner, forms, segment) -> int:
    seconds = segment.params["seconds"]       # None: until the operator's `end`
    start, n = runner.elapsed, 0
    while seconds is None or (done := runner.elapsed - start) < seconds:
        n += 1
        runner.step(segment.verb, n, fraction=None if seconds is None else done / seconds)
    return n


def _command(runner, forms, segment) -> int:
    label = segment.params["label"]
    request = segment.params["request"]
    timeout = segment.params["timeout_s"]
    WriteCommand(request, label)
    start, n = runner.elapsed, 0
    while CommandPending(label):
        if runner.elapsed - start >= timeout:
            loaded = ", ".join(rscripts.loaded()) or "none"
            raise SequenceError(
                f"{label}: request {request} not taken within {timeout:g} s. Is the rScript "
                f"that reads '{label}' loaded and connected? (loaded: {loaded})")
        n += 1
        runner.step(segment.verb, n)
    return n


def _convert(value: float, have: str | None, want: str | None) -> float:
    if want is None or have is None or have == want:
        return value
    if (have, want) == ("K", "C"):
        return value - 273.15
    if (have, want) == ("C", "K"):
        return value + 273.15
    raise SequenceError(f"cannot compare a value in {have} with a limit in {want}")


def _until(runner, forms, segment) -> int:
    p = segment.params
    name, side, limit, unit = p["variable"], p["side"], p["value"], p["unit"]
    shown = f" {unit}" if unit else ""
    start, n, value = runner.elapsed, 0, None
    while True:
        var = forms.get_variable(name)
        if var is not None:
            try:
                value = _convert(float(var.value), getattr(var, "unit", None), unit)
            except (TypeError, ValueError):
                value = math.nan
            if value > limit if side == "above" else value < limit:
                forms.log(f"{name} = {value:.4g}{shown}, {side} {limit:g}{shown}", component=COMPONENT)
                return n
        if runner.elapsed - start >= p["timeout_s"]:
            last = "it was never published" if value is None else f"last {value:.4g}{shown}"
            raise SequenceError(f"{name} not {side} {limit:g}{shown} within "
                                f"{p['timeout_s']:g} s ({last})")
        n += 1
        runner.step(segment.verb, n)


def _log(runner, forms, segment) -> int:
    forms.log(segment.params["message"], component=COMPONENT)
    return 0


_EXECUTORS = {"hold": _hold, "command": _command, "until": _until, "log": _log}
