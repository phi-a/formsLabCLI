"""Cast commands declared by rScripts.

An rScript that owns CAST labels declares its console commands next to the code
that applies them -- plain data plus one pure function:

    CAST_LABELS = ("hvc",)
    CAST_HELP = [("hvc platen <C>", "Platen setpoint, C"), ...]    # (usage, meaning)

    def cast_request(label, words):        # words typed after the label
        ...                                # -> the request dict, or raise CastUsage

Two processes use them. The console's cast panel imports the script (importing
must not touch hardware), turns `hvc vent open` into ``{"vent": "open"}`` and
writes it to CAST. The host runs the same script, which reads the request back
from CAST and applies it. So a command means the same thing typed in the panel,
written by a lab plan, or sent by any other CAST writer.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from formslab.rscripts.loader import search_dirs


class CastUsage(ValueError):
    """The words do not make a request; the message says what would."""


# --- helpers for cast_request implementations ----------------------------------

def number(word: str, what: str, lo: float | None = None, hi: float | None = None) -> float:
    try:
        v = float(word)
    except ValueError:
        raise CastUsage(f"{what}: {word!r} is not a number") from None
    if not math.isfinite(v):
        raise CastUsage(f"{what}: {word!r} is not a number")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise CastUsage(f"{what} {v:g} is outside {lo:g}..{hi:g}")
    return v


def integer(word: str, what: str, lo: int | None = None, hi: int | None = None) -> int:
    v = number(word, what, lo, hi)
    if v != int(v):
        raise CastUsage(f"{what}: {word!r} is not a whole number")
    return int(v)


def choice(word: str, words: tuple[str, str], what: str) -> bool:
    """True for words[0], False for words[1] (e.g. ("on", "off"))."""
    w = word.lower()
    if w not in words:
        raise CastUsage(f"{what}: expected {' or '.join(words)}, got {word!r}")
    return w == words[0]


# --- discovery ---------------------------------------------------------------------

_cache: dict[Path, tuple[float, object]] = {}


def _import(path: Path):
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    spec = importlib.util.spec_from_file_location(f"castspec.{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _cache[path] = (mtime, module)
    return module


def owners() -> tuple[dict[str, object], dict[str, str]]:
    """({label: rScript module}, {script: import error}) for every rScript on
    the search path that declares CAST_LABELS. First match per name wins, as
    for the loader."""
    seen, labels, errors = set(), {}, {}
    for d in search_dirs():
        for path in sorted(d.glob("r*.py")):
            if path.stem in seen:
                continue
            seen.add(path.stem)
            try:
                module = _import(path)
            except Exception as exc:          # a broken script must not break the panel
                errors[path.stem] = f"{type(exc).__name__}: {exc}"
                continue
            for label in getattr(module, "CAST_LABELS", ()):
                labels.setdefault(label, module)
    return labels, errors


def request(label: str, words: list[str]) -> dict:
    """The request dict for `label` from the words typed after it."""
    labels, _ = owners()
    module = labels.get(label)
    if module is None:
        raise CastUsage(f"no rScript declares CAST label {label!r} "
                        f"(declared: {', '.join(sorted(labels)) or 'none'})")
    req = module.cast_request(label, list(words))
    if not isinstance(req, dict) or not req:
        raise CastUsage(f"{label}: nothing to send")
    return req


def help_rows() -> list[tuple[str, str, str]]:
    """(script, usage, meaning) for every declared command."""
    labels, _ = owners()
    rows, done = [], set()
    for module in labels.values():
        if id(module) in done:
            continue
        done.add(id(module))
        script = module.__name__.split(".")[-1]
        rows += [(script, usage, meaning) for usage, meaning in getattr(module, "CAST_HELP", ())]
    return rows
