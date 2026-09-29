"""`LabForms`: the `forms` handle for a run without FORMS.

An rScript receives one object, ``forms``, and touches only what it offers.
When a test has an orbit, that object is a real FORMS instance. When it does
not -- a chamber soak, a PSU sweep -- it is this: the subset of the FORMS handle
that hardware scripts actually use, built on the standard library.

    log(message, level, component)       one line on stdout (the host's log)
    types.scalar(name, value, unit)      a named value; `get_variable(name)`
    record(value=30, unit="seconds")     set the CSV cadence; `record()` emits
    time.clock() / time.timestamp        wall-clock, realtime only
    transition.request(mode)             ask the host to change mode

A script that needs more than this (satellite state, frames) declares
``requires = ("forms",)`` and the loader skips it on this handle. Anything an
orbit script publishes through `types.scalar` is an ordinary variable here, so
a homemade orbit rScript works without FORMS as well.
"""
from __future__ import annotations

import csv
import sys
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
    error rather than a silent conversion."""

    registered = True

    def __init__(self, name: str, value=0, unit: str | None = None) -> None:
        self.name = name
        self.value = value
        self.unit = unit

    def set(self, value, unit: str | None = None) -> None:
        if unit is not None and self.unit is not None and unit != self.unit:
            raise ValueError(f"{self.name} is in {self.unit}, got a value in {unit}")
        self.value = value

    def get(self):
        return self.value

    def as_record_value(self):
        return self.value

    def record(self):
        """FORMS marks a variable for CSV here. The lab handle records every
        variable, so this is accepted and does nothing."""
        return self

    def __float__(self) -> float:
        return float(self.value)

    def __repr__(self) -> str:
        unit = f" {self.unit}" if self.unit else ""
        return f"Scalar({self.name}={self.value!r}{unit})"


class _Types:
    def __init__(self, forms: "LabForms") -> None:
        self._forms = forms

    def scalar(self, name: str, value=0, unit: str | None = None, overwrite: bool = True) -> Scalar:
        existing = self._forms._variables.get(name)
        if existing is not None and not overwrite:
            return existing
        var = Scalar(name, value, unit)
        self._forms.register_variable(name, var)
        return var


class _Clock:
    """Wall-clock time since the handle was made."""

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


class TransitionMode:
    """A pending host mode change, same contract as FORMS ``forms.transition``."""

    def __init__(self) -> None:
        self.target = None
        self.pending = False
        self.source = None

    def request(self, mode, source=None) -> None:
        self.target, self.pending, self.source = mode, True, source

    def consume(self):
        if not self.pending:
            return None
        mode = self.target
        self.target, self.pending, self.source = None, False, None
        return mode

    def __bool__(self) -> bool:
        return self.pending


class _Recorder:
    """Every variable to CSV at a wall-clock cadence.

    ``<output>/<name>_<UTC start>.csv``, one file per run -- lab data is never
    overwritten. Columns are ``index, timestamp`` then ``<variable> [<unit>]``.
    A variable appearing mid-run starts a new file (``_1``, ``_2``...) rather
    than misaligning columns.
    """

    def __init__(self, forms: "LabForms", directory: Path | None) -> None:
        self._forms = forms
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
        if not self._forms.recording:
            return
        now = time.monotonic()
        if not force and (self._interval is None or now < self._next):
            return
        if self._interval is not None:
            self._next = now + self._interval
        self._write_row()

    def set_interval(self, value=10, unit: str = "seconds") -> None:
        if unit not in _SECONDS:
            raise ValueError(f"record unit must be one of {sorted(_SECONDS)}, got {unit!r}")
        self._interval = float(value) * _SECONDS[unit]
        self._next = time.monotonic()
        self._forms.log(f"Record every {value} {unit}", component="LAB")

    def _write_row(self) -> None:
        names = self._forms.list_variables()
        if not names:
            return
        header = ["index", "timestamp"] + [
            f"{n} [{v.unit}]" if (v := self._forms.get_variable(n)).unit else n for n in names]
        if header != self._header:
            if self._header is not None:
                self._part += 1
            self._header, self._index = header, 0
            directory = self._dir or output_dir()
            suffix = f"_{self._part}" if self._part else ""
            self.path = Path(directory) / f"{self._forms.name}_{self._stamp}{suffix}.csv"
            with self.path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(header)
        row = [self._index, _utc_now()] + [self._forms.get_variable(n).value for n in names]
        with self.path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
        self._index += 1


class LabForms:
    """The `forms` handle for a hardware-only run. See the module docstring."""

    is_lab_handle = True

    def __init__(self, name: str = "LAB", *, record_dir: Path | None = None,
                 log_level: str = "INFO", stream=None) -> None:
        self.name = name
        self.component = "LAB"
        self.log_level = log_level
        self._stream = stream
        self._variables: dict[str, object] = {}
        self.types = _Types(self)
        self.time = _Clock()
        self.transition = TransitionMode()
        self.recording = True
        self.record = _Recorder(self, record_dir)

    def log(self, message=None, level: str = "INFO", component: str | None = None, **_fields) -> None:
        level = str(level).upper()
        if _LEVELS.get(level, 20) < _LEVELS.get(self.log_level, 20):
            return
        line = f"{_utc_now()} [{level}] [{component or self.component}] {message}"
        try:
            print(line, file=self._stream or sys.stdout, flush=True)
        except OSError:
            pass

    def register_variable(self, name: str, variable) -> None:
        self._variables[name] = variable

    def get_variable(self, name: str):
        return self._variables.get(name)

    def list_variables(self) -> list[str]:
        return list(self._variables)

    def derive(self) -> None:
        """FORMS updates derived state here each step. Nothing is derived in a
        hardware-only run."""
