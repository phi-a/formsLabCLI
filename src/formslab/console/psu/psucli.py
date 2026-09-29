import sys, json, subprocess
from pathlib import Path
from rich.text import Text
from formslab.console.sessions.base import CLIResult
from formslab.console.style import console, TEXT, ERROR, INFO, NUMBER, UNIT, LABEL, STATE_ON, STATE_OFF, SUCCESS, DIM
from formslab.devices.DP832A import PSU
from formslab.config import usbmap_path
from formslab.devices.psu_config import enabled_psu_labels

# Load command definitions
env = Path(__file__).parent
CONFIG = env / "psufile.json"
# The powerswitch driver is also runnable as a script; psucli shells out to it
# for outlet control. `lab/` became `formslab/devices/` in the extraction.
POWERSWITCH_SCRIPT = Path(__file__).resolve().parents[2] / "devices" / "powerswitch.py"

def load_commands():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}

# Only the PSU targets enabled in the shared USB map, built on first use.
#
# Built lazily, not at import: reading the map at import time seeded the config
# directory as a side effect of `import formslab.console.psu.psucli`, which put
# a file in the operator's home merely for loading a module.
#
# Cached, and not rebuilt per call, because these objects hold the open session:
# `--connect` followed by `--status` has to reach the same PSU. The cache key is
# the resolved map path, so pointing $FORMSLAB_CONFIG_DIR at another bench
# rebuilds instead of serving the previous bench's supplies.
_psu_cache: dict = {}
_psu_cache_key = None


def psus() -> dict:
    """The enabled PSU objects, keyed by label."""
    global _psu_cache, _psu_cache_key
    key = str(usbmap_path())
    if key != _psu_cache_key:
        _psu_cache = {label: PSU(label) for label in enabled_psu_labels()}
        _psu_cache_key = key
    return _psu_cache

def status_handler(args, *, target=None):
    names = psus().keys() if target in ("all", None) else [target]
    result = Text()

    for name in names:
        p = psus()[name]
        try:
            p.connect()
            # Device header with identifier
            result.append(f"[", DIM)
            result.append(name, INFO)
            result.append("] ", DIM)
            result.append(f"{p.idn().strip()}\n", DIM)

            # Channel status with semantic colors
            st = p.status()
            for ch, s in sorted(st.items()):
                result.append("CH", LABEL)
                result.append(str(ch), INFO)
                result.append(": ", DIM)
                result.append(f"{s['vmeas']:.3f}", NUMBER)
                result.append("V", UNIT)
                result.append(" / ", DIM)
                result.append(f"{s['cmeas']:.3f}", NUMBER)
                result.append("A", UNIT)
                result.append("  ", DIM)

                # State with color
                if s['on']:
                    result.append("ON\n", STATE_ON)
                else:
                    result.append("OFF\n", STATE_OFF)
        finally:
            p.disconnect()

    return CLIResult(result, clear=True)

def connect_handler(args, *, target=None):
    names = psus().keys() if target in ("all", None) else [target]
    result = Text()

    for name in names:
        p = psus()[name]
        try:
            p.connect()
            result.append("✔ ", SUCCESS)
            result.append(name, INFO)
            result.append(" connected\n", TEXT)
        except (FileNotFoundError, OSError) as e:
            # Device not physically connected
            if "No such file or directory" in str(e) or "could not open port" in str(e):
                result.append("○ ", DIM)
                result.append(name, INFO)
                result.append(" not available ", DIM)
                result.append("(device not connected)\n", DIM)
            else:
                result.append("✗ ", ERROR)
                result.append(name, INFO)
                result.append(f" error: {e}\n", ERROR)
        except Exception as e:
            result.append("✗ ", ERROR)
            result.append(name, INFO)
            result.append(f" connect failed: {e}\n", ERROR)

    return CLIResult(result, clear=True)

def disconnect_handler(args, *, target=None):
    names = psus().keys() if target in ("all", None) else [target]
    for name in names:
        psus()[name].disconnect()

    result = Text()
    result.append("✔ ", SUCCESS)
    result.append(' and '.join(names), INFO)
    result.append(" disconnected\n", TEXT)
    return CLIResult(result, clear=True)

def shutdown_handler(args, *, target=None):
    names = psus().keys() if target in ("all", None) else [target]
    for name in names:
        psus()[name].shutdown()
        psus()[name].disconnect()

    result = Text()
    result.append("✔ ", SUCCESS)
    result.append(' and '.join(names), INFO)
    result.append(" shutdown and disconnected\n", TEXT)
    return CLIResult(result, clear=True)

