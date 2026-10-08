"""Command grammar: what can be typed, declared as data.

A command is ``(pattern, help, builder)``. The pattern is words separated by
spaces, each one of:

    word                       a keyword; matched ignoring case, not captured
    a|b|c                      a choice; captured as declared (``chamberp`` -> ``chamberP``)
    <name:a|b|c>               the same choice, shown as ``<name>`` in help
    <name:type lo..hi unit>    a typed slot; captured as its value

A word may be a symbol (``<|<=|>|>=`` is a choice of four): only ``<name:`` starts
a slot.

Slot types are ``number``, ``integer``, ``text`` (one word, case kept) and
``rest`` (every remaining word; last only). The range may be open on either
side (``0..``, ``..32``) or left out, and so may the unit:

    ("<zone:platen|shroud> on|off", "Thermal control", lambda zone, s: {...})
    ("set <V:number 0..32 V> <A:number 0..3.2 A>",  "Voltage and current limit", ...)
    ("closeall", "Close every valve", {"close_all": True})

The help may run to several lines: the first is the one-line summary (a
tooltip, the help table), the rest is the detail a help card shows -- what the
command does, what its inputs mean, what to expect.

The builder gets the captures in order (choices and slots, not keywords) and
returns what the command means; a dict builder is returned as a copy. Anything
that depends on the bench (zone names, their limits) is filled into the pattern
by the code that builds the list, so a grammar is plain data with no I/O.

One `Grammar` answers four questions: `parse` (what does this line mean),
`complete` (what can come next -- a console hint, a GUI dropdown), `rows` (the
help table) and `describe` (the help card for a line, whole or begun). Errors come from the farthest point any command matched, so
the message and the dropdown always agree.
"""
from __future__ import annotations

import copy
import difflib
import math
import re
import textwrap
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Sequence

TYPES = ("number", "integer", "text", "rest")
_MAX_LISTED = 10


class GrammarError(ValueError):
    """A line the grammar does not accept. `expected` is what could have come
    at the point it failed."""

    def __init__(self, message: str, expected: Sequence["Option"] = ()) -> None:
        super().__init__(message)
        self.expected = tuple(expected)


@dataclass(frozen=True)
class Option:
    """One thing that can come next: a keyword, or a slot to fill."""
    kind: str                   # "word", or a slot type
    text: str                   # the keyword, or the slot's name
    help: str = ""              # set when this option leads to one command
    lo: float | None = None
    hi: float | None = None
    unit: str = ""

    def __str__(self) -> str:
        if self.kind == "word":
            return self.text
        limits = " ".join(p for p in (_range(self.lo, self.hi), self.unit) if p)
        return f"<{self.text}>" + (f" ({limits})" if limits else "")


def _range(lo, hi) -> str:
    if lo is not None and hi is not None:
        return f"{lo:g}..{hi:g}"
    if lo is not None:
        return f">= {lo:g}"
    if hi is not None:
        return f"<= {hi:g}"
    return ""


# --- pattern elements ----------------------------------------------------------------

_SKIP = object()        # a keyword matched; nothing to capture


class _Word:
    def __init__(self, text: str) -> None:
        self.text = text

    def options(self, help: str = "") -> list[Option]:
        return [Option("word", self.text, help)]

    def match(self, word: str):
        return (_SKIP, None) if word.lower() == self.text.lower() else (None, None)

    def usage(self) -> str:
        return self.text


class _Choice:
    def __init__(self, members: Sequence[str], name: str = "") -> None:
        self.members = tuple(members)
        self.name = name

    def options(self, help: str = "") -> list[Option]:
        return [Option("word", m, help) for m in self.members]

    def match(self, word: str):
        for m in self.members:
            if word.lower() == m.lower():
                return m, None
        return None, None

    def usage(self) -> str:
        return f"<{self.name}>" if self.name else "|".join(self.members)


