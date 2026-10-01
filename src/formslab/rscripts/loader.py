"""Find, load and run rScripts.

An rScript is a file ``<name>.py`` in an rScripts directory with a
module-level ``def rScript(forms):``. The host loads a list of them by name,
then calls `tick` once per loop, which calls each script's ``rScript``.

Directories are searched in order, first match wins:

1. ``$FORMSLAB_RSCRIPTS_DIR`` (``os.pathsep``-separated)
2. ``<cwd>/rScripts``
3. the ``rScripts/`` beside ``src/`` in a formsLabCLI checkout

Scripts are loaded by file path, not through ``sys.path``, so nothing about
where the host was started from decides which file runs.

Two module-level flags a script may set:

    enable = False            never loaded (checked before import)
    requires = ("forms",)     needs a real FORMS handle; skipped on LabForms

and one optional hook:

    def rShutdown(forms):     called once when the host stops, however it stops
                              (end of plan, `end`, a crash), last loaded first.
                              Where a script leaves its hardware safe.
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


def _log(forms, message: str, level: str = "INFO") -> None:
    forms.log(message=message, level=level, component="rScript")


def _needs_forms(exc: BaseException) -> bool:
    missing = getattr(exc, "name", None) or ""
    return isinstance(exc, ModuleNotFoundError) and (missing == "forms" or missing.startswith("forms."))


def load(forms, names) -> list[str]:
    """Load the named rScripts, replacing any loaded before. Returns the names
    that loaded. Nothing runs until `tick`."""
    _loaded.clear()
    _shutdown.clear()
    _last_error.clear()
    lab = getattr(forms, "is_lab_handle", False)

    for raw in names:
        name = raw[:-3] if raw.endswith(".py") else raw
        if name in disabled:
            _log(forms, f"{name}: disabled, not loaded")
            continue
        path = find(name)
        if path is None:
            where = ", ".join(str(d) for d in search_dirs()) or "no rScripts directory found"
            _log(forms, f"{name}: not found (searched {where})", "ERROR")
            continue
        if _STATIC_DISABLE.search(path.read_text(encoding="utf-8", errors="ignore")):
            _log(forms, f"{name}: enable = False, not loaded")
            continue

        module_name = f"rScripts.{name}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(module_name, None)
            if _needs_forms(exc):
                _log(forms, f"{name}: needs FORMS, which is not installed; skipped", "WARNING")
            else:
                _log(forms, f"{name}: import failed\n{traceback.format_exc()}", "ERROR")
            continue

        if lab and "forms" in tuple(getattr(module, "requires", ())):
            _log(forms, f"{name}: requires a FORMS handle; skipped on this lab run", "WARNING")
            continue
        func = getattr(module, "rScript", None)
        if not callable(func):
            _log(forms, f"{name}: no rScript(forms) function; skipped", "WARNING")
            continue
        _loaded.append((name, func))
        if callable(hook := getattr(module, "rShutdown", None)):
            _shutdown.append((name, hook))
        _log(forms, f"{name}: loaded from {path}")

    return [n for n, _ in _loaded]


def loaded() -> list[str]:
    return [n for n, _ in _loaded]


def tick(forms) -> None:
    """Call every loaded, enabled rScript once, in load order.

    A failing script is logged when its error first appears or changes, not on
    every loop, and again when it recovers.
    """
    for name, func in _loaded:
        if name in disabled:
            continue
        try:
            func(forms)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            if _last_error.get(name) != msg:
                _last_error[name] = msg
                _log(forms, f"{name}: {msg}\n{traceback.format_exc()}", "ERROR")
        else:
            if _last_error.pop(name, None) is not None:
                _log(forms, f"{name}: recovered")


def shutdown(forms) -> None:
    """Run every loaded script's ``rShutdown(forms)``, last loaded first, once.
    A failing hook is logged and the rest still run."""
    hooks = list(reversed(_shutdown))
    _shutdown.clear()
    for name, hook in hooks:
        try:
            hook(forms)
        except Exception as exc:
            _log(forms, f"{name}: rShutdown failed: {type(exc).__name__}: {exc}\n"
                        f"{traceback.format_exc()}", "ERROR")
