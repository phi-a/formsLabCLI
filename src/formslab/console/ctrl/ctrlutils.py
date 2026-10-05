import json
import shutil
import sys
import threading
from contextlib import contextmanager
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


def LoadCommands() -> Optional[Dict]:
    """The command table, or None when it cannot be read right now.

    Never `{}`: a caller that wrote `{}` back would erase the table, and one
    that tested it would take "unreadable" for "nothing pending"."""
    try:
        return read_json(ctrl_state_path())
    except FileNotFoundError:
        return _default_commands()                   # nothing written yet: nothing pending
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


def DropUnknownCommands() -> list:
    """Remove entries the host does not read (an older version's `plans`,
    `missions`, `exit`...) and their old `desc` text; returns the names removed."""
    if not ctrl_state_path().exists():
        return []
    known = _default_commands()
    with _locked():
        cmds = _load_for_update()
        stale = [k for k in cmds if k not in known]
        described = any(isinstance(b, dict) and "desc" in b for b in cmds.values())
        if stale or described or set(known) - set(cmds):
            _write_ctrljson({k: {"key": (cmds.get(k) or {}).get("key"),
                                 "processed": (cmds.get(k) or {}).get("processed", True)} for k in known})
    return stale


def _write_ctrljson(cmds: Dict):
    """The table in its hand-formatted layout (one block per line), replacing
    the file in one step. Callers hold the lock."""
    lines = ["{"]
    keys = list(cmds.keys())
    for i, k in enumerate(keys):
        block = cmds[k]
        line = (f'  "{k}": {{ "key": {json.dumps(block.get("key"))}, '
                f'"processed": {json.dumps(block.get("processed", True))} }}')
        if i < len(keys) - 1:
            line += ","
        lines.append(line)
    lines.append("}")
    atomic_write_text(ctrl_state_path(), "\n".join(lines))

