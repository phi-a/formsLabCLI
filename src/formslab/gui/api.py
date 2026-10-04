"""What the web GUI asks of formsLabCLI, as plain functions (no sockets here).

Reads only, in this stage: the host's lock, CAST status blocks, the host log
and the recorded runs. Nothing here opens an instrument. CAST is read with one
short read of the file and never through `ReadStatus`, which writes back.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from formslab import config
from formslab.console.log.logcli import log_path
from formslab.console.safefile import read_json
from formslab.gui import runs
from formslab.host.sequence import is_host, read_lock
from formslab.state import cast_state_path

# How often each block is republished while its owner runs (seconds). A block
# older than three of these, plus a little slack, is not live.
CADENCE_S = {"tc": 2.0, "hvc": 6.0, "psu1": 1.0, "psu2": 1.0, "cryo": 1.0, "slta": 3.0}
DEFAULT_CADENCE_S = 5.0
SLACK_S = 2.0
LOG_TAIL_BYTES = 64 * 1024


def host() -> dict | None:
    """The live host's lock ({pid, plan, started, output}), or None."""
    lock = read_lock()
    return lock if lock and is_host(lock["pid"]) else None


def _epoch(iso: str) -> float | None:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def freshness(label: str, block: dict, running: dict | None, now: float) -> dict:
    """Is this block live, and if not, why. A block has one timestamp, bumped by
    its owner's status writes but also by a command written to it and by a host
    start (which stamps every block "now" and keeps the old status), so age alone
    is not enough: it needs a running host, and a chamber that says it is connected."""
    age = now - float(block.get("timestamp") or 0)
    if running is None:
        return {"live": False, "age_s": age, "reason": "no run is going"}
    limit = 3 * CADENCE_S.get(label, DEFAULT_CADENCE_S) + SLACK_S
    if age > limit:
        return {"live": False, "age_s": age, "reason": f"not updated for {age:.0f} s "
                                                       f"(its owner may not be loaded)"}
    status = block.get("status") or {}
    if label == "hvc" and not status.get("connected"):
        return {"live": False, "age_s": age, "reason": status.get("error") or "chamber not connected"}
    return {"live": True, "age_s": age, "reason": ""}


def read_blocks() -> dict | None:
    """castfile.json as written by the host, or None when it cannot be read."""
    try:
        data = read_json(cast_state_path())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def status(log_lines: int = 40) -> dict:
    now = time.time()
    running = host()
    data = read_blocks()
    blocks = {}
    for label, block in (data or {}).items():
        if not isinstance(block, dict):
            continue
        blocks[label] = {"status": block.get("status") or {},
                         "pending": bool(block.get("request")) and not block.get("processed", True),
                         **freshness(label, block, running, now)}
    return {"now": now, "host": running, "last_run": None if running else read_lock(),
            "blocks": blocks, "cast_unreadable": data is None, "log": log_tail(log_lines)}


def log_tail(lines: int = 40) -> list[str]:
    """The last `lines` lines of the host log (read from its tail only)."""
    path = log_path()
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-max(1, min(int(lines), 500)):]


# --- recorded runs -----------------------------------------------------------------

def run_dirs() -> list[Path]:
    """Where recorded runs may be: this machine's output folder, and the one the
    live host's lock says it is writing to (the host starts from wherever it was
    launched)."""
    dirs = [config.output_dir()]
    lock = host() or read_lock()
    if lock and lock.get("output") not in (None, "?"):
        extra = Path(lock["output"])
        if extra.is_dir() and extra.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(extra)
    return dirs


def list_runs() -> dict:
    found = runs.scan(run_dirs())
    for r in found["runs"]:
        r["live"] = bool(host()) and r["modified"] > time.time() - 3 * 60
    return found


def run_series(run_id: str, variables: list[str] | None, max_points: int = runs.MAX_POINTS) -> dict | None:
    run = runs.find(run_dirs(), run_id)
    if run is None:
        return None
    return {"id": run_id, "name": run["name"], "started": run["started"],
            **runs.series(run, variables, max(10, min(int(max_points), 20000)))}
