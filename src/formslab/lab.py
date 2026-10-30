"""The lab as both front ends see and drive it.

formsLAB (the web GUI) and labcli (the console and `labcli <command>`) call
these functions and nothing lower: what is going now, start a plan, end it,
pause and resume, send a command. One backend, so the two cannot disagree.

Nothing here opens an instrument. Reads (the host's lock, where its run is,
the CAST blocks, the host log) are one short read of a file each, never
`ReadStatus`, which writes back. Control goes one way: a run is started as the
sequence host process, and `end`, `pause`, `resume` and commands are written to
the ctrl and CAST files for the host to take. End never kills the host: a host
that does not take it is left alive and said so.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from formslab import config, rscripts
from formslab.state import castfile as castutils
from formslab.state import ctrlfile as ctrlutils
from formslab.state.safefile import read_json
from formslab.host import runstate
from formslab.host.sequence import (
    END_CAP_S, END_SCRIPT, END_SETTLE_S, ended_path, is_host, lost_run, read_lock,
)
from formslab.orbit import file as orbitfile
from formslab.rscripts import cast
from formslab.rscripts.grammar import GrammarError
from formslab.sequence import PlanError, discover, find_plan, load_plan
from formslab.sequence import block as blockfile
from formslab.sequence.plan import is_editable
from formslab.state import cast_state_path

# How often each block is republished while its owner runs (seconds). A block
# older than three of these, plus a little slack, is not live.
CADENCE_S = {"tc": 2.0, "hvc": 6.0, "psu1": 1.0, "psu2": 1.0, "cryo": 5.0, "slta": 3.0, "orbit": 1.0}
DEFAULT_CADENCE_S = 5.0
SLACK_S = 2.0
LOG_TAIL_BYTES = 64 * 1024
CAST_TAKE_S = castutils.TAKE_S     # how long a command may wait to be taken
LAUNCH_WAIT_S = 5.0                # the host writes its lock at start
# How long an `end` is kept being sent to a host that is still up: the end script's
# own limit, then its routines' threads (15 s) and their shutdowns.
END_WAIT_S = END_CAP_S + 30.0
END_RESEND_S = 3.0
ACTIONS_LOG = "actions.log"


class LabError(Exception):
    """A refusal, with the HTTP status the GUI's server answers it with."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# rScript-importing calls (plans, completion) share a module cache that is not
# thread-safe, and this process's own CAST writes are serialised.
_lock = threading.RLock()
_action_lock = threading.Lock()
_action: dict = {"name": None, "since": 0.0}


# --- what is going ------------------------------------------------------------------

def host() -> dict | None:
    """The live host's lock ({pid, plan, started, output}), or None."""
    lock = read_lock()
    return lock if lock and is_host(lock["pid"]) else None


def run_state() -> dict | None:
    """Where the run is (run.json: step, paused, ending), for the lock's host,
    live or lost; None when there is no lock or the file is another run's."""
    lock, state = read_lock(), runstate.read()
    if lock is None or state is None or state.get("pid") != lock["pid"]:
        return None
    return state


def freshness(label: str, block: dict, running: dict | None, now: float) -> dict:
    """Is this block live, and if not, why: a host is running, its owner reported
    within three of its publish cadences (the block's timestamp is only ever set
    by an owner's status write), and a chamber says it is connected."""
    age = now - float(block.get("timestamp") or 0)
    limit = 3 * CADENCE_S.get(label, DEFAULT_CADENCE_S) + SLACK_S
    if running is None:
        # Between runs the chamber monitor (formslab.host.monitor) keeps "hvc" read.
        status = block.get("status") or {}
        if label == "hvc" and age <= limit and status.get("connected"):
            return {"live": True, "watched": True, "age_s": age, "reason": ""}
        return {"live": False, "age_s": age, "reason": "no run is going"}
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
    """Everything a front end shows at once: the host, where its run is, each
    block with its readings and whether it is live, the log tail, how the last
    run ended, a run whose host was lost, and who is reading the chamber."""
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
    return {"now": now, "host": running, "run": run_state(),
            "last_run": None if running else read_lock(),
            "action": action(), "blocks": blocks, "cast_unreadable": data is None,
            "log": log_tail(log_lines), "ended": None if running else last_ended(),
            "lost": None if running else lost_run(), "monitor": monitor_state()}


