"""Lab plans: `.plan` files that run rScripts against the bench.

One step per line. ``load`` names the rScripts that own the instruments;
``record`` sets the CSV cadence; every other line is a step:

    # PSU1 CH1 on for a minute, thermocouples recording
    load rPSU rSMTC08
    record every 2 s

    psu1 ch1 set 1.0 0.1
    psu1 ch1 on
    hold 60 s
    until TC01 above 30 C timeout 10 min
    psu1 ch1 off
    log done

Steps:

    <label> <words>     a command to the rScript that owns the label -- the same
                        words as the cast tab (``hvc vent open``). Sent and
                        awaited until that rScript takes it (10 s limit)
    hold <n> s|min|h    run the loaded rScripts for a while
    hold until end      until the operator's ctrl `end` (plans/tvac.plan)
    until <variable> above|below <value> [C|K] timeout <n> s|min|h
                        run until a published value crosses a limit, or fail
                        at the timeout (required: a wait on hardware always has
                        a limit). C or K converts from the variable's own unit
    log <text>          one line in the run log

``#`` starts a comment on its own line. Commands and variable names come from
what the loaded rScripts declare (COMMANDS, VARIABLES), checked while the plan
is read; a mistake is reported with its line number before anything runs.
Orbit content (``orbit.*``, ``propagate``, ``@procedure``) is FORMS': a plan
runs on the wall clock.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from formslab.config import PACKAGE_ROOT, config_dir
from formslab.rscripts.grammar import Grammar, GrammarError
from formslab.sequence.spec import Segment, Sequence

ENV = "FORMSLAB_PLANS_DIR"
SUFFIX = ".plan"
COMMAND_TIMEOUT_S = 10.0
_SECONDS = {"s": 1.0, "min": 60.0, "h": 3600.0}
_RECORD_UNIT = {"s": "seconds", "min": "minutes", "h": "hours"}
_FORMS_VERBS = ("propagate", "call", "observe")
_GLUED = re.compile(r"^\d+(\.\d+)?(s|min|h)$", re.IGNORECASE)


class PlanError(ValueError):
    """The document is not a runnable lab plan. The message says where and why
    for the first problem; `errors` lists every one found as (line, message),
    line 0 meaning the document as a whole."""

    def __init__(self, message: str, errors: list[tuple[int, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors if errors is not None else [(0, message)]


class _LineError(Exception):
    """A problem on one line; the line is skipped and reading goes on."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line, self.message = line, message


@dataclass(frozen=True)
class Plan:
    name: str
    path: Path | None
    rscripts: tuple[str, ...]
    record_interval: float
    record_unit: str
    sequence: Sequence


# --- finding plans -----------------------------------------------------------

def user_plans_dir() -> Path:
    """This machine's own plans (the GUI saves here): ``<config>/plans``."""
    return config_dir() / "plans"


def search_dirs() -> list[Path]:
    """``$FORMSLAB_PLANS_DIR``, then ``<cwd>/plans``, then the checkout's
    ``plans/`` -- the same order rScripts are found in -- and last this
    machine's own folder, so a plan of the same name as a shipped one never
    changes what `run <name>` does for everyone."""
    dirs: list[Path] = []
    env = os.environ.get(ENV)
    if env:
        dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    dirs += [Path.cwd() / "plans", PACKAGE_ROOT.parents[1] / "plans", user_plans_dir()]
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
            if p.stem not in seen:
                seen.add(p.stem)
                out.append(p)
    return out


# --- reading -----------------------------------------------------------------

def load_plan(path) -> Plan:
    path = Path(path)
    return parse_plan(path.read_text(encoding="utf-8"), path=path)


