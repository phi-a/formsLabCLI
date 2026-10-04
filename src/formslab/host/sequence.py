"""The sequence host: the process that runs rScripts against the bench.

Started by the console's ctrl tab or `labcli run <plan>`, or directly:

    python -m formslab.host.sequence tvac              # manual operation, until `end`
    python -m formslab.host.sequence plans/psu1_smtc08_first.plan

Every run is a lab plan (see `formslab.sequence`): the plan names its rScripts,
the host loads them on a `Run` handle and runs the plan's steps in one
loop paced in real time at `LOOP_HZ` -- poll ctrl (pause, resume, reset, end),
run the plan's current step, write a CSV row when one is due. Each rScript runs
on its own thread at the same rate (rscripts.workers), so a slow instrument
does not hold up the others or the plan. A plan ends after its last step; one
that holds "until end" (plans/tvac.plan) runs until ctrl `end`.

The lock file (`<config>/.run/sequence.lock`, one per machine) names the
running host: its process id, plan, start time and the folder its CSV goes to.
The console's ctrl tab reads it to find, show and stop the run, however and
from wherever it was started.

However a run ends, each loaded rScript's ``rShutdown`` runs before the process
exits. Only one host runs at a time: two would fight over the same instruments.
"""
from __future__ import annotations

import os
import signal
import sys
import time
import traceback
from argparse import ArgumentParser
from datetime import datetime, timezone
from pathlib import Path

import psutil

from formslab import rscripts
from formslab.config import output_dir, run_dir
from formslab.console.cast.castutils import ResetJson
from formslab.console.ctrl.ctrlutils import ReadCommands, ResetCtrlState
from formslab.rscripts.workers import Workers
from formslab.sequence import (
    JsonlEventSink, LabSequenceRunner, PlanError, SequenceError, find_plan, load_plan,
)

# Loop rate. Scripts gate their own hardware cadence; this bounds how quickly a
# ctrl request or a plan's next segment is seen.
LOOP_HZ = 10.0
COMPONENT = "sequence"

_CTRL_LABELS = ("end", "reset", "resume", "pause")
paused = False


class GracefulExit(SystemExit):
    """Raised by ctrl `end` or a signal; ends the run through the normal cleanup."""


# --- one host at a time ---------------------------------------------------------

def lock_path():
    return run_dir() / "sequence.lock"


def events_path():
    return run_dir() / "sequence.events.jsonl"


def is_host(pid: int) -> bool:
    """True when `pid` is a live sequence host (not a reused pid)."""
    try:
        cmdline = " ".join(psutil.Process(pid).cmdline())
    except (psutil.Error, OSError, ValueError):
        return False
    return "formslab.host.sequence" in cmdline


def read_lock() -> dict | None:
    """The lock's {pid, plan, started, output} -- the last host that took it,
    alive or not (see `is_host`). None when there is no lock."""
    try:
        lines = lock_path().read_text(encoding="utf-8").splitlines()
        pid = int(lines[0])
    except (OSError, ValueError, IndexError):
        return None
    return {"pid": pid, "plan": lines[1] if len(lines) > 1 else "?",
            "started": lines[2] if len(lines) > 2 else "?",
            "output": lines[3] if len(lines) > 3 else "?"}


def _acquire_lock(emit, plan: str) -> bool:
    path = lock_path()
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = read_lock()
            if owner and owner["pid"] != os.getpid() and is_host(owner["pid"]):
                emit(f"Another sequence host is already running (pid={owner['pid']}, "
                     f"plan {owner['plan']}).")
                return False
            if owner:
                emit(f"The previous run (pid {owner['pid']}, plan {owner['plan']}) ended "
                     "without cleanup; its instruments may be as it left them.")
            path.unlink(missing_ok=True)       # stale: its process is gone
            continue
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        os.write(fd, f"{os.getpid()}\n{plan}\n{started}\n{output_dir()}\n".encode())
        os.close(fd)
        return True
    emit("Could not take the sequence lock.")
    return False


def _release_lock() -> None:
    path = lock_path()
    try:
        if int(path.read_text(encoding="utf-8").split()[0]) == os.getpid():
            path.unlink()
    except (OSError, ValueError, IndexError):
        pass