def monitor_state() -> dict | None:
    """Who reads the chamber between runs: {pid, mine}, or None when nobody does."""
    from formslab.host import monitor

    pid = monitor.holder()
    return None if pid is None else {"pid": pid, "mine": pid == os.getpid()}


def last_ended() -> dict | None:
    """How the last run ended (the host's ended.json): plan, when, how, the chamber
    as it was left, and what the end script could not do."""
    try:
        return json.loads(ended_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def end_steps() -> list[str]:
    """What End will do for the running plan, or for the run whose host was lost:
    the end script's steps for the routines it loaded, as the End dialog lists them."""
    running = host() or lost_run()
    if running is None:
        raise LabError(409, "no run is going")
    path = next((p for p in discover() if p.stem == END_SCRIPT), None)
    if path is None:
        return []
    from formslab.host.sequence import _needs

    with _lock:
        try:
            script, plan = load_plan(path), load_plan(next(p for p in discover() if p.stem == running["plan"]))
        except (PlanError, StopIteration):
            return []
        labels, loaded = cast.owners()[0], set(plan.rscripts)
        return [s.label for s in script.sequence.segments if _needs(s, labels) <= loaded]


def log_path() -> Path:
    """The sequence host's log: `launch` redirects the host's stdout here, and
    `log_tail`, `labcli log` and the GUI read it back."""
    return config.run_dir() / "host.log"


def log_tail(lines: int = 40) -> list[str]:
    """The last `lines` lines of the host log (read from its tail only)."""
    try:
        with log_path().open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-max(1, min(int(lines), 500)):]


def list_plans() -> list[dict]:
    """The plans `run <name>` can start, each with the rScripts it loads, or why
    it does not read; then the blocks (kind "block"), which plans call, and the
    orbit files (kind "orbit"), which the editor opens; neither is run itself."""
    out = []
    with _lock:
        for path in discover():
            try:
                plan = load_plan(path)
            except PlanError as e:
                out.append({"name": path.stem, "kind": "plan", "error": str(e),
                            "editable": is_editable(path)})
                continue
            out.append({"name": path.stem, "kind": "plan", "rscripts": list(plan.rscripts),
                        "steps": len(plan.sequence.segments), "editable": is_editable(path),
                        "warnings": [f"line {n}: {m}" for n, m in plan.warnings],
                        "open_ended": plan.sequence.open_ended,
                        **({"end": True} if path.stem == END_SCRIPT else {})})   # run by End, never started
        taken = {p["name"] for p in out}
        for path in blockfile.discover():
            if path.stem in taken:                      # a plan of the same name is found first
                continue
            errors, _ = blockfile.review(path.read_text(encoding="utf-8"))
            out.append({"name": path.stem, "kind": "block", "editable": is_editable(path),
                        **({"error": f"line {errors[0][0]}: {errors[0][1]}"} if errors else {})})
            taken.add(path.stem)
        for path in orbitfile.discover():
            if path.stem in taken:                      # a plan of the same name is found first
                continue
            errors, _ = orbitfile.review(path.read_text(encoding="utf-8"))
            out.append({"name": path.stem, "kind": "orbit", "editable": is_editable(path),
                        **({"error": f"line {errors[0][0]}: {errors[0][1]}"} if errors else {})})
    return out


def complete(words: list[str]) -> list[dict]:
    """What can come after `words` in a command, for the command box and `?`."""
    with _lock:
        try:
            options = cast.complete(words)
            parts = cast.option_parts(words, options)
        except Exception as e:                       # a broken rScript must not break the box
            raise LabError(500, f"{type(e).__name__}: {e}")
    return [{"kind": o.kind, "text": o.text, "help": o.help, "lo": o.lo, "hi": o.hi, "unit": o.unit,
             "part": p} for o, p in zip(options, parts)]


# --- control ---------------------------------------------------------------------------

def action() -> str | None:
    """"starting" or "ending" while this process has a start or an end in progress."""
    with _action_lock:
        return _action["name"]


def _begin(name: str) -> None:
    with _action_lock:
        if _action["name"]:
            raise LabError(409, f"a run is already {_action['name']}")
        _action.update(name=name, since=time.time())


def _finish() -> None:
    with _action_lock:
        _action["name"] = None


