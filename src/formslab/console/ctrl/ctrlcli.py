"""The ctrl tab: start, watch and stop the sequence host.

    run <plan>        a lab plan from plans/, or a path to a .forms plan
    run tvac          manual chamber operation (plans/tvac.forms) until `end`
    plans             list lab plans
    status, ps        is a host running
    pause, resume     hold / continue the running plan or mode
    end               stop it; rScripts' rShutdown runs before it exits

Every handler returns a CLIResult; nothing prints.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
from rich.text import Text

from formslab.config import output_dir
from formslab.console.ctrl.ctrlutils import WriteCommand, process_exists
from formslab.console.log.logcli import log_path
from formslab.console.sessions.base import CLIResult
from formslab.console.style import DIM, ERROR, HEADER, INFO, LABEL, SUCCESS, TEXT, WARNING
from formslab.sequence import discover as discover_plans, find_plan, is_lab_plan

# How long `end` waits for the host to stop on its own (running rShutdown)
# before it is killed.
END_GRACE_S = 15.0


def _get_pid_path():
    return output_dir() / "sequence.pid"


def _running_pid():
    try:
        pid = int(_get_pid_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if process_exists(pid) else None


def _launch_sequence(plan_path: str) -> CLIResult:
    """Start `python -m formslab.host.sequence <plan>`. Its output goes to the
    log tab's file; it inherits this working directory."""
    pid = _running_pid()
    if pid is not None:
        return CLIResult(f"✔ sequence host already running (pid {pid})")

    cmd = [sys.executable, "-m", "formslab.host.sequence", plan_path]
    proc = subprocess.Popen(
        cmd,
        stdout=log_path().open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _get_pid_path().write_text(str(proc.pid))
    return CLIResult(f"🟢 sequence host started (pid {proc.pid}, plan={Path(plan_path).stem})",
                     clear=False)


def run_sequence(args=None) -> CLIResult:
    if not args:
        return CLIResult("✗ run what? A plan name, e.g. `run tvac` (see `plans`)", clear=False)
    plan = find_plan(args[0])
    if plan is None or not is_lab_plan(plan):
        return CLIResult(f"✗ No plan '{args[0]}'. Use 'plans' to list them.", clear=False)
    return _launch_sequence(str(plan.resolve()))


def plans_command() -> CLIResult:
    from formslab.sequence import PlanError, load_plan

    result = Text()
    result.append("LAB PLANS\n", HEADER)
    result.append("═" * 60 + "\n", DIM)
    plans = discover_plans()
    if not plans:
        result.append("  No lab plans found in plans/\n", DIM)
    for path in plans:
        try:
            plan = load_plan(path)
        except PlanError as e:
            result.append(f"  ✗   {path.stem.ljust(22)}", ERROR)
            result.append(f"{e}\n", DIM)
            continue
        open_ended = any(s.verb == "hold" and s.params["seconds"] is None
                         for s in plan.sequence.segments)
        result.append("  ●   " if open_ended else "  ▶   ", WARNING if open_ended else SUCCESS)
        result.append(path.stem.ljust(22), INFO)
        result.append(("until end  " if open_ended else "           "), WARNING)
        result.append(", ".join(plan.rscripts) + "\n", DIM)
    result.append("\nUsage: ", DIM)
    result.append("run <plan>   (run tvac: manual operation until `end`)\n", INFO)
    return CLIResult(result, clear=True)


def status_panel() -> CLIResult:
    pid = _running_pid()
    if pid is not None:
        return CLIResult(f"● sequence host running (pid {pid})")
    _get_pid_path().unlink(missing_ok=True)
    return CLIResult("✗ sequence host not running.", clear=False)


def list_sequence() -> CLIResult:
    """Every running sequence host, found by its module name on a Python
    process's command line."""
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "python" not in (proc.info["name"] or "").lower():
                continue
            cmdline = " ".join(proc.info["cmdline"] or ())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "formslab.host.sequence" in cmdline:
            found.append(proc.info["pid"])
    if not found:
        return CLIResult("No sequence host processes found.", clear=False)
    return CLIResult("sequence host processes:\n" + "\n".join(f"PID: {p}" for p in sorted(found)))


def end_sequence(grace_s: float = END_GRACE_S) -> CLIResult:
    """Ask the host to stop through ctrl, so its rScripts' rShutdown runs (a
    plan's PSU outputs go off). Kill it only if it has not stopped in time: on
    Windows a signal is a hard kill that skips that cleanup."""
    pid = _running_pid()
    if pid is None:
        _get_pid_path().unlink(missing_ok=True)
        return CLIResult("✗ sequence host not running.", clear=False)
    WriteCommand("end")
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not process_exists(pid):
            _get_pid_path().unlink(missing_ok=True)
            return CLIResult(f"✖ sequence host (pid {pid}) stopped cleanly.")
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        return CLIResult(f"✗ Could not stop pid {pid}: {e}", clear=False)
    _get_pid_path().unlink(missing_ok=True)
    return CLIResult(Text(f"⚠ sequence host (pid {pid}) did not stop in {grace_s:g} s and "
                          "was killed; rShutdown did not run. Check the instruments.",
                          style=ERROR))


def help_panel() -> CLIResult:
    result = Text()
    result.append("═" * 60 + "\n", DIM)
    result.append("NAVIGATION\n", HEADER)
    for cmd, desc in (("--cast", "Hardware status and commands"), ("--psu", "PSU controls"),
                      ("--log", "Host output"), ("--ctrl", "Return here"), ("--exit", "Quit")):
        result.append(f"  {cmd:<16}", LABEL)
        result.append(desc + "\n", TEXT)

    result.append("\n" + "═" * 60 + "\n", DIM)
    result.append("RUNS\n", HEADER)
    for cmd, desc in (("plans", "List lab plans"),
                      ("run tvac", "Manual chamber operation until `end` (cast tab)"),
                      ("run <plan>", "Run a lab plan, e.g. run laco_pumpdown")):
        result.append(f"  {cmd:<16}", LABEL)
        result.append(desc + "\n", TEXT)

    result.append("\n" + "═" * 60 + "\n", DIM)
    result.append("PROCESS CONTROL\n", HEADER)
    for cmd, desc in (("status", "Is the sequence host running"),
                      ("ps", "List all sequence host processes"),
                      ("pause", "Pause the running plan or mode"),
                      ("resume", "Resume it"),
                      ("end", "Stop it; rShutdown leaves the hardware safe")):
        result.append(f"  {cmd:<16}", LABEL)
        result.append(desc + "\n", TEXT)
    return CLIResult(result)


COMMANDS = {
    "plans": plans_command,
    "missions": plans_command,   # the old name
    "modes": plans_command,      # tvac is a plan now
    "list": plans_command,
    "status": status_panel,
    "ps": list_sequence,
    "end": end_sequence,
    "help": help_panel,
}


def execute_command(args: list[str]) -> CLIResult:
    if not args:
        return help_panel()
    cmd = args[0].lstrip("-").lower()
    if cmd == "run":
        return run_sequence(args[1:])
    handler = COMMANDS.get(cmd)
    if handler:
        return handler()
    # pause, resume, reset: straight to the host through ctrl
    WriteCommand(cmd, args[1] if len(args) > 1 else None)
    return CLIResult(f"✔ dispatched '{cmd}'", clear=False)


if __name__ == "__main__":
    res = execute_command(sys.argv[1:])
    content = res.content
    print(content.render() if hasattr(content, "render") else content)
