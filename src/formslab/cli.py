"""`labcli <command>`: one command, then an exit status, for SSH and scripts.
A bare `labcli` opens the console.

Every command goes through `formslab.lab`, the same functions the web GUI
uses, so a refusal reads the same in both. Exit status: 0 done, 1 refused or
failed, 2 usage. The run state is per machine, so these work from any folder
or SSH session.
"""
from __future__ import annotations

import getpass
import json
import sys
from datetime import datetime

from rich.console import Console

USAGE = 2
ORDER = ("hvc", "psu1", "psu2", "tc", "cryo", "slta", "orbit")

# (usage, summary): what `labcli help` prints, and the one list of what exists.
# A summary follows memory/design/writing.md: one plain phrase, at most 50 characters.
VERBS = (
    ("labcli", "Watch the bench and command it"),
    ("labcli status", "What is going now, once"),
    ("labcli status label", "One instrument's last readings"),
    ("labcli plans", "The plans, blocks and orbits found"),
    ("labcli check name", "Read a plan, block or orbit without running it"),
    ("labcli run plan", "Start a plan"),
    ("labcli end", "End the run"),
    ("labcli end fast", "End without waiting to confirm each step"),
    ("labcli pause", "Hold the plan's steps"),
    ("labcli resume", "Go on with the plan's steps"),
    ("labcli kill", "Stop the host without its end"),
    ("labcli log lines", "The host's last lines"),
    ("labcli cast label words", "A command to an instrument"),
    ("labcli supplies off", "Every supply output off"),
    ("labcli power", "The PowerSwitch and what each outlet feeds"),
    ("labcli power outlet on", "An outlet on, off or cycled"),
    ("labcli power setup", "Give this computer the PowerSwitch's address"),
    ("labcli hardware", "What is plugged in, beside the map"),
    ("labcli gui", "The web GUI on this machine"),
)

NOTES = (
    "A plan is named, or given as a path; `labcli plans` lists the names.",
    "`labcli cast hvc platen 20` waits until the rScript that owns hvc takes it. "
    "`labcli cast hvc platen ?` says what can come next, with the ? quoted in bash and zsh.",
    "When something must go off: end; end fast; kill, then end again; supplies off; "
    "power N cycle, then supplies off again. The supplies and the switch are refused while a run is going.",
    "kill, supplies off, power N off and power N cycle ask first; add the word yes to do them from a script.",
    "`labcli gui` takes --port N, --listen, --set-login and --no-monitor.",
    "Exit status: 0 done, 1 refused or failed, 2 usage.",
)


def _console() -> Console:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    return Console(highlight=False, soft_wrap=True)


def main(argv: list[str], out: Console | None = None) -> int:
    out = out or _console()
    if not argv or argv[0].lower() in ("help", "-h", "--help"):
        return _help(out)
    verb, args = argv[0].lower(), argv[1:]

    if verb == "gui":
        from formslab.gui.server import main as gui_main
        return gui_main(args)
    if verb == "hardware":
        return _hardware(out)

    from formslab.state import ensure_runtime_files
    ensure_runtime_files()

    if verb == "check":
        return _check(out, args)
    if verb == "status":
        return _status(out, args[0] if args else None)
    if verb == "plans":
        return _plans(out)
    if verb == "run":
        return _run(out, args)
    if verb in ("end", "pause", "resume"):
        return _ctrl(out, verb, args)
    if verb == "kill":
        return _kill(out, args)
    if verb == "log":
        return _log(out, args)
    if verb == "cast":
        return _cast(out, args)
    if verb == "supplies":
        return _supplies(out, args)
    if verb == "power":
        return _power(out, args)
    out.print(f"✗ unknown command {verb!r}; `labcli help` lists them", markup=False)
    return USAGE


def question(words: list[str]) -> str | None:
    """What is asked before a command that cannot be taken back, or None. The
    watch screen asks the same question on its own line."""
    w = [x.lower() for x in words]
    if w[:1] == ["kill"]:
        return "Stop the host without its end?"
    if w[:2] == ["supplies", "off"]:
        return "Every supply output off?"
    if w[:1] == ["power"] and len(w) >= 3 and w[2] in ("off", "cycle"):
        return f"Outlet {w[1]} {'off' if w[2] == 'off' else 'off, then on again'}?"
    return None