def audit(who: str, what: str, where: str = "labcli") -> None:
    """One line in ``<config>/.run/actions.log`` for each action a person asked
    for, from either front end: when, who, where (gui or labcli), what."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with (config.run_dir() / ACTIONS_LOG).open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {who} {where} {what}\n")
    except OSError:
        pass


def launch(plan_path: str | None, *, end_only: bool = False, fast: bool = False) -> dict:
    """Start `python -m formslab.host.sequence <plan>`, its output to the host log,
    detached (closing the window that started it does not kill a run). `end_only`
    (no plan): the end of the run whose host was lost (`--end`); `fast`: a Fast
    End. Returns {state, plan, pid}: "running" (a host already was), "started"
    (its lock appeared) or "starting" (not yet, within LAUNCH_WAIT_S)."""
    running = host()
    if running:
        return {"state": "running", "plan": running["plan"], "pid": running["pid"]}
    name = Path(plan_path).stem if plan_path else "the lost run's end"
    cmd = [sys.executable, "-m", "formslab.host.sequence"] + (
        (["--end"] + (["--fast"] if fast else [])) if end_only else [plan_path])
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        flags = {"start_new_session": True}
    # The host log is UTF-8 whatever the console's codepage: on Windows a redirected
    # stdout defaults to cp1252, and a log line with a character outside it ("→", "❌")
    # would otherwise raise inside the host.
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:                                              # the last run's log is kept, one back
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:                                   # none yet, or held open by a reader
        pass
    log = path.open("w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                            stdin=subprocess.DEVNULL, **flags)
    log.close()                                       # the host has its own copy
    # Collect the host when it exits, so a long-lived caller (the web GUI) does
    # not leave a zombie per run on Linux.
    threading.Thread(target=proc.wait, daemon=True).start()
    deadline = time.monotonic() + LAUNCH_WAIT_S
    while time.monotonic() < deadline:
        running = host()
        if running:
            return {"state": "started", "plan": running["plan"], "pid": running["pid"]}
        time.sleep(0.2)
    return {"state": "starting", "plan": name, "pid": None}


def start(name: str, *, wait: bool = True) -> dict:
    """Start the host on a plan, named or by path. Refused when the plan does not
    read, an rScript it loads is not found, or a run is going. `wait`: return
    once the host's lock appears (`launch`); else launch on a thread and return
    {"state": "starting"} at once, as the GUI does."""
    path = find_plan(name)
    if path is None:
        raise LabError(404, f"no plan {name!r}")
    with _lock:
        try:
            plan = load_plan(path)
        except PlanError as e:
            raise LabError(400, str(e))
        missing = [n for n in plan.rscripts if rscripts.find(n) is None]
        if missing:                              # the host refuses a plan whose scripts are not found
            raise LabError(400, f"rScript {', '.join(missing)} not found "
                                f"(searched {', '.join(str(d) for d in rscripts.search_dirs())})")
    if host():
        raise LabError(409, "a run is already going")
    _begin("starting")

    def go() -> dict:
        try:
            return launch(str(path.resolve()))
        finally:
            _finish()

    if not wait:
        threading.Thread(target=go, name="lab-start", daemon=True).start()
        return {"state": "starting", "plan": path.stem}
    return go()


_end_key: dict = {"key": None}            # what End sends: "fast" for a Fast End


def end(fast: bool = False) -> dict:
    """Ask the host to stop, so the end script and every rScript's rShutdown run.
    `end` is sent again every few seconds while the host lives (a host that is
    starting clears the ctrl file and can erase it), and the host is never
    killed from here. With no host but a lost run (its host was killed), that
    run's end runs from a fresh host. `fast`: a Fast End, the end script's steps
    sent without waiting to confirm them; asked while an End is going, it makes
    the rest of it fast. Returns at once; `wait_ended` waits."""
    running = host()
    if running is None:
        if lost_run() is None:
            raise LabError(409, "no run is going")
        return _end_lost(fast)
    with _action_lock:
        already = fast and _action["name"] == "ending"
    if already:
        _end_key["key"] = "fast"
        ctrlutils.WriteCommand("end", "fast")
        return {"state": "ending", "plan": running["plan"]}
    _begin("ending")
    _end_key["key"] = "fast" if fast else None

    def go(pid: int) -> None:
        try:
            deadline, resend_at = time.monotonic() + END_WAIT_S, 0.0
            while time.monotonic() < deadline and is_host(pid):
                if time.monotonic() >= resend_at:
                    resend_at = time.monotonic() + END_RESEND_S
                    try:
                        ctrlutils.WriteCommand("end", _end_key["key"])
                    except (OSError, ValueError):
                        pass                             # tried again next round
                time.sleep(0.25)
        finally:
            _finish()

    threading.Thread(target=go, args=(running["pid"],), name="lab-end", daemon=True).start()
    return {"state": "ending", "plan": running["plan"]}


def _end_lost(fast: bool = False) -> dict:
    """Start a host that runs only the end of the run whose host was lost
    (`--end`), and show "ending" until it has gone. No `end` is sent to it: it
    stops by itself, and an `end` would cut short its wait for first readings."""
    lost = lost_run()
    _begin("ending")

    def go() -> None:
        try:
            launch(None, end_only=True, fast=fast)
            deadline = time.monotonic() + END_SETTLE_S + END_WAIT_S
            while time.monotonic() < deadline and host() is not None:
                time.sleep(0.25)
        finally:
            _finish()

    threading.Thread(target=go, name="lab-end-lost", daemon=True).start()
    return {"state": "ending", "plan": lost["plan"]}


def wait_ended(timeout: float = END_WAIT_S + END_SETTLE_S) -> dict | None:
    """Wait until this process's start or end is over (its thread has finished),
    within `timeout`; then how the last run ended, if a record is there."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and action() is not None:
        time.sleep(0.2)
    return last_ended()


