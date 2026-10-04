"""Reading and writing the shared state files (castfile.json, ctrlfile.json) safely.

The console, the sequence host and (later) the web GUI are separate processes
that read and write the same two files. Three rules keep that sound:

* `file_lock` -- a read-modify-write holds a cross-process lock, so one
  process's change cannot overwrite another's (a thread lock only covers one
  process). The OS drops the lock if its holder dies, so there is no stale lock.
* `atomic_write_text` -- written to a private temp file and renamed over the
  target, retried while a reader has it open (Windows refuses to replace an
  open file). It never falls back to writing in place: that is what lets a
  reader see half a file.
* `read_json` -- one short read, retried while the file is being replaced.

Nothing here touches the filesystem at import.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

LOCK_TIMEOUT_S = 10.0
REPLACE_TIMEOUT_S = 1.0


@contextmanager
def file_lock(path: Path, timeout: float = LOCK_TIMEOUT_S):
    """Hold the cross-process lock for `path` (on the sidecar ``<name>.lock``)."""
    fd = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise TimeoutError(f"could not lock {path.name} within {timeout:g} s")
            time.sleep(0.005)
        yield
    finally:
        os.close(fd)                    # closing releases the lock


def atomic_write_text(path: Path, text: str, timeout: float = REPLACE_TIMEOUT_S) -> None:
    """Replace `path` with `text` in one step. Retries for `timeout` seconds
    while a reader holds the file; raises OSError if it still cannot."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
        deadline = time.monotonic() + timeout
        while True:
            try:
                tmp.replace(path)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path, attempts: int = 5, delay: float = 0.02):
    """The parsed file, from one read. Retried briefly on a read error or a
    parse error (a replace in progress); a missing file raises at once."""
    for attempt in range(attempts):
        try:
            return json.loads(path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
