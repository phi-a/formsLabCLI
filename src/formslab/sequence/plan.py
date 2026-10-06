"""Lab plans: `.plan` files that run rScripts against the bench.

One step per line. ``load`` names the rScripts that own the instruments;
``record`` sets the CSV cadence; every other line is a step:

    # PSU1 CH1 on for a minute, thermocouples recording
    load rPSU rSMTC08
    record every 2 s

    psu1 ch1 set 1.0 0.1
    psu1 ch1 on
    hold 60 s
    until TC01 > 30 C within 10 min
    psu1 ch1 off
    log done

Steps:

    <label> <words>     a command to the rScript that owns the label -- the same
                        words as the cast tab (``hvc vent open``). Sent and
                        awaited until that rScript takes it (10 s limit), and
                        for one that reports results until it is done or refused
    hold <time> s|min|h run the loaded rScripts for a while
    hold until end      until the operator's ctrl `end` (plans/tvac.plan)
    until <condition> within <time> s|min|h [or go on]
                        run until a condition on a published value holds: a
                        number with < <= > >= and a limit (C or K converts from
                        its own unit), an on/off value = or != true or false.
                        Not held in time, the run stops (required: a wait on
                        hardware always has a limit), or with `or go on` goes on
    log <text>          one line in the run log
    repeat ... / end    a loop: <n> times, until <condition> within ..., or until end

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
MAX_LOOP_DEPTH = 8
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
    from formslab.sequence import block as blocks_

    grammar, owner, published = _grammar(scripts)
    blocks, broken = blocks_.available(owner)

    def check_step(n, words):
        head = words[0].lower()
        if head in broken and head not in owner:              # (an instrument's name stays the instrument's)
            fail(n, broken[head])
        if module := owner.get(head):
            if cast.script_name(module) not in scripts:
                fail(n, f"{head} is declared by {cast.script_name(module)}; add it to `load`")
            if not cast.commands(module):
                fail(n, f"{head} takes no commands (it only reports readings)")
        lowered = [w.lower() for w in words]
        cond = (words if head == "until" else
                words[1:] if lowered[:2] == ["repeat", "until"] and lowered[2:3] != ["end"] else None)
        if cond:
            what = "until" if head == "until" else "repeat until"
            if old := next((w for w in lowered if w in _OLD_WORDS), None):
                fail(n, f"`{old}` is no longer a word of a condition: {_OLD_WORDS[old]}")
            if glued := next((w for w in cond[1:] if _GLUED_OP.match(w)), None):
                fail(n, f"{glued!r}: write the value, the comparison and the limit apart, with spaces "
                        "(platenT < 60)")
            if len(cond) > 1 and cond[1].lower() not in {v.lower() for v in published}:
                if by := _publisher(cond[1], scripts):
                    fail(n, f"{cond[1]} is published by {by}; add it to `load`")
                if not published:
                    fail(n, "no loaded rScript publishes a value to wait on")
            elif len(cond) > 2 and (own := next((u for v, u in published.items()
                                                  if v.lower() == cond[1].lower()), 0)) != 0:
                if own == "bool" and cond[2] in ("<", "<=", ">", ">="):
                    fail(n, f"{cond[1]} is on or off: write {cond[1]} = true or {cond[1]} = false")
                if own != "bool" and cond[2] in ("=", "!="):
                    fail(n, f"{cond[1]} is a number, compared with <, <=, > or >=; two readings are "
                            "almost never exactly equal")
            if "within" not in lowered:
                fail(n, f"{what} needs `within <time> s|min|h`: a wait on hardware always has a limit")
        return replace(_parse(fail, n, grammar, words), label=" ".join(words))

    def expand(n, seg, stack):
        """A block call's steps, inputs replaced, each checked as a step of this plan
        and labelled with the block (`pumpdown > hvc rough open`)."""
        b = blocks[seg.params["name"]]
        chain = " > ".join((*stack, b.name))
        if b.name in stack:
            fail(n, f"block {b.name} calls itself: {chain}")
        if len(stack) >= blocks_.MAX_DEPTH:
            fail(n, f"blocks nest more than {blocks_.MAX_DEPTH} deep: {chain}")
        if missing := [s for s in b.scripts if s not in scripts]:
            fail(n, f"{b.name} needs {', '.join(missing)}; add {'it' if len(missing) == 1 else 'them'} to `load`")
        out, depth = [], 0
        for ln, words in b.body(seg.params["values"]):
            head = words[0].lower()
            depth += (head == "repeat") - (head == "end")
            if depth < 0:
                fail(n, f"In {b.name} (line {ln}): `end` with no `repeat` above it")
            try:
                inner = check_step(n, words)
                parts = expand(n, inner, (*stack, b.name)) if inner.verb == "block" else [inner]
            except _LineError as e:
                fail(n, f"In {b.name} (line {ln}): {e.message}")
            out += [replace(p, label=f"{b.name} > {p.label}", origin=((b.name, ln), *p.origin))
                    for p in parts]
        if depth:
            fail(n, f"In {b.name}: a `repeat` with no `end`; a block's loops close inside it")
        return out

    def check_line(n, words):
        seg = check_step(n, words)
        return expand(n, seg, ()) if seg.verb == "block" else [seg]

    def read_line(n, words):
        segs = attempt(check_line, n, words)
        if segs is None and words[0].lower() == "repeat":          # a mistake in it, already noted:
            return [_loop()]                                       # its `end` still has its `repeat`
        return segs or []

    numbered = _pair_loops([(n, seg) for n, words in steps for seg in read_line(n, words)], errors)
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


def _pair_loops(numbered, errors):
    """`numbered` with each `repeat` and its `end` told of each other (`end_at`,
    `start_at`: indexes in the list); an `end` with no `repeat`, a `repeat` with no
    `end` and loops more than MAX_LOOP_DEPTH deep go in `errors`."""
    out, open_ = list(numbered), []
    for k, (n, seg) in enumerate(out):
        if seg.verb == "repeat":
            if len(open_) == MAX_LOOP_DEPTH:
                errors.append((n, f"loops nest more than {MAX_LOOP_DEPTH} deep"))
            open_.append(k)
        elif seg.verb == "end":
            if not open_:
                errors.append((n, "`end` with no `repeat` above it"))
                continue
            s = open_.pop()
            out[s] = (out[s][0], replace(out[s][1], params={**out[s][1].params, "end_at": k}))
            out[k] = (n, replace(seg, params={"start_at": s}))
    for s in open_:
        errors.append((out[s][0], "`repeat` with no `end` below it; every loop closes with `end`"))
    return out


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
    from formslab.sequence import block as blocks_

    blocks, _ = blocks_.available(cast.owners()[0])
    for c in cards:
        if (b := blocks.get(c["words"][0]["text"].lower()) if c.get("words") else None):
            c["steps"] = [" ".join(w) for _, w in b.steps]
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
    from formslab.sequence import block as blocks_

    blocks, _ = blocks_.available(labels)
    need: set[str] = set()

    def visit(lines, seen):
        for words in lines:
            if not words or words[0].startswith("#") or words[0].lower() in ("load", "record", "block"):
                continue
            head = words[0].lower()
            if head in labels:
                need.add(cast.script_name(labels[head]))
            elif head == "until" and len(words) > 1 and words[1].lower() in value_owner:
                need.add(value_owner[words[1].lower()])
            elif head == "repeat" and len(words) > 2 and words[2].lower() in value_owner:
                need.add(value_owner[words[2].lower()])
            elif head in blocks and head not in seen:             # a block: what its steps use
                need.update(blocks[head].scripts)
                visit([list(w) for _, w in blocks[head].steps], seen | {head})

    visit([line.split() for line in text.splitlines()], set())
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
                        for w, r in zip(words, g.roles(words))])
    return out


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


def _until(units, go_on=False):
    """The builder for an `until` condition: a number against a limit (`platenT <
    60 C`), or an on/off value against true or false (`InUmbra = true`), then the
    time it has, `within <time>`; `go_on`: at the limit, the plan goes on."""
    def build(variable, op, value, *rest):
        unit, t, t_unit = rest if len(rest) == 3 else (None, *rest)
        own = units.get(variable)
        if unit and own not in ("C", "K"):
            raise GrammarError(f"{variable} is in {own or 'no unit'}; C and K only apply to temperatures")
        if op in ("=", "!="):
            value = value == "true"
        return Segment("until", {"variable": variable, "op": op, "value": value, "unit": unit,
                                 "timeout_s": _positive("within", t) * _SECONDS[t_unit], "go_on": go_on})
    return build


# Words a condition used to be written with, and what to write now.
_OLD_WORDS = {"above": "write > (or >=) in place of above", "below": "write < (or <=) in place of below",
              "timeout": "write within in place of timeout"}
_GLUED_OP = re.compile(r"^(?!(<=|>=|!=|<|>|=)$).*[<>=]")      # a comparison stuck to a word


def _loop(times=None, until=None, forever=False):
    """A `repeat` line: `times` passes, or passes until the `until` condition is
    met, or passes until the run is ended. Its `end` is paired once the plan is read."""
    return Segment("repeat", {"times": times, "until": until, "forever": forever})


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
        ("repeat <count:integer 1..10000> times", """Repeat the steps up to end a number of times
         The steps between this line and its end line run count times, in order, one
         pass after another.""",
         lambda count: _loop(times=count)),
        ("repeat until end", """Repeat until the run is ended
         The steps between this line and its end line run again and again, until
         someone ends the run: End run in the GUI, or labcli end. Use it to hold the
         chamber in a cycle for as long as a test lasts.""",
         lambda: _loop(forever=True)),
        ("end", """Close the loop above
         Marks where the steps of the nearest open repeat end. The plan goes back to
         that repeat, which decides whether to run another pass.""",
         lambda: Segment("end", {})),
    ]
    if published:
        steps += _conditions(published)
    from formslab.sequence import block as blocks_

    blocks, _ = blocks_.available(labels)
    calls = [(b.pattern, b.help, lambda *captured, b=b: Segment("block", {"name": b.name,
                                                                            "values": b.values(captured)}))
             for b in blocks.values()]
    return Grammar(steps + cast.label_commands(scripts, build=_command) + calls), labels, published


_WAIT_WHY = """
 Runs until the condition holds, then goes on. A number is compared with <, <=, >
 or >=; an on/off value is = true or = false, or != either. If the condition does
 not hold within the time given, the run stops here and each rScript's shutdown
 runs: a wait on hardware always has a limit. Add or go on at the end to carry on
 instead."""
_PROOF_WHY = """ Just before a command, a wait that stops the run also proves that
 command's prerequisite: until platenT < 60 C before hvc vent open."""
