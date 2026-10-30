"""The ctrl tab: start, watch and stop the sequence host, over `formslab.lab`.

    run <plan>        a plan from plans/, or a path to a .plan file
    run tvac          manual chamber operation (plans/tvac.plan) until `end`
    plans             list lab plans
    status, ps        is a host running, and which plan
    pause, resume     hold / continue the running plan's steps
    end               stop it; the end script and the rScripts' rShutdown run before
                      it exits. With no host but a lost run's lock: that run's end,
                      from a fresh host
    end fast          a Fast End: the end's steps, not waiting to confirm each

Every handler returns a CLIResult; nothing prints. The run itself is
`formslab.lab`'s: the same functions the web GUI and `labcli <command>` use.
"""
from rich.text import Text

from formslab import lab
from formslab.console.ctrl.ctrlutils import WriteCommand
from formslab.console.sessions.base import CLIResult
from formslab.console.style import DIM, ERROR, HEADER, INFO, LABEL, SUCCESS, TEXT, WARNING
from formslab.host.sequence import read_lock
from formslab.lab import LabError
from formslab.sequence import discover as discover_plans


def running():
    """The lock of the live host, or None."""
    return lab.host()


def _launched(r: dict) -> CLIResult:
    if r["state"] == "running":
        return CLIResult(f"✔ sequence host already running (pid {r['pid']}, plan {r['plan']})")
    if r["state"] == "started":
        return CLIResult(f"🟢 sequence host started (pid {r['pid']}, plan {r['plan']})", clear=False)
    return CLIResult(f"🟢 sequence host starting (plan {r['plan']}); `status` will show it, "
                     "`--log` if it does not", clear=False)


def run_sequence(args=None) -> CLIResult:
    if not args:
        return CLIResult("✗ run what? A plan name, e.g. `run tvac` (see `plans`)", clear=False, ok=False)
    try:
        return _launched(lab.start(args[0]))
    except LabError as e:
        if e.code == 404:
            return CLIResult(f"✗ No plan '{args[0]}'. Use 'plans' to list them.", clear=False, ok=False)
        return CLIResult(Text(f"✗ {e}", style=ERROR), clear=False, ok=False)


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

        usages = {name: Grammar([(b.call, "", None)]).rows()[0][0] for name, b in usable.items()}
        width = max(30, *(len(u) + 2 for u in usages.values()))     # the longest call, then two spaces
        for name, b in usable.items():
            result.append("  ■   ", INFO)
            result.append(usages[name].ljust(width), INFO)
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
                         f"since {host['started']})\n  data in {host['output']}")
    stale = read_lock()
    if stale:
        return CLIResult(Text(f"⚠ sequence host not running -- the last run (plan "
                              f"{stale['plan']}, pid {stale['pid']}) ended without cleanup; "
                              "its instruments may be as it left them. `end` runs its end now.",
                              style=WARNING),
                         clear=False, ok=False)
    return CLIResult("✗ sequence host not running.", clear=False, ok=False)


def list_sequence() -> CLIResult:
    """Every running host. On Windows each shows as the venv launcher and the
    interpreter it starts; they are one run, listed once."""
    procs = lab.host_processes()
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


def end_sequence(fast: bool = False) -> CLIResult:
    """Ask the host to stop through ctrl, so the end script and its rScripts'
    rShutdown run, and wait for it. The host is never killed: one that does not
    take the request is said to be still running. `fast`: a Fast End; also to a
    host already ending. With a lost run, its end runs from a fresh host."""
    try:
        r = lab.end(fast)
    except LabError:
        return status_panel()
    before = lab.last_ended()
    ended = lab.wait_ended()
    if running() is not None:
        return CLIResult(Text(f"⚠ {r['plan']} has not stopped; its host is still running. "
                              "`status` shows it.", style=WARNING), ok=False)
    if ended and ended != before:
        lines = [f"✖ {ended['plan']} {ended['how']}."]
        lines += [ended["chamber"]] if ended.get("chamber") else []
        lines += [f"  not done: {w}" for w in ended.get("warnings", [])]
        return CLIResult(Text("\n".join(lines), style=WARNING if ended.get("warnings") else SUCCESS))
    return CLIResult(f"✖ {r['plan']} ended.")


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
                      ("run <plan>", "Run a lab plan, e.g. run ambient_to_rest")):
        result.append(f"  {cmd:<16}", LABEL)
        result.append(desc + "\n", TEXT)

    result.append("\n" + "═" * 60 + "\n", DIM)
    result.append("PROCESS CONTROL\n", HEADER)
    for cmd, desc in (("status", "Is the sequence host running"),
                      ("ps", "List all sequence host processes"),
                      ("pause", "Pause the running plan"),
                      ("resume", "Resume it"),
                      ("end", "Stop it; the end script and rShutdown leave the hardware safe"),
                      ("end fast", "Fast End: the same steps, not waiting to confirm each")):
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
    if cmd == "end" and [a.lower() for a in args[1:]] == ["fast"]:
        return end_sequence(fast=True)
    handler = COMMANDS.get(cmd)
    if handler:
        return handler()
    if cmd in ("pause", "resume"):
        try:
            lab.ctrl(cmd)
        except LabError as e:
            return CLIResult(Text(f"✗ {e}", style=ERROR), ok=False)
        return CLIResult(f"✔ dispatched '{cmd}'", clear=False)
    if cmd == "reset":                                # straight to the host through ctrl
        WriteCommand(cmd, args[1] if len(args) > 1 else None)
        return CLIResult(f"✔ dispatched '{cmd}'", clear=False)
    return CLIResult(Text(f"✗ unknown command {cmd!r}; `help` lists them", style=ERROR), ok=False)
