"""Find, load and run rScripts.

An rScript is a file ``<name>.py`` in an rScripts directory with a
module-level ``def rScript(run):``. The host loads a list of them by name,
then calls `tick` once per loop, which calls each script's ``rScript``.

Directories are searched in order, first match wins:

1. ``$FORMSLAB_RSCRIPTS_DIR`` (``os.pathsep``-separated)
2. ``<cwd>/rScripts``
3. the ``rScripts/`` beside ``src/`` in a formsLabCLI checkout

Scripts are loaded by file path, not through ``sys.path``, so nothing about
where the host was started from decides which file runs.

One module-level flag a script may set:

    enable = False            never loaded (checked before import)

and one optional hook:

    def rShutdown(run):     called once when the host stops, however it stops
                              (end of plan, `end`, a crash), last loaded first.
                              Where a script leaves its hardware safe.
    SHUTDOWN_BEFORE = ("rPSU",)
                            the scripts this one's rShutdown must run before,
                              whatever the load order: a script that asks another
                              for its supply shuts down first.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import traceback
from pathlib import Path

from formslab.config import PACKAGE_ROOT

ENV = "FORMSLAB_RSCRIPTS_DIR"
_STATIC_DISABLE = re.compile(r"(?mi)^enable\s*=\s*false\b")

_loaded: list[tuple[str, object]] = []   # (name, rScript function), run order
_shutdown: list[tuple[str, object]] = []  # (name, rShutdown function), load order
disabled: set[str] = set()               # disabled at runtime, by name
_last_error: dict[str, str] = {}


def search_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get(ENV)
    if env:
        dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    dirs.append(Path.cwd() / "rScripts")
    dirs.append(PACKAGE_ROOT.parents[1] / "rScripts")
    seen, out = set(), []
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen and d.is_dir():
            seen.add(key)
            out.append(d)
    return out


def find(name: str) -> Path | None:
    for d in search_dirs():
        path = d / f"{name}.py"
        if path.is_file():
            return path
    return None


def _log(run, message: str, level: str = "INFO") -> None:
    run.log(message=message, level=level, component="rScript")


def load(run, names) -> list[str]:
    """Load the named rScripts, replacing any loaded before. Returns the names
    that loaded. Nothing runs until `tick`."""
    _loaded.clear()
    _shutdown.clear()
    _last_error.clear()

    for raw in names:
        name = raw[:-3] if raw.endswith(".py") else raw
        if name in disabled:
            _log(run, f"{name}: disabled, not loaded")
            continue
        path = find(name)
        if path is None:
            where = ", ".join(str(d) for d in search_dirs()) or "no rScripts directory found"
            _log(run, f"{name}: not found (searched {where})", "ERROR")
            continue
        if _STATIC_DISABLE.search(path.read_text(encoding="utf-8", errors="ignore")):
            _log(run, f"{name}: enable = False, not loaded")
            continue

        module_name = f"rScripts.{name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            _log(run, f"{name}: import failed\n{traceback.format_exc()}", "ERROR")
            continue

        func = getattr(module, "rScript", None)
        if not callable(func):
            _log(run, f"{name}: no rScript(run) function; skipped", "WARNING")
            continue
        _loaded.append((name, func))
        if callable(hook := getattr(module, "rShutdown", None)):
            _shutdown.append((name, hook))
        _log(run, f"{name}: loaded from {path}")

    return [n for n, _ in _loaded]


def _ordered(hooks):
    """`hooks` with each script moved before the ones its SHUTDOWN_BEFORE names."""
    names = [n for n, _ in hooks]
    for _ in range(len(hooks)):                      # settles in at most one pass per script
        moved = False
        for name, hook in list(hooks):
            module = sys.modules.get(f"rScripts.{name}")
            for other in getattr(module, "SHUTDOWN_BEFORE", ()):
                if other in names and names.index(other) < names.index(name):
                    hooks.remove((name, hook))
                    hooks.insert(names.index(other), (name, hook))
                    names = [n for n, _ in hooks]
                    moved = True
        if not moved:
            break
    return hooks


def loaded() -> list[str]:
    return [n for n, _ in _loaded]


def call(run, name: str, func) -> None:
    """Call one rScript once. A failing script is logged when its error first
    appears or changes, not on every call, and again when it recovers."""
    if name in disabled:
        return
    try:
        func(run)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        if _last_error.get(name) != msg:
            _last_error[name] = msg
            _log(run, f"{name}: {msg}\n{traceback.format_exc()}", "ERROR")
    else:
        if _last_error.pop(name, None) is not None:
            _log(run, f"{name}: recovered")


def reports_results(label: str) -> bool:
    """True when a loaded rScript answers `label`'s requests with a result
    (it lists the label in RESULT_LABELS), so a sender can wait for ok or why not."""
    for name, _ in _loaded:
        module = sys.modules.get(f"rScripts.{name}")
        if label.lower() in (str(x).lower() for x in getattr(module, "RESULT_LABELS", ())):
            return True
    return False


def scripts() -> list[tuple[str, object]]:
    """The loaded (name, rScript function) pairs, in load order."""
    return list(_loaded)


def tick(run) -> None:
    """Call every loaded, enabled rScript once, in load order, on this thread.
    (The host runs each script on its own thread instead -- see `workers`.)"""
    for name, func in _loaded:
        call(run, name, func)


def shutdown(run) -> None:
    """Run every loaded script's ``rShutdown(run)``, last loaded first, once; a
    script that names others in SHUTDOWN_BEFORE runs before them, whatever the
    load order. A failing hook is logged and the rest still run."""
    hooks = _ordered(list(reversed(_shutdown)))
    _shutdown.clear()
    for name, hook in hooks:
        try:
            hook(run)
        except Exception as exc:
            _log(run, f"{name}: rShutdown failed: {type(exc).__name__}: {exc}\n"
                        f"{traceback.format_exc()}", "ERROR")
