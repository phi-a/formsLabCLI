"""The ctrl tab: start, watch and stop the sequence host.

    run <plan>        a plan from plans/, or a path to a .plan file
    run tvac          manual chamber operation (plans/tvac.plan) until `end`
    plans             list lab plans
    status, ps        is a host running, and which plan
    pause, resume     hold / continue the running plan's steps
    end               stop it; rScripts' rShutdown runs before it exits

The running host is found through its lock file, which the host itself writes
(pid, plan, start time) -- so it is found however it was started, and on
Windows the pid is the real interpreter, not the venv launcher in front of it.

Every handler returns a CLIResult; nothing prints.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
from rich.text import Text

from formslab.console.ctrl.ctrlutils import LoadCommands, WriteCommand
from formslab.console.log.logcli import log_path
from formslab.console.sessions.base import CLIResult
from formslab.console.style import DIM, ERROR, HEADER, INFO, LABEL, SUCCESS, TEXT, WARNING
from formslab.host.sequence import is_host, read_lock
from formslab.sequence import discover as discover_plans, find_plan

# `end`: how long the host gets to take the request, then to finish cleanup.
END_TAKE_S = 15.0
END_FINISH_S = 30.0
# A host that is just starting clears the ctrl file after it takes its lock, which
# can erase an `end` sent in that window; so `end` is sent again while it waits.
END_RESEND_S = 3.0


def running():
    """The lock of the live host, or None."""
    lock = read_lock()
    return lock if lock and is_host(lock["pid"]) else None


def _launch_sequence(plan_path: str) -> CLIResult:
    """Start `python -m formslab.host.sequence <plan>`, output to the log tab's
    file, in this working directory. On Windows it is detached from the
    console, so closing the console window does not kill a run."""
    host = running()
    if host:
        return CLIResult(f"✔ sequence host already running (pid {host['pid']}, "
                         f"plan {host['plan']})")
    cmd = [sys.executable, "-m", "formslab.host.sequence", plan_path]
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    else:
        flags = {"start_new_session": True}
    # The host log is UTF-8 whatever the console's codepage: on Windows a redirected
    # stdout defaults to cp1252, and a log line with a character outside it ("→", "❌")
    # would otherwise raise inside the host.
    log = log_path().open("w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
                            stdin=subprocess.DEVNULL, **flags)
    log.close()                                       # the host has its own copy
    # Collect the host when it exits, so a long-lived caller (the web GUI) does
    # not leave a zombie per run on Linux.
    threading.Thread(target=proc.wait, daemon=True).start()
    deadline = time.monotonic() + 5.0                 # the host writes its lock at start
    while time.monotonic() < deadline:
        host = running()
        if host:
            return CLIResult(f"🟢 sequence host started (pid {host['pid']}, plan {host['plan']})",
                             clear=False)
        time.sleep(0.2)
    return CLIResult(f"🟢 sequence host starting (plan {Path(plan_path).stem}); "
                     "`status` will show it, `--log` if it does not", clear=False)


def run_sequence(args=None) -> CLIResult:
    if not args:
        return CLIResult("✗ run what? A plan name, e.g. `run tvac` (see `plans`)", clear=False, ok=False)
    plan = find_plan(args[0])
    if plan is None:
        return CLIResult(f"✗ No plan '{args[0]}'. Use 'plans' to list them.", clear=False, ok=False)
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
        open_ended = plan.sequence.open_ended
        result.append("  ●   " if open_ended else "  ▶   ", WARNING if open_ended else SUCCESS)
        result.append(path.stem.ljust(22), INFO)
        result.append(("until end  " if open_ended else "           "), WARNING)
        result.append(", ".join(plan.rscripts) + "\n", DIM)
    from formslab.rscripts import cast
    from formslab.sequence import block as blocks_

    usable, broken = blocks_.available(cast.owners()[0])
    if usable or broken:
        result.append("\nBLOCKS (a plan calls them by name)\n", HEADER)
        from formslab.rscripts.grammar import Grammar

        for b in usable.values():
            usage = Grammar([(b.pattern, "", None)]).rows()[0][0]
            result.append("  ■   ", INFO)
            result.append(usage.ljust(30), INFO)
            result.append(b.summary + "\n", DIM)
        for name, why in broken.items():
            result.append(f"  ✗   {name.ljust(22)}", ERROR)
            result.append(f"{why}\n", DIM)
    result.append("\nUsage: ", DIM)
    result.append("run <plan>   (run tvac: manual operation until `end`)\n", INFO)
    return CLIResult(result, clear=True)


def status_panel() -> CLIResult:
    host = running()
    if host:
        return CLIResult(f"● sequence host running: plan {host['plan']} (pid {host['pid']}, "
                         f"since {host['started']})\n  CSV in {host['output']}")
    stale = read_lock()
    if stale:
        return CLIResult(Text(f"⚠ sequence host not running -- the last run (plan "
                              f"{stale['plan']}, pid {stale['pid']}) ended without cleanup; "
                              "its instruments may be as it left them.", style=WARNING),
                         clear=False, ok=False)
    return CLIResult("✗ sequence host not running.", clear=False, ok=False)


def _host_processes() -> list[psutil.Process]:
    found = []
    for proc in psutil.process_iter(["pid", "ppid", "cmdline"]):
        try:
            if "formslab.host.sequence" in " ".join(proc.info["cmdline"] or ()):
                found.append(proc)
        except (psutil.Error, OSError):
            continue
    return found


def list_sequence() -> CLIResult:
    """Every running host. On Windows each shows as the venv launcher and the
    interpreter it starts; they are one run, listed once."""
    procs = _host_processes()
    pids = {p.pid for p in procs}
    runs = [p for p in procs if p.info["ppid"] not in pids]       # the top of each tree
    if not runs:
        return CLIResult("No sequence host processes found.", clear=False)
    lock = read_lock()
    lines = []
    for top in runs:
        tree = [top.pid] + [c.pid for c in procs if c.info["ppid"] == top.pid]
        owner = lock and lock["pid"] in tree
        lines.append(f"PID {tree[-1]}" + (f" (launcher {tree[0]})" if len(tree) > 1 else "")
                     + (f"  plan {lock['plan']}, since {lock['started']}" if owner else
                        "  (not the lock owner)"))
    return CLIResult("sequence hosts:\n" + "\n".join(lines))


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


def _end_pending() -> bool:
    """`end` is written and not yet taken. An unreadable table counts as not
    taken: only the host's own read-and-clear marks it taken."""
    cmds = LoadCommands()
    return cmds is None or cmds.get("end", {}).get("processed") is False


