"""The cast tab: live instrument status, and commands to the rScripts that own them.

    status [label]         every CAST block, or one (psu1 psu2 hvc tc cryo slta)
    <label> <words...>     a command to the rScript that owns the label,
                           e.g. `hvc vent open`, `psu1 ch1 set 12 1.25`
    help                   every command the rScripts declare
    init                   regenerate a clean castfile.json

The commands are not defined here. Each rScript declares its own (CAST_LABELS,
CAST_HELP, cast_request -- see formslab.rscripts.cast); this tab turns the words
into that script's request and writes it to CAST, where the script, running in
the host, applies it. With no host running a request waits, shown as pending.
"""
import json
import time

from rich.console import Group
from rich.table import Table
from rich.text import Text

from formslab.console.cast.castutils import GenerateCleanCast, WriteCommand
from formslab.console.sessions.base import CLIResult
from formslab.console.style import (
    DIM, ERROR, HEADER, INFO, LABEL, NUMBER, STATE_ERR, STATE_OFF, STATE_ON, SUCCESS, TEXT,
    UNIT, WARNING,
)
from formslab.rscripts import cast
from formslab.state import cast_state_path

# Display order for the full status: PSUs side by side, then these, then the rest.
_DISPLAY_ORDER = ["hvc", "tc", "cryo", "slta"]


def _load_cast() -> dict:
    data = json.loads(cast_state_path().read_text())
    if not isinstance(data, dict) or not data:
        raise ValueError("empty or invalid")
    return data


def _ago(ts_epoch: float) -> str:
    dt = time.time() - ts_epoch
    if dt < 0:
        return "just now"
    if dt < 60:
        return f"{int(dt)}s ago"
    if dt < 3600:
        return f"{int(dt // 60)}m {int(dt % 60)}s ago"
    return f"{int(dt // 3600)}h {int((dt % 3600) // 60)}m ago"


# --- help ---------------------------------------------------------------------------

def help_panel() -> CLIResult:
    result = Text()
    result.append("CAST\n", HEADER)
    result.append("─" * 64 + "\n", DIM)
    for usage, meaning in (("status [label]", "Status of every block, or one"),
                           ("init", "Regenerate a clean castfile.json")):
        result.append(f"  {usage:<44}", LABEL)
        result.append(meaning + "\n", TEXT)
    rows = cast.help_rows()
    _, errors = cast.owners()
    script = None
    for owner, usage, meaning in rows:
        if owner != script:
            script = owner
            result.append(f"\n{owner}\n", HEADER)
        result.append(f"  {usage:<44}", LABEL)
        result.append(meaning + "\n", TEXT)
    for owner, err in errors.items():
        result.append(f"\n{owner}: commands unavailable ({err})\n", WARNING)
    return CLIResult(result, clear=True, suppress_prompt=True)


# --- status -------------------------------------------------------------------------

def _render_psu(name: str, entry: dict) -> Text:
    """A PSU block, compact, for the side-by-side layout."""
    result = Text()
    ts_val = entry.get("timestamp", 0)
    result.append(f"{name.upper()}", HEADER)
    result.append(f"  {_ago(ts_val) if ts_val else 'unknown'}\n", DIM)

    stats = entry.get("status", {}) or {}
    reqs = entry.get("request", {}) or {}
    channels = sorted((k for k in stats if k.isdigit() and isinstance(stats[k], dict)), key=int)
    if not channels:
        result.append("(no channel data)\n", DIM)
        return result

    for head in ("CH ", "Vset   ", "Iset   ", "Vmeas   ", "Imeas   ", "State\n"):
        result.append(head, LABEL)
    result.append("─" * 44 + "\n", DIM)
    for ch in channels:
        st = stats[ch]
        rq = (reqs.get(ch) if isinstance(reqs, dict) else None) or {}
        result.append(f"{ch}  ", INFO)
        for key, fmt, width, unit in (("vset", "5.2f", 5, "V "), ("cset", "5.3f", 5, "A "),
                                      ("vmeas", "6.3f", 6, "V "), ("cmeas", "6.3f", 6, "A ")):
            v = st.get(key)
            result.append(f"{'---':>{width}} " if v is None else f"{v:{fmt}}",
                          DIM if v is None else NUMBER)
            result.append(unit, UNIT)
        on = st.get("on", False)
        result.append("ERR" if on is None else "ON" if on else "OFF",
                      STATE_ERR if on is None else STATE_ON if on else STATE_OFF)
        if rq.get("voltage") is not None or rq.get("current") is not None:
            result.append(f" ← pending {rq.get('voltage')}V {rq.get('current')}A", WARNING)
        result.append("\n")
    return result