def alloff_handler(args, *, target=None):
    names = psus().keys() if target in ("all", None) else [target]
    for name in names:
        psus()[name].alloff()

    result = Text()
    result.append("✔ ", SUCCESS)
    if target in ("all", None):
        result.append("All channels turned ", TEXT)
        result.append("OFF\n", STATE_OFF)
    else:
        result.append(f"All channels on ", TEXT)
        result.append(target, INFO)
        result.append(" turned ", TEXT)
        result.append("OFF\n", STATE_OFF)

    return CLIResult(result, clear=True)

def channel_handler(args, *, target=None):
    if not args:
        return CLIResult(Text("✗ Channel number required. Usage: ch <N> [--set V A] [--on] [--off]", style=ERROR), clear=True)
    try:
        ch = int(args[0])
    except ValueError:
        return CLIResult(Text("✗ Invalid channel number (must be integer)", style=ERROR), clear=True)

    # Validate channel number range (typically 1-3 for most PSUs)
    if ch < 1 or ch > 3:
        return CLIResult(Text(f"✗ Channel {ch} out of range (valid: 1-3)", style=ERROR), clear=True)

    names = psus().keys() if target in ("all", None) else [target]
    result = Text()

    if "--set" in args:
        idx = args.index("--set")
        try:
            v = float(args[idx+1])
            a = float(args[idx+2])
        except (IndexError, ValueError):
            return CLIResult(Text("✗ Invalid voltage/current values. Usage: ch <N> --set <voltage> <current>", style=ERROR), clear=True)
        except IndexError:
            return CLIResult(Text("✗ Missing voltage or current value. Usage: ch <N> --set <voltage> <current>", style=ERROR), clear=True)

        # Validate voltage and current ranges
        if v < 0 or v > 30:
            return CLIResult(Text(f"✗ Voltage {v}V out of safe range (0-30V)", style=ERROR), clear=True)
        if a < 0 or a > 5:
            return CLIResult(Text(f"✗ Current {a}A out of safe range (0-5A)", style=ERROR), clear=True)

        for name in names:
            p = psus()[name]
            p.set(ch, v, a)
            result.append("✔ ", SUCCESS)
            result.append(name, INFO)
            result.append(" CH", LABEL)
            result.append(str(ch), INFO)
            result.append(" → ", DIM)
            result.append(f"{v}", NUMBER)
            result.append("V", UNIT)
            result.append(" @ ", DIM)
            result.append(f"{a}", NUMBER)
            result.append("A\n", UNIT)

    if "--on" in args:
        for name in names:
            p = psus()[name]; p.on(ch)
            result.append("✔ ", SUCCESS)
            result.append(name, INFO)
            result.append(" CH", LABEL)
            result.append(str(ch), INFO)
            result.append(" ", DIM)
            result.append("ON\n", STATE_ON)

    if "--off" in args:
        for name in names:
            p = psus()[name]; p.off(ch)
            result.append("✔ ", SUCCESS)
            result.append(name, INFO)
            result.append(" CH", LABEL)
            result.append(str(ch), INFO)
            result.append(" ", DIM)
            result.append("OFF\n", STATE_OFF)

    return CLIResult(result, clear=False)

