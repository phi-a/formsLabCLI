from contextlib import contextmanager
from pathlib import Path
import json
import shutil
import sys
import time
import threading
import uuid
from typing import Callable, Optional, Tuple

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
                _mark_taken(block)
                changed = True
                results[normalized] = request
        if changed:
            AtomicJsonWrite(data, path)  # single write (only when needed)
    return results


# --- command ids and results --------------------------------------------------
#
# Every request written gets an id, kept on the block (never inside the request,
# which goes to the instrument as it is). A request merged into one not yet read
# keeps every sender's id. Taking a request marks each id "taken"; an owner that
# reports results then marks each "done" with ok and messages. A sender can
# therefore tell taken from refused from cleared (a ctrl reset or a host restart
# empties the request without taking it). Results are kept for the last
# KEEP_RESULTS ids, apart from `status`, so a status write cannot erase them.

KEEP_RESULTS = 20


def _trim(results: dict) -> None:
    while len(results) > KEEP_RESULTS:
        results.pop(next(iter(results)))


def _mark_taken(block: dict) -> list:
    ids = list(block.get("request_ids") or [])
    results = block.setdefault("results", {})
    now = time.time()
    for rid in ids:
        results[rid] = {"state": "taken", "t": now}
    _trim(results)
    block["processed"] = True
    block["request"] = {}
    block["request_ids"] = []
    return ids


def TakeCommand(label: str, path: Path = None) -> Tuple[dict, list]:
    """The owner's read: the pending request and its senders' ids, marked taken."""
    if path is None:
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, changed = _get_or_create_block(data, label)
        request = block.get("request") or {}
        if not request:
            if changed:
                AtomicJsonWrite(data, path)
            return {}, []
        ids = _mark_taken(block)
        AtomicJsonWrite(data, path)
    return request, ids


def ReadCommand(label: str, path: Path = None) -> dict:
    return TakeCommand(label, path)[0]


def ReportResult(label: str, ids, ok: bool, messages=(), path: Path = None) -> None:
    """The owner's answer for the requests it took: ok, and why not."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return
    if path is None:
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
        results = block.setdefault("results", {})
        now = time.time()
        for rid in ids:
            results[rid] = {"state": "done", "ok": bool(ok), "messages": [str(m) for m in messages], "t": now}
        _trim(results)
        AtomicJsonWrite(data, path)


def CommandState(label: str, rid: str, path: Path = None) -> dict:
    """What became of request `rid`: {"state": "pending" | "taken" | "done" |
    "cleared", "ok"?, "messages"?}. Read-only."""
    if path is None or not path.exists():
        path = cast_state_path()
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {"state": "unknown"}
    block = next((b for k, b in data.items() if isinstance(k, str) and k.lower() == label.lower()), {}) \
        if isinstance(data, dict) else {}
    if rid in (block.get("request_ids") or []) and block.get("request") and not block.get("processed", True):
        return {"state": "pending"}
    found = (block.get("results") or {}).get(rid)
    return dict(found) if found else {"state": "cleared"}


TAKE_S = 10.0      # an owner reads its block at about 10 Hz; this is plenty
RESULT_S = 60.0    # a valve toggle settles ~1 s and is verified; `stop` does two; a reconnect can take 3 s


def send_request(request: dict, label: str, *, wait_result: bool = False, take_s: float = TAKE_S,
                 result_s: float = RESULT_S, clock: Optional[Callable[[], float]] = None,
                 tick: Optional[Callable[[], None]] = None, path: Path = None) -> dict:
    """Write `request` to `label`, wait for its owner to take it, and with
    `wait_result` for the owner's result. Returns {"id", "state", "ok"?,
    "messages"?}, where state is "not_taken" (nobody read it in `take_s`),
    "cleared", "taken" (no result asked for, or none within `result_s`) or
    "done". `clock` and `tick` let the plan runner count only active time."""
    clock = clock or time.monotonic
    tick = tick or (lambda: time.sleep(0.05))
    rid = WriteCommand(request, label, path)
    start = clock()
    while True:
        st = CommandState(label, rid, path)
        if st["state"] not in ("pending", "unknown"):
            break
        if clock() - start >= take_s:
            return {"id": rid, "state": "not_taken"}
        tick()
    if st["state"] == "cleared" or not wait_result:
        return {"id": rid, **st}
    start = clock()
    while st["state"] in ("taken", "unknown"):
        if clock() - start >= result_s:
            return {"id": rid, "state": "taken"}
        tick()
        st = CommandState(label, rid, path)
    return {"id": rid, **st}

def WriteCommand(request: dict, label: str, path: Path = None) -> str:
    """Write a request for `label`'s owner; returns its id (see CommandState)."""
    if path is None or not path.exists():
        path = cast_state_path()
    rid = uuid.uuid4().hex
    with _locked(path):
        data = _safe_read_json(path)
        _, block, _ = _get_or_create_block(data, label)
        # Merge into any existing unprocessed request instead of replacing; every
        # sender's id is kept, so each can learn what became of its part.
        existing = block.get("request")
        if existing and isinstance(existing, dict) and not block.get("processed", True):
            block["request"] = _merge_nested_dicts(existing, request)
            block["request_ids"] = list(block.get("request_ids") or []) + [rid]
        else:
            block['request'] = request
            block["request_ids"] = [rid]
        block['processed'] = False
        # `timestamp` is when the block's owner last reported; a command is not a
        # report, so it is recorded apart (otherwise sending a command to an
        # instrument nobody is running would make it look as if it had just spoken).
        block['request_timestamp'] = time.time()
        AtomicJsonWrite(data, path)
    return rid

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
    """Clear every pending request, as a starting host does. The blocks keep
    their last status *and the time it was reported*, so the previous run's
    values read as old until an owner reports again, not as just updated."""
    if path is None or not path.exists():
        path = cast_state_path()
    with _locked(path):
        data = _safe_read_json(path)
        for block in data.values():
            if isinstance(block, dict):
                block['request'] = {}
                block['processed'] = True
                block['request_ids'] = []
        AtomicJsonWrite(data, path)

def DropUnknownBlocks(path: Path = None) -> list:
    """Remove blocks for labels no rScript owns any more (an older version's
    `tvac`); returns their names."""
    if path is None:
        path = cast_state_path()
    if not path.exists():
        return []
    with _locked(path):
        data = _safe_read_json(path)
        stale = [k for k in data if not (isinstance(k, str) and k.lower() in _KNOWN_CAST_LABELS)]
        if stale:
            for k in stale:
                data.pop(k)
            AtomicJsonWrite(data, path)
    return stale


def GenerateCleanCast(path: Path = None):
    if path is None:
        path = cast_state_path()
    template = build_default_cast_state()
    with _locked(path):
        AtomicJsonWrite(template, path)
    return template