def ctrl(command: str) -> dict:
    """`pause` or `resume` the running plan's steps."""
    if command not in ("pause", "resume"):
        raise LabError(400, f"unknown ctrl command {command!r}")
    if host() is None:
        raise LabError(409, "no run is going")
    try:
        ctrlutils.WriteCommand(command)
    except (OSError, ValueError) as e:
        raise LabError(503, f"could not write the ctrl file: {e}")
    return {"sent": command}


def send_command(line: str, *, take_s: float | None = None) -> dict:
    """Send `hvc platen 20`-style words to the rScript that owns the label
    (cast.send), and say what became of it.

    Refused unless a run is going and the instrument's block is live: a command
    to a chamber that is not connected, or whose rScript is not loaded, would sit
    unread, and the page would look as if it had worked."""
    words = line.split()
    if not words:
        raise LabError(400, "type a command, e.g. hvc vent open")
    label = words[0].lower()
    with _lock:
        try:
            request = cast.request(label, words[1:])
            wait = cast.reports_results(label)
        except GrammarError as e:
            raise LabError(400, str(e))
    running = host()
    if running is None:
        raise LabError(409, "no run is going, so nothing would apply it")
    blocks = read_blocks() or {}
    fresh = freshness(label, blocks.get(label) or {}, running, time.time())
    if not fresh["live"]:
        raise LabError(409, f"{label} is not live ({fresh['reason']}), so the command would not be taken")
    # Outside the lock: the wait (up to cast.REPLY_S) must not hold up the other pages.
    return cast.send(label, request, host=running, wait_result=wait,
                     take_s=CAST_TAKE_S if take_s is None else take_s)


def kill() -> dict:
    """Stop the host without its end: the deliberate act for a host that will
    not take End. Its lock stays, so the run is lost and `end` runs its end from
    a fresh host (D10). Returns {killed, plan, lost}."""
    running = host()
    if running is None:
        raise LabError(409, "no run is going")
    _kill_tree(running["pid"])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and is_host(running["pid"]):
        time.sleep(0.1)
    return {"killed": running["pid"], "plan": running["plan"], "lost": lost_run() is not None}


# --- the host's processes -------------------------------------------------------------

def host_processes() -> list[psutil.Process]:
    """Every process that is a sequence host (on Windows, the venv launcher and
    the interpreter it starts both count)."""
    found = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            if "formslab.host.sequence" in " ".join(proc.info["cmdline"] or ()):
                found.append(proc)
        except (psutil.Error, OSError):
            continue
    return found


def _kill_tree(pid: int) -> None:
    """The host, its children, and the venv launcher in front of it."""
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return
    victims = [proc] + proc.children(recursive=True)
    parent = proc.parent()
    if parent is not None and is_host(parent.pid):
        victims.append(parent)
    for v in victims:
        try:
            v.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(victims, timeout=5)