class _Slot:
    def __init__(self, name: str, kind: str, lo, hi, unit: str) -> None:
        self.name, self.kind, self.lo, self.hi, self.unit = name, kind, lo, hi, unit

    def options(self, help: str = "") -> list[Option]:
        return [Option(self.kind, self.name, help, self.lo, self.hi, self.unit)]

    def match(self, word: str):
        """(value, None) on a match; (None, reason) when the word has the
        right shape but the wrong value; (None, None) when it is not one."""
        if self.kind == "text":
            return word, None
        try:
            value = float(word)
        except ValueError:
            return None, None
        if not math.isfinite(value):
            return None, None
        if self.kind == "integer":
            if value != int(value):
                return None, f"{word} is not a whole number"
            value = int(value)
        if ((self.lo is not None and value < self.lo)
                or (self.hi is not None and value > self.hi)):
            limits = " ".join(p for p in (_range(self.lo, self.hi), self.unit) if p)
            if self.lo is not None and self.hi is not None:
                return None, f"{word} is outside {limits}"
            return None, f"{word} must be {limits}"
        return value, None

    def usage(self) -> str:
        return f"<{self.name}>"


def _compile(pattern: str) -> tuple:
    elements = []
    tokens = re.findall(r"<\s*\w[^>]*>|\S+", pattern)        # a slot is <name...>; a bare < is a word
    if not tokens:
        raise GrammarError("empty pattern")
    for i, tok in enumerate(tokens):
        if (tok.startswith("<") and (m := re.fullmatch(r"<\s*(\w+):([^\s|>]+(?:\|[^\s|>]+)*)\s*>", tok))
                and m.group(2) not in TYPES):        # <name:a|b>, or <name:a> -- one member is a choice too
            elements.append(_Choice(m.group(2).split("|"), m.group(1)))
        elif re.match(r"<\s*\w", tok):                          # meant as a slot (<, <= are words)
            m = re.fullmatch(r"<\s*(\w+):(\w+)(?:\s+([-+\d.eE]*\.\.[-+\d.eE]*))?(?:\s+(\S+))?\s*>", tok)
            if not m or m.group(2) not in TYPES:
                raise GrammarError(f"bad slot {tok!r} in {pattern!r} (want <name:{'|'.join(TYPES)} lo..hi unit>)")
            name, kind, limits, unit = m.groups()
            lo = hi = None
            if limits:
                if kind not in ("number", "integer"):
                    raise GrammarError(f"{tok!r}: only number and integer slots take a range")
                a, b = limits.split("..", 1)
                lo, hi = (float(a) if a else None), (float(b) if b else None)
            if kind == "rest" and i != len(tokens) - 1:
                raise GrammarError(f"{tok!r}: a rest slot must be last in {pattern!r}")
            elements.append(_Slot(name, kind, lo, hi, unit or ""))
        elif "|" in tok:
            members = [m for m in tok.split("|") if m]
            if len(members) < 2:
                raise GrammarError(f"bad choice {tok!r} in {pattern!r}")
            elements.append(_Choice(members))
        else:
            elements.append(_Word(tok))
    return tuple(elements)


@dataclass(frozen=True)
class _Command:
    elements: tuple
    help: str                   # the first line
    builder: object
    details: str = ""           # the rest

    def build(self, captures: list):
        if callable(self.builder):
            return self.builder(*captures)
        return copy.deepcopy(self.builder)


# --- the grammar ---------------------------------------------------------------------

Command = tuple  # (pattern, help, builder)