def end_sequence(take_s: float = END_TAKE_S, finish_s: float = END_FINISH_S) -> CLIResult:
    """Ask the host to stop through ctrl, so its rScripts' rShutdown runs (a
    plan's PSU outputs go off, pumping it started stops). A host that takes
    the request is left to finish its cleanup; only one that never takes it
    (hung) is killed -- and then rShutdown has not run."""
    host = running()
    if host is None:
        return status_panel()
    pid = host["pid"]
    try:
        WriteCommand("end")
    except (OSError, ValueError) as e:
        return CLIResult(Text(f"✗ could not send `end`: {e}", style=ERROR), ok=False)
    resend_at = time.monotonic() + END_RESEND_S

    def resend_if_due() -> None:
        # The start-up reset erases an `end` and marks it "processed", which looks
        # exactly like the host having taken it -- so keep sending while it lives.
        nonlocal resend_at
        if time.monotonic() >= resend_at:
            resend_at += END_RESEND_S
            try:
                WriteCommand("end")
            except (OSError, ValueError):
                pass                                  # the next pass tries again

    deadline = time.monotonic() + take_s
    while time.monotonic() < deadline and _end_pending():
        if not is_host(pid):
            break
        resend_if_due()
        time.sleep(0.2)
    if is_host(pid) and _end_pending():
        _kill_tree(pid)
        return CLIResult(Text(f"⚠ sequence host (pid {pid}) did not take `end` in {take_s:g} s "
                              "and was killed; rShutdown did not run. Check the instruments.",
                              style=ERROR), ok=False)
    deadline = time.monotonic() + finish_s
    while time.monotonic() < deadline and is_host(pid):
        resend_if_due()
        time.sleep(0.2)
    if is_host(pid):
        return CLIResult(f"… host (pid {pid}) took `end` and is still cleaning up; "
                         "`status` shows when it has stopped.", clear=False)
    return CLIResult(f"✖ sequence host (pid {pid}, plan {host['plan']}) stopped cleanly.")


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
                      ("pause", "Pause the running plan"),
                      ("resume", "Resume it"),
                      ("end", "Stop it; rShutdown leaves the hardware safe")):
        result.append(f"  {cmd:<16}", LABEL)
        result.append(desc + "\n", TEXT)
    return CLIResult(result)


COMMANDS = {
    "plans": plans_command,
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
    if cmd in ("pause", "resume", "reset"):           # straight to the host through ctrl
        WriteCommand(cmd, args[1] if len(args) > 1 else None)
        return CLIResult(f"✔ dispatched '{cmd}'", clear=False)
    return CLIResult(Text(f"✗ unknown command {cmd!r}; `help` lists them", style=ERROR), ok=False)


if __name__ == "__main__":
    res = execute_command(sys.argv[1:])
    content = res.content
    print(content.render() if hasattr(content, "render") else content)
