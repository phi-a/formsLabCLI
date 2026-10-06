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
from formslab.console.cast.castutils import RESULT_S, send_request
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
    def __init__(self, run, sequence: Sequence, *, sink=None,
                 poll: Optional[Callable[[], None]] = None,
                 write: Optional[Callable[[object], None]] = None,
                 tick: Optional[Callable[[object], None]] = None,
                 hz: float = 10.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.run = run
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
        # Each open loop, by its `repeat` index: passes begun, when it was entered
        # (active seconds), and the loop count at the start of the current pass.
        self._passes: dict[int, int] = {}
        self._entered: dict[int, float] = {}
        self._pass_began: dict[int, int] = {}

    def step(self, verb: str, segment_step: int, fraction: float | None = None) -> None:
        """One loop. Only the part after `poll` returns counts as active time."""
        self._poll()
        t0 = self._clock()
        self._tick(self.run)
        self._write(self.run)
        self.run.record()
        self._sleep(max(0.0, self._period - (self._clock() - t0)))
        self.elapsed += self._clock() - t0
        self.total_steps += 1
        self.sink.emit(Progress(index=self.index, verb=verb, step=self.total_steps,
                                segment_step=segment_step, elapsed_s=round(self.elapsed, 3),
                                fraction=fraction))

    def execute(self) -> RunResult:
        seq = self.sequence
        self.sink.emit(SequenceStarted(name=seq.name, segment_count=len(seq.segments),
                                       manifest=seq.to_manifest()))
        result = RunResult(name=seq.name)
        error = ended = None
        try:
            i, segments = 0, seq.segments
            while i < len(segments):
                segment = segments[i]
                self.index = i
                if segment.verb == "end":                 # back to its repeat, which decides
                    if self.total_steps == self._pass_began.get(segment.params["start_at"]):
                        self.step(segment.verb, 1)        # a pass that waited for nothing: still poll once
                    i = segment.params["start_at"]
                    continue
                if segment.verb == "repeat":
                    i = self._repeat(i, segment)
                    continue
                self.sink.emit(SegmentStarted(index=i, verb=segment.verb,
                                              label=segment.label or segment.verb))
                self.run.log(f"[{i + 1}/{len(segments)}] {segment.label or segment.verb}",
                               component=COMPONENT)
                steps = _EXECUTORS[segment.verb](self, self.run, segment)
                result.segment_steps.append(steps)
                self.sink.emit(SegmentFinished(index=i, verb=segment.verb, steps=steps))
                i += 1
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

    def _repeat(self, i: int, segment) -> int:
        """At a `repeat`: the index to go to next, the first step of another pass or
        the step after its `end`. Its condition is read before each pass."""
        p = segment.params
        if i not in self._passes:                        # entered from above: a fresh loop
            self._passes[i], self._entered[i] = 0, self.elapsed
        done = self._passes[i]
        if p["times"] is not None:
            finished = done >= p["times"]
        elif p["until"] is not None:
            u = p["until"]
            met, value = _met(self.run, u)
            finished = met
            if not met and self.elapsed - self._entered[i] >= u["timeout_s"]:
                why = _not_met(u, value, f", after {done} pass(es)")
                if not u.get("go_on"):
                    raise SequenceError(f"{segment.label}: {why}")
                self.run.log(f"{segment.label}: {why}; going on", level="WARNING", component=COMPONENT)
                finished = True
        else:
            finished = False                             # until the run is ended
        if finished:
            del self._passes[i], self._entered[i]
            self.run.log(f"{segment.label}: done after {done} pass(es)", component=COMPONENT)
            return p["end_at"] + 1
        self._passes[i] = done + 1
        self._pass_began[i] = self.total_steps
        of = f" of {p['times']}" if p["times"] is not None else ""
        self.sink.emit(SegmentStarted(index=i, verb=segment.verb, label=segment.label))
        self.run.log(f"[{i + 1}/{len(self.sequence.segments)}] {segment.label}: pass {done + 1}{of}",
                     component=COMPONENT)
        return i + 1


# --- executors: (runner, run, segment) -> loops taken ------------------------

def _hold(runner, run, segment) -> int:
    seconds = segment.params["seconds"]       # None: until the operator's `end`
    start, n = runner.elapsed, 0
    while seconds is None or (done := runner.elapsed - start) < seconds:
        n += 1
        runner.step(segment.verb, n, fraction=None if seconds is None else done / seconds)
    return n


def _command(runner, run, segment) -> int:
    label = segment.params["label"]
    request = segment.params["request"]
    timeout = segment.params["timeout_s"]
    from formslab.rscripts.rules import assess

    n = 0

    def tick():
        nonlocal n
        n += 1
        runner.step(segment.verb, n)

    # The owner's rules first. One the chamber's last report breaks stops the plan
    # here; one it cannot tell yet (the owner has not reported since the start) is
    # waited for, within the step's limit.
    start = runner.elapsed
    while problems := assess(label, request):
        if any(definite for _, definite in problems) or runner.elapsed - start >= timeout:
            raise SequenceError(f"{label}: {request} not sent. " + " ".join(w for w, _ in problems))
        tick()
    # The step ends when the request is taken, or, for an owner that reports
    # results (rLACO), when it has been carried out: a refusal stops the plan.
    wait = rscripts.reports_results(label)

    out = send_request(request, label, wait_result=wait, take_s=timeout,
                       clock=lambda: runner.elapsed, tick=tick)
    state = out["state"]
    if state == "not_taken":
        loaded = ", ".join(rscripts.loaded()) or "none"
        raise SequenceError(
            f"{label}: request {request} not taken within {timeout:g} s. Is the rScript "
            f"that reads '{label}' loaded and connected? (loaded: {loaded})")
    if state == "cleared":
        raise SequenceError(f"{label}: request {request} was cleared before it was taken")
    if state == "taken" and wait:
        raise SequenceError(f"{label}: request {request} taken, but no result within {RESULT_S:g} s")
    if state == "done" and not out.get("ok"):
        why = "; ".join(out.get("messages") or ()) or "no reason given"
        raise SequenceError(f"{label}: {request} refused: {why}")
    return n


def _convert(value: float, have: str | None, want: str | None) -> float:
    if want is None or have is None or have == want:
        return value
    if (have, want) == ("K", "C"):
        return value - 273.15
    if (have, want) == ("C", "K"):
        return value + 273.15
    raise SequenceError(f"cannot compare a value in {have} with a limit in {want}")


_COMPARE = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
            ">=": lambda a, b: a >= b, "=": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _met(run, p) -> tuple[bool, float | None]:
    """(the condition holds, the value in the limit's unit) for an `until`
    condition `p`; (False, None) while nothing publishes it. An on/off value is
    true when it is not 0; one that cannot be read (NaN) is neither."""
    var = run.variable(p["variable"])
    if var is None:
        return False, None
    try:
        value = _convert(float(var.value), getattr(var, "unit", None), p["unit"])
    except (TypeError, ValueError):
        value = math.nan
    if p["op"] in ("=", "!=") and isinstance(p["value"], bool):
        return (not math.isnan(value)) and _COMPARE[p["op"]](value != 0, p["value"]), value
    return _COMPARE[p["op"]](value, p["value"]), value


def _shown(p) -> str:
    """The condition as written: `platenT < 60 C`, `InUmbra = true`."""
    limit = str(p["value"]).lower() if isinstance(p["value"], bool) else f"{p['value']:g}"
    return f"{p['variable']} {p['op']} {limit}" + (f" {p['unit']}" if p["unit"] else "")


def _not_met(p, value, after: str = "") -> str:
    """Why a condition's time ran out: `platenT < 60 C not met within 1800 s (last 72.3 C)`."""
    shown = f" {p['unit']}" if p["unit"] else ""
    last = "it was never published" if value is None else f"last {value:.4g}{shown}"
    return f"{_shown(p)} not met within {p['timeout_s']:g} s{after} ({last})"


def _until(runner, run, segment) -> int:
    p = segment.params
    shown = f" {p['unit']}" if p["unit"] else ""
    start, n, value = runner.elapsed, 0, None
    while True:
        met, now = _met(run, p)
        value = now if now is not None else value
        if met:
            run.log(f"{p['variable']} = {value:.4g}{shown}: {_shown(p)}", component=COMPONENT)
            return n
        if runner.elapsed - start >= p["timeout_s"]:
            if p.get("go_on"):
                run.log(_not_met(p, value) + "; going on", level="WARNING", component=COMPONENT)
                return n
            raise SequenceError(_not_met(p, value))
        n += 1
        runner.step(segment.verb, n)


def _log(runner, run, segment) -> int:
    run.log(segment.params["message"], component=COMPONENT)
    return 0


_EXECUTORS = {"hold": _hold, "command": _command, "until": _until, "log": _log}
