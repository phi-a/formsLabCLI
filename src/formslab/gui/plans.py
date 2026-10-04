"""Plan files for the GUI's editor.

A plan is named by a short name and found only through the server's own plan
list (`discover`), never through a path from a request. Plans that ship with the
checkout, or sit in the folder `labcli` was started from, are read-only here;
a copy saved from the editor goes in this machine's own plans folder
(``<config>/plans``), which is searched after the others -- so a copy can never
change what `run <name>` does for everyone, and a name already taken by any plan
is refused.

Saving writes the file in one step and refuses to overwrite a file that changed
since it was opened (compared by a hash of its contents). Deleting moves the file
into ``<config>/plans/.trash`` rather than erasing it.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from formslab.console.safefile import atomic_write_text
from formslab.sequence.plan import ENV, SUFFIX, check_text, discover, user_plans_dir

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class PlanFileError(Exception):
    """A refusal, with the HTTP status the server should answer it with."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def editable_dirs() -> set[Path]:
    """Where a plan may be changed in place: this machine's own folder and any
    folder $FORMSLAB_PLANS_DIR names (a bench keeps its own copies there)."""
    dirs = {user_plans_dir().resolve()}
    for p in (os.environ.get(ENV) or "").split(os.pathsep):
        if p:
            dirs.add(Path(p).expanduser().resolve())
    return dirs


def is_editable(path: Path) -> bool:
    return path.resolve().parent in editable_dirs()


def find(name: str) -> Path | None:
    """The plan called `name`, as the host would find it (first match wins)."""
    return next((p for p in discover() if p.stem == name), None)


def read(name: str) -> dict:
    path = find(name) if NAME.match(name or "") else None
    if path is None:
        raise PlanFileError(404, f"no plan {name!r}")
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")      # a Windows checkout may have CRLF
    return {"name": name, "text": text, "hash": content_hash(data), "editable": is_editable(path),
            "errors": [{"line": n, "message": m} for n, m in check_text(text)]}


def save(name: str, text: str, base_hash: str | None, as_new: bool) -> dict:
    """Write plan `text`. `as_new` makes a new plan in this machine's folder;
    otherwise the named editable plan is overwritten, if it is unchanged since
    `base_hash`. A draft with mistakes is saved too: the errors come back."""
    if not NAME.match(name or ""):
        raise PlanFileError(400, "a plan name is letters, digits, - and _ (at most 64, starting with a letter or digit)")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    existing = find(name)
    if as_new:
        if existing is not None:
            raise PlanFileError(409, f"a plan named {name!r} already exists; choose another name")
        target = user_plans_dir() / f"{name}{SUFFIX}"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        if existing is None:
            raise PlanFileError(404, f"no plan {name!r}")
        if not is_editable(existing):
            raise PlanFileError(403, f"{name!r} ships with formsLabCLI and is read-only here; "
                                     "use Save as to make your own copy")
        if base_hash != content_hash(existing.read_bytes()):
            raise PlanFileError(409, f"{name!r} changed on disk since you opened it; reload it "
                                     "(or save under another name) so nothing is overwritten")
        target = existing
    atomic_write_text(target, text)
    return {"name": name, "hash": content_hash(text.encode("utf-8")), "editable": True,
            "errors": [{"line": n, "message": m} for n, m in check_text(text)]}


def trash_dir() -> Path:
    return user_plans_dir() / ".trash"


def delete(name: str, base_hash: str | None) -> dict:
    """Move an editable plan to the trash (never a shipped one), if it is
    unchanged since `base_hash`. It is kept as ``<name>_<UTC>.plan`` there and
    can be put back by moving it out again."""
    path = find(name) if NAME.match(name or "") else None
    if path is None:
        raise PlanFileError(404, f"no plan {name!r}")
    if not is_editable(path):
        raise PlanFileError(403, f"{name!r} ships with formsLabCLI and cannot be deleted here")
    if base_hash != content_hash(path.read_bytes()):
        raise PlanFileError(409, f"{name!r} changed on disk since you opened it; reload it first")
    trash_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = trash_dir() / f"{name}_{stamp}{SUFFIX}"
    shutil.move(str(path), str(target))
    return {"deleted": name, "trash": str(target)}
