"""`Run`: the object every rScript receives.

    def rScript(run):
        run.log("connected", component="rMine")
        run.publish("chamberP", 4.4, "Torr")      # create-or-update a named value
        run.get("chamberP")                        # read it back (None if unset)

A run is one execution of a plan. It is the shared state of that run: what
rScripts publish, what the plan's `until` steps and the CSV read. Built on the
standard library; it knows nothing about any instrument.

    log(message, level, component)       one line on stdout (the host's log)
    publish(name, value, unit)           set a named value; creates it on first use
    get(name) / variable(name)           its value / the `Scalar` (value and unit)
    names()                              every published name, in creation order
    record(value=30, unit="seconds")     set the CSV cadence; `record()` emits a row
    time.clock() / time.timestamp        wall-clock

A value keeps the unit it was first published in; publishing it in another unit
is an error, not a silent conversion. Orbit-driven inputs (in umbra or not, a
sun angle) are published like any other value, from a profile computed offline.
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from formslab.config import output_dir

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_SECONDS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Scalar:
    """A named number with a fixed unit. Setting it in another unit is an
    error rather than a silent conversion. `updated` is when it was last set
    (time.monotonic), so a reader can tell an old value from a current one."""

    def __init__(self, name: str, value=0, unit: str | None = None) -> None:
        self.name = name
        self.value = value
        self.unit = unit
        self.updated = time.monotonic()     # when it was last set

    def set(self, value, unit: str | None = None) -> None:
        if unit is not None and self.unit is not None and unit != self.unit:
            raise ValueError(f"{self.name} is in {self.unit}, got a value in {unit}")
        self.value = value
        self.updated = time.monotonic()

    def get(self):
        return self.value

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return f"Scalar({self.name}={self.value!r}{unit})"


class _Clock:
    """Wall-clock time since the run was made."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()

    def clock(self) -> float:
        return time.monotonic() - self._t0

    @property
    def elapsed(self) -> float:
        return self.clock()

    @property
    def timestamp(self) -> str:
        return _utc_now()


class _Recorder:
    """Every variable to CSV at a wall-clock cadence.

    ``<output>/<name>_<UTC start>.csv``, one file per run -- lab data is never
    overwritten. Columns are ``index, timestamp`` then ``<variable> [<unit>]``.
    A variable appearing mid-run starts a new file (``_1``, ``_2``...) rather
    than misaligning columns.
    """

    def __init__(self, run: "Run", directory: Path | None) -> None:
        self._run = run
        self._dir = directory
        self._interval = None
        self._next = 0.0
        self._stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._part = 0
        self._header = None
        self._index = 0
        self.path: Path | None = None

    def __call__(self, value=None, unit: str = "seconds", *, force: bool = False) -> None:
        if value is not None:
            return self.set_interval(value, unit)
        if not self._run.recording:
            return
        now = time.monotonic()
        if not force and (self._interval is None or now < self._next):
            return
        # Advance only once a row is written: the first row waits for the
        # rScripts (on their own threads) to publish something, not a whole
        # interval.
        if self._write_row() and self._interval is not None:
            self._next = now + self._interval

    def set_interval(self, value=10, unit: str = "seconds") -> None:
        if unit not in _SECONDS:
            raise ValueError(f"record unit must be one of {sorted(_SECONDS)}, got {unit!r}")
        self._interval = float(value) * _SECONDS[unit]
        self._next = time.monotonic()
        self._run.log(f"Record every {value} {unit}", component="LAB")

    def _write_row(self) -> bool:
        names = self._run.names()
        if not names:
            return False
        header = ["index", "timestamp"] + [
            f"{n} [{v.unit}]" if (v := self._run.variable(n)).unit else n for n in names]
        if header != self._header:
            if self._header is not None:
                self._part += 1
            self._header, self._index = header, 0
            directory = self._dir or output_dir()
            suffix = f"_{self._part}" if self._part else ""
            self.path = Path(directory) / f"{self._run.name}_{self._stamp}{suffix}.csv"
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)
        row = [self._index, _utc_now()] + [self._run.variable(n).value for n in names]
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        self._index += 1
        return True


class Run:
    """One execution of a plan. See the module docstring."""

    def __init__(self, name: str = "LAB", *, record_dir: Path | None = None,
                 log_level: str = "INFO", stream=None) -> None:
        self.name = name
        self.component = "LAB"
        self.log_level = log_level
        self._stream = stream
        self._variables: dict[str, Scalar] = {}
        self._lock = threading.Lock()
        self.time = _Clock()
        self.recording = True
        self.record = _Recorder(self, record_dir)

    def log(self, message=None, level: str = "INFO", component: str | None = None, **_fields) -> None:
        level = str(level).upper()
        if _LEVELS.get(level, 20) < _LEVELS.get(self.log_level, 20):
            return
        line = f"{_utc_now()} [{level}] [{component or self.component}] {message}"
        try:
            print(line, file=self._stream or sys.stdout, flush=True)
        except UnicodeEncodeError:                 # a stream that cannot hold the text: keep the line
            stream = self._stream or sys.stdout
            enc = getattr(stream, "encoding", None) or "ascii"
            try:
                print(line.encode(enc, "replace").decode(enc), file=stream, flush=True)
            except (OSError, ValueError):
                pass
        except OSError:
            pass

    def publish(self, name: str, value=0, unit: str | None = None) -> Scalar:
        """Set a named value, creating it on first use. Safe to call from any
        rScript's thread. Returns the `Scalar`."""
        with self._lock:
            var = self._variables.get(name)
            if var is None:
                var = self._variables[name] = Scalar(name, value, unit)
                return var
        var.set(value, unit)
        return var

    def variable(self, name: str) -> Scalar | None:
        return self._variables.get(name)

    def get(self, name: str, default=None):
        var = self._variables.get(name)
        return default if var is None else var.value

    def names(self) -> list[str]:
        return list(self._variables)