def _confirm(out, words: list[str]) -> int | None:
    """None to go on: the word yes is among `words`, or a y was typed at a
    terminal. Else the exit status to return: 1 for a no, 2 with no terminal."""
    q = question(words)
    if q is None or "yes" in (w.lower() for w in words):
        return None
    try:
        at_terminal = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        at_terminal = False
    if not at_terminal:
        out.print(f"✗ {q} Add the word yes to do it without a terminal: labcli {' '.join(words)} yes",
                  markup=False)
        return USAGE
    try:
        answer = out.input(f"{q} y/n ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return None
    out.print("Not done.", markup=False)
    return 1


def _help(out) -> int:
    width = max(len(u) for u, _ in VERBS) + 2
    for usage, summary in VERBS:
        out.print(f"  {usage.ljust(width)}{summary}", markup=False)
    out.print()
    for note in NOTES:
        out.print(note, markup=False)
    return 0


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "?"


# --- what is going ------------------------------------------------------------------

def _status(out, label: str | None) -> int:
    from formslab import lab

    s = lab.status(log_lines=0)
    if label:
        label = label.lower()
        block = s["blocks"].get(label)
        if block is None:
            out.print(f"✗ no instrument {label!r}; one of {', '.join(sorted(s['blocks']))}", markup=False)
            return 1
        _card(out, label, block)
        return 0
    _run_lines(out, s)
    for name in sorted(s["blocks"], key=lambda n: (ORDER.index(n) if n in ORDER else 99, n)):
        _card(out, name, s["blocks"][name])
    return 0 if s["host"] else 1


def _run_lines(out, s: dict) -> None:
    host, run, lost = s["host"], s["run"] or {}, s["lost"]
    step = run.get("step")
    if host:
        line = f"● {host['plan']} running since {_clock(host['started'])}, pid {host['pid']}"
        if run.get("ending"):
            line += ", ending"
        elif run.get("paused"):
            line += f", paused since {_clock(run.get('paused_since'))}"
        out.print(line, style="bold green", markup=False)
        if step:
            out.print(f"  step {step['index']} of {step['total']}: {step['text']}", markup=False)
        out.print(f"  data in {host['output']}", markup=False)
    elif lost:
        out.print(f"⚠ {lost['plan']} stopped without its end: its host (pid {lost['pid']}) is gone, "
                  "and its instruments may be as it left them. `labcli end` runs its end now.",
                  style="yellow", markup=False)
        if step:
            out.print(f"  it was at step {step['index']} of {step['total']}: {step['text']}", markup=False)
    else:
        last = s["last_run"]
        out.print("No run is going." + (f" Last run: {last['plan']}." if last else ""), markup=False)
        if s["ended"]:
            out.print(_ended_text(s["ended"]), markup=False)
    if not host:
        out.print("The chamber is " + ("read between runs." if s["monitor"] else
                                       "not being read; formsLAB reads it while it is open."),
                  style="dim", markup=False)


def _ended_text(e: dict) -> str:
    how = {"ended": "ended", "lost": "was ended after its host was lost", "complete": "completed",
           "failed": "stopped on a failed step", "error": "stopped on an error"}.get(e.get("how"), e.get("how"))
    if e.get("fast"):
        how += " with Fast End"
    lines = [f"{e.get('plan')} {how} at {_clock(e.get('when'))}."]
    if e.get("chamber"):
        lines.append(f"  {e['chamber']}")
    lines += [f"  not done: {w}" for w in e.get("warnings", [])]
    return "\n".join(lines)


def _card(out, label: str, block: dict) -> None:
    badge = ("live" if block["live"] else "not live") + f" · {_age(block['age_s'])}"
    head = f"{label:<5} {badge}" + ("" if block["live"] else f"  {block['reason']}")
    out.print(head, style="bold" if block["live"] else "dim", markup=False)
    if block.get("pending"):
        out.print("      a command is waiting to be taken", style="yellow", markup=False)
    for g in block["groups"]:
        rows = " · ".join(f"{r['name']} {r.get('text', _value(r['value']))}" for r in g["rows"])
        title = f"{g['title']}: " if g.get("title") else ""
        out.print(f"      {title}{rows}", markup=False)


def _age(seconds: float) -> str:
    if seconds > 10 * 365 * 86400:
        return "never"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def _value(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _clock(iso) -> str:
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime("%H:%M")
    except (TypeError, ValueError):
        return str(iso)


# --- plans ----------------------------------------------------------------------------

def _plans(out) -> int:
    from formslab import lab

    items = lab.list_plans()
    plans = [p for p in items if p["kind"] == "plan"]
    out.print("plans", style="bold", markup=False)
    for p in plans:
        if "error" in p:
            out.print(f"  ✗ {p['name']:<20} {p['error']}", style="red", markup=False)
            continue
        note = ("run by End, not started" if p.get("end") else "until end" if p["open_ended"] else "")
        mark = "●" if p["open_ended"] else "▶"
        yours = "  (yours)" if p["editable"] else ""
        out.print(f"  {mark} {p['name']:<20} {note:<24} {', '.join(p['rscripts'])}{yours}", markup=False)
    for kind, mark in (("block", "■"), ("orbit", "○")):
        names = [p for p in items if p["kind"] == kind]
        if names:
            out.print(f"{kind}s", style="bold", markup=False)
            for p in names:
                err = f"  {p['error']}" if "error" in p else ""
                out.print(f"  {mark} {p['name']}{err}", style="red" if err else None, markup=False)
    out.print("\n`labcli run <plan>` starts one; `labcli check <name>` reads it first.", style="dim", markup=False)
    return 0


def _check(out, args) -> int:
    if not args:
        out.print("✗ check what? A plan, block or orbit name, or a path to a .plan file", markup=False)
        return USAGE
    name = args[0]
    from formslab.sequence import find_plan

    if find_plan(name) is not None:
        from formslab.sequence.__main__ import main as check_plan
        return check_plan([name], out=out.file)
    from formslab.orbit import file as orbitfile
    from formslab.sequence import block as blockfile

    for kind, mod in (("block", blockfile), ("orbit", orbitfile)):
        path = next((p for p in mod.discover() if p.stem == name), None)
        if path is None:
            continue
        errors, warnings = mod.review(path.read_text(encoding="utf-8"))
        out.print(f"{kind:<6}    {name}  ({path})", markup=False)
        for n, m in errors:
            out.print(f"  {path.name}:{n}: {m}", markup=False)
        for n, m in warnings:
            out.print(f"  {path.name}:{n}: warning: {m}", markup=False)
        if not errors and not warnings:
            out.print("  reads clean", markup=False)
        return 1 if errors else 0
    out.print(f"✗ no plan, block or orbit {name!r} (`labcli plans` lists them)", markup=False)
    return USAGE


# --- control ----------------------------------------------------------------------------

def _run(out, args) -> int:
    from formslab import lab

    if not args:
        out.print("✗ run what? A plan name (`labcli plans` lists them)", markup=False)
        return USAGE
    try:
        r = lab.start(args[0])
    except lab.LabError as e:
        out.print(f"✗ {e}", markup=False)
        return USAGE if e.code == 404 else 1
    lab.audit(_who(), f"run {args[0]}")
    if r["state"] == "running":
        out.print(f"✔ a run is already going: {r['plan']} (pid {r['pid']})", markup=False)
        return 1
    if r["state"] == "started":
        out.print(f"🟢 {r['plan']} started (pid {r['pid']}); `labcli status` shows it", markup=False)
    else:
        out.print(f"🟢 {r['plan']} starting; `labcli status` shows it, `labcli log` if it does not", markup=False)
    return 0


def _ctrl(out, verb: str, args) -> int:
    from formslab import lab

    args = [a.lower() for a in args]
    if args not in ([], ["fast"]) or (args and verb != "end"):
        out.print("✗ end, end fast, pause or resume", markup=False)
        return USAGE
    if verb in ("pause", "resume"):
        try:
            lab.ctrl(verb)
        except lab.LabError as e:
            out.print(f"✗ {e}.", markup=False)
            return 1
        lab.audit(_who(), verb)
        out.print(f"✔ {verb} sent", markup=False)
        return 0
    fast = args == ["fast"]
    if lab.host() is None and lab.lost_run() is None:
        out.print("✗ no run is going.", markup=False)
        return 0                                     # ending what has ended is done
    before = lab.last_ended()
    try:
        r = lab.end(fast)
    except lab.LabError as e:
        out.print(f"✗ {e}", markup=False)
        return 1
    lab.audit(_who(), "end fast" if fast else "end")
    out.print(f"… ending {r['plan']}" + (" with Fast End" if fast else "")
              + ": the end script, then each instrument's shutdown", markup=False)
    ended = lab.wait_ended()
    if lab.host() is not None:
        out.print(f"⚠ {r['plan']} has not stopped; its host is still running. `labcli status` shows it.",
                  markup=False)
        return 1
    if ended and ended != before:
        out.print(_ended_text(ended), markup=False)
    else:
        out.print(f"✖ {r['plan']} ended.", markup=False)
    return 0


def _kill(out, args) -> int:
    """The host stopped without its end, for one that will not take End. Its
    lock stays, so `labcli end` then runs the end from a fresh host."""
    from formslab import lab

    if lab.host() is None:
        out.print("✗ no run is going." + (" Its host is already gone; `labcli end` runs its end."
                                           if lab.lost_run() else ""), markup=False)
        return 1
    if (code := _confirm(out, ["kill", *args])) is not None:
        return code
    r = lab.kill()
    lab.audit(_who(), "kill")
    out.print(f"✖ {r['plan']}'s host (pid {r['killed']}) was stopped without its end. "
              "`labcli end` runs its end from a fresh host.", markup=False)
    return 0


def _supplies(out, args) -> int:
    from formslab import lab, safety

    if args[:1] != ["off"]:
        out.print("✗ supplies off, or supplies off psu1", markup=False)
        return USAGE
    if (code := _confirm(out, ["supplies", *args])) is not None:
        return code
    labels = [a for a in args[1:] if a.lower() != "yes"]
    try:
        done = safety.supplies_off(labels)
    except safety.Refused as e:
        out.print(f"✗ {e}", markup=False)
        return 1
    lab.audit(_who(), "supplies off" + (" " + " ".join(labels) if labels else ""))
    for d in done:
        out.print(("✔ " if d["ok"] else "✗ ") + d["text"], markup=False)
    if not done:
        out.print("no supply is enabled in the hardware map", markup=False)
    return 0 if done and all(d["ok"] for d in done) else 1


def _power(out, args) -> int:
    """The PowerSwitch: what it is and what each outlet feeds; an outlet on,
    off or cycled; or this computer's address for it (the driver's setup)."""
    import subprocess

    from formslab import lab, safety

    args = list(args)
    if args[:1] == ["setup"]:
        cmd = [sys.executable, "-m", "formslab.devices.powerswitch", "setup"] + \
              (["--dry-run"] if "--dry-run" in args else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            out.print("✗ the setup did not finish in 60 s", markup=False)
            return 1
        out.print((r.stdout + r.stderr).rstrip(), markup=False)
        return 0 if r.returncode == 0 else 1
    if not args or args[:1] == ["status"]:
        s = safety.outlets()
        out.print(f"PowerSwitch at {s['host']}, from {s['local_ip']} on {s['adapter']}: "
                  f"{'reached' if s['reachable'] else 'not reached'}", markup=False)
        if not s["reachable"]:
            out.print(f"  {s['note']}", markup=False)
        for o in s["outlets"]:
            out.print(f"  outlet {o['number']}  {o['feeds'] or '-'}", markup=False)
        return 0 if s["reachable"] else 1
    number, action = args[0], (args[1].lower() if len(args) > 1 else "")
    if not number.isdigit() or action not in safety.ACTIONS:
        out.print("✗ power, power setup, or power <outlet> on, off or cycle", markup=False)
        return USAGE
    if (code := _confirm(out, ["power", number, action, *args[2:]])) is not None:
        return code
    try:
        r = safety.outlet(int(number), action)
    except (safety.Refused, ValueError) as e:
        out.print(f"✗ {e}", markup=False)
        return 1
    lab.audit(_who(), f"power {number} {action}")
    for line in r["lines"]:
        out.print(("✔ " if r["ok"] else "✗ ") + line, markup=False)
    return 0 if r["ok"] else 1


def _log(out, args) -> int:
    from formslab.lab import log_path

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
    from formslab import lab

    if not args:
        out.print("✗ cast <label> <words>, e.g. `labcli cast hvc platen 20`", markup=False)
        return USAGE
    if args[-1] == "?":
        words = args[:-1]
        try:
            options = lab.complete(words)
        except lab.LabError as e:
            out.print(f"✗ {e}", markup=False)
            return 1
        if not options:
            out.print(f"nothing can follow {' '.join(words)}", markup=False)
            return 0
        out.print(f"after {' '.join(words)}:" if words else "a command starts with:", markup=False)
        for o in options:
            span = f"  {o['lo']:g} to {o['hi']:g} {o['unit'] or ''}" if o.get("lo") is not None else ""
            out.print(f"  {o['text']:<14} {o['help'] or ''}{span}", markup=False)
        return 0
    try:
        r = lab.send_command(" ".join(args))
    except lab.LabError as e:
        if e.code == 409 and str(e).startswith("no run"):
            out.print("✗ nothing sent: no run is going (`labcli run tvac` starts manual operation)", markup=False)
        else:
            out.print(f"✗ {e}", markup=False)
        return 1
    lab.audit(_who(), f"cast {' '.join(args)}" + ("" if r["ok"] else f" ({r['text']})"))
    out.print(f"{'✔' if r['ok'] else '✗'} {r['label']} ← {json.dumps(r['request'])}: {r['text']}", markup=False)
    return 0 if r["ok"] else 1
