import os
import json

import psutil
from typing import Dict, Optional

from formslab.console.style import (
    console,
    TEXT,
    HEADER,
    ERROR,
    NUMBER,
    ACCENT1,
    ACCENT2,
    PANEL_BORDER,
    PANEL_PADDING,
    PROMPT_PREFIX,
    PROMPT_SUFFIX,
)
from formslab.state import build_default_ctrl_commands, ctrl_state_path



def _default_commands() -> Dict:
    return build_default_ctrl_commands()

def _ensure_ctrlfile() -> Dict:
    cmds = _default_commands()
    ctrl_state_path().parent.mkdir(parents=True, exist_ok=True)
    _write_ctrljson(cmds)
    return cmds

def LoadCommands():
    try:
        with open(ctrl_state_path(), "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return _ensure_ctrlfile()
    except Exception as e:
        print(f"✗ Failed to load ctrl.json: {e}")
        return {}

def ReadCommand(label: str) -> Optional[Dict]:
    cmds = LoadCommands()
    block = cmds.get(label)
    if not isinstance(block, dict):
        return None
    if block.get("processed", True):
        return None

    cmds[label] = {"key": None, "processed": True}

    with open(ctrl_state_path(), "w") as f:
        json.dump(cmds, f, indent=2)

    return block

def WriteCommand(label: str, key: Optional[str] = None):
    cmds = LoadCommands()
    if label not in cmds:
        raise ValueError(f"[ctrl] '{label}' not found")

    block = cmds[label]
    block["key"] = key
    block["processed"] = False

    _write_ctrljson(cmds)

def ResetCtrlState():
    cmds = LoadCommands()
    for k, block in cmds.items():
        block["key"] = None
        block["processed"] = True
    _write_ctrljson(cmds)

def _write_ctrljson(cmds: Dict):
    with open(ctrl_state_path(), "w") as f:
        lines = ["{"]
        keys = list(cmds.keys())
        for i, k in enumerate(keys):
            block = cmds[k]
            desc = f', "desc": {json.dumps(block["desc"])}' if "desc" in block else ""
            line = (
                f'  "{k}": {{ "key": {json.dumps(block["key"])}, '
                f'"processed": {json.dumps(block["processed"])}{desc} }}'
            )
            if i < len(keys) - 1:
                line += ","
            lines.append(line)
        lines.append("}")
        f.write("\n".join(lines))

def process_exists(pid: int) -> bool:
    """Whether a pid is live, the same way on every platform.

    Was a `tasklist` shell-out on Windows and `os.kill(pid, 0)` elsewhere: two
    code paths, one of them spawning a shell per call. psutil is already a
    dependency and covers both, including the "exists but not ours" case that
    `os.kill` reported as PermissionError.
    """
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False