class Grammar:
    def __init__(self, commands: Iterable[Command]) -> None:
        self._commands = []
        for p, h, b in commands:
            first, _, rest = (h or "").partition("\n")
            self._commands.append(_Command(_compile(p), first.strip(), b, _paragraphs(rest)))

    def __len__(self) -> int:
        return len(self._commands)

    def _walk(self, cmd: _Command, words: Sequence[str]):
        """How far `cmd` matches `words`. Returns (kind, position, detail):
        ("full", n, captures) | ("short", i, element) | ("long", n, None) |
        ("miss", i, element) | ("bad", i, reason)."""
        captures = []
        for i, el in enumerate(cmd.elements):
            if i >= len(words):
                return "short", i, el
            if isinstance(el, _Slot) and el.kind == "rest":
                captures.append(" ".join(words[i:]))
                return "full", len(words), captures
            value, reason = el.match(words[i])
            if reason:
                return "bad", i, reason
            if value is None:
                return "miss", i, el
            if value is not _SKIP:
                captures.append(value)
        if len(words) > len(cmd.elements):
            return "long", len(cmd.elements), None
        return "full", len(words), captures

    def complete(self, words: Sequence[str]) -> list[Option]:
        """Everything that can come after `words` (each a whole typed word).
        A caller filters by the prefix of a word still being typed."""
        found: dict[tuple, tuple[Option, set, set]] = {}
        for cmd in self._commands:
            kind, i, el = self._walk(cmd, words)
            if kind != "short" or i != len(words):
                continue
            for opt in el.options():
                key = (opt.kind, opt.text.lower(), opt.lo, opt.hi, opt.unit)
                _, helps, ends = found.setdefault(key, (opt, set(), set()))
                helps.add(cmd.help)
                if i == len(cmd.elements) - 1:
                    ends.add(cmd.help)
        # An option that leads to one command carries that command's help; one that
        # leads to several carries the help of the one it ends (`hvc platen 40`,
        # before `at TC01` makes it a hold).
        def helped(opt, helps, ends):
            if len(helps) == 1 or len(ends) == 1:
                return replace(opt, help=next(iter(helps if len(helps) == 1 else ends)))
            return opt
        return [helped(*v) for v in found.values()]

    def parse(self, words: Sequence[str]):
        """What `words` mean: the matching command's builder result."""
        words = list(words)
        results = [(cmd, self._walk(cmd, words)) for cmd in self._commands]
        full = [(cmd, r) for cmd, r in results if r[0] == "full"]
        if len(full) == 1:
            cmd, (_, _, captures) = full[0]
            return cmd.build(captures)
        if len(full) > 1:
            usages = " / ".join(" ".join(e.usage() for e in c.elements) for c, _ in full)
            raise GrammarError(f"{' '.join(words)!r} is ambiguous: {usages}")
        raise self._error(words, [r for _, r in results])

    def _error(self, words: list[str], results: list) -> GrammarError:
        if not results:
            return GrammarError("nothing is accepted here")
        far = max(r[1] for r in results)
        at = [r for r in results if r[1] == far]
        before = " ".join(words[:far])
        after = f" after {before!r}" if before else ""
        bad = [r[2] for r in at if r[0] == "bad"]
        if bad:                                     # the right kind of value, out of range
            return GrammarError(f"{before}: {bad[0]}" if before else bad[0])
        expected = self.complete(words[:far])
        if far >= len(words):                       # ran out of words
            return GrammarError(f"incomplete: expected {_listing(expected)}{after}", expected)
        word = words[far]
        if all(r[0] == "long" for r in at):
            return GrammarError(f"unexpected {word!r}{after}")
        if len(expected) == 1 and expected[0].kind not in ("word", "text"):
            slot = expected[0]
            what = "a whole number" if slot.kind == "integer" else "a number"
            return GrammarError(f"expected {slot}{after}, got {word!r}: not {what}", expected)
        message = f"expected {_listing(expected)}{after}, got {word!r}"
        if guess := _did_you_mean(word, [o.text for o in expected if o.kind == "word"]):
            message += f"; did you mean {guess!r}?"
        return GrammarError(message, expected)

    def rows(self) -> list[tuple[str, str]]:
        """(usage, help) for each command, in declaration order."""
        return [(" ".join(e.usage() for e in c.elements), c.help) for c in self._commands]

    def describe(self, words: Sequence[str], limit: int = 6) -> list[dict]:
        """Help cards for `words`: the command they are, when they are a whole
        one; else every command they begin (a value out of range still counts,
        so a mistyped line keeps its card). More than `limit` begun commands are
        too many to help: none is returned, and the next words narrow it. Each card
        is {usage, words, help, details, inputs, complete}; an input is a slot
        {name, kind, lo, hi, unit} or a choice {name, kind: "choice", choices}."""
        words = list(words)
        if not words:
            return []
        full, begun = [], []
        for cmd in self._commands:
            kind, i, _ = self._walk(cmd, words)
            if kind == "full":
                full.append(cmd)
            elif (kind == "short" and i == len(words)) or kind == "bad":
                begun.append(cmd)
        chosen = full or begun
        return [] if len(chosen) > limit else [_card(c, bool(full)) for c in chosen]

    def roles(self, words: Sequence[str]) -> list[str]:
        """What each word is, so a GUI can draw the grammar: `verb` (the first
        word), `kw` (a fixed word after it), `value` (a number or word typed into a
        slot), `text` (free text) or `bad` (out of range; or fits nothing, and then
        neither does any word after it)."""
        roles: list[str] = []
        for k, word in enumerate(words):
            options = self.complete(words[:k])
            if any(o.kind == "rest" for o in options):
                return roles + ["text"] * (len(words) - k)
            if any(o.kind == "word" and o.text.lower() == word.lower() for o in options):
                roles.append("verb" if k == 0 else "kw")
            elif any(o.kind in ("number", "integer") for o in options) and _is_number(word):
                v = float(word)
                fits = any(o.kind in ("number", "integer") and (o.lo is None or v >= o.lo)
                           and (o.hi is None or v <= o.hi) for o in options)
                roles.append("value" if fits else "bad")         # out of range: flagged, the rest still read
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


