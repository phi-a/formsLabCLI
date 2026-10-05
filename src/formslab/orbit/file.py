"""Orbit files: `.orbit`, one classical element per line.

    # orbit: a dawn-dusk sun-synchronous orbit, 550 km
    epoch 2026-10-05T12:00:00Z
    a 6928 km
    e 0.001
    i 97.6 deg
    raan 120 deg
    argp 90 deg
    nu 0 deg

Each of the seven appears once, in any order. ``#`` starts a comment on its own
line. The grammar is data (`rscripts.grammar`), so the editor's dropdowns, its
token drawing and the errors here come from one place, as they do for plans.
Orbit files sit beside plans (`sequence.plan.search_dirs`) but are never run.
`load` gives the `Orbit` the view-factor and thermal models sweep.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from formslab.rscripts.grammar import Grammar, GrammarError

from .propagate.constants import R_E
from .propagate.kepler import Elements
from .propagate.orbit import Orbit

SUFFIX = ".orbit"
ORDER = ("epoch", "a", "e", "i", "raan", "argp", "nu")
MIN_PERIGEE_KM = 100
# What each element describes; the GUI draws it as a symbol beside the element.
GROUPS = {"a": "shape", "e": "shape", "i": "plane", "raan": "plane",
          "argp": "place", "nu": "place", "epoch": "time"}
_GLUED = re.compile(r"^\d+(\.\d+)?(km|deg)$", re.IGNORECASE)


class OrbitError(ValueError):
    """The text is not a whole orbit. `errors` lists every problem as (line,
    message), line 0 meaning the file as a whole; the message is the first."""

    def __init__(self, message: str, errors: list[tuple[int, str]]) -> None:
        super().__init__(message)
        self.errors = errors


def _epoch(text: str):
    try:
        t = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in "zZ" else text)
    except ValueError:
        raise GrammarError(f"{text!r} is not a time; write it as 2026-10-05T12:00:00Z") from None
    if t.utcoffset() != timezone.utc.utcoffset(None):
        raise GrammarError(f"{text!r}: give the time in UTC, ending in Z")
    return "epoch", t


def _deg(name):
    return lambda value: (name, math.radians(value))


GRAMMAR = Grammar([
    ("epoch <utc:text>", """Set the moment the elements describe
     The time at which the other six elements hold, in UTC, written as
     2026-10-05T12:00:00Z. The satellite is carried from here to the wall clock,
     forward or back.""", _epoch),
    ("a <semimajor:number 6480..> km", """Set the size of the orbit
     The semi-major axis: half the ellipse's longest diameter, from the Earth's
     centre. For a circular orbit it is the Earth's radius, 6378 km, plus the
     altitude. It sets the period: at 6928 km the satellite goes round in about
     96 minutes.""", lambda km: ("a", km * 1000.0)),
    ("e <eccentricity:number 0..0.99>", """Set the shape of the orbit
     Eccentricity: 0 is a circle, and the ellipse stretches toward 1. Low orbits
     are near 0, such as 0.001. The lowest point, the perigee, is a(1 - e) from the
     Earth's centre and must stay 100 km above the surface.""", lambda e: ("e", e)),
    ("i <inclination:number 0..180> deg", """Tilt the orbit's plane
     Inclination: the angle between the orbit's plane and the equator. 0 is
     equatorial, 90 passes over the poles, and above 90 the satellite goes against
     the Earth's turn. A sun-synchronous orbit is near 98.""", _deg("i")),
    ("raan <node:number 0..360> deg", """Turn the orbit's plane about the pole
     Right ascension of the ascending node: where the satellite crosses the equator
     going north, measured east from the vernal equinox. With the epoch, it sets
     where the Sun is relative to the plane, and so the beta angle and the
     eclipses.""", _deg("raan")),
    ("argp <perigee:number 0..360> deg", """Place the perigee in the plane
     Argument of perigee: the angle from the ascending node to the perigee, in the
     direction of motion. It matters only when e is above 0.""", _deg("argp")),
    ("nu <anomaly:number 0..360> deg", """Place the satellite at the epoch
     True anomaly: the angle from the perigee to the satellite at the epoch, in the
     direction of motion.""", _deg("nu")),
])


# --- finding orbit files -----------------------------------------------------

def discover() -> list[Path]:
    """Every orbit file on the plan search path, first of each name."""
    from formslab.sequence.plan import search_dirs

    seen, out = set(), []
    for d in search_dirs():
        for p in sorted(d.glob(f"*{SUFFIX}")):
            if p.stem not in seen:
                seen.add(p.stem)
                out.append(p)
    return out


