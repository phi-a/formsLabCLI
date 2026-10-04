"""The GUI's one login, and its sessions.

The credentials live in ``<config>/gui.json`` as a salted PBKDF2 hash, set with
`labcli gui --set-login`; nothing is in the source or the repo. A session is a
random token held in memory, so restarting the server logs everyone out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from formslab import config

FILE = "gui.json"
ITERATIONS = 600_000
SESSION_S = 8 * 3600
FAIL_DELAY_S = 1.0


def _path():
    return config.state_path(FILE)


def _hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def set_login(user: str, password: str) -> None:
    """Store the login. The file is private to the account on POSIX; Windows
    ignores the mode, so there it relies on the profile's own permissions."""
    if not user or not password:
        raise ValueError("a login needs a user name and a password")
    salt = os.urandom(16)
    record = {"user": user, "salt": salt.hex(), "iterations": ITERATIONS,
              "hash": _hash(password, salt, ITERATIONS).hex()}
    path = _path()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def configured() -> bool:
    return load() is not None


def load() -> dict | None:
    try:
        record = json.loads(_path().read_text(encoding="utf-8"))
        if all(k in record for k in ("user", "salt", "iterations", "hash")):
            return record
    except (OSError, ValueError):
        pass
    return None


_verify_lock = threading.Lock()


def check(user: str, password: str) -> bool:
    """True for the stored login. Attempts are serialised and a wrong one waits
    about a second: behind an SSH tunnel every client is 127.0.0.1, so a lockout
    would only lock out the lab, but the delay still makes guessing slow."""
    with _verify_lock:
        record = load()
        if record is None:
            ok = False
        else:
            digest = _hash(password, bytes.fromhex(record["salt"]), int(record["iterations"]))
            ok = (hmac.compare_digest(digest, bytes.fromhex(record["hash"]))
                  & hmac.compare_digest(user.encode(), record["user"].encode()))
        if not ok:
            time.sleep(FAIL_DELAY_S)
        return ok


class Sessions:
    """Session tokens in memory, each good for `SESSION_S` seconds."""

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def new(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._tokens = {t: v for t, v in self._tokens.items() if v[1] > now}
            self._tokens[token] = (user, now + SESSION_S)
        return token

    def user(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            entry = self._tokens.get(token)
            if entry and entry[1] > time.time():
                return entry[0]
            self._tokens.pop(token, None)
        return None

    def drop(self, token: str | None) -> None:
        with self._lock:
            self._tokens.pop(token, None)
