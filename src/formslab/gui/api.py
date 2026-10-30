"""What the web GUI asks of formsLabCLI beyond `formslab.lab`, as plain functions
(no sockets here): recorded runs, the hardware survey, the plan editor and
orbit files. Run control and status are `formslab.lab`, shared with labcli.

Nothing here opens an instrument. Reads use one short read of a file, never
`ReadStatus`, which writes back.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from formslab import config, lab
from formslab.gui import plans, runs
from formslab.host.sequence import read_lock
from formslab.lab import LabError as ApiError, _lock
from formslab.orbit import file as orbitfile
from formslab.orbit.propagate.kepler import live
from formslab.sequence import block as blockfile
from formslab.sequence import discover
from formslab.sequence.plan import (
    available_rscripts, describe_step, line_options, lines_options, needed_rscripts, tokens,
)


# --- recorded runs -----------------------------------------------------------------

def run_dirs() -> list[Path]:
    """Where recorded runs may be: this machine's output folder, and the one the
    live host's lock says it is writing to (the host starts from wherever it was
    launched)."""
    dirs = [config.output_dir()]
    lock = lab.host() or read_lock()
    if lock and lock.get("output") not in (None, "?"):
        extra = Path(lock["output"])
        if extra.is_dir() and extra.resolve() not in {d.resolve() for d in dirs}:
            dirs.append(extra)
    return dirs


def list_runs() -> dict:
    found = runs.scan(run_dirs())
    for r in found["runs"]:
        r["live"] = bool(lab.host()) and r["modified"] > time.time() - 3 * 60
    return found


def run_series(run_id: str, variables: list[str] | None, max_points: int = runs.MAX_POINTS) -> dict | None:
    """The run's series, and `next_s`: how long until its recorder's next row is due
    (a second after it, so the row is written), by this machine's clock, which
    stamped the rows. A run whose next row is overdue (it has ended) is asked for
    again one cadence later."""
    run = runs.find(run_dirs(), run_id)
    if run is None:
        return None
    out = runs.series(run, variables, max(10, min(int(max_points), 20000)))
    next_s = every = out["every_s"]
    if every and (due := out["t"][-1] + every + 1.0 - time.time()) > 0:
        next_s = due
    return {"id": run_id, "name": run["name"], "started": run["started"], **out, "next_s": next_s}


def hardware() -> dict:
    """What is plugged in, beside the hardware map (`formslab.hardware`). Opens nothing."""
    from formslab import hardware as survey
    return survey.survey()


# --- control -----------------------------------------------------------------------------

def start_run(name: str) -> dict:
    """Start the host on a plan the server itself lists (never a path from the
    request). The launch waits for the host's lock, so `lab.start` runs it on a
    thread and the page shows "starting"."""
    with _lock:
        path = next((p for p in discover() if p.stem == name), None)
    if path is None:
        raise ApiError(404, f"no plan {name!r}")
    return lab.start(name, wait=False)


# --- the plan editor ---------------------------------------------------------------------

def _plan_call(fn, *args):
    """A plans.py call, its refusals turned into API errors."""
    try:
        return fn(*args)
    except plans.PlanFileError as e:
        raise ApiError(e.code, str(e))


def plan_read(name: str) -> dict:
    with _lock:
        return _plan_call(plans.read, name)


def plan_save(name: str, text: str, base_hash: str | None, as_new: bool, kind: str = "plan") -> dict:
    with _lock:
        return _plan_call(plans.save, name, text, base_hash, as_new, kind)


def plan_check(text: str, kind: str = "plan") -> dict:
    """{errors, warnings} in the plan or orbit text, each [{line, message}] (0: the whole file)."""
    with _lock:
        return plans.problems(text, kind)


def describe(words: list[str] | None = None, text: str | None = None, line: int | None = None,
             kind: str = "plan") -> dict:
    """The help card: for line `line` of plan or orbit `text` (the editor), or for
    the command `words` (the command box, its prerequisites checked now)."""
    from formslab.rscripts import cast

    with _lock:
        try:
            if text is not None and kind == "orbit":
                return orbitfile.describe(text, int(line or 0))
            if text is not None and kind == "block":
                return blockfile.describe(text, int(line or 0))
            if text is not None:
                return describe_step(text, int(line or 0))
            return cast.describe(list(words or []))
        except Exception as e:                       # a broken rScript must not break the page
            raise ApiError(500, f"{type(e).__name__}: {e}")


def plan_line(scripts: list[str], words: list[str], kind: str = "plan") -> dict:
    """What can come at each position of a step or orbit line, for the editor's dropdowns."""
    if kind == "orbit":
        return orbitfile.line_options(words)
    with _lock:
        return (blockfile.line_options if kind == "block" else line_options)(scripts, words)


def plan_lines(scripts: list[str], lines: list[list[str]], kind: str = "plan") -> list[dict]:
    """`plan_line` for every row the editor draws at once, in one request."""
    if kind == "orbit":
        return [orbitfile.line_options(words) for words in lines]
    with _lock:
        return (blockfile.lines_options if kind == "block" else lines_options)(scripts, lines)


def rscripts_available() -> list[str]:
    with _lock:
        return available_rscripts()


def _not_running(name: str) -> None:
    running = lab.host()
    if running and running.get("plan") == name:
        raise ApiError(409, f"{name!r} is running; end the run first")


def plan_delete(name: str, base_hash: str | None) -> dict:
    """Move one of your plans to the trash; not while it is the plan running."""
    _not_running(name)
    with _lock:
        return _plan_call(plans.delete, name, base_hash)


def plan_rename(name: str, new: str, base_hash: str | None) -> dict:
    """Rename one of your files, and what refers to it; not the plan running."""
    _not_running(name)
    with _lock:
        return _plan_call(plans.rename, name, new, base_hash)


def plan_move(action: str, name: str, base_hash: str | None) -> dict:
    """`edit` (take a shipped file out to yours) or `ship` (put yours in the
    shipped plans); not while it is the plan running."""
    _not_running(name)
    with _lock:
        return _plan_call({"edit": plans.edit, "ship": plans.ship}[action], name, base_hash)


def plan_tokens(text: str, kind: str = "plan") -> list[list[dict]]:
    """Each line's words with their role in the grammar, for drawing it."""
    if kind == "orbit":
        return orbitfile.tokens(text)
    with _lock:
        return (blockfile.tokens if kind == "block" else tokens)(text)


def plan_needs(text: str) -> list[str]:
    """The rScripts a plan's steps use, for restoring its `load` line."""
    with _lock:
        return needed_rscripts(text)


# --- orbit files ------------------------------------------------------------------------------

def orbit_live(text: str) -> dict:
    """The orbit in `text` propagated to now (kepler.live), or {error} naming its
    first problem, so the panel can say why it shows nothing."""
    try:
        elements = orbitfile.parse(text)
    except orbitfile.OrbitError as e:
        return {"error": str(e)}
    return live(elements, datetime.now(timezone.utc))
