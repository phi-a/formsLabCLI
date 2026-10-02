"""Lab plans: `.forms` documents that run rScripts against the bench.

A lab plan uses the `.forms` assignment grammar -- literal ``block.field =
value`` assignments and an explicit ``sequence.operations`` list -- plus
``rscripts.load``, the scripts that own the instruments. Reading is static: the
file is parsed, never executed. (FORMS itself refuses ``rscripts.load``, so a
lab plan is formsLabCLI's alone.)

    mission.name = "psu1_smtc08_first"
    rscripts.load = ["rPSU", "rSMTC08"]
    recording.interval = 2
    recording.unit = "seconds"
    sequence.operations = [
        {"command": "psu1", "request": {"1": {"voltage": 1.0, "current": 0.1}}},
        {"command": "psu1", "request": {"1": {"on": True}}},
        {"hold": 60, "units": "seconds"},
        {"until": "TC01", "above": 30.0, "unit": "C", "timeout_s": 600},
        {"log": "done"},
    ]

Operations, one verb each:

    hold      run the loaded rScripts for a duration (``units``: seconds,
              minutes, hours)
    command   write a CAST request to a label and wait (``timeout_s``, default
              10) until the rScript that owns the label takes it
    cast      the same, written as the cast tab's words: {"cast": "hvc pump on"}
              (the owning rScript's grammar builds and checks the request)
    until     run until a variable is above/below a value, or fail after
              ``timeout_s`` (required: a wait on hardware always has a limit).
              ``unit`` converts between C and K when it differs from the
              variable's own
    log       one line in the run log

Anything that needs an orbit (``propagate``, ``orbit.*``, ``@procedure``...) is
refused with a pointer to FORMS: a lab plan runs on the wall clock.
"""
from __future__ import annotations

import ast
import math
import os
from dataclasses import dataclass
from pathlib import Path

from formslab.config import PACKAGE_ROOT
from formslab.sequence.spec import Segment, Sequence

ENV = "FORMSLAB_PLANS_DIR"
SUFFIX = ".forms"

_FIELDS = {
    "mission": {"name", "format", "description"},
    "rscripts": {"load"},
    "recording": {"interval", "unit"},
    "sequence": {"operations"},
}
# Blocks that only mean something with an orbit behind them.
_FORMS_BLOCKS = {
    "orbit", "time", "propagator", "satellite", "environment", "attitude",
    "numerics", "procedures", "dynamics", "triggers", "report", "requirements",
    "routines", "planet",
}
_UNITS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
_VERBS = {
    "hold": {"hold", "units"},
    "command": {"command", "request", "timeout_s"},
    "cast": {"cast", "timeout_s"},
    "until": {"until", "above", "below", "unit", "timeout_s"},
    "log": {"log"},
}
COMMAND_TIMEOUT_S = 10.0


class PlanError(ValueError):
    """The document is not a runnable lab plan. The message says where and why."""


@dataclass(frozen=True)
class Plan:
    name: str
    path: Path | None
    rscripts: tuple[str, ...]
    record_interval: float
    record_unit: str
    sequence: Sequence


# --- finding plans -----------------------------------------------------------

def search_dirs() -> list[Path]:
    """``$FORMSLAB_PLANS_DIR``, then ``<cwd>/plans``, then the checkout's
    ``plans/`` -- the same order rScripts are found in."""
    dirs: list[Path] = []
    env = os.environ.get(ENV)
    if env:
        dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    dirs += [Path.cwd() / "plans", PACKAGE_ROOT.parents[1] / "plans"]
    out, seen = [], set()
    for d in dirs:
        if d.is_dir() and (key := str(d.resolve())) not in seen:
            seen.add(key)
            out.append(d)
    return out


def find_plan(target: str) -> Path | None:
    """A path to a plan file, or a plan name looked up in `search_dirs`."""
    path = Path(target).expanduser()
    if path.suffix == SUFFIX and path.is_file():
        return path
    stem = target[: -len(SUFFIX)] if target.endswith(SUFFIX) else target
    for d in search_dirs():
        candidate = d / f"{stem}{SUFFIX}"
        if candidate.is_file():
            return candidate
    return None