def powerswitch_channel_handler(args, *, target=None):
    """Handle powerswitch channel operations: --ch N --on/--off/--cycle [--delay X]"""
    if not args:
        return CLIResult(Text("✗ Channel number required. Usage: --ch <N> [--on|--off|--cycle] [--delay X]", style=ERROR), clear=True)

    try:
        ch = int(args[0])
    except ValueError:
        return CLIResult(Text("✗ Invalid channel number (must be integer)", style=ERROR), clear=True)

    # Validate channel number range (outlets 1-8)
    if ch < 1 or ch > 8:
        return CLIResult(Text(f"✗ Channel {ch} out of range (valid outlets: 1-8)", style=ERROR), clear=True)

    # Get path to powerswitch.py
    powerswitch_path = POWERSWITCH_SCRIPT
    if not powerswitch_path.exists():
        return CLIResult(Text(f"✗ powerswitch.py not found at {powerswitch_path}", style=ERROR), clear=True)

    result = Text()

    # Parse delay parameter
    delay = None
    if "--delay" in args:
        try:
            idx = args.index("--delay")
            delay = float(args[idx + 1])
            if delay < 0:
                return CLIResult(Text(f"✗ Delay must be positive", style=ERROR), clear=True)
        except (IndexError, ValueError):
            return CLIResult(Text("✗ Invalid --delay value. Usage: --delay <seconds>", style=ERROR), clear=True)

    # Handle --on
    if "--on" in args:
        cmd_args = [sys.executable, str(powerswitch_path), "--outlet", str(ch), "on"]
        try:
            proc_result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15)

            if proc_result.returncode == 0:
                result.append("✔ ", SUCCESS)
                result.append(f"Outlet {ch} ", TEXT)
                result.append("ON\n", STATE_ON)
                if proc_result.stdout:
                    result.append(f"{proc_result.stdout}\n", DIM)
            else:
                result.append("✗ ", ERROR)
                result.append(f"Outlet {ch} ON failed\n", ERROR)
                if proc_result.stderr:
                    result.append(f"{proc_result.stderr}\n", DIM)
        except subprocess.TimeoutExpired:
            return CLIResult(Text(f"✗ Outlet {ch} ON timed out", style=ERROR), clear=True)
        except Exception as e:
            return CLIResult(Text(f"✗ Outlet {ch} ON failed: {e}", style=ERROR), clear=True)

    # Handle --off
    if "--off" in args:
        cmd_args = [sys.executable, str(powerswitch_path), "--outlet", str(ch), "off"]
        try:
            proc_result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15)

            if proc_result.returncode == 0:
                result.append("✔ ", SUCCESS)
                result.append(f"Outlet {ch} ", TEXT)
                result.append("OFF\n", STATE_OFF)
                if proc_result.stdout:
                    result.append(f"{proc_result.stdout}\n", DIM)
            else:
                result.append("✗ ", ERROR)
                result.append(f"Outlet {ch} OFF failed\n", ERROR)
                if proc_result.stderr:
                    result.append(f"{proc_result.stderr}\n", DIM)
        except subprocess.TimeoutExpired:
            return CLIResult(Text(f"✗ Outlet {ch} OFF timed out", style=ERROR), clear=True)
        except Exception as e:
            return CLIResult(Text(f"✗ Outlet {ch} OFF failed: {e}", style=ERROR), clear=True)

    # Handle --cycle
    if "--cycle" in args:
        import time

        # First turn off
        cmd_args = [sys.executable, str(powerswitch_path), "--outlet", str(ch), "off"]
        try:
            proc_result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15)
            if proc_result.returncode == 0:
                result.append("✔ ", SUCCESS)
                result.append(f"Outlet {ch} ", TEXT)
                result.append("OFF", STATE_OFF)
                result.append(" (cycle step 1/2)\n", DIM)
            else:
                return CLIResult(Text(f"✗ Outlet {ch} cycle failed at OFF step", style=ERROR), clear=True)
        except Exception as e:
            return CLIResult(Text(f"✗ Outlet {ch} cycle failed: {e}", style=ERROR), clear=True)

        # Wait for delay (default 3 seconds)
        wait_time = delay if delay is not None else 3.0
        result.append(f"⏱  Waiting {wait_time}s...\n", DIM)
        time.sleep(wait_time)

        # Then turn on
        cmd_args = [sys.executable, str(powerswitch_path), "--outlet", str(ch), "on"]
        try:
            proc_result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15)
            if proc_result.returncode == 0:
                result.append("✔ ", SUCCESS)
                result.append(f"Outlet {ch} ", TEXT)
                result.append("ON", STATE_ON)
                result.append(" (cycle step 2/2)\n", DIM)
                result.append(f"✔ Power cycle complete\n", SUCCESS)
            else:
                return CLIResult(Text(f"✗ Outlet {ch} cycle failed at ON step", style=ERROR), clear=True)
        except Exception as e:
            return CLIResult(Text(f"✗ Outlet {ch} cycle failed at ON step: {e}", style=ERROR), clear=True)

    return CLIResult(result, clear=False)

def powerswitch_setup_handler(args=None, **kwargs):
    """Configure the network interface registered for the PowerSwitch."""
    powerswitch_path = POWERSWITCH_SCRIPT
    if not powerswitch_path.exists():
        return CLIResult(Text(f"✗ powerswitch.py not found at {powerswitch_path}", style=ERROR), clear=True)

    command = [sys.executable, str(powerswitch_path), "setup"]
    if args and "--dry-run" in args:
        command.append("--dry-run")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30
        )

        msgs = []
        msgs.append("⚙️  PowerSwitch Network Setup\n")
        if result.stdout:
            msgs.append(f"\n{result.stdout}\n")
        if result.returncode != 0:
            msgs.append(f"\n✗ Setup failed with code {result.returncode}\n")
            if result.stderr:
                msgs.append(f"Error: {result.stderr}\n")
        else:
            msgs.append("\n✔ PowerSwitch network setup completed successfully\n")

        return CLIResult(Text(''.join(msgs), style=TEXT), clear=True)

    except subprocess.TimeoutExpired:
        return CLIResult(Text("✗ Setup script timed out", style=ERROR), clear=True)
    except Exception as e:
        return CLIResult(Text(f"✗ Setup failed: {e}", style=ERROR), clear=True)

