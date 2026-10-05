"""Plan, block and orbit files for the GUI's editor.

The editor opens three kinds of file: plans (`.plan`, sequence.plan), blocks
(`.block`, sequence.block: steps a plan calls by name) and orbits (`.orbit`,
orbit.file). They share one list and one set of names, so a name says which
file it is. A plan is named by a short name and found only through the server's own plan
list (`discover`), never through a path from a request. Plans that ship with the
checkout, or sit in the folder `labcli` was started from, are read-only here;
a copy saved from the editor goes in this machine's own plans folder
(``<config>/plans``), which is searched after the others -- so a copy can never
change what `run <name>` does for everyone, and a name already taken by any plan
is refused.

*Edit* moves a shipped file into this machine's folder, so it can be changed;
*Ship* moves one of yours into the checkout's ``plans/``, read-only again. A name
is only ever in one place, so neither shadows anything.

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
from formslab.orbit import file as orbitfile
from formslab.sequence import block as blockfile
from formslab.sequence.plan import ENV, PACKAGE_ROOT, SUFFIX, discover, review, user_plans_dir

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SUFFIXES = {"plan": SUFFIX, "block": blockfile.SUFFIX, "orbit": orbitfile.SUFFIX}
KINDS = {suffix: kind for kind, suffix in SUFFIXES.items()}


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


def shipped_dir() -> Path:
    """The checkout's own ``plans/``, where *Ship* puts a file."""
    return PACKAGE_ROOT.parents[1] / "plans"


def find(name: str) -> Path | None:
    """The plan, block or orbit called `name`, as the host would find it (first
    match wins; a plan, then a block, then an orbit of the same name)."""
    return next((p for p in [*discover(), *blockfile.discover(), *orbitfile.discover()]
                 if p.stem == name), None)


def kind_of(path: Path) -> str:
    return KINDS.get(path.suffix, "plan")


def read(name: str) -> dict:
    path = find(name) if NAME.match(name or "") else None
    if path is None:
        raise PlanFileError(404, f"no plan {name!r}")
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n")      # a Windows checkout may have CRLF
    return {"name": name, "kind": kind_of(path), "text": text, "hash": content_hash(data),
            "editable": is_editable(path), **problems(text, kind_of(path))}


def problems(text: str, kind: str = "plan") -> dict:
    """{errors, warnings}: [{line, message}] each (see sequence.plan.review,
    sequence.block.review and orbit.file.review)."""
    errors, warnings = {"orbit": orbitfile.review, "block": blockfile.review}.get(kind, review)(text)
    return {"errors": [{"line": n, "message": m} for n, m in errors],
            "warnings": [{"line": n, "message": m} for n, m in warnings]}


def save(name: str, text: str, base_hash: str | None, as_new: bool, kind: str = "plan") -> dict:
    """Write plan `text`. `as_new` makes a new file of this `kind` (plan, block or orbit)
    in this machine's folder; otherwise the named editable file is overwritten, if
    it is unchanged since `base_hash`, and keeps its kind. A draft with mistakes is
    saved too: the errors come back."""
    if not NAME.match(name or ""):
        raise PlanFileError(400, "a plan name is letters, digits, - and _ (at most 64, starting with a letter or digit)")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    existing = find(name)
    if as_new:
        if existing is not None:
            raise PlanFileError(409, f"a plan, block or orbit named {name!r} already exists; choose another name")
        if kind not in SUFFIXES:
            raise PlanFileError(400, f"a file is a plan, a block or an orbit, not {kind!r}")
        target = user_plans_dir() / f"{name}{SUFFIXES[kind]}"
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
    return {"name": name, "kind": kind_of(target), "hash": content_hash(text.encode("utf-8")),
            "editable": True, **problems(text, kind_of(target))}


def trash_dir() -> Path:
    return user_plans_dir() / ".trash"


def delete(name: str, base_hash: str | None) -> dict:
    """Move an editable plan to the trash (never a shipped one), if it is
    unchanged since `base_hash`. It is kept as ``<name>_<UTC>.plan`` (or
    ``.orbit``) there and
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
    target = trash_dir() / f"{name}_{stamp}{path.suffix}"
    shutil.move(str(path), str(target))
    return {"deleted": name, "trash": str(target)}


def _move(name: str, base_hash: str | None, shipped: bool) -> tuple[Path, Path]:
    """The file `name` and where it goes: out of the shipped files (`shipped`
    True, *Edit*) or into them (*Ship*); refused if it is not there now, has
    changed since `base_hash`, or the target is taken."""
    path = find(name) if NAME.match(name or "") else None
    if path is None:
        raise PlanFileError(404, f"no plan {name!r}")
    if is_editable(path) == shipped:
        hidden = user_plans_dir() / path.name
        raise PlanFileError(409, f"{name!r} is already yours" if shipped
                            else f"a shipped {name!r} is in {path.parent}, and your copy, {hidden}, is "
                                 "hidden behind it; move or delete one of them" if hidden.exists()
                            else f"{name!r} is already shipped")
    if base_hash != content_hash(path.read_bytes()):
        raise PlanFileError(409, f"{name!r} changed on disk since you opened it; reload it first")
    target = (user_plans_dir() if shipped else shipped_dir()) / path.name
    if target.exists():
        raise PlanFileError(409, f"{target} already exists; move or delete it first")
    return path, target


def _moved(path: Path, target: Path, name: str) -> dict:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    except OSError as e:
        raise PlanFileError(403, f"could not move {name!r} to {target.parent}: {e}") from None
    return read(name)


def edit(name: str, base_hash: str | None) -> dict:
    """Move a shipped file into this machine's folder, so it can be changed. It
    keeps its name, and `run <name>` still finds it."""
    return _moved(*_move(name, base_hash, shipped=True), name)


def ship(name: str, base_hash: str | None) -> dict:
    """Move one of your files into the checkout's ``plans/``, read-only again. A
    file with errors is refused: a shipped plan must run."""
    path, target = _move(name, base_hash, shipped=False)
    text = path.read_text(encoding="utf-8")
    if errors := problems(text, kind_of(path))["errors"]:
        raise PlanFileError(409, f"{name!r} has {len(errors)} problem(s); fix them before shipping it")
    return _moved(path, target, name)