def discover() -> list[Path]:
    seen, out = set(), []
    for d in search_dirs():
        for p in sorted(d.glob(f"*{SUFFIX}")):
            if p.stem not in seen and is_lab_plan(p):
                seen.add(p.stem)
                out.append(p)
    return out


def is_lab_plan(path) -> bool:
    """True when a `.forms` document declares ``rscripts.load`` -- the field
    that makes it formsLabCLI's to run rather than FORMS'."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    return any(_target(node) == ("rscripts", "load") for node in tree.body
               if isinstance(node, ast.Assign))


# --- reading -----------------------------------------------------------------

def load_plan(path) -> Plan:
    path = Path(path)
    return parse_plan(path.read_text(encoding="utf-8"), path=path)


def parse_plan(source: str, *, path: Path | None = None) -> Plan:
    where = path.name if path else "<plan>"
    try:
        tree = ast.parse(source, filename=where)
    except SyntaxError as e:
        raise PlanError(f"{where}:{e.lineno}: {e.msg}") from None

    values: dict[tuple[str, str], object] = {}
    for node in tree.body:
        line = f"{where}:{node.lineno}"
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # a docstring
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            deco = f"@{ast.unparse(node.decorator_list[0])}" if node.decorator_list else "def"
            raise PlanError(f"{line}: `{deco} {node.name}` is FORMS mission code; "
                            "a lab plan has no definitions")
        target = _target(node) if isinstance(node, ast.Assign) else None
        if target is None:
            raise PlanError(f"{line}: only literal `block.field = value` assignments belong in a plan")
        block, field = target
        dotted = f"{block}.{field}"
        if block in _FORMS_BLOCKS:
            raise PlanError(f"{line}: `{dotted}` is FORMS mission configuration. A lab plan "
                            "runs on the wall clock with no orbit; run this document in FORMS "
                            "without rscripts.load")
        if field not in _FIELDS.get(block, ()):
            allowed = ", ".join(f"{b}.{f}" for b, fs in _FIELDS.items() for f in sorted(fs))
            raise PlanError(f"{line}: `{dotted}` is not a lab plan field (allowed: {allowed})")
        if target in values:
            raise PlanError(f"{line}: `{dotted}` is assigned twice")
        try:
            values[target] = ast.literal_eval(node.value)
        except ValueError:
            raise PlanError(f"{line}: `{dotted}` must be a literal value") from None

    name = values.get(("mission", "name")) or (path.stem if path else "plan")
    fmt = values.get(("mission", "format"), 1)
    if fmt != 1:
        raise PlanError(f"{where}: mission.format {fmt!r} is not supported (1 is)")

    scripts = values.get(("rscripts", "load"))
    if not isinstance(scripts, list) or not scripts or not all(isinstance(s, str) for s in scripts):
        raise PlanError(f"{where}: rscripts.load must be a non-empty list of rScript names")
    scripts = tuple(s[:-3] if s.endswith(".py") else s for s in scripts)

    interval = values.get(("recording", "interval"), 10)
    unit = values.get(("recording", "unit"), "seconds")
    if unit not in _UNITS:
        raise PlanError(f"{where}: recording.unit must be one of {sorted(_UNITS)}")
    if not _positive(interval):
        raise PlanError(f"{where}: recording.interval must be a positive number")

    ops = values.get(("sequence", "operations"))
    if not isinstance(ops, list) or not ops:
        raise PlanError(f"{where}: sequence.operations must be a non-empty list")
    segments = tuple(_segment(f"{where}: sequence.operations[{i}]", op) for i, op in enumerate(ops))

    return Plan(name=str(name), path=path, rscripts=scripts, record_interval=float(interval),
                record_unit=unit, sequence=Sequence(name=str(name), segments=segments,
                                                    rscripts=scripts))


def _target(node: ast.Assign) -> tuple[str, str] | None:
    if len(node.targets) != 1:
        return None
    t = node.targets[0]
    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name):
        return t.value.id, t.attr
    return None


def _positive(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _segment(where: str, op) -> Segment:
    if not isinstance(op, dict):
        raise PlanError(f"{where}: an operation is a dict, got {op!r}")
    for forms_verb in ("propagate", "call", "observe"):
        if forms_verb in op:
            raise PlanError(f"{where}: `{forms_verb}` is a FORMS mission operation; "
                            f"lab plans support {sorted(_VERBS)}")
    verbs = [v for v in _VERBS if v in op]
    if len(verbs) != 1:
        raise PlanError(f"{where}: needs exactly one of {sorted(_VERBS)}, got {sorted(op)}")
    verb = verbs[0]
    extra = set(op) - _VERBS[verb]
    if extra:
        raise PlanError(f"{where}: `{verb}` does not take {sorted(extra)} "
                        f"(it takes {sorted(_VERBS[verb] - {verb})})")
    return {"hold": _hold, "command": _command, "cast": _cast, "until": _until,
            "log": _log}[verb](where, op)


def _hold(where, op) -> Segment:
    units = op.get("units", "seconds")
    if units not in _UNITS:
        raise PlanError(f"{where}: hold units must be one of {sorted(_UNITS)}")
    if not _positive(op["hold"]):
        raise PlanError(f"{where}: hold needs a positive duration")
    seconds = float(op["hold"]) * _UNITS[units]
    return Segment("hold", {"seconds": seconds}, label=f"hold {op['hold']:g} {units}")


def _command(where, op) -> Segment:
    from formslab.state import build_default_cast_state

    label = op["command"]
    labels = sorted(build_default_cast_state())
    if label not in labels:
        raise PlanError(f"{where}: `{label}` is not a CAST label (known: {labels})")
    request = op.get("request")
    if not isinstance(request, dict) or not request:
        raise PlanError(f"{where}: command needs a non-empty `request` dict")
    timeout = op.get("timeout_s", COMMAND_TIMEOUT_S)
    if not _positive(timeout):
        raise PlanError(f"{where}: timeout_s must be a positive number")
    return Segment("command", {"label": label, "request": request, "timeout_s": float(timeout)},
                   label=f"command {label} {request}")


def _cast(where, op) -> Segment:
    """A cast-tab command, e.g. "hvc pump on": the owning rScript's grammar
    turns it into the request (checked now, while the plan is read), and it is
    sent and awaited like `command`."""
    from formslab.rscripts import cast

    words = op["cast"].split() if isinstance(op["cast"], str) else []
    if len(words) < 1:
        raise PlanError(f"{where}: cast takes the words typed in the cast tab, e.g. \"hvc pump on\"")
    try:
        request = cast.request(words[0], words[1:])
    except cast.CastUsage as e:
        raise PlanError(f"{where}: {e}") from None
    seg = _command(where, {"command": words[0], "request": request,
                           **({"timeout_s": op["timeout_s"]} if "timeout_s" in op else {})})
    return Segment("command", seg.params, label=f"cast {op['cast']}")


def _until(where, op) -> Segment:
    var = op["until"]
    if not isinstance(var, str) or not var:
        raise PlanError(f"{where}: until names a variable")
    sides = [s for s in ("above", "below") if s in op]
    if len(sides) != 1:
        raise PlanError(f"{where}: until needs exactly one of above / below")
    side = sides[0]
    value = op[side]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise PlanError(f"{where}: `{side}` must be a number")
    if "timeout_s" not in op:
        raise PlanError(f"{where}: until needs timeout_s; a wait on hardware always has a limit")
    if not _positive(op["timeout_s"]):
        raise PlanError(f"{where}: timeout_s must be a positive number")
    unit = op.get("unit")
    shown = f" {unit}" if unit else ""
    return Segment("until", {"variable": var, "side": side, "value": float(value), "unit": unit,
                             "timeout_s": float(op["timeout_s"])},
                   label=f"until {var} {side} {value:g}{shown} (limit {op['timeout_s']:g} s)")


def _log(where, op) -> Segment:
    if not isinstance(op["log"], str):
        raise PlanError(f"{where}: log takes a string")
    return Segment("log", {"message": op["log"]}, label=f"log {op['log']!r}")
