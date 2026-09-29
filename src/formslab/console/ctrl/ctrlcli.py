"""
Refactored ctrlcli.py
- Unified execute_command to dynamically dispatch based on a command map.
- Removed use of rich.Panel in help to simplify plain-text output (avoiding "panels").
- Uses CLIResult exclusively for all responses.
- No direct printing in handlers; __main__ prints rendered content.
- Prepared for easy addition of new commands by extending COMMANDS dict.

Next steps:
- In app/view/console_tab.py, remove the special-case for "--run" and let session.handle(raw) handle it like other commands.
- Ensure labcli.py integrates CLIResult.content directly via render_output.
"""
from pathlib import Path
import os, sys, signal, subprocess

import psutil

from rich.text import Text
from formslab.console.sessions.base import CLIResult
from formslab.console.ctrl.ctrlutils import ReadCommand, WriteCommand, LoadCommands, process_exists
from formslab.console.style import HEADER, DIM, ERROR, INFO, NUMBER, LABEL, TEXT, WARNING, SUCCESS
from formslab import bridge
from formslab.config import output_dir
from formslab.console.log.logcli import log_path

def missions_dir():
    """The `.zen` mission library, as FORMS resolves it.

    Asked of the library rather than computed here: FORMS already resolves a
    workspace ($FORMS_MISSIONS_DIR, then a walk up for `missions/`, then a
    remembered choice), and only by asking do the console and the host agree on
    which missions exist. Returns None when FORMS is absent -- there is no
    mission library without it, and `missions` says so rather than guessing.
    """
    try:
        return bridge.paths().missions_root()
    except bridge.FormsUnavailable:
        return None


