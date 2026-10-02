"""The sequence host: the process that runs rScripts against the bench.

Started by the console's ctrl tab (`run <plan>`), or directly:

    python -m formslab.host.sequence tvac              # manual operation, until `end`
    python -m formslab.host.sequence plans/psu1_smtc08_first.forms

Every run is a lab plan (see `formslab.sequence`): the plan names its rScripts,
the host loads them on a `LabForms` handle and runs the plan's steps in one
loop paced in real time at `LOOP_HZ` -- poll ctrl (pause, resume, reset, end),
tick each rScript once, write a CSV row when one is due. A plan ends after its
last step; one that holds "until end" (plans/tvac.forms) runs until ctrl `end`.

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

import psutil

from formslab import rscripts
from formslab.config import run_dir
from formslab.console.cast.castutils import ResetJson
from formslab.console.ctrl.ctrlutils import ReadCommand, ResetCtrlState
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


def _acquire_lock(emit) -> bool:
    path = lock_path()
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = int(path.read_text(encoding="utf-8").split()[0])
            except (OSError, ValueError, IndexError):
                owner = -1
            if owner > 0 and owner != os.getpid() and psutil.pid_exists(owner):
                emit(f"Another sequence host is already running (pid={owner}).")
                return False
            path.unlink(missing_ok=True)       # stale: its process is gone
            continue
        os.write(fd, f"{os.getpid()}\n".encode())
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

def check_ctrl_commands(forms) -> None:
    """Apply ctrl requests: end, pause, resume, reset (clears pending CAST requests)."""
    global paused
    for label in _CTRL_LABELS:
        if ReadCommand(label) is None:
            continue
        forms.log(f"ctrl command received: {label}", component=COMPONENT)
        if label == "end":
            raise GracefulExit()
        if label == "pause":
            paused = True
        elif label == "resume":
            paused = False
        elif label == "reset":
            ResetCtrlState()
            ResetJson()


def poll(forms) -> None:
    """ctrl once, then block here while paused."""
    check_ctrl_commands(forms)
    while paused:
        time.sleep(0.1)
        check_ctrl_commands(forms)


def _on_signal(sig, _frame):  # pragma: no cover - signal path
    raise GracefulExit()


# --- the run ---------------------------------------------------------------------------

def _build(plan_path):
    """The plan, and a handle with its rScripts loaded and recording set. A
    plan whose rScripts do not all load does not start: its steps would only
    time out against a missing instrument owner."""
    plan = load_plan(plan_path)
    forms = rscripts.LabForms(name=plan.name)
    loaded = rscripts.load(forms, plan.rscripts)
    missing = [n for n in plan.rscripts if n not in loaded]
    if missing:
        raise PlanError(f"{plan.name}: rScripts did not load: {', '.join(missing)} "
                        "(the log above says why)")
    forms.record(value=plan.record_interval, unit=plan.record_unit)
    return forms, plan


def channel(plan_path, relay_func=None):
    """Run a lab plan until its last step, or until ctrl `end`."""
    global paused
    paused = False

    def emit(msg: str) -> None:
        (relay_func or print)(msg)

    if not _acquire_lock(emit):
        return
    try:
        ResetCtrlState()
        ResetJson()
        events_path().write_text("", encoding="utf-8")
        forms, plan = _build(plan_path)
    except BaseException:
        _release_lock()
        raise

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    try:
        forms.log(f"Plan {plan.name} running ({LOOP_HZ:g} Hz).", component=COMPONENT)
        LabSequenceRunner(forms, plan.sequence, sink=JsonlEventSink(events_path()),
                          poll=lambda: poll(forms), hz=LOOP_HZ).run()
        forms.log(f"Plan {plan.name} complete.", component=COMPONENT)
    except GracefulExit:
        forms.log("Run ended.", component=COMPONENT)
    except SequenceError as exc:
        forms.log(f"Plan stopped: {exc}", level="ERROR", component=COMPONENT)
        raise
    except Exception:
        forms.log(f"error: {traceback.format_exc()}", level="ERROR", component=COMPONENT)
        raise
    finally:
        # Hardware first: each rScript's rShutdown leaves its instrument safe.
        try:
            rscripts.shutdown(forms)
        except BaseException:
            pass
        forms.log("Host stopped.", component=COMPONENT)
        _release_lock()


def main(argv=None) -> int:
    ap = ArgumentParser(prog="python -m formslab.host.sequence",
                        description="Run a lab plan against the bench.")
    ap.add_argument("plan", help="a .forms lab plan: a path, or a name found in plans/ (e.g. tvac)")
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
