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

A `when` rule is watched every loop, and while paused, from its line to the end
of the run (``watch``): when its condition comes to hold it sends its command,
without waiting in the plan's steps, and follows the request until the owner
has carried it out; a refusal stops the run as a failed step does.

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
from formslab.console.cast.castutils import (
    RESULT_S, CommandPending, CommandState, WriteCommand, send_request,
)
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
        # The `when` rules armed so far, by their line: condition, action, whether
        # the condition held at the last look, a firing not yet sent (`due`: since
        # when), and the request in flight (`sent`: id, when).
        self._rules: dict[str, dict] = {}

    def step(self, verb: str, segment_step: int, fraction: float | None = None) -> None:
        """One loop. Only the part after `poll` returns counts as active time."""
        self._poll()
        self.watch()
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
            while self._in_flight():                     # a rule's command: see what became of it
                self.step("when", 0)
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

    def attempt_each(self, segments, cap_s: float) -> list[str]:
        """Run `segments` one after another, each on its own: one that fails (refused,
        not taken, a state not confirmed in time) is logged and the next still runs.
        After `cap_s` of wall clock the rest are logged as not run. Returns what went
        wrong, one line each: the end script, where every step is tried."""
        warnings: list[str] = []
        start = self._clock()
        self._ending = True
        for i, seg in enumerate(segments):
            label = seg.label or seg.verb
            if self._clock() - start >= cap_s:
                warnings.append(f"{label}: not run, the {cap_s:g} s for ending ran out")
                continue
            self.index = i
            self.run.log(f"[end {i + 1}/{len(segments)}] {label}", component=COMPONENT)
            if seg.verb == "until" and self.run.variable(seg.params["variable"]) is None:
                # Never published (the instrument was never reached): waiting cannot confirm it.
                why = f"{seg.params['variable']} was never read, so it cannot be confirmed"
                warnings.append(f"{label}: {why}")
                self.run.log(f"end: {label}: {why}", level="WARNING", component=COMPONENT)
                continue
            try:
                _EXECUTORS[seg.verb](self, self.run, seg)
            except SequenceError as e:
                warnings.append(f"{label}: {e}")
                self.run.log(f"end: {label}: {e}", level="WARNING", component=COMPONENT)
        return warnings

    # --- `when` rules ------------------------------------------------------------

    def watch(self) -> None:
        """Each rule once: on its condition coming to hold (or holding when it was
        armed), send its command; follow a command in flight. Called every loop,
        and by the host while the plan is paused."""
        for text, r in self._rules.items():
            met, value = _met(self.run, r["cond"])
            if met and not r["last"] and r["sent"] is None and r["due"] is None:
                r["due"] = self._clock()
                self.run.log(f"{text} ({_reading(r['cond'], value)})", component=COMPONENT)
            r["last"] = met
            if r["due"] is not None:
                self._fire(text, r)
            elif r["sent"] is not None:
                self._follow(text, r)

    def _fire(self, text: str, r: dict) -> None:
        """Send a rule's command once its owner's rules allow it and no other
        request to that owner is waiting (CAST would merge the two)."""
        do = r["do"]
        if do["verb"] == "log":
            self.run.log(do["message"], component=COMPONENT)
            r["due"] = None
            return
        from formslab.rscripts.rules import assess

        label, request, timeout = do["label"], do["request"], do["timeout_s"]
        problems = assess(label, request)
        if any(definite for _, definite in problems):
            raise SequenceError(f"{text}: {request} not sent. " + " ".join(w for w, _ in problems))
        if problems or CommandPending(label):
            if self._clock() - r["due"] >= timeout:
                why = " ".join(w for w, _ in problems) or f"another request to {label} was not taken"
                raise SequenceError(f"{text}: {request} not sent within {timeout:g} s. {why}")
            return                                       # look again next loop
        r["sent"], r["due"] = (WriteCommand(request, label), self._clock()), None

    def _follow(self, text: str, r: dict) -> None:
        """What became of a rule's request: done, or the run stops with why."""
        (rid, at), do = r["sent"], r["do"]
        label, request, timeout = do["label"], do["request"], do["timeout_s"]
        wait = rscripts.reports_results(label)
        out = CommandState(label, rid)
        waited = self._clock() - at
        if out["state"] in ("pending", "unknown") and waited < timeout:
            return
        if out["state"] == "taken" and wait and waited < timeout + RESULT_S:
            return
        if out["state"] in ("pending", "unknown"):
            out = {"state": "not_taken"}
        r["sent"] = None
        try:
            _outcome(label, request, out, wait, timeout)
        except SequenceError as e:
            raise SequenceError(f"{text}: {e}") from None
        self.run.log(f"{text}: done", component=COMPONENT)

    def _in_flight(self, label: str | None = None) -> bool:
        """A rule's command not yet sent or not yet answered (to `label`, if given)."""
        return any((r["due"] is not None or r["sent"] is not None)
                   and (label is None or r["do"].get("label") == label) for r in self._rules.values())

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
            if not met and u["timeout_s"] is not None and self.elapsed - self._entered[i] >= u["timeout_s"]:
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
    while problems := assess(label, request, ending=getattr(runner, "_ending", False)):
        if any(definite for _, definite in problems) or runner.elapsed - start >= timeout:
            raise SequenceError(f"{label}: {request} not sent. " + " ".join(w for w, _ in problems))
        tick()
    # A rule's command to the same owner goes first: CAST would merge the two.
    while runner._in_flight(label):
        tick()
    # The step ends when the request is taken, or, for an owner that reports
    # results (rLACO), when it has been carried out: a refusal stops the plan.
    wait = rscripts.reports_results(label)

    out = send_request(request, label, wait_result=wait, take_s=timeout,
                       clock=lambda: runner.elapsed, tick=tick)
    _outcome(label, request, out, wait, timeout)
    return n


def _outcome(label, request, out, wait, timeout) -> None:
    """Raise why a request to `label` failed, from what became of it (send_request,
    CommandState): not taken, cleared, no result, or refused."""
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
        if p["timeout_s"] is not None and runner.elapsed - start >= p["timeout_s"]:
            if p.get("go_on"):
                run.log(_not_met(p, value) + "; going on", level="WARNING", component=COMPONENT)
                return n
            raise SequenceError(_not_met(p, value))
        n += 1
        runner.step(segment.verb, n)


def _log(runner, run, segment) -> int:
    run.log(segment.params["message"], component=COMPONENT)
    return 0


def _reading(p, value) -> str:
    """A condition's value as read: `platenT 91.2 C`, `InUmbra true`."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return f"{p['variable']} not read"
    if isinstance(p["value"], bool):
        return f"{p['variable']} {str(value != 0).lower()}"
    return f"{p['variable']} {value:.4g}" + (f" {p['unit']}" if p["unit"] else "")


def _when(runner, run, segment) -> int:
    """Arm a rule (once: a loop's later pass or the same line again does not
    re-arm it), and look at it now, so one that already holds acts at once."""
    text = segment.label or "when"
    if text not in runner._rules:
        runner._rules[text] = {"cond": segment.params["cond"], "do": segment.params["do"],
                               "last": False, "due": None, "sent": None}
    runner.watch()
    return 0


_EXECUTORS = {"hold": _hold, "command": _command, "until": _until, "log": _log, "when": _when}