def _render_generic(name: str, entry: dict) -> Text:
    """Any block as key: value, plus a pending request if one is waiting."""
    result = Text()
    ts_val = entry.get("timestamp", 0)
    result.append(f"  {name.upper()}", HEADER)
    result.append(f"  {_ago(ts_val) if ts_val else 'unknown'}\n", DIM)

    stats = entry.get("status", {}) or {}
    if not stats:
        result.append("  (no status data)\n", DIM)
    else:
        width = max(len(str(k)) for k in stats) + 2
        for k, v in stats.items():
            result.append(f"  {str(k).ljust(width)}", LABEL)
            if isinstance(v, bool):
                result.append("ON/OPEN" if v else "off/closed", STATE_ON if v else STATE_OFF)
            elif isinstance(v, float):
                result.append(f"{v:.4g}", NUMBER)
            elif isinstance(v, int):
                result.append(f"{v}", NUMBER)
            elif v is None:
                result.append("—", DIM)
            else:
                result.append(f"{v}", TEXT)
            result.append("\n")
    request = entry.get("request") or {}
    if request and not entry.get("processed", True):
        result.append(f"  pending: {request}\n", WARNING)
    return result


def status_panel(label: str = None) -> CLIResult:
    try:
        data = _load_cast()
    except FileNotFoundError:
        return CLIResult(Text("castfile.json not found — run init to generate", style=ERROR), clear=True)
    except (json.JSONDecodeError, ValueError) as e:
        return CLIResult(Text(f"castfile.json is corrupted — run init to regenerate\n({e})", style=ERROR), clear=True)

    if label:
        key = {k.lower(): k for k in data}.get(label.lower())
        if not key or not isinstance(data[key], dict):
            return CLIResult(Text(f"No data for label: {label}", style=ERROR), clear=True)
        render = _render_psu if key.lower().startswith("psu") else _render_generic
        return CLIResult(render(key, data[key]), clear=True)

    renderables = []
    psus = [_render_psu(n, data[n]) for n in ("psu1", "psu2") if isinstance(data.get(n), dict)]
    if psus:
        grid = Table.grid(padding=(0, 4))
        grid.add_row(*psus)
        renderables += [grid, Text("")]
    rest = [n for n in _DISPLAY_ORDER if n in data] + sorted(
        n for n in data if n not in _DISPLAY_ORDER and not n.startswith("psu"))
    for n in rest:
        if isinstance(data[n], dict):
            renderables += [_render_generic(n, data[n]), Text("")]
    return CLIResult(Group(*renderables), clear=True)


# --- commands -----------------------------------------------------------------------

def execute_command(args: list[str]) -> CLIResult:
    if not args:
        return help_panel()
    cmd = args[0].lstrip("-").lower()
    if cmd == "help":
        return help_panel()
    if cmd == "init":
        GenerateCleanCast()
        return CLIResult(Text("✔ Clean castfile.json generated.", style=SUCCESS), clear=True)
    if cmd == "status":
        return status_panel(args[1] if len(args) > 1 else None)

    try:
        request = cast.request(cmd, args[1:])
    except cast.CastUsage as e:
        return CLIResult(Text(f"✗ {e}", style=ERROR))
    except Exception as e:                      # a broken rScript must not crash the tab
        return CLIResult(Text(f"✗ {cmd}: {type(e).__name__}: {e}", style=ERROR))
    WriteCommand(request, cmd)
    r = Text()
    r.append("✔ ", SUCCESS)
    r.append(f"{cmd} ← ", TEXT)
    r.append(json.dumps(request), INFO)
    return CLIResult(r)