def parse_plan(source: str, *, path: Path | None = None) -> Plan:
    """The plan in `source`. Every problem found is in `PlanError.errors` (the
    editor shows them all); the message is the first."""
    where = path.name if path else "<plan>"
    name = path.stem if path else "plan"
    errors: list[tuple[int, str]] = []

    def fail(n, message):
        raise _LineError(n, message)

    def attempt(fn, *args):
        """fn(*args), or None with the problem noted when it raises _LineError."""
        try:
            return fn(*args)
        except _LineError as e:
            errors.append((e.line, e.message))

    def done():
        if errors:
            errors.sort(key=lambda e: e[0])
            n, message = errors[0]
            raise PlanError(f"{where}:{n}: {message}" if n else f"{where}: {message}", list(errors))

    if "sequence.operations" in source:
        errors.append((0, "this is the old plan format (sequence.operations = [...]); "
                          "a plan is now one step per line -- see docs/SEQUENCE.md"))
        done()
    lines = [(n, line.strip()) for n, line in enumerate(source.splitlines(), 1)]
    lines = [(n, t) for n, t in lines if t and not t.startswith("#")]

    def forms_check(n, t):
        first = t.split()[0]
        if re.match(r"[A-Za-z_]\w*\.\w+\s*=", t):
            fail(n, f"`{t.split('=')[0].strip()}` is FORMS mission configuration. A plan runs on "
                    "the wall clock with no orbit; orbit work belongs in a FORMS mission")
        if t.startswith("@") or first == "def":
            fail(n, f"`{first}` is FORMS mission code; a plan has no definitions")
        if first.lower() in _FORMS_VERBS:
            fail(n, f"`{first}` is a FORMS mission operation; a plan's steps are commands, "
                    "hold, until and log")

    for n, t in lines:
        attempt(forms_check, n, t)
    flagged = {n for n, _ in errors}
    lines = [(n, t) for n, t in lines if n not in flagged]

    state = {"scripts": None, "record": (10.0, "seconds"), "steps": []}

    def classify(n, t):
        words = t.split()
        head = words[0].lower()
        if head in ("load", "record") and state["steps"]:
            fail(n, f"`{head}` goes before the first step")
        if head == "load":
            if state["scripts"] is not None:
                fail(n, "`load` appears twice; name every rScript on one line")
            if len(words) < 2:
                fail(n, "load names the rScripts to run, e.g. `load rLACO rSMTC08`")
            state["scripts"] = tuple(words[1:])
        elif head == "record":
            state["record"] = _parse(fail, n, _RECORD, words)
        else:
            if head != "log" and "#" in t:
                fail(n, "comments go on their own line")
            state["steps"].append((n, words))

    for n, t in lines:
        attempt(classify, n, t)
    scripts, steps, record = state["scripts"], state["steps"], state["record"]
    if scripts is None:                      # no grammar without it: stop here
        errors.append((0, "a plan starts with `load <rScript> ...`, the rScripts that "
                          "own its instruments"))
        done()
    if not steps and not errors:             # (a flagged line already explains an empty plan)
        errors.append((0, "the plan has no steps"))

    from formslab.rscripts import cast

    grammar, owner, published = _grammar(scripts)

    def check_step(n, words):
        head = words[0].lower()
        if module := owner.get(head):
            if cast.script_name(module) not in scripts:
                fail(n, f"{head} is declared by {cast.script_name(module)}; add it to `load`")
            if not cast.commands(module):
                fail(n, f"{head} takes no commands (it only reports readings)")
        if head == "until":
            if len(words) > 1 and words[1].lower() not in {v.lower() for v in published}:
                if by := _publisher(words[1], scripts):
                    fail(n, f"{words[1]} is published by {by}; add it to `load`")
                if not published:
                    fail(n, "no loaded rScript publishes a value to wait on")
            if "timeout" not in (w.lower() for w in words):
                fail(n, "until needs `timeout <n> s|min|h`: a wait on hardware always has a limit")
        return replace(_parse(fail, n, grammar, words), label=" ".join(words))

    segments = [seg for n, words in steps if (seg := attempt(check_step, n, words)) is not None]
    done()

    interval, unit = record
    return Plan(name=name, path=path, rscripts=scripts, record_interval=interval, record_unit=unit,
                sequence=Sequence(name=name, segments=tuple(segments), rscripts=scripts))


def check_text(text: str) -> list[tuple[int, str]]:
    """Every problem in plan `text` as (line, message), line 0 for the document
    as a whole; [] for a plan that can run. Also notes rScripts on the `load`
    line that cannot be found, which reading alone does not mind."""
    try:
        plan = parse_plan(text)
        errors: list[tuple[int, str]] = []
    except PlanError as e:
        plan, errors = None, list(e.errors)
    from formslab import rscripts

    for n, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if words and words[0].lower() == "load":
            errors += [(n, f"rScript {w} not found") for w in words[1:] if rscripts.find(w) is None]
    return sorted(errors, key=lambda e: e[0])


def available_rscripts() -> list[str]:
    """The rScripts a plan can `load`: every ``r*.py`` on the search path."""
    from formslab.rscripts import loader

    return sorted({p.stem for d in loader.search_dirs() for p in d.glob("r*.py")})


