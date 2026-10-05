"""What the web GUI asks of formsLabCLI, as plain functions (no sockets here).

Nothing here opens an instrument. Reads (the host's lock, CAST status blocks, the
host log, recorded runs) use one short read of the file, never `ReadStatus`,
which writes back. Control goes the way the console's does: a run is started
as the sequence host process, and `end`, `pause`, `resume` and commands are
written to the ctrl and CAST files for the host to take.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from formslab import config
from formslab.console.cast import castutils
from formslab.console.ctrl import ctrlcli, ctrlutils
from formslab.console.log.logcli import log_path
from formslab.console.safefile import read_json
from formslab.gui import plans, runs
from formslab.host.sequence import is_host, read_lock
from formslab import rscripts
from formslab.rscripts import cast
from formslab.rscripts.grammar import GrammarError
from formslab.sequence import PlanError, discover, load_plan
from formslab.sequence.plan import available_rscripts, describe_step, line_options, needed_rscripts, tokens
from formslab.state import cast_state_path

# How often each block is republished while its owner runs (seconds). A block
# older than three of these, plus a little slack, is not live.
CADENCE_S = {"tc": 2.0, "hvc": 6.0, "psu1": 1.0, "psu2": 1.0, "cryo": 1.0, "slta": 3.0}
DEFAULT_CADENCE_S = 5.0
SLACK_S = 2.0
LOG_TAIL_BYTES = 64 * 1024
CAST_TAKE_S = castutils.TAKE_S     # how long a command may wait to be taken
END_WAIT_S = 120.0         # how long an `end` is kept being sent to a host that is still up
END_RESEND_S = 3.0


class ApiError(Exception):
    """A refusal, with the HTTP status the server should answer it with."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# rScript-importing calls (plans, completion) share a module cache that is not
# thread-safe, and the GUI's own CAST writes are serialised.
_lock = threading.RLock()
_action_lock = threading.Lock()
_action: dict = {"name": None, "since": 0.0}


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
    """Is this block live, and if not, why: a host is running, its owner reported
    within three of its publish cadences (the block's timestamp is only ever set
    by an owner's status write), and a chamber says it is connected."""
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
        status_ = block.get("status") or {}
        with _lock:
            try:
                groups = cast.readings(label, status_) if isinstance(status_, dict) else []
            except Exception:                        # a broken rScript must not break the page
                groups = []
        blocks[label] = {"status": status_, "groups": groups,
                         "pending": bool(block.get("request")) and not block.get("processed", True),
                         **freshness(label, block, running, now)}
    return {"now": now, "host": running, "last_run": None if running else read_lock(),
            "action": current_action(), "blocks": blocks, "cast_unreadable": data is None,
            "log": log_tail(log_lines)}


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


# --- control --------------------------------------------------------------------------

def current_action() -> str | None:
    """"starting" or "ending" while a start or an end is in progress, else None."""
    with _action_lock:
        return _action["name"]


def _begin(name: str) -> None:
    with _action_lock:
        if _action["name"]:
            raise ApiError(409, f"a run is already {_action['name']}")
        _action.update(name=name, since=time.time())


def _finish() -> None:
    with _action_lock:
        _action["name"] = None


def list_plans() -> list[dict]:
    """The plans `run <name>` can start, each with the rScripts it loads, or why
    it does not read."""
    out = []
    with _lock:
        for path in discover():
            try:
                plan = load_plan(path)
            except PlanError as e:
                out.append({"name": path.stem, "error": str(e), "editable": plans.is_editable(path)})
                continue
            out.append({"name": path.stem, "rscripts": list(plan.rscripts),
                        "steps": len(plan.sequence.segments), "editable": plans.is_editable(path),
                        "warnings": [f"line {n}: {m}" for n, m in plan.warnings],
                        "open_ended": any(s.verb == "hold" and s.params["seconds"] is None
                                          for s in plan.sequence.segments)})
    return out


def start_run(name: str) -> dict:
    """Start the host on a plan the server itself lists (never a path from the
    request). The launch waits for the host's lock, so it runs on a thread."""
    with _lock:
        path = next((p for p in discover() if p.stem == name), None)
        if path is None:
            raise ApiError(404, f"no plan {name!r}")
        try:
            plan = load_plan(path)
        except PlanError as e:
            raise ApiError(400, str(e))
        missing = [n for n in plan.rscripts if rscripts.find(n) is None]
        if missing:                              # the host refuses a plan whose scripts are not found
            raise ApiError(400, f"rScript {', '.join(missing)} not found "
                                f"(searched {', '.join(str(d) for d in rscripts.search_dirs())})")
    if host():
        raise ApiError(409, "a run is already going")
    _begin("starting")

    def launch() -> None:
        try:
            ctrlcli._launch_sequence(str(path.resolve()))
        finally:
            _finish()

    threading.Thread(target=launch, name="gui-start", daemon=True).start()
    return {"state": "starting", "plan": name}


