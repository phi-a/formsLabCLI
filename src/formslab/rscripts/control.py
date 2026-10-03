"""Realtime gates for an rScript: run once, hold, tick.

An rScript is called every host loop. These gates decide whether its body runs
on this call. They count **wall-clock** seconds, never simulated time: an
rScript talks to hardware, and a sim running faster than real time must not
poll a chamber controller faster than it would in real time.

    r = RScriptControl(run, name)
    r.hold(seconds=5)     # body runs once every 5 s, first after 5 s
    r.tick(seconds=5)     # body runs now, then every 5 s
    if r:                 # True = blocked on this call
        return

State lives on the Run, keyed by script name, so each run starts clean and
two handles never share gates.
"""
from __future__ import annotations

import time


def clock() -> float:
    """Wall-clock seconds for every gate. One function so tests can move time."""
    return time.monotonic()


class _Gates:
    __slots__ = ("initialized", "hold_until", "hold_calls", "hold_armed",
                 "tick_last", "tick_count")

    def __init__(self) -> None:
        self.initialized = False
        self.hold_armed = False
        self.hold_until = 0.0
        self.hold_calls = 0
        self.tick_last = None
        self.tick_count = 0


def _gates(run, name: str) -> _Gates:
    table = getattr(run, "_rscript_gates", None)
    if table is None:
        table = {}
        setattr(run, "_rscript_gates", table)
    return table.setdefault(name, _Gates())


class RScriptControl:
    """The gates for one rScript on one handle. Cheap; build it every call."""

    def __init__(self, run, name: str) -> None:
        self.run = run
        self.name = name
        self._g = _gates(run, name)
        self._hold_blocked = None     # None = hold not used on this call
        self._tick_blocked = None

    def initialize(self) -> bool:
        """True on the first call for this script, False after."""
        if self._g.initialized:
            return False
        self._g.initialized = True
        return True

    def hold(self, seconds: float = 0, iterations: int = 1) -> "RScriptControl":
        """Block for at least ``seconds`` and at least ``iterations`` calls,
        then let one call through and re-arm on the next."""
        g, now = self._g, clock()
        if not g.hold_armed:
            g.hold_armed = True
            g.hold_until = now + seconds
            g.hold_calls = 0
        g.hold_calls += 1
        if now < g.hold_until or g.hold_calls <= iterations:
            self._hold_blocked = True
        else:
            g.hold_armed = False
            self._hold_blocked = False
        return self

    def tick(self, seconds: float | None = None, iterations: int | None = None) -> "RScriptControl":
        """Let the first call through, then one every ``seconds`` (or every
        ``iterations`` calls)."""
        if seconds is None and iterations is None:
            raise ValueError("tick() needs seconds or iterations")
        g = self._g
        if iterations is not None:
            fire = g.tick_count % max(int(iterations), 1) == 0
            g.tick_count += 1
        else:
            now = clock()
            fire = g.tick_last is None or now - g.tick_last >= seconds
            if fire:
                g.tick_last = now
        self._tick_blocked = not fire
        return self

    def __bool__(self) -> bool:
        """True when this call is blocked by any gate used on it."""
        return bool(self._hold_blocked) or bool(self._tick_blocked)

    @property
    def active(self) -> bool:
        """True while a hold is armed."""
        return self._g.hold_armed