def powerswitch_status_handler(args=None, **kwargs):
    """Check powerswitch status."""
    powerswitch_path = POWERSWITCH_SCRIPT
    if not powerswitch_path.exists():
        return CLIResult(Text(f"✗ powerswitch.py not found at {powerswitch_path}", style=ERROR), clear=True)

    cmd_args = [sys.executable, str(powerswitch_path), "status"]

    try:
        result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=5)

        msgs = []
        if result.stdout:
            msgs.append(result.stdout)
        if result.returncode != 0:
            if result.stderr:
                msgs.append(f"\n{result.stderr}")
            msgs.append(f"\n✗ Status check failed (exit code {result.returncode})")

        return CLIResult(Text(''.join(msgs) if msgs else "No output", style=TEXT), clear=True)

    except subprocess.TimeoutExpired:
        return CLIResult(Text("✗ Status check timed out", style=ERROR), clear=True)
    except Exception as e:
        return CLIResult(Text(f"✗ Status check failed: {e}", style=ERROR), clear=True)

def help_panel():
    """Generate enhanced help panel with color-coded examples."""
    from formslab.console.style import ACCENT1, ACCENT2, HEADER

    result = Text()

    # Header
    result.append("PSU CLI Commands\n", style=HEADER)
    result.append("─" * 60 + "\n\n", style=DIM)

    # Device Selection
    result.append("Device Selection:\n", style=TEXT)
    result.append("  --device ", style=LABEL)
    targets = (*psus(), "all", "ps")
    for index, target in enumerate(targets):
        if index:
            result.append(" | ", style=DIM)
        result.append(target, style=ACCENT2 if target == "ps" else INFO)
    result.append("     Select target device\n\n", style=DIM)

    # Connection Commands (PSU only)
    result.append("PSU Connection:\n", style=TEXT)
    result.append("  --connect       ", style=LABEL)
    result.append("Open session & enter remote mode\n", style=TEXT)
    result.append("  --disconnect    ", style=LABEL)
    result.append("Exit remote mode & close session\n", style=TEXT)
    result.append("  --shutdown      ", style=LABEL)
    result.append("Gracefully shutdown & disconnect\n", style=TEXT)
    result.append("  --status        ", style=LABEL)
    result.append("Display voltage, current & state\n\n", style=TEXT)

    # PSU Channel Commands
    result.append("PSU Channel Operations:\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --set ", style=LABEL)
    result.append("V", style=NUMBER)
    result.append(" ", style=DIM)
    result.append("A", style=NUMBER)
    result.append("        Set voltage & current for channel N\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --on             ", style=TEXT)
    result.append("Turn channel N ON\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --off            ", style=TEXT)
    result.append("Turn channel N OFF\n", style=TEXT)
    result.append("  --alloff              ", style=LABEL)
    result.append("Turn OFF all channels\n\n", style=TEXT)

    # PowerSwitch Commands
    result.append("PowerSwitch (Hard Power - use ", style=TEXT)
    result.append("--device ps", style=ACCENT2)
    result.append("):\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --on             ", style=TEXT)
    result.append("Turn outlet N ON\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --off            ", style=TEXT)
    result.append("Turn outlet N OFF\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --cycle          ", style=TEXT)
    result.append("Power cycle outlet N\n", style=TEXT)
    result.append("  --ch ", style=LABEL)
    result.append("N", style=NUMBER)
    result.append(" --cycle --delay ", style=LABEL)
    result.append("X", style=NUMBER)
    result.append("  Power cycle with custom delay (seconds)\n", style=TEXT)
    result.append("  --setup               ", style=LABEL)
    result.append("Configure the registered PowerSwitch network adapter\n", style=TEXT)
    result.append("  --setup --dry-run     ", style=LABEL)
    result.append("Show the network change without applying it\n", style=TEXT)
    result.append("  --status              ", style=LABEL)
    result.append("Check powerswitch reachability\n\n", style=TEXT)

    # Examples Section
    result.append("Usage Examples:\n", style=HEADER)
    result.append("─" * 60 + "\n", style=DIM)

    # Example 1 - PSU
    result.append("  Set PSU1 CH1 to 5V @ 2A and turn ON:\n", style=DIM)
    result.append("    --device ", style=LABEL)
    result.append("psu1", style=INFO)
    result.append("\n    --ch ", style=LABEL)
    result.append("1", style=NUMBER)
    result.append(" --set ", style=LABEL)
    result.append("5.0", style=NUMBER)
    result.append(" ", style=DIM)
    result.append("2.0", style=NUMBER)
    result.append(" --on\n\n", style=LABEL)

    # Example 2 - PowerSwitch
    result.append("  Power cycle outlet 3 with 5s delay:\n", style=DIM)
    result.append("    --device ", style=LABEL)
    result.append("ps", style=ACCENT2)
    result.append("\n    --ch ", style=LABEL)
    result.append("3", style=NUMBER)
    result.append(" --cycle --delay ", style=LABEL)
    result.append("5\n\n", style=NUMBER)

    # Example 3 - PowerSwitch simple
    result.append("  Turn OFF outlet 2:\n", style=DIM)
    result.append("    --device ", style=LABEL)
    result.append("ps", style=ACCENT2)
    result.append("\n    --ch ", style=LABEL)
    result.append("2", style=NUMBER)
    result.append(" --off\n\n", style=LABEL)

    # Example 4 - All PSUs
    result.append("  Check status of all PSUs:\n", style=DIM)
    result.append("    --device ", style=LABEL)
    result.append("all", style=INFO)
    result.append("\n    --status\n\n", style=LABEL)

    # Add separator and result section header
    result.append("═" * 60 + "\n", style=ACCENT1)
    result.append("RESULT:\n", style=HEADER)

    return CLIResult(result, clear=False)

