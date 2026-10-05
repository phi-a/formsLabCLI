"""Blocks: named, reusable groups of plan steps, called from a plan by name.

A block is a `.block` file beside the plans (the same folders, docs/SEQUENCE.md):

    # Rough the chamber down to a pressure, then seal it
    # Closes the vent, fill and gate valves, starts the vacuum pump ...
    block pumpdown to <pressure:number 0.01..760 Torr>
    load rLACO

    hvc vent close
    ...
    until chamberP below {pressure} timeout 20 min
    hvc stop

- The leading comments describe it: the first is the summary, the rest the
  details (docs/WRITING.md).
- The `block` line names it and is the pattern a plan writes to call it, each
  input a number slot in the grammar's own syntax.
- `load` names the rScripts it needs; a plan that calls it must load them too.
- Each step is a plan step; `{input}` is replaced by the value given.

A plan reads a call by putting the block's steps in its place (plan.py), so the
runner, the rules and the live checks see ordinary steps. Blocks may call
blocks, to a depth of `MAX_DEPTH`; a block that calls itself is an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from formslab.rscripts.grammar import Grammar, GrammarError, _Slot, _Word, _compile

SUFFIX = ".block"
MAX_DEPTH = 8
# Words a block may not be named: the plan's own steps, and FORMS' verbs.
RESERVED = ("hold", "until", "log", "load", "record", "block", "repeat", "end", "propagate", "call", "observe")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_INPUT = re.compile(r"^\{(\w+)\}$")


class BlockError(ValueError):
    """A block file that cannot be used. `line` is where (0: the file as a whole)."""

    def __init__(self, message: str, line: int = 0) -> None:
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class Block:
    name: str
    pattern: str                                  # the call: "pumpdown to <pressure:number ...>"
    summary: str
    details: str
    scripts: tuple[str, ...]                      # its `load` line
    steps: tuple[tuple[int, tuple[str, ...]], ...]   # (line in the file, words), {inputs} unreplaced
    inputs: tuple[str, ...]                       # input names, in the order the call gives them
    captures: tuple[str | None, ...]              # per captured word of the call: its input, or None
    path: Path | None = None

    @property
    def help(self) -> str:
        """Summary, then details: the shape a command's help has (grammar.py)."""
        return self.summary + ("\n" + self.details if self.details else "")

    def values(self, captured) -> dict[str, float]:
        """{input: value} from what the call's grammar captured."""
        return {name: v for name, v in zip(self.captures, captured) if name}

    def body(self, values: dict) -> list[tuple[int, list[str]]]:
        """Its steps with the inputs replaced by `values`."""
        return [(n, [f"{values[m.group(1)]:g}" if (m := _INPUT.match(w)) else w for w in words])
                for n, words in self.steps]

    def sample_values(self) -> dict[str, float]:
        """A value for each input within its range, to read the block on its own."""
        out = {}
        for e in _compile(self.pattern):
            if isinstance(e, _Slot):
                out[e.name] = e.lo if e.lo is not None else e.hi if e.hi is not None else 1
        return out


