"""Where the run is, for both front ends: ``<config>/.run/run.json``.

The lock says which host runs; this says where it is: the plan's current step,
whether its steps are held (paused), whether its end is running. The host
replaces the file in one step on each change (safefile.atomic_write_text), so a
reader never sees half of it, and reads it back with `read_json`. A reader takes
it as true only while its pid is the lock's. A host that is killed leaves it
behind with the step it died on, which is what the lost-run banner shows.
"""
from __future__ import annotations

import json

from formslab.config import run_dir
from formslab.state.safefile import atomic_write_text, read_json

FIELDS = ("pid", "plan", "started", "output", "step", "paused", "paused_since", "ending")


def path():
    return run_dir() / "run.json"


def read() -> dict | None:
    """The file as it is, or None when there is none or it cannot be read."""
    try:
        data = read_json(path())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def begin(lock: dict) -> None:
    """A fresh file for the host that has just taken `lock` ({pid, plan, started, output})."""
    _put({**lock, "step": None, "paused": False, "paused_since": None, "ending": False})


def write(**fields) -> None:
    """Change `fields` and keep the rest. Never raises: telemetry must not stop a run."""
    current = read()
    if current is None:                               # no run of ours: nothing to change
        return
    _put({**current, **fields})


def clear() -> None:
    try:
        path().unlink(missing_ok=True)
    except OSError:
        pass


def _put(data: dict) -> None:
    try:
        atomic_write_text(path(), json.dumps(data, indent=1))
    except Exception:
        pass