def line_options(scripts, words) -> dict:
    """For the plan editor: what can come at each position of a step line.

    {'positions': [options before word 0, before word 1, ..., after the last],
     'complete': the words are a whole step, 'error': why not, or None}. Each
    option is {kind, text, help, lo, hi, unit}. A line that is merely unfinished
    has no error: it is a valid start."""
    from formslab.rscripts import cast

    scripts, words = tuple(scripts), list(words)
    grammar, owner, _ = _grammar(scripts)
    positions = [[_option(o) for o in grammar.complete(words[:k])] for k in range(len(words) + 1)]
    result = {"positions": positions, "complete": False, "error": None}
    if not words:
        return result
    module = owner.get(words[0].lower())
    if module is not None and cast.script_name(module) not in scripts:
        result["error"] = f"{words[0].lower()} is declared by {cast.script_name(module)}; add it to `load`"
        return result
    try:
        grammar.parse(words)
        result["complete"] = True
    except GrammarError as e:
        if not positions[-1]:                                   # not even a valid start
            message = str(e)
            if glued := next((w for w in words if _GLUED.match(w)), None):
                number, unit = re.match(r"([\d.]+)(\D+)", glued).groups()
                message += f" (write `{number} {unit}`, with a space)"
            result["error"] = message
    return result


def _option(o) -> dict:
    return {"kind": o.kind, "text": o.text, "help": o.help, "lo": o.lo, "hi": o.hi, "unit": o.unit}


def _parse(fail, n, grammar, words):
    try:
        return grammar.parse(words)
    except GrammarError as e:
        message = str(e)
        if glued := next((w for w in words if _GLUED.match(w)), None):
            number, unit = re.match(r"([\d.]+)(\D+)", glued).groups()
            message += f" (write `{number} {unit}`, with a space)"
        fail(n, message)


# --- the plan's grammar --------------------------------------------------------------

def _positive(what, value):
    if value <= 0:
        raise GrammarError(f"{what} needs a positive duration")
    return value


def _hold(n, unit):
    return Segment("hold", {"seconds": _positive("hold", n) * _SECONDS[unit]})


def _until(units):
    def build(variable, side, value, *rest):
        unit, t, t_unit = rest if len(rest) == 3 else (None, *rest)
        own = units.get(variable)
        if unit and own not in ("C", "K"):
            raise GrammarError(f"{variable} is in {own or 'no unit'}; C and K only apply to temperatures")
        return Segment("until", {"variable": variable, "side": side, "value": value, "unit": unit,
                                 "timeout_s": _positive("timeout", t) * _SECONDS[t_unit]})
    return build


def _command(label, request):
    return Segment("command", {"label": label, "request": request, "timeout_s": COMMAND_TIMEOUT_S})


_RECORD = Grammar([("record every <n:number 0..> s|min|h", "CSV cadence",
                    lambda n, u: (_positive("record", n), _RECORD_UNIT[u]))])


def _grammar(scripts):
    """The plan's grammar for these rScripts, {label: owning module} for every
    label on the path, and {variable: unit} published by the loaded scripts."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    published = {}
    for module in {id(m): m for m in labels.values()}.values():
        if cast.script_name(module) in scripts:
            published.update(cast.variables(module))
    steps = [
        ("hold <n:number 0..> s|min|h", "Run the loaded rScripts for a while", _hold),
        ("hold until end", "Until ctrl `end` (manual operation)",
         lambda: Segment("hold", {"seconds": None})),
        ("log <message:rest>", "One line in the run log", lambda m: Segment("log", {"message": m})),
    ]
    if published:
        var = f"<variable:{'|'.join(published)}>"
        until = f"until {var} above|below <value:number>"
        steps += [
            (f"{until} timeout <t:number 0..> s|min|h", "Wait for a value, with a limit",
             _until(published)),
            (f"{until} C|K timeout <t:number 0..> s|min|h", "The same, the value in C or K",
             _until(published)),
        ]
    return Grammar(steps + cast.label_commands(scripts, build=_command)), labels, published


def _publisher(variable, scripts):
    """The script not in `scripts` that declares `variable`, if any."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    for module in {id(m): m for m in labels.values()}.values():
        if cast.script_name(module) not in scripts and any(
                v.lower() == variable.lower() for v, _ in cast.variables(module)):
            return cast.script_name(module)
    return None