from formslab.console.style import PROMPT_MARKUP, PROMPT_SUFFIX, TEXT

def prompt(active):
    return f"[{PROMPT_MARKUP}]{active}{PROMPT_SUFFIX} [/] "

def status_panel(psu_target):
    targets = (psu_target.values() if isinstance(psu_target, dict) else [psu_target])
    lines = []
    for p in targets:
        p.connect()
        lines.append(f"{p.idn().strip()}\n")
        st = p.status()
        for ch, s in sorted(st.items()):
            lines.append(
                f"CH{ch}: {s['vmeas']:.3f}V/"
                f"{s['cmeas']:.3f}A "
                f"{'ON' if s['on'] else 'OFF'}\n"
            )
        p.disconnect()
    return CLIResult(Text("".join(lines), style=TEXT))

COMMANDS = {
    "status":     status_handler,
    "connect":    connect_handler,
    "disconnect": disconnect_handler,
    "shutdown":   shutdown_handler,
    "alloff":     alloff_handler,
    "allchannel": alloff_handler,
    "ch":         channel_handler,
    "help":       lambda args=None, **kwargs: help_panel(),
}

# PowerSwitch-specific commands
PS_COMMANDS = {
    "ch":      powerswitch_channel_handler,
    "setup":   powerswitch_setup_handler,
    "status":  powerswitch_status_handler,
    "help":    lambda args=None, **kwargs: help_panel(),
}

def execute_command(args, *, context="cli", target=None):
    """
    args: list of CLI tokens
    context: CLI or other context
    target: "psu1", "psu2", "all", or "ps" (powerswitch)
    """
    if not args:
        return help_panel()

    cmd = args[0].lstrip("-")

    # Route to powerswitch commands if target is 'ps'
    if target == "ps":
        handler = PS_COMMANDS.get(cmd)
        if handler:
            return handler(args[1:], target=target)
        return CLIResult(Text(f"✗ Unknown PowerSwitch command: {cmd}\nTry --help for available commands", style=ERROR))

    # Route to PSU commands
    handler = COMMANDS.get(cmd)
    if handler:
        return handler(args[1:], target=target)
    return CLIResult(Text(f"✗ Unknown command: {' '.join(args)}", style=ERROR))

if __name__ == "__main__":
    console.print(help_panel().content)
    if len(sys.argv) > 1:
        res = execute_command(sys.argv[1:], target=None)
        console.print(res.content)
    else:
        while True:
            raw = console.input("psu> ").strip()
            if raw in ("--exit", "exit", "quit"): break
            parts = raw.split()
            active = None  # default for standalone
            res = execute_command(parts, target=active)
            console.print(res.content)