def parse_block(text: str, path: Path | None = None) -> Block:
    """The block in `text`, or BlockError with the line at fault."""
    lines = list(enumerate(text.splitlines(), 1))
    about, header, scripts, steps = [], None, None, []
    for n, raw in lines:
        t = raw.strip()
        if not t:
            continue
        if t.startswith("#"):
            if header is None and re.search(r"[A-Za-z]", t):
                about.append(t.lstrip("#").strip())
            continue
        words = t.split()
        head = words[0].lower()
        if header is None:
            if head != "block":
                raise BlockError("a block starts with `block <name> ...`, the words a plan writes to call it", n)
            header = (n, words)
        elif head == "block":
            raise BlockError("`block` appears twice; a file holds one block", n)
        elif head == "load":
            if scripts is not None:
                raise BlockError("`load` appears twice; name every rScript on one line", n)
            if steps:
                raise BlockError("`load` goes before the first step", n)
            if len(words) < 2:
                raise BlockError("load names the rScripts the block needs, e.g. `load rLACO`", n)
            scripts = tuple(words[1:])
        elif head == "record":
            raise BlockError("a block has no `record`: how often to record is the plan's", n)
        else:
            if [w.lower() for w in words] in (["hold", "until", "end"], ["repeat", "until", "end"]):
                raise BlockError(f"a block cannot `{' '.join(words[:3]).lower()}`: the steps after its call "
                                 "would never run", n)
            if head != "log" and "#" in t:
                raise BlockError("comments go on their own line", n)
            steps.append((n, tuple(words)))
    if header is None:
        raise BlockError("a block starts with `block <name> ...`, the words a plan writes to call it")
    hn, hwords = header
    if len(hwords) < 2:
        raise BlockError("name the block: `block <name> ...`", hn)
    name = hwords[1]
    if not _NAME.match(name):
        raise BlockError(f"a block's name is a lowercase word (letters, digits, _), not {name!r}", hn)
    if name in RESERVED:
        raise BlockError(f"{name!r} is a step word; choose another name", hn)
    pattern = " ".join(hwords[1:])
    try:
        elements = _compile(pattern)
        Grammar([(pattern, "", None)])
    except GrammarError as e:
        raise BlockError(str(e), hn) from None
    inputs, captures = [], []
    for e in elements[1:]:
        if isinstance(e, _Slot):
            if e.kind not in ("number", "integer"):
                raise BlockError(f"input {e.name!r} is a {e.kind}; a block's inputs are numbers", hn)
            if e.name in inputs:
                raise BlockError(f"input {e.name!r} appears twice", hn)
            inputs.append(e.name)
            captures.append(e.name)
        elif not isinstance(e, _Word):
            captures.append(None)                 # a choice: captured, not an input
    if scripts is None:
        raise BlockError("a block names the rScripts it needs: `load <rScript> ...`")
    if not steps:
        raise BlockError("the block has no steps")
    used = set()
    for n, words in steps:
        for w in words:
            if m := _INPUT.match(w):
                if m.group(1) not in inputs:
                    raise BlockError(f"{{{m.group(1)}}} is not an input of this block; "
                                     f"its inputs are {', '.join(inputs) or 'none'}", n)
                used.add(m.group(1))
            elif "{" in w or "}" in w:
                raise BlockError(f"{w!r}: an input is a whole word, like {{pressure}}", n)
    if unused := [i for i in inputs if i not in used]:
        raise BlockError(f"input {unused[0]!r} is not used by any step", hn)
    summary = about[0] if about else ""
    details = " ".join(about[1:])
    return Block(name=name, pattern=pattern, summary=summary, details=details, scripts=scripts,
                 steps=tuple(steps), inputs=tuple(inputs), captures=tuple(captures), path=path)


# --- a block file in the editor ------------------------------------------------------
#
# A block is read as the plan it would be, line for line: its `block` line a
# comment, each {input} a value from its range. The plan's own checks, tokens,
# dropdowns and help then apply to its steps, at the block's own line numbers.

_SLOT = re.compile(r"<\s*(\w+)\s*:\s*(?:number|integer)(?:\s+([-+\d.eE]*)\.\.([-+\d.eE]*))?[^>]*>")


def _samples(text: str) -> dict[str, float]:
    """A value for each input the `block` line declares, read leniently (the file
    may be half written)."""
    header = next((ln for ln in text.splitlines() if ln.strip().lower().startswith("block")), "")
    out = {}
    for name, lo, hi in _SLOT.findall(header):
        try:
            out[name] = float(lo) if lo else float(hi) if hi else 1.0
        except ValueError:
            out[name] = 1.0
    return out


def as_plan(text: str) -> str:
    """The block in `text` as a plan, line for line."""
    samples = _samples(text)
    out = []
    for line in text.splitlines():
        words = line.split()
        if words and words[0].lower() == "block":
            out.append("# " + line.strip())
        else:
            out.append(" ".join(f"{samples.get(m.group(1), 1):g}" if (m := _INPUT.match(w)) else w
                                for w in words) if words else line)
    return "\n".join(out) + "\n"


def review(text: str) -> tuple[list, list]:
    """(errors, warnings) in a block file, each (line, message), as plan.review."""
    from formslab.rscripts import cast
    from formslab.sequence.plan import review as review_plan

    try:
        b = parse_block(text)
    except BlockError as e:
        return [(e.line, str(e))], []
    if b.name in cast.owners()[0]:
        return [(0, f"{b.name} is an instrument's name; choose another name for the block")], []
    return review_plan(as_plan(text))


