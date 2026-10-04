from contextlib import contextmanager
from pathlib import Path
import json
import shutil
import sys
import time
import threading
from typing import Tuple

from formslab.console.safefile import atomic_write_text, file_lock, read_json
from formslab.state import build_default_cast_state, cast_state_path

_KNOWN_CAST_LABELS = set(build_default_cast_state())

# Every read-modify-write of castfile.json holds this thread lock (the threads
# of one process) and the cross-process lock (the console, the host and the
# web GUI are separate processes), so no process's change overwrites another's.
_cast_lock = threading.Lock()


@contextmanager
def _locked(path: Path):
    with _cast_lock, file_lock(path):
        yield


def _normalize_label(label: str) -> str:
    return str(label).strip().lower()


def _merge_block(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        if key in ("request", "status"):
            base_value = merged.get(key)
            if isinstance(base_value, dict) and isinstance(value, dict):
                merged[key] = _merge_nested_dicts(base_value, value)
                continue
        merged[key] = value
    return merged


def _merge_nested_dicts(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_nested_dicts(existing, value)
        else:
            merged[key] = value
    return merged


def _default_block(label: str) -> dict:
    return build_default_cast_state()[label]


def _get_or_create_block(data: dict, label: str) -> Tuple[str, dict, bool]:
    normalized = _normalize_label(label)
    if normalized not in _KNOWN_CAST_LABELS:
        raise KeyError(f"Unknown CAST label: {label}")

    existing_key = None
    for key in data:
        if isinstance(key, str) and key.lower() == normalized:
            existing_key = key
            break

    changed = False
    if existing_key is None:
        data[normalized] = _default_block(normalized)
        changed = True
    elif existing_key != normalized:
        data[normalized] = _merge_block(
            _default_block(normalized),
            data.pop(existing_key),
        )
        changed = True

    return normalized, data[normalized], changed

def _safe_read_json(path: Path) -> dict:
    """The file's blocks. A missing file is `{}`.

    Writes are atomic and a read-modify-write holds the lock, so a file that
    will not parse is damaged, not mid-write. Callers write what they read
    back, so handing them `{}` would wipe every other block: keep the damaged
    file as ``<name>.bad`` and start from the defaults instead. A file that
    cannot be *read* (OSError) raises, and the caller tries again next time."""
    try:
        return read_json(path)
    except FileNotFoundError:
        return {}
    except ValueError:
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        print(f"✗ {path.name} was damaged; kept as {path.name}.bad and regenerated from defaults",
              file=sys.stderr)
        return build_default_cast_state()

def AtomicJsonWrite(data: dict, path: Path):
    """Replace `path` with `data` in one step; retried while a reader has the
    file open, and it raises rather than ever writing in place (a reader would
    see half a file)."""
    atomic_write_text(path, json.dumps(data, indent=2))

def ReadAllCommands(labels: list, path: Path = None) -> dict:
    """
    Read castfile.json ONCE and return all unprocessed requests as a dict.

    More efficient than calling ReadCommand() N times (N file reads → 1 file read).

    Args:
        labels: List of command labels to check (e.g. ["end","reset","resume","pause"]).
        path:   Path to the cast file (defaults to the configured cast state).

    Returns:
        Dict mapping label → request dict for each label that had an unprocessed request.
        Labels with no pending request are absent from the result.
    """
    if path is None:
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)   # single read
        results = {}
        changed = False
        for label in labels:
            try:
                normalized, block, block_changed = _get_or_create_block(data, label)
            except KeyError:
                continue
            changed = changed or block_changed
            request = block.get("request") or {}
            if request:
                block["processed"] = True
                block["request"] = {}
                changed = True
                results[normalized] = request
        if changed:
            AtomicJsonWrite(data, path)  # single write (only when needed)
    return results


def ReadCommand(label: str, path: Path = None) -> dict:
    if path is None:
        path = cast_state_path()
    with _locked(path):
        data  = _safe_read_json(path)
        _, block, changed = _get_or_create_block(data, label)
        request = block.get("request") or {}
        if not request:
            if changed:
                AtomicJsonWrite(data, path)
            return {}
        block["processed"] = True
        block["request"]   = {}
        AtomicJsonWrite(data, path)
    return request

def WriteCommand(request: dict, label: str, path: Path = None):
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
        # Merge into any existing unprocessed request instead of replacing
        existing = block.get("request")
        if existing and isinstance(existing, dict) and not block.get("processed", True):
            block["request"] = _merge_nested_dicts(existing, request)
        else:
            block['request'] = request
        block['processed'] = False
        block['timestamp'] = time.time()
        AtomicJsonWrite(data, path)

def CommandPending(label: str, path: Path = None) -> bool:
    """True while a request written to ``label`` has not been taken by its
    reader. Looks without consuming, unlike `ReadCommand`."""
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
    return bool(block.get("request")) and not block.get("processed", True)

def ReadStatus(label: str, path: Path = None) -> dict:
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, changed = _get_or_create_block(data, label)
        if changed:
            AtomicJsonWrite(data, path)
    return block.get("status", {})

def UpdateStatus(label: str, status: dict, path: Path = None):
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
        block['status'] = status
        block['timestamp'] = time.time()
        AtomicJsonWrite(data, path)

def ResetJson(path: Path = None):
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        now = time.time()
        for block in data.values():
            if isinstance(block, dict):
                block['request'] = {}
                block['processed'] = True
                block['timestamp'] = now
        AtomicJsonWrite(data, path)

def GenerateCleanCast(path: Path = None):
    if path is None:
        path = cast_state_path()
    template = build_default_cast_state()
    with _locked(path):
        AtomicJsonWrite(template, path)
    return template
