"""`labcli <command>`: one command, then exit -- for SSH sessions and scripts.

    labcli                          the interactive console
    labcli status                   is a run going (exit 1 if not)
    labcli status <label>           one instrument's CAST block (hvc, tc, psu1...)
    labcli plans                    the plans and the rScripts each loads
    labcli check <plan>             read a plan without running it
    labcli run <plan>               start a plan; returns once its host is up
    labcli end | pause | resume     the running plan
    labcli end fast                 a Fast End: the end's steps, not waiting to confirm each
    labcli ps                       every sequence host process
    labcli log [N]                  the host's last N log lines (default 40)
    labcli cast <label> <words>     a command to an instrument, e.g. cast hvc platen 20;
                                    waits until the rScript that owns it takes it
    labcli cast <label> <words> ?   what can come next ('?' quoted in bash/zsh)
    labcli hardware                 what is plugged in, beside the hardware map
    labcli gui [--port N] [--listen]  the web GUI on this machine (http://localhost:8080/)
    labcli gui --set-login          set the GUI's user name and password

Exit status: 0 done, 1 refused or failed, 2 usage. The run state is per
machine, so these work from any folder or SSH session.
"""
from __future__ import annotations

import json
import sys

from rich.console import Console

USAGE = 2


def _console() -> Console:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    return Console(highlight=False, soft_wrap=True)


def main(argv: list[str]) -> int:
    out = _console()
    if not argv or argv[0].lower() in ("help", "-h", "--help"):
        out.print(__doc__.strip(), markup=False)
        return 0
    verb, args = argv[0].lower(), argv[1:]

    if verb == "gui":
        from formslab.gui.server import main as gui_main
        return gui_main(args)
    if verb == "hardware":
        return _hardware(out)

    from formslab.console.cast import castcli
    from formslab.console.ctrl import ctrlcli
    from formslab.state import ensure_runtime_files

    ensure_runtime_files()

    def show(result) -> int:
        out.print(result.content)
        return 0 if result.ok else 1

    if verb == "check":
        from formslab.sequence.__main__ import main as check
        return check(args)
    if verb == "status":
        return show(castcli.status_panel(args[0]) if args else ctrlcli.status_panel())
    if verb == "plans":
        return show(ctrlcli.plans_command())
    if verb == "ps":
        return show(ctrlcli.list_sequence())
    if verb == "run":
        from formslab.sequence import find_plan
        if not args or find_plan(args[0]) is None:
            out.print(f"✗ run what? {'no plan ' + repr(args[0]) if args else 'a plan name'} "
                      "(`labcli plans` lists them)", markup=False)
            return USAGE
        return show(ctrlcli.run_sequence(args))
    if verb in ("end", "pause", "resume"):
        args = [a.lower() for a in args]
        if args not in ([], ["fast"]) or (args and verb != "end"):
            out.print("✗ end, end fast, pause or resume", markup=False)
            return USAGE
        if ctrlcli.running() is None and not (verb == "end" and ctrlcli.lost_run()):
            out.print("✗ no run is going.", markup=False)
            return 0 if verb == "end" else 1           # ending what has ended is done
        if verb == "end":
            return show(ctrlcli.end_sequence(fast=args == ["fast"]))
        return show(ctrlcli.execute_command([verb]))
    if verb == "log":
        return _log(out, args)
    if verb == "cast":
        return _cast(out, args)
    out.print(f"✗ unknown command {verb!r}; `labcli help` lists them", markup=False)
    return USAGE


def _log(out, args) -> int:
    from formslab.console.log.logcli import log_path

    try:
        n = int(args[0]) if args else 40
        lines = log_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except ValueError:
        out.print("✗ log [N]: N is a number of lines", markup=False)
        return USAGE
    except OSError:
        out.print("✗ no host log yet (it starts with the first `run`)", markup=False)
        return 1
    out.print("\n".join(lines[-n:]), markup=False)
    return 0


def _hardware(out) -> int:
    """The hardware map's entries, each found or not, then what is plugged in
    that the map does not name. Opens nothing, so it is safe during a run."""
    from rich.table import Table

    from formslab import hardware

    s = hardware.survey()
    mapped = Table(title=f"The hardware map, {s['map']}", title_justify="left", box=None)
    for col in ("name", "", "where", "found by", "the map says"):
        mapped.add_column(col)
    for e in s["entries"]:
        mark = {"found": "[green]found[/]", "missing": "[yellow]missing[/]"}.get(e["status"], e["status"])
        mapped.add_row(e["label"] + ("" if e["enabled"] else " (off)"), mark, e["address"] or "",
                       e["by"] or "", e["wants"] or "nothing to find it by")
    out.print(mapped)
    other = [d for d in s["plugged"] if d["label"] is None]
    if other:
        loose = Table(title="Plugged in, not in the map", title_justify="left", box=None)
        for col in ("where", "what", "in the map as"):
            loose.add_column(col)
        for d in other:
            loose.add_row(d["address"], d["description"], d["entry"])
        out.print()
        out.print(loose)
    for err in s["errors"]:
        out.print(f"✗ could not list {err}", markup=False)
    return 0


def _cast(out, args) -> int:
    """Send one command and wait until the owning rScript takes it, and, for an
    owner that reports (rLACO), until it is done or refused. Refused when no run
    is going: the host clears CAST when it starts, so a request written
    beforehand would be silently lost."""
    from formslab.console.cast import castcli
    from formslab.console.cast.castutils import RESULT_S
    from formslab.console.ctrl import ctrlcli
    from formslab.rscripts import cast

    if not args:
        out.print("✗ cast <label> <words>, e.g. `labcli cast hvc platen 20`", markup=False)
        return USAGE
    if args[-1] == "?":
        result = castcli.execute_command(args)
        out.print(result.content)
        return 0 if result.ok else 1
    try:
        request = cast.request(args[0], args[1:])
    except cast.GrammarError as e:
        out.print(f"✗ {e}", markup=False)
        return 1
    sent = cast.send(args[0], request, host=ctrlcli.running(), result_s=RESULT_S)
    hint = " (`labcli run tvac` starts manual operation)" if sent["state"] == "refused" else ""
    out.print(f"{'✔' if sent['ok'] else '✗'} {sent['label']} ← {json.dumps(request)}: "
              f"{sent['text']}{hint}", markup=False)
    return 0 if sent["ok"] else 1
