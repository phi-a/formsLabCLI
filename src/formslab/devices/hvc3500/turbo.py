"""The turbo pump's spin-down, which the controller does not report.

The HVC-3500 says whether the turbo pump is on or off, not how fast it turns. After
`turbo off` the rotor coasts for minutes, and all that while it needs its backing:
the foreline valve open and the vacuum pump on. So formsLAB counts the turbo as
turning until `turbo_spindown_s` (the bench profile's `limits`; 30 minutes unless
the turbo's manual says otherwise) after it was last seen on. The rules on the
foreline and the pumps ask for `turbo_spinning` false, not `turbo` off.

The last sighting is kept beside the host's lock, `.run/turbo_on.json`, by whoever
reads the chamber: rLACO during a run, the monitor between runs. A host that starts
after another ended, or was killed, then knows what that one saw.
"""
from __future__ import annotations

import json
import time

from formslab.config import run_dir
from formslab.console.safefile import atomic_write_text, read_json

DEFAULT_SPINDOWN_S = 1800.0
_WRITE_EVERY_S = 30.0          # a sighting is written at most this often

_last_on: float | None = None  # when this process last saw the turbo on, or the record's
_loaded = False
_written = 0.0
_warned = False


def _path():
    return run_dir() / "turbo_on.json"


def last_on() -> float | None:
    """When the turbo pump was last seen on (time.time()), by this process or by the
    one that wrote the record; None if never."""
    global _last_on, _loaded
    if not _loaded:
        _loaded = True
        try:
            data = read_json(_path())
            t = data.get("last_on") if isinstance(data, dict) else None
            if isinstance(t, (int, float)):
                _last_on = max(_last_on or 0.0, float(t))
        except (OSError, ValueError):
            pass
    return _last_on


def seen_on(now: float | None = None, warn=None) -> None:
    """The turbo pump reads on now: remember it, here and in the record. `warn`
    takes a message if the record cannot be written, once."""
    global _last_on, _written, _warned
    now = time.time() if now is None else now
    last_on()
    _last_on = now
    if now - _written < _WRITE_EVERY_S:
        return
    _written = now
    try:
        atomic_write_text(_path(), json.dumps({"last_on": now}))
    except (OSError, TimeoutError) as exc:
        if warn and not _warned:
            _warned = True
            warn(f"turbo sighting not recorded in {_path()}: {exc}; a host started after this "
                 "one will not know the turbo ran")


def spinning(turbo_on: bool | None, spindown_s: float, now: float | None = None, warn=None) -> bool | None:
    """Whether the turbo pump may still be turning: it reads on, or it was seen on
    less than `spindown_s` ago. None when it reads nothing and was not seen on
    within the time."""
    now = time.time() if now is None else now
    if turbo_on:
        seen_on(now, warn)
        return True
    t = last_on()
    recent = t is not None and now - t < spindown_s
    if turbo_on is None:
        return True if recent else None
    return recent


def annotate(cast: dict, limits: dict, now: float | None = None, warn=None) -> dict:
    """`cast`, the chamber's status block, with `turbo_spinning` from its `turbo`."""
    s = spinning(cast.get("turbo"), float(limits.get("turbo_spindown_s", DEFAULT_SPINDOWN_S)), now, warn)
    if s is not None:
        cast["turbo_spinning"] = s
    return cast


def forget() -> None:
    """Drop what this process remembers (tests)."""
    global _last_on, _loaded, _written, _warned
    _last_on, _loaded, _written, _warned = None, False, 0.0, False