# --- control ---------------------------------------------------------------------

def check_ctrl_commands(run) -> None:
    """Apply ctrl requests: end, pause, resume, reset (clears pending CAST requests)."""
    global paused
    pending = ReadCommands(_CTRL_LABELS)            # one read, one write
    for label in _CTRL_LABELS:
        if label not in pending:
            continue
        run.log(f"ctrl command received: {label}", component=COMPONENT)
        if label == "end":
            raise GracefulExit()
        if label == "pause":
            paused = True
        elif label == "resume":
            paused = False
        elif label == "reset":
            ResetCtrlState()
            ResetJson()


def poll(run) -> None:
    """ctrl once, then block here while paused."""
    check_ctrl_commands(run)
    while paused:
        time.sleep(0.1)
        check_ctrl_commands(run)


def _on_signal(sig, _frame):  # pragma: no cover - signal path
    raise GracefulExit()


# --- the run ---------------------------------------------------------------------------

def _build(plan_path):
    """The plan, and a handle with its rScripts loaded and recording set. A
    plan whose rScripts do not all load does not start: its steps would only
    time out against a missing instrument owner."""
    plan = load_plan(plan_path)
    run = rscripts.Run(name=plan.name)
    loaded = rscripts.load(run, plan.rscripts)
    missing = [n for n in plan.rscripts if n not in loaded]
    if missing:
        raise PlanError(f"{plan.name}: rScripts did not load: {', '.join(missing)} "
                        "(the log above says why)")
    run.record(value=plan.record_interval, unit=plan.record_unit)
    return run, plan


def channel(plan_path, relay_func=None):
    """Run a lab plan until its last step, or until ctrl `end`."""
    global paused
    paused = False

    def emit(msg: str) -> None:
        (relay_func or print)(msg)

    if not _acquire_lock(emit, Path(plan_path).stem):
        return
    try:
        ResetCtrlState()
        ResetJson()
        events_path().write_text("", encoding="utf-8")
        run, plan = _build(plan_path)
    except BaseException:
        _release_lock()
        raise

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGBREAK"):              # Windows: Ctrl+Break / CTRL_BREAK_EVENT
        signal.signal(signal.SIGBREAK, _on_signal)
    workers = Workers(run, hz=LOOP_HZ)
    try:
        run.log(f"Plan {plan.name} running ({LOOP_HZ:g} Hz, pid {os.getpid()}).",
                  component=COMPONENT)
        workers.start()
        LabSequenceRunner(run, plan.sequence, sink=JsonlEventSink(events_path()),
                          poll=lambda: poll(run), tick=lambda _run: None,
                          hz=LOOP_HZ).execute()
        run.log(f"Plan {plan.name} complete.", component=COMPONENT)
    except GracefulExit:
        run.log("Run ended.", component=COMPONENT)
    except SequenceError as exc:
        run.log(f"Plan stopped: {exc}", level="ERROR", component=COMPONENT)
        raise
    except Exception:
        run.log(f"error: {traceback.format_exc()}", level="ERROR", component=COMPONENT)
        raise
    finally:
        # Hardware first: stop the script threads, then each rScript's
        # rShutdown leaves its instrument safe.
        stuck = workers.stop()
        if stuck:
            run.log(f"rScripts still busy after 15 s: {', '.join(stuck)}; running "
                      "their rShutdown anyway", level="WARNING", component=COMPONENT)
        try:
            rscripts.shutdown(run)
        except BaseException:
            pass
        run.log("Host stopped.", component=COMPONENT)
        _release_lock()


def main(argv=None) -> int:
    ap = ArgumentParser(prog="python -m formslab.host.sequence",
                        description="Run a lab plan against the bench.")
    ap.add_argument("plan", help="a .plan file: a path, or a name found in plans/ (e.g. tvac)")
    args = ap.parse_args(argv)

    plan_path = find_plan(args.plan)
    if plan_path is None:
        print(f"Plan not found: {args.plan}", flush=True)
        return 2
    try:
        channel(plan_path)
    except GracefulExit:
        pass
    except SequenceError:
        return 1
    except PlanError as exc:
        print(f"Plan not started: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