def tokens(text: str) -> list[list[dict]]:
    """The block's lines as plan tokens (plan.tokens); the `block` line drawn as its
    words and inputs, and each {input} as a value."""
    from formslab.sequence.plan import tokens as plan_tokens

    lines = text.splitlines()
    out = plan_tokens(as_plan(text))
    for i, line in enumerate(lines):
        words = line.split()
        if words and words[0].lower() == "block":
            rest = line.strip()[len(words[0]):].strip()
            toks = [{"text": words[0], "role": "verb"}]
            for part in re.split(r"(<[^>]*>)", rest):
                if part.startswith("<"):
                    toks.append({"text": part.strip("<>").split(":")[0], "role": "value"})
                else:
                    toks += [{"text": w, "role": "kw"} for w in part.split()]
            if len(toks) > 1 and toks[1]["role"] == "kw":
                toks[1]["role"] = "name"                    # the block's name: the first word after `block`
            out[i] = toks
        elif any(_INPUT.match(w) for w in words) and i < len(out):
            out[i] = [{**t, "text": w, "role": "value"} if _INPUT.match(w) else t
                      for t, w in zip(out[i], words)]
    return out


def line_options(scripts, words) -> dict:
    """plan.line_options for a block's step: each {input} read as a value that fits
    where it stands."""
    from formslab.sequence.plan import _grammar, line_options as plan_line_options

    grammar = _grammar(tuple(scripts))[0]
    filled = []
    for w in words:
        if _INPUT.match(w):
            slot = next((o for o in grammar.complete(filled) if o.kind in ("number", "integer")), None)
            w = f"{slot.lo if slot and slot.lo is not None else slot.hi if slot and slot.hi is not None else 1:g}"
        filled.append(w)
    return plan_line_options(scripts, filled)


def describe(text: str, line: int) -> dict:
    """The help card for line `line` of a block file: its `block` line is the call."""
    from formslab.rscripts.grammar import _card
    from formslab.sequence.plan import describe_step

    lines = text.splitlines()
    words = lines[line - 1].split() if 0 < line <= len(lines) else []
    if words and words[0].lower() == "block":
        try:
            b = parse_block(text)
        except BlockError:
            return {"cards": [], "rules": []}
        card = _card(Grammar([(b.pattern, b.help, None)])._commands[0], True)
        return {"cards": [{**card, "part": None, "steps": [" ".join(w) for _, w in b.steps]}], "rules": []}
    return describe_step(as_plan(text), line)


# --- finding blocks: the plans' own folders ------------------------------------------

def discover() -> list[Path]:
    from formslab.sequence.plan import search_dirs

    seen, out = set(), []
    for d in search_dirs():
        for p in sorted(d.glob(f"*{SUFFIX}")):
            if p.stem not in seen:
                seen.add(p.stem)
                out.append(p)
    return out


_cache: dict[Path, tuple[float, object]] = {}


def read(path: Path) -> Block:
    """The block in `path`, cached by the file's time (plans are read per keystroke)."""
    mtime = path.stat().st_mtime
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        if isinstance(hit[1], BlockError):
            raise hit[1]
        return hit[1]
    try:
        result = parse_block(path.read_text(encoding="utf-8"), path)
    except BlockError as e:
        _cache[path] = (mtime, e)
        raise
    _cache[path] = (mtime, result)
    return result


def available(labels=()) -> tuple[dict[str, Block], dict[str, str]]:
    """({name: Block} usable from a plan, {name: why not} for the rest). A block
    named like an instrument (`labels`) is refused, not the instrument."""
    blocks, broken = {}, {}
    taken = {str(lb).lower() for lb in labels}
    for path in discover():
        try:
            b = read(path)
        except (BlockError, OSError) as e:
            broken[path.stem.lower()] = f"block {path.stem} cannot be used: {e}"
            continue
        if b.name != path.stem.lower():
            broken[b.name] = f"block {b.name} is in {path.name}; a block's file is named after it"
        elif b.name in taken:
            broken[b.name] = f"block {b.name} has an instrument's name; rename the block"
        else:
            blocks[b.name] = b
    return blocks, broken