def _paragraphs(text: str) -> str:
    """Help details as written in source, hard-wrapped and indented, as
    paragraphs: lines joined, a blank line between paragraphs kept."""
    paras = re.split(r"\n\s*\n", textwrap.dedent(text).strip())
    return "\n\n".join(" ".join(p.split()) for p in paras if p.strip())


def _card(cmd: _Command, complete: bool) -> dict:
    inputs = []
    for e in cmd.elements:
        if isinstance(e, _Slot):
            inputs.append({"name": e.name, "kind": e.kind, "lo": e.lo, "hi": e.hi, "unit": e.unit})
        elif isinstance(e, _Choice):
            inputs.append({"name": e.name, "kind": "choice", "choices": list(e.members)})
    words = []
    for e in cmd.elements:
        if isinstance(e, _Slot):
            words.append({"text": e.name, "role": "slot", "kind": e.kind, "unit": e.unit})
        elif isinstance(e, _Choice):
            words.append({"text": e.name or "|".join(e.members), "role": "choice",
                          "choices": list(e.members)})
        else:
            words.append({"text": e.text, "role": "word"})
    return {"usage": " ".join(e.usage() for e in cmd.elements), "words": words, "help": cmd.help,
            "details": cmd.details, "inputs": inputs, "complete": complete}


def _listing(options: Sequence[Option]) -> str:
    names = [str(o) for o in options]
    if len(names) > _MAX_LISTED:
        return "one of " + ", ".join(names[:_MAX_LISTED]) + f", ... ({len(names)} in all)"
    if len(names) <= 1:
        return names[0] if names else "nothing"
    return ", ".join(names[:-1]) + " or " + names[-1]


def _did_you_mean(word: str, candidates: Sequence[str]) -> str | None:
    w = word.lower()
    lower = {c.lower(): c for c in candidates}
    if len(w) >= 2:
        for c_low, c in lower.items():
            if c_low.startswith(w) or w.startswith(c_low):
                return c
    close = difflib.get_close_matches(w, list(lower), n=1, cutoff=0.6)
    return lower[close[0]] if close else None