_LOOP_WHY = """
 The steps between this line and its end line run again and again. Before each
 pass the condition is read: once it holds, the loop is done and the plan goes on
 after its end line, so a loop whose condition already holds runs no pass. A pass
 is never cut short; a change during a pass is seen when that pass ends. If the
 condition does not hold within the time given, the run stops there. Add or go on
 at the end to leave the loop and carry on instead."""


def _conditions(published):
    """The `until` and `repeat until` patterns for the values published: numbers
    against a limit, on/off values (unit `bool`) against true or false; each with
    its time limit, `within`, and with or without `or go on`."""
    numbers = [v for v, u in published.items() if u != "bool"]
    flags = [v for v, u in published.items() if u == "bool"]
    within = "within <time:number 0..> s|min|h"
    shapes = []                                      # (condition, summary end)
    if numbers:
        number = f"<value:{'|'.join(numbers)}> <|<=|>|>= <limit:number>"
        shapes += [(number, "a value passes a limit"), (f"{number} C|K", "a temperature passes a limit")]
    if flags:
        shapes += [(f"<value:{'|'.join(flags)}> =|!= true|false", "a value is true or false")]
    out = []
    for cond, what in shapes:
        for go_on in (False, True):
            tail = " or go on" if go_on else ""
            build = _until(published, go_on)
            out += [(f"until {cond} {within}{tail}", f"Wait until {what}{tail}"
                     + _WAIT_WHY + ("" if go_on else _PROOF_WHY), build),
                    (f"repeat until {cond} {within}{tail}", f"Repeat until {what}{tail}" + _LOOP_WHY,
                     lambda *a, build=build: _loop(until=build(*a).params))]
    return out


def _publisher(variable, scripts):
    """The script not in `scripts` that declares `variable`, if any."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    for module in {id(m): m for m in labels.values()}.values():
        if cast.script_name(module) not in scripts and any(
                v.lower() == variable.lower() for v, _ in cast.variables(module)):
            return cast.script_name(module)
    return None