def _parse_zen_header(path: Path) -> dict:
    """Extract mission metadata from .zen file comments and config."""
    meta = {}
    try:
        content = path.read_text()
        for line in content.split('\n')[:50]:
            line = line.strip()
            if line.startswith('#'):
                lower = line.lower()
                if 'mission:' in lower:
                    meta['mission'] = line.split(':', 1)[1].strip()
                elif 'author:' in lower:
                    meta['author'] = line.split(':', 1)[1].strip()
            elif line.startswith('satellite.name'):
                val = line.split('=', 1)[1].strip().strip('"\'')
                meta['satellite'] = val
            elif line.startswith('time.duration'):
                meta['duration'] = line.split('=', 1)[1].strip()
            elif line.startswith('time.units'):
                meta['units'] = line.split('=', 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return meta


def discover_missions() -> list[dict]:
    """Discover .zen mission files with metadata."""
    missions = []
    root = missions_dir()
    if root is None or not root.exists():
        return missions
    for zen_path in sorted(root.glob("*.zen")):
        meta = _parse_zen_header(zen_path)
        missions.append({
            "name": zen_path.stem,
            "path": str(zen_path),
            "satellite": meta.get("satellite"),
            "duration": meta.get("duration"),
            "units": meta.get("units"),
        })
    return missions


def _resolve_mission(target: str, missions: list[dict]) -> dict | None:
    """Resolve mission by name or index."""
    # Try numeric index first
    if target.isdigit():
        idx = int(target) - 1
        if 0 <= idx < len(missions):
            return missions[idx]
        return None

    # Try exact name match
    for m in missions:
        if m['name'].lower() == target.lower():
            return m

    # Try partial match
    matches = [m for m in missions if target.lower() in m['name'].lower()]
    if len(matches) == 1:
        return matches[0]

    return None


def missions_command() -> CLIResult:
    """List available mission files and operational modes."""
    result = Text()

    result.append("MISSIONS\n", HEADER)
    result.append("═" * 60 + "\n\n", DIM)

    missions = discover_missions()

    if not missions:
        result.append("  No .zen files found in missions/\n", DIM)
    else:
        # Header row
        result.append("  #   ", LABEL)
        result.append("Name".ljust(14), LABEL)
        result.append("Satellite".ljust(14), LABEL)
        result.append("Duration\n", LABEL)
        result.append("  " + "─" * 50 + "\n", DIM)

        for i, m in enumerate(missions, 1):
            result.append(f"  {i}   ", NUMBER)
            result.append(f"{m['name'][:12].ljust(14)}", INFO)
            sat = m.get('satellite') or '—'
            result.append(f"{sat[:12].ljust(14)}", TEXT)
            dur = m.get('duration') or '—'
            units = m.get('units') or ''
            result.append(f"{dur} {units}\n", TEXT)

    result.append("\n")
    result.append("OPERATIONAL MODES\n", HEADER)
    result.append("═" * 60 + "\n", DIM)
    result.append("  ●   ", WARNING)
    result.append("tvac".ljust(14), WARNING)
    result.append("TVAC maintenance mode (no propagation)\n", DIM)

    result.append("\n")
    result.append("Usage: ", DIM)
    result.append("run <name|#|tvac>\n", INFO)

    return CLIResult(result, clear=True)


# The pid file, the log and the session record used to resolve against three
# *different* "data" directories (parents[3], parents[2], and parents[2].parent),
# so the console could report a mission as not running while its own log sat
# somewhere else. They share the output directory now.
def _get_pid_path():
    return output_dir() / "sequence.pid"


def _launch_sequence(mode: str, config_path: str | None = None) -> CLIResult:
    """Launch the sequence host with a mode and optional mission config.

    Started as `python -m formslab.host.sequence` rather than by path: the host
    is an installed module, and resolving it as a file would put us back to
    guessing where the package lives.
    """
    if not bridge.available():
        return CLIResult(Text(
            "✗ run needs FORMS. Install it with "
            "`pip install \"formslab[forms]\"` to launch missions.", style=ERROR))

    pid_path = _get_pid_path()
    messages = []

    # Check for existing process
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text())
            os.kill(pid, 0)
            return CLIResult(f"✔ sequence host already running (pid {pid})")
        except (ProcessLookupError, ValueError):
            messages.append("⚠ stale PID file, restarting")
        except PermissionError:
            return CLIResult(f"✗ permission denied when checking pid {pid}")

    # Build command
    cmd = [sys.executable, "-m", "formslab.host.sequence", "--mode", mode]
    if config_path:
        cmd.extend(["--config", config_path])

    # Launch. The host inherits this working directory, so a mission's outputs
    # land beside the session that started it.
    log_file = log_path()

    proc = subprocess.Popen(
        cmd,
        stdout=log_file.open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    pid_path.write_text(str(proc.pid))

    # Write shared session file for GUI attachment
    import json, time as _time
    session_path = output_dir() / "sequence.session.json"
    session = {
        "pid": proc.pid,
        "mode": mode,
        "config_path": str(Path(config_path).resolve()) if config_path else None,
        "started_at": _time.time(),
        "source": "cli",
    }
    try:
        session_path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Format response
    if config_path:
        mission_name = Path(config_path).stem
        messages.append(f"🟢 sequence host started (pid {proc.pid}, mission={mission_name})")
    else:
        messages.append(f"🟢 sequence host started (pid {proc.pid}, mode={mode})")

    return CLIResult("\n".join(messages), clear=False)


def run_sequence(args=None) -> CLIResult:
    """
    Enhanced run command supporting:
      - run               -> default mission (first available)
      - run tvac          -> TVAC operational mode
      - run darkness      -> missions/darkness.zen
      - run 2             -> second mission by index
      - run sequence tvac -> legacy compatibility
    """
    missions = discover_missions()

    if not args:
        # Default: first available mission or darkness fallback
        if missions:
            return _launch_sequence(mode="zen", config_path=missions[0]['path'])
        return _launch_sequence(mode="mission", config_path=None)

    target = args[0].lower()

    # Legacy compatibility: "run sequence tvac" or "run sequence mission"
    if target == "sequence":
        mode = args[1] if len(args) > 1 else "mission"
        return _launch_sequence(mode=mode, config_path=None)

    # TVAC operational modes (special case): 'tvac' is the Rigol/RTD bench,
    # 'laco' is the LACO chamber driven through its HVC-3500 controller.
    if target in ("tvac", "laco"):
        return _launch_sequence(mode=target, config_path=None)

    # Resolve mission by name or index
    mission = _resolve_mission(target, missions)

    if not mission:
        # Check for ambiguous partial match
        matches = [m for m in missions if target in m['name'].lower()]
        if len(matches) > 1:
            names = ', '.join(m['name'] for m in matches)
            return CLIResult(f"✗ Ambiguous: '{target}' matches [{names}]")
        return CLIResult(f"✗ Unknown mission: '{target}'. Use 'missions' to list available.")

    return _launch_sequence(mode="zen", config_path=mission['path'])

def status_panel() -> CLIResult:
    pid_path = _get_pid_path()
    try:
        pid = int(Path(pid_path).read_text().strip())
    except Exception:
        return CLIResult("✗ No valid sequence.pid found.")
    if process_exists(pid):
        return CLIResult(f"● sequence.py running (pid {pid})")
    else:
        try:
            Path(pid_path).unlink()
        except FileNotFoundError:
            pass
        return CLIResult(f"✗ sequence.py not running. Cleaned pid file.", clear=False)


def list_sequence() -> CLIResult:
    """Every running sequence host.

    Was `pgrep -f sequence.py`, which is Unix-only and, since the extraction,
    looking for the wrong thing as well: the host is launched as
    `python -m formslab.host.sequence`, so "sequence.py" is no longer on its
    command line at all. Both spellings are matched so a host started the old
    way is still found.
    """
    needles = ("formslab.host.sequence", "sequence.py")
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            # The host is always a Python process. Without that check any
            # command line merely *mentioning* the host -- an editor, a grep --
            # would be reported as a running sequence.
            if "python" not in (proc.info["name"] or "").lower():
                continue
            cmdline = " ".join(proc.info["cmdline"] or ())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(needle in cmdline for needle in needles):
            found.append(proc.info["pid"])

    if not found:
        return CLIResult("No sequence host processes found.", clear=False)
    listed = "\n".join(f"PID: {pid}" for pid in sorted(found))
    return CLIResult("sequence host processes:\n" + listed)

def end_sequence() -> CLIResult:
    pid_path = _get_pid_path()
    try:
        pid = int(Path(pid_path).read_text().strip())
        os.kill(pid, signal.SIGTERM)
        Path(pid_path).unlink()
        return CLIResult(f"✖ sequence.py (pid {pid}) terminated and pid file removed.")
    except Exception as e:
        return CLIResult(f"✗ Error terminating sequence.py: {e}", clear=False)

def help_panel() -> CLIResult:
    """Generate structured help panel with mission and control commands."""
    result = Text()

    # Navigation
    result.append("═" * 60 + "\n", DIM)
    result.append("NAVIGATION\n", HEADER)
    result.append("  /switch astrid  ", LABEL)
    result.append("Switch to Astrid\n", TEXT)
    result.append("  /switch console ", LABEL)
    result.append("Return to the forms console\n", TEXT)
    result.append("  --cast          ", LABEL)
    result.append("Hardware status panel\n", TEXT)
    result.append("  --psu           ", LABEL)
    result.append("PSU controls\n", TEXT)
    result.append("  --ctrl          ", LABEL)
    result.append("Return here\n", TEXT)
    result.append("  --exit          ", LABEL)
    result.append("Quit\n", TEXT)

    # Missions
    result.append("\n")
    result.append("═" * 60 + "\n", DIM)
    result.append("MISSIONS\n", HEADER)
    result.append("  missions        ", LABEL)
    result.append("List available missions and modes\n", TEXT)
    result.append("  run <target>    ", LABEL)
    result.append("Launch mission (name, #, 'tvac' or 'laco')\n", TEXT)

    # Process control
    result.append("\n")
    result.append("═" * 60 + "\n", DIM)
    result.append("PROCESS CONTROL\n", HEADER)
    result.append("  status          ", LABEL)
    result.append("Check if sequence.py is running\n", TEXT)
    result.append("  ps              ", LABEL)
    result.append("List all sequence.py processes\n", TEXT)
    result.append("  pause           ", LABEL)
    result.append("Pause the running sequence\n", TEXT)
    result.append("  resume          ", LABEL)
    result.append("Resume a paused sequence\n", TEXT)
    result.append("  end             ", LABEL)
    result.append("Terminate the running sequence\n", TEXT)

    # Examples
    result.append("\n")
    result.append("═" * 60 + "\n", DIM)
    result.append("EXAMPLES\n", HEADER)
    result.append("  run darkness    ", INFO)
    result.append("Launch the darkness mission\n", DIM)
    result.append("  run 1           ", INFO)
    result.append("Launch first listed mission\n", DIM)
    result.append("  run tvac        ", INFO)
    result.append("Enter TVAC maintenance mode\n", DIM)

    return CLIResult(result)

# Command registry
COMMANDS = {
    "run": run_sequence,
    "missions": missions_command,
    "list": missions_command,  # Alias
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
    # Fallback for other commands
    WriteCommand(cmd, args[1] if len(args) > 1 else None)
    return CLIResult(f"✔ dispatched '{cmd}'", clear=False)

# CLI entrypoint
if __name__ == "__main__":
    res = execute_command(sys.argv[1:])
    # Render result
    content = res.content
    if hasattr(content, 'render'):
        print(content.render())
    else:
        print(content)
