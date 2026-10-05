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
                        awaited until that rScript takes it (10 s limit), and
                        for one that reports results until it is done or refused
    hold <time> s|min|h run the loaded rScripts for a while
    hold until end      until the operator's ctrl `end` (plans/tvac.plan)
    until <variable> above|below <limit> [C|K] timeout <time> s|min|h
                        run until a published value crosses a limit, or fail
                        at the timeout (required: a wait on hardware always has
                        a limit). C or K converts from the variable's own unit
    log <text>          one line in the run log

``#`` starts a comment on its own line. Commands and variable names come from
what the loaded rScripts declare (COMMANDS, VARIABLES), checked while the plan
is read; a mistake is reported with its line number before anything runs. So
are the owners' prerequisites (RULES; formslab.sequence.rules): a step that
breaks one is an error, one the plan does not establish a warning.
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

    def __init__(self, message: str, errors: list[tuple[int, str]] | None = None,
                 warnings: list[tuple[int, str]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors if errors is not None else [(0, message)]
        self.warnings = warnings or []


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
    # (line, message): a rule this plan does not establish, checked when the step runs
    warnings: tuple[tuple[int, str], ...] = ()


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


def parse_plan(source: str, *, path: Path | None = None, steps_out: list | None = None) -> Plan:
    """The plan in `source`. Every problem found is in `PlanError.errors` (the
    editor shows them all); the message is the first. `steps_out`, when given,
    receives the steps that did read, as (line, Segment), even if others did not."""
    where = path.name if path else "<plan>"
    name = path.stem if path else "plan"
    errors: list[tuple[int, str]] = []
    warnings: list[tuple[int, str]] = []

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
            raise PlanError(f"{where}:{n}: {message}" if n else f"{where}: {message}", list(errors),
                            list(warnings))

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
                fail(n, "until needs `timeout <time> s|min|h`: a wait on hardware always has a limit")
        return replace(_parse(fail, n, grammar, words), label=" ".join(words))

    numbered = [(n, seg) for n, words in steps if (seg := attempt(check_step, n, words)) is not None]
    if steps_out is not None:
        steps_out.extend(numbered)
    from formslab.sequence.rules import check as check_rules

    broken, warnings[:] = check_rules(numbered, scripts, published)
    errors.extend(broken)
    done()

    interval, unit = record
    segments = tuple(seg for _, seg in numbered)
    return Plan(name=name, path=path, rscripts=scripts, record_interval=interval, record_unit=unit,
                sequence=Sequence(name=name, segments=segments, rscripts=scripts),
                warnings=tuple(sorted(warnings)))


def review(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(errors, warnings) in plan `text`, each (line, message), line 0 for the
    document as a whole. An error keeps the plan from running; a warning is a
    rule the plan does not establish, checked when its step runs. Also notes
    rScripts on the `load` line that cannot be found, which reading alone does
    not mind."""
    try:
        plan = parse_plan(text)
        errors, warnings = [], list(plan.warnings)
    except PlanError as e:
        errors, warnings = list(e.errors), list(e.warnings)
    from formslab import rscripts

    for n, line in enumerate(text.splitlines(), 1):
        words = line.split()
        if words and words[0].lower() == "load":
            errors += [(n, f"rScript {w} not found") for w in words[1:] if rscripts.find(w) is None]
    return sorted(errors, key=lambda e: e[0]), sorted(warnings, key=lambda e: e[0])


def check_text(text: str) -> list[tuple[int, str]]:
    """Every error in plan `text` (see `review`); [] for a plan that can run."""
    return review(text)[0]


def describe_step(text: str, line: int) -> dict:
    """For the editor's help panel: {cards, rules} for line `line` of plan `text`.
    `cards` are the commands the line is or begins (Grammar.describe); `rules`
    are the prerequisites of the command it is, each {why, conditions: [{text,
    status}]}, status as this plan leaves it there: ok, broken, unknown (checked
    when the step runs) or live (checkable only then)."""
    from formslab.rscripts import cast
    from formslab.sequence import rules as plan_rules

    lines = text.splitlines()
    words = lines[line - 1].split() if 0 < line <= len(lines) else []
    if not words or words[0].startswith("#"):
        return {"cards": [], "rules": []}
    head = words[0].lower()
    if head == "load":
        return {"cards": [_LOAD_CARD], "rules": []}
    if head == "record":
        return {"cards": _RECORD.describe(words) or _RECORD.describe(["record"]), "rules": []}
    load = next((ln.split() for ln in lines if ln.split()[:1] and ln.split()[0].lower() == "load"), [])
    scripts = tuple(load[1:])
    grammar, _, published = _grammar(scripts)
    cards = [{**c, "part": cast.card_part(c)} for c in grammar.describe(words)]
    rules = []
    if cards and cards[0]["complete"]:
        steps: list = []
        try:
            parse_plan(text, steps_out=steps)
        except PlanError:
            pass
        rules = plan_rules.at_line(steps, scripts, published, line)
    return {"cards": cards, "rules": rules}


def needed_rscripts(text: str) -> list[str]:
    """The rScripts the steps in plan `text` use: the owner of each instrument a
    step commands, and of each value an `until` waits on. For the editor, to put a
    deleted `load` line back as it was."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    value_owner: dict[str, str] = {}
    for module in {id(m): m for m in labels.values()}.values():
        for name, _unit in cast.variables(module):
            value_owner.setdefault(name.lower(), cast.script_name(module))
    need: set[str] = set()
    for line in text.splitlines():
        words = line.split()
        if not words or words[0].startswith("#") or words[0].lower() in ("load", "record"):
            continue
        head = words[0].lower()
        if head in labels:
            need.add(cast.script_name(labels[head]))
        elif head == "until" and len(words) > 1 and words[1].lower() in value_owner:
            need.add(value_owner[words[1].lower()])
    return [n for n in available_rscripts() if n in need]


def available_rscripts() -> list[str]:
    """The rScripts a plan can `load`: every ``r*.py`` on the search path."""
    from formslab.rscripts import loader

    return sorted({p.stem for d in loader.search_dirs() for p in d.glob("r*.py")})


def line_options(scripts, words) -> dict:
    """For the plan editor: what can come at each position of a step line.

    {'positions': [options before word 0, before word 1, ..., after the last],
     'complete': the words are a whole step, 'error': why not, or None}. Each
    option is {kind, text, help, lo, hi, unit, part}; `part` is the kind of part it
    names or belongs to (cast.option_parts). A line that is merely unfinished has
    no error: it is a valid start."""
    from formslab.rscripts import cast

    scripts, words = tuple(scripts), list(words)
    grammar, owner, _ = _grammar(scripts)
    positions = []
    for k in range(len(words) + 1):
        options = grammar.complete(words[:k])
        positions.append([_option(o, p) for o, p in zip(options, cast.option_parts(words[:k], options))])
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


# What each word of a plan is, so the GUI can draw the grammar. A role is one of
#   verb     the first word: a step (hold, until, log, load, record) or an instrument
#   kw       a fixed keyword after it (platen, on, rate, every, s, min...)
#   value    a number or a one-word value typed in a slot
#   text     free text (a log message)
#   script   an rScript named on the load line ("bad" when it cannot be found)
#   comment  a whole comment line
#   bad      a word that fits nothing here (it, and every word after it)
def tokens(text: str) -> list[list[dict]]:
    """Every line of plan `text` as [{text, role, part?}, ...]; [] for a blank line.
    A command's keywords carry its part (`hvc gate open`: valve)."""
    from formslab import rscripts
    from formslab.rscripts import cast

    lines = text.splitlines()
    load = next((ln.split() for ln in lines if ln.split()[:1] and ln.split()[0].lower() == "load"), [])
    grammar = _grammar(tuple(load[1:]))[0]
    out = []
    for line in lines:
        t = line.strip()
        words = t.split()
        if not words:
            out.append([])
        elif t.startswith("#"):
            out.append([{"text": t, "role": "comment"}])
        elif words[0].lower() == "load":
            out.append([{"text": words[0], "role": "verb"}]
                       + [{"text": w, "role": "script" if rscripts.find(w) else "bad"} for w in words[1:]])
        else:
            g = _RECORD if words[0].lower() == "record" else grammar
            part = cast.part_of(words[0], words)
            out.append([{"text": w, "role": r, **({"part": part} if part and r == "kw" else {})}
                        for w, r in zip(words, _roles(g, words))])
    return out


def _roles(grammar, words) -> list[str]:
    roles: list[str] = []
    for k, word in enumerate(words):
        options = grammar.complete(words[:k])
        if any(o.kind == "rest" for o in options):
            return roles + ["text"] * (len(words) - k)
        if any(o.kind == "word" and o.text.lower() == word.lower() for o in options):
            roles.append("verb" if k == 0 else "kw")
        elif any(o.kind in ("number", "integer") for o in options) and _is_number(word):
            v = float(word)
            fits = any(o.kind in ("number", "integer") and (o.lo is None or v >= o.lo)
                       and (o.hi is None or v <= o.hi) for o in options)
            roles.append("value" if fits else "bad")             # out of range: flagged, the rest still read
        elif any(o.kind == "text" for o in options):
            roles.append("value")
        else:
            return roles + ["bad"] * (len(words) - k)
    return roles


def _is_number(word: str) -> bool:
    try:
        float(word)
    except ValueError:
        return False
    return True


def _option(o, part=None) -> dict:
    return {"kind": o.kind, "text": o.text, "help": o.help, "lo": o.lo, "hi": o.hi, "unit": o.unit,
            "part": part}


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


_RECORD = Grammar([("record every <interval:number 0..> s|min|h", """Set how often values are recorded
                     How often every published value is written to the run's CSV file,
                     in the outputs folder. It is the line after load.""",
                    lambda n, u: (_positive("record", n), _RECORD_UNIT[u]))])

_LOAD_CARD = {"usage": "load <rScript> ...", "help": "Choose the rScripts this plan runs", "complete": True,
              "inputs": [], "details": (
                  "Each rScript owns instruments: rLACO the chamber (hvc), rPSU the supplies (psu1, "
                  "psu2), rSMTC08 the thermocouples (tc), rCryoBoard the cryocooler board (cryo), "
                  "rSLTA the camera (slta). A step can only command, or wait on a value of, a loaded "
                  "rScript. Always the first line.")}


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
        ("hold <time:number 0..> s|min|h", """Wait while the rScripts run
         Nothing is sent; the instruments keep being read and recorded. Time paused
         from the console or the GUI does not count.""", _hold),
        ("hold until end", """Wait until the run is ended
         The plan stays here, recording, until someone ends the run: End run in the
         GUI, or labcli end. Use it to operate by hand from the command box.""",
         lambda: Segment("hold", {"seconds": None})),
        ("log <message:rest>", """Write a line in the run log
         The text goes to the host log with the time, to mark where a phase begins.""",
         lambda m: Segment("log", {"message": m})),
    ]
    if published:
        var = f"<variable:{'|'.join(published)}>"
        until = f"until {var} above|below <limit:number>"
        why = """
         Runs until the value is past the limit, then goes on. If it is not past it
         within the timeout, the plan stops here and each rScript's shutdown runs. A
         wait on hardware always has a timeout. Just before a command, it also proves
         that command's prerequisite: until platenT below 60 C before hvc vent open."""
        steps += [
            (f"{until} timeout <time:number 0..> s|min|h", "Wait for a value to pass a limit" + why,
             _until(published)),
            (f"{until} C|K timeout <time:number 0..> s|min|h",
             "Wait for a temperature in °C or K to pass a limit" + why, _until(published)),
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
