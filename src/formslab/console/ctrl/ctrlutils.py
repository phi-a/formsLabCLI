import json
import shutil
import sys
import threading
from contextlib import contextmanager

import psutil
from typing import Dict, Iterable, Optional

from formslab.console.safefile import atomic_write_text, file_lock, read_json
from formslab.state import build_default_ctrl_commands, ctrl_state_path

# The ctrl command table is shared by the console, the host and the web GUI,
# which are separate processes: every change is a read-modify-write under a
# thread lock and a cross-process lock, and a write replaces the file in one
# step (see safefile).
_ctrl_lock = threading.Lock()


@contextmanager
def _locked():
    with _ctrl_lock, file_lock(ctrl_state_path()):
        yield


def _default_commands() -> Dict:
    return build_default_ctrl_commands()


def _ensure_ctrlfile() -> Dict:
    cmds = _default_commands()
    ctrl_state_path().parent.mkdir(parents=True, exist_ok=True)
    with _locked():
        if not ctrl_state_path().exists():
            _write_ctrljson(cmds)
    return cmds


def LoadCommands() -> Optional[Dict]:
    """The command table, or None when it cannot be read right now.

    Never `{}`: a caller that wrote `{}` back would erase the table, and one
    that tested it would take "unreadable" for "nothing pending"."""
    try:
        return read_json(ctrl_state_path())
    except FileNotFoundError:
        return _ensure_ctrlfile()
    except (OSError, ValueError) as e:
        print(f"✗ Failed to load ctrl file: {e}", file=sys.stderr)
        return None


def _load_for_update() -> Dict:
    """The table, for a change made under the lock. A file that will not parse
    is damaged (writes are atomic): keep it as ``.bad`` and start from the
    defaults. One that cannot be read raises, and the caller tries again."""
    try:
        return read_json(ctrl_state_path())
    except FileNotFoundError:
        return _default_commands()
    except ValueError:
        path = ctrl_state_path()
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        print(f"✗ {path.name} was damaged; kept as {path.name}.bad and regenerated from defaults",
              file=sys.stderr)
        return _default_commands()


def ReadCommands(labels: Iterable[str]) -> Dict[str, Dict]:
    """Take every pending request among `labels` in one read and one write:
    {label: block} for each that had one, marked processed."""
    with _locked():
        cmds = _load_for_update()
        taken = {}
        for label in labels:
            block = cmds.get(label)
            if isinstance(block, dict) and not block.get("processed", True):
                taken[label] = block
                cmds[label] = {**block, "key": None, "processed": True}
        if taken:
            _write_ctrljson(cmds)
    return taken


def ReadCommand(label: str) -> Optional[Dict]:
    return ReadCommands([label]).get(label)


def WriteCommand(label: str, key: Optional[str] = None):
    with _locked():
        cmds = _load_for_update()
        if label not in cmds:
            raise ValueError(f"[ctrl] '{label}' not found")
        cmds[label] = {**cmds[label], "key": key, "processed": False}
        _write_ctrljson(cmds)


def ResetCtrlState():
    with _locked():
        cmds = _load_for_update()
        for k, block in cmds.items():
            block["key"] = None
            block["processed"] = True
        _write_ctrljson(cmds)


def _write_ctrljson(cmds: Dict):
    """The table in its hand-formatted layout (one block per line), replacing
    the file in one step. Callers hold the lock."""
    lines = ["{"]
    keys = list(cmds.keys())
    for i, k in enumerate(keys):
        block = cmds[k]
        desc = f', "desc": {json.dumps(block["desc"])}' if "desc" in block else ""
        line = (
            f'  "{k}": {{ "key": {json.dumps(block.get("key"))}, '
            f'"processed": {json.dumps(block.get("processed", True))}{desc} }}'
        )
        if i < len(keys) - 1:
            line += ","
        lines.append(line)
    lines.append("}")
    atomic_write_text(ctrl_state_path(), "\n".join(lines))


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