# --- reading -----------------------------------------------------------------

def parse(text: str) -> Elements:
    """The orbit in `text`, or `OrbitError` with every problem found."""
    errors: list[tuple[int, str]] = []
    values: dict[str, object] = {}
    where: dict[str, int] = {}
    for n, line in enumerate(text.splitlines(), 1):
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        words = t.split()
        head = words[0].lower()
        if head in where:
            errors.append((n, f"`{head}` is already on line {where[head]}; an orbit names each element once"))
            continue
        if head in GROUPS:                  # a line that names an element, read or not, is not missing
            where[head] = n
        if "#" in t:
            errors.append((n, "comments go on their own line"))
            continue
        try:
            key, value = GRAMMAR.parse(words)
        except GrammarError as e:
            message = str(e)
            if glued := next((w for w in words if _GLUED.match(w)), None):
                number, unit = re.match(r"([\d.]+)(\D+)", glued).groups()
                message += f" (write `{number} {unit}`, with a space)"
            errors.append((n, message))
            continue
        values[key] = value
    missing = [k for k in ORDER if k not in where]
    if missing:
        errors.append((0, f"the orbit has no {' or '.join(f'`{k}`' for k in missing)}; "
                          f"every element is needed: {', '.join(ORDER)}"))
    elif not errors and values["a"] * (1 - values["e"]) - R_E < MIN_PERIGEE_KM * 1000:
        perigee = (values["a"] * (1 - values["e"]) - R_E) / 1000
        errors.append((where["e"], f"the perigee, a(1 - e), is {perigee:.0f} km above the surface; "
                                   f"it must be at least {MIN_PERIGEE_KM} km"))
    if errors:
        errors.sort(key=lambda e: e[0])
        n, message = errors[0]
        raise OrbitError(f"{n}: {message}" if n else message, errors)
    return Elements(**values)


def load(path) -> Orbit:
    """The orbit in the `.orbit` file at `path`, for the models (`OrbitError` if
    it does not read)."""
    return Orbit(parse(Path(path).read_text(encoding="utf-8")))


def review(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(errors, warnings) in orbit `text`, as `sequence.plan.review` gives them for
    a plan. An orbit has no warnings."""
    try:
        parse(text)
    except OrbitError as e:
        return e.errors, []
    return [], []


# --- for the editor ----------------------------------------------------------

def line_options(words) -> dict:
    """What can come at each position of an orbit line; the same shape as
    `sequence.plan.line_options`. Every option carries its element's group."""
    words = list(words)
    positions = []
    for k in range(len(words) + 1):
        group = GROUPS.get(words[0].lower()) if k else None
        positions.append([{"kind": o.kind, "text": o.text, "help": o.help, "lo": o.lo, "hi": o.hi,
                           "unit": o.unit, "part": group or GROUPS.get(o.text.lower())}
                          for o in GRAMMAR.complete(words[:k])])
    result = {"positions": positions, "complete": False, "error": None}
    if not words:
        return result
    try:
        GRAMMAR.parse(words)
        result["complete"] = True
    except GrammarError as e:
        if not positions[-1]:                               # not even a valid start
            result["error"] = str(e)
    return result


def tokens(text: str) -> list[list[dict]]:
    """Every line as [{text, role, part?}, ...] (see `Grammar.roles`); the element,
    the first word, carries its group."""
    out = []
    for line in text.splitlines():
        t = line.strip()
        words = t.split()
        if not words:
            out.append([])
        elif t.startswith("#"):
            out.append([{"text": t, "role": "comment"}])
        else:
            group = GROUPS.get(words[0].lower())
            out.append([{"text": w, "role": r, **({"part": group} if group and k == 0 and r == "verb" else {})}
                        for k, (w, r) in enumerate(zip(words, GRAMMAR.roles(words)))])
    return out


def describe(text: str, line: int) -> dict:
    """The help card for line `line`, in the shape of `sequence.plan.describe_step`."""
    lines = text.splitlines()
    words = lines[line - 1].split() if 0 < line <= len(lines) else []
    if not words or words[0].startswith("#"):
        return {"cards": [], "rules": []}
    return {"cards": [{**c, "part": GROUPS.get(c["words"][0]["text"])} for c in GRAMMAR.describe(words)],
            "rules": []}
