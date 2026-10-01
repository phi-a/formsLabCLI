from pathlib import Path
import json
import time
import traceback
import threading
from typing import Tuple

from formslab.state import build_default_cast_state, cast_state_path

_KNOWN_CAST_LABELS = set(build_default_cast_state())

# --- File lock for all castfile.json read-modify-write operations ---
_cast_lock = threading.Lock()


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
    """Read and parse JSON with fallback on decode errors."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        # File was mid-write or corrupted — retry once after brief pause
        time.sleep(0.02)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}  # Return empty rather than crash the caller
    except FileNotFoundError:
        return {}

def AtomicJsonWrite(data: dict, path: Path, retries: int = 3):
    """
    Atomically write JSON data to a file.
    Uses retry logic to handle Windows file locking issues when other
    processes (like the Tauri GUI) are reading the file.
    """
    tmp_path = path.with_suffix(".tmp")

    # Write to temp file
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Try to rename with retries for file contention (cross-process)
    for attempt in range(retries):
        try:
            tmp_path.replace(path)
            return  # Success
        except OSError:
            if attempt < retries - 1:
                time.sleep(0.01)  # Brief pause, then retry
            else:
                # Final attempt: try direct write instead of atomic rename
                try:
                    with path.open("w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    raise  # Re-raise if even direct write fails

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
    with _cast_lock:
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
    with _cast_lock:
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
    with _cast_lock:
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
    with _cast_lock:
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
    return bool(block.get("request")) and not block.get("processed", True)

def ReadStatus(label: str, path: Path = None) -> dict:
    if path is None or not path.exists():
        path = cast_state_path()
    with _cast_lock:
        data = _safe_read_json(path)
        _, block, changed = _get_or_create_block(data, label)
        if changed:
            AtomicJsonWrite(data, path)
    return block.get("status", {})

def UpdateStatus(label: str, status: dict, path: Path = None):
    if path is None or not path.exists():
        path = cast_state_path()
    with _cast_lock:
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
        block['status'] = status
        block['timestamp'] = time.time()
        AtomicJsonWrite(data, path)

def ResetJson(path: Path = None):
    if path is None or not path.exists():
        path = cast_state_path()
    with _cast_lock:
        try:
            data = _safe_read_json(path)
            now = time.time()
            for block in data.values():
                if isinstance(block, dict):
                    block['request'] = {}
                    block['processed'] = True
                    block['timestamp'] = now
            AtomicJsonWrite(data, path)
        except Exception as e:
            print("✗ ResetJson failed:", e)
            traceback.print_exc()

def GenerateCleanCast(path: Path = None):
    if path is None:
        path = cast_state_path()
    template = build_default_cast_state()
    with _cast_lock:
        AtomicJsonWrite(template, path)
    return template