def end_run() -> dict:
    """Ask the host to stop, so every rScript's rShutdown runs. `end` is sent
    again every few seconds while the host lives (a host that is starting clears
    the ctrl file and can erase it), and the host is never killed from here."""
    running = host()
    if running is None:
        raise ApiError(409, "no run is going")
    _begin("ending")

    def end(pid: int) -> None:
        try:
            deadline, resend_at = time.monotonic() + END_WAIT_S, 0.0
            while time.monotonic() < deadline and is_host(pid):
                if time.monotonic() >= resend_at:
                    resend_at = time.monotonic() + END_RESEND_S
                    try:
                        ctrlutils.WriteCommand("end")
                    except (OSError, ValueError):
                        pass                             # tried again next round
                time.sleep(0.25)
        finally:
            _finish()

    threading.Thread(target=end, args=(running["pid"],), name="gui-end", daemon=True).start()
    return {"state": "ending", "plan": running["plan"]}


def ctrl(command: str) -> dict:
    """`pause` or `resume` the running plan's steps."""
    if command not in ("pause", "resume"):
        raise ApiError(400, f"unknown ctrl command {command!r}")
    if host() is None:
        raise ApiError(409, "no run is going")
    try:
        ctrlutils.WriteCommand(command)
    except (OSError, ValueError) as e:
        raise ApiError(503, f"could not write the ctrl file: {e}")
    return {"sent": command}


def complete(words: list[str]) -> list[dict]:
    """What can come after `words` in a command, for the command box."""
    with _lock:
        try:
            options = cast.complete(words)
            parts = cast.option_parts(words, options)
        except Exception as e:                       # a broken rScript must not break the box
            raise ApiError(500, f"{type(e).__name__}: {e}")
    return [{"kind": o.kind, "text": o.text, "help": o.help, "lo": o.lo, "hi": o.hi, "unit": o.unit,
             "part": p} for o, p in zip(options, parts)]


def send_command(line: str) -> dict:
    """Send `hvc platen 20`-style words to the rScript that owns the label,
    exactly as the cast tab does (cast.send), and say what became of it.

    Refused unless a run is going and the instrument's block is live: a command
    to a chamber that is not connected, or whose rScript is not loaded, would sit
    unread, and the page would look as if it had worked."""
    words = line.split()
    if not words:
        raise ApiError(400, "type a command, e.g. hvc vent open")
    label = words[0].lower()
    with _lock:
        try:
            request = cast.request(label, words[1:])
            wait = cast.reports_results(label)
        except GrammarError as e:
            raise ApiError(400, str(e))
    running = host()
    if running is None:
        raise ApiError(409, "no run is going, so nothing would apply it")
    blocks = read_blocks() or {}
    fresh = freshness(label, blocks.get(label) or {}, running, time.time())
    if not fresh["live"]:
        raise ApiError(409, f"{label} is not live ({fresh['reason']}), so the command would not be taken")
    # Outside the lock: the wait (up to cast.REPLY_S) must not hold up the other pages.
    return cast.send(label, request, host=running, wait_result=wait, take_s=CAST_TAKE_S)


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


def plan_save(name: str, text: str, base_hash: str | None, as_new: bool) -> dict:
    with _lock:
        return _plan_call(plans.save, name, text, base_hash, as_new)


def plan_check(text: str) -> dict:
    """{errors, warnings} in the plan text, each [{line, message}] (0: the whole file)."""
    with _lock:
        return plans.problems(text)


def describe(words: list[str] | None = None, text: str | None = None, line: int | None = None) -> dict:
    """The help card: for line `line` of plan `text` (the editor), or for the
    command `words` (the command box, its prerequisites checked now)."""
    with _lock:
        try:
            if text is not None:
                return describe_step(text, int(line or 0))
            return cast.describe(list(words or []))
        except Exception as e:                       # a broken rScript must not break the page
            raise ApiError(500, f"{type(e).__name__}: {e}")


def plan_line(scripts: list[str], words: list[str]) -> dict:
    """What can come at each position of a step line, for the editor's dropdowns."""
    with _lock:
        return line_options(scripts, words)


def rscripts_available() -> list[str]:
    with _lock:
        return available_rscripts()


def plan_delete(name: str, base_hash: str | None) -> dict:
    """Move one of your plans to the trash; not while it is the plan running."""
    running = host()
    if running and running.get("plan") == name:
        raise ApiError(409, f"{name!r} is running; end the run first")
    with _lock:
        return _plan_call(plans.delete, name, base_hash)


def plan_tokens(text: str) -> list[list[dict]]:
    """Each line's words with their role in the grammar, for drawing it."""
    with _lock:
        return tokens(text)


def plan_needs(text: str) -> list[str]:
    """The rScripts a plan's steps use, for restoring its `load` line."""
    with _lock:
        return needed_rscripts(text)
