"""Cast commands declared by rScripts.

An rScript that owns CAST labels declares, as data, the commands it accepts and
the values it publishes (see `formslab.rscripts.grammar` for the patterns):

    CAST_LABELS = ("psu1", "psu2")
    COMMANDS = [("<ch:ch1|ch2|ch3> on|off", "Channel output",
                 lambda ch, s: {ch[2:]: {"on": s == "on"}}), ...]
    VARIABLES = [("PSU1_CH1_V", "V"), ...]       # (name, unit); [] if none

Either may be a function returning the list, for a script whose commands depend
on the bench (rLACO reads its zones from tvac_bench.json). Importing a script
must not touch hardware, start threads or write files: the console imports it
to read these.

Two processes use them. The console's cast tab (and a plan, and `labcli cast`)
turns `hvc vent open` into ``{"vent": "open"}`` and writes it to CAST. The host
runs the same script, which reads the request back from CAST and applies it. So
a command means the same thing however it is sent.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

from formslab.rscripts.grammar import Grammar, GrammarError, Option
from formslab.rscripts.loader import search_dirs

__all__ = ["Grammar", "GrammarError", "Option", "commands", "complete", "grammar",
           "help_rows", "owners", "reports_results", "request", "send", "variables"]

# Plan keywords: a CAST label may not be one of these.
RESERVED = ("hold", "until", "log", "load", "record", "repeat", "end", "when")


def _declared(module, attr: str) -> list:
    value = getattr(module, attr, ())
    return list(value() if callable(value) else value)


def commands(module) -> list[tuple]:
    """A script's (pattern, help, builder) list."""
    return _declared(module, "COMMANDS")


def variables(module) -> list[tuple[str, str | None]]:
    """A script's (name, unit) list of the values it publishes."""
    return [(n, u or None) for n, u in _declared(module, "VARIABLES")]


def sensors() -> list[str]:
    """Every temperature reading a routine on the path publishes, by name (TC01,
    HVC_T5): what a zone can be held at, and what a block's thermocouple input
    offers. A routine's SENSORS if it declares them (a list, or a function
    returning one), else its values in K."""
    labels, _ = owners()
    out: list[str] = []
    for module in {id(m): m for m in labels.values()}.values():
        declared = getattr(module, "SENSORS", None)
        names = (list(declared() if callable(declared) else declared) if declared is not None
                 else [v for v, unit in variables(module) if unit == "K"])
        out += [n for n in names if n not in out]
    return out


def script_name(module) -> str:
    return module.__name__.split(".")[-1]


def labels_of(module) -> tuple[str, ...]:
    """The CAST labels a script owns."""
    return tuple(str(x).lower() for x in getattr(module, "CAST_LABELS", ()))


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
                labels.setdefault(label.lower(), module)
    return labels, errors


# --- the composed grammar ----------------------------------------------------------

def _request_only(label, request):
    return request


def label_commands(scripts=None, build=_request_only) -> list[tuple]:
    """Every declared command behind its label (``hvc vent open``), for the
    scripts named in `scripts` (all on the path when None). Each builder
    returns ``build(label, request)``."""
    labels, _ = owners()
    out = []
    for label, module in labels.items():
        if scripts is not None and script_name(module) not in scripts:
            continue
        if label in RESERVED:
            raise GrammarError(f"{script_name(module)}: CAST label {label!r} is a plan keyword")
        for pattern, help, builder in commands(module):
            out.append((f"{label} {pattern}", help, _bound(label, builder, build)))
    return out


def _bound(label, builder, build):
    def make(*captures):
        req = builder(*captures) if callable(builder) else copy.deepcopy(builder)
        if not isinstance(req, dict) or not req:
            raise GrammarError(f"{label}: nothing to send")
        return build(label, req)
    return make


def grammar(scripts=None) -> Grammar:
    return Grammar(label_commands(scripts))


def request(label: str, words: list[str]) -> dict:
    """The request dict for `label` from the words typed after it."""
    labels, _ = owners()
    module = labels.get(label.lower())
    if module is not None and not commands(module):
        raise GrammarError(f"{label} takes no commands: `status {label}` shows its readings")
    return grammar().parse([label, *words])


def reports_results(label: str) -> bool:
    """True when the rScript that owns `label` answers each request with a
    result (it lists the label in RESULT_LABELS), so a sender can wait for it."""
    labels, _ = owners()
    module = labels.get(label.lower())
    return label.lower() in (str(x).lower() for x in getattr(module, "RESULT_LABELS", ()))


# --- sending -----------------------------------------------------------------------

REPLY_S = 15.0     # how long the command box and the cast tab wait for a result


def send(label: str, request: dict, *, host: dict | None, wait_result: bool | None = None,
         take_s: float | None = None, result_s: float = REPLY_S) -> dict:
    """Send a parsed request to `label`'s owner and say what became of it, the
    same way for the command box, the cast tab and `labcli cast`.

    `host` is the running host's lock. With none the command is refused and
    nothing is written: the host clears CAST when it starts, so a request written
    beforehand would be lost. A command the owner's rules forbid right now
    (formslab.rscripts.rules) is refused too, with the reason. Returns {label,
    request, state, ok, messages, text},
    where state is "refused" (not sent), "not_taken", "cleared", "taken" or "done",
    and `text` says it in one line."""
    from formslab.console.cast import castutils
    from formslab.rscripts.rules import refusal

    label = label.lower()
    out = {"label": label, "request": request, "messages": []}
    if host is None:
        return {**out, "state": "refused", "ok": False,
                "text": "refused: no run is going, so nothing would apply it; nothing sent"}
    if why := refusal(label, request):
        return {**out, "state": "refused", "ok": False, "messages": [why],
                "text": f"refused, nothing sent. {why}"}
    if wait_result is None:
        wait_result = reports_results(label)
    take_s = castutils.TAKE_S if take_s is None else take_s
    r = castutils.send_request(request, label, wait_result=wait_result, take_s=take_s,
                               result_s=result_s)
    state, messages, plan = r["state"], list(r.get("messages") or []), host.get("plan", "?")
    if state == "not_taken":
        ok, text = False, (f"not taken within {take_s:g} s; it stays queued until plan {plan} "
                           f"ends. Does that plan load the rScript that owns {label}?")
    elif state == "cleared":
        ok, text = False, "cleared before it was taken (the run ended or was reset)"
    elif state == "taken":
        ok, text = True, "taken; result pending (see the log)" if wait_result else f"taken by plan {plan}"
    elif r.get("ok"):
        ok, text = True, "done" + (": " + "; ".join(messages) if messages else "")
    else:
        ok, text = False, "refused: " + ("; ".join(messages) or "no reason given")
    return {**out, "state": state, "ok": ok, "messages": messages, "text": text}


def parts(label: str) -> dict[str, str]:
    """{command word: kind of part} as the owner of `label` declares it (PARTS: a
    dict, or a function returning one). Empty for an owner that declares none."""
    labels, _ = owners()
    value = getattr(labels.get(str(label).lower()), "PARTS", None)
    value = value() if callable(value) else (value or {})
    return {str(k).lower(): str(v) for k, v in value.items()}


def part_of(label: str, words) -> str | None:
    """The kind of part a command is about: the part named by the word after the
    label, shared by every keyword of the command (`hvc gate open` is a valve)."""
    words = list(words)
    return parts(label).get(str(words[1]).lower()) if len(words) > 1 else None


def option_parts(words, options) -> list:
    """The part for each option that can follow `words`: right after the label,
    each option's own; later, the command's."""
    words = list(words)
    if not words:
        return [None] * len(options)
    if len(words) == 1:
        own = parts(words[0])
        return [own.get(o.text.lower()) if o.kind == "word" else None for o in options]
    return [part_of(words[0], words)] * len(options)


def card_part(card: dict, then: int | None = None) -> str | None:
    """The part a help card's command is about, from its structured usage; for a
    `when` rule (`then`: where its then is), the part of the command after it."""
    w = card.get("words") or []
    if then is not None:
        w = w[then + 1:]
    elif w and w[0].get("text", "").lower() == "when":
        return None
    if len(w) < 2:
        return None
    second = w[1]
    word = second["choices"][0] if second.get("role") == "choice" else second["text"]
    return parts(w[0]["text"]).get(word.lower())


# How a yes/no reading reads, by the kind of part its group is about.
_WORDS = {"valve": ("Open", "Closed"), "pump": ("On", "Off")}


def readings(label: str, status: dict) -> list[dict]:
    """`label`'s status block as the status page shows it: [{title, part, rows:
    [{key, name, value, text?}]}], from its owner's READINGS. That is a dict
    {key: name}, or a function (label, flat status) returning [(title, part,
    [(key, name) or (key, name, (yes, no))])]. Nested blocks are flattened as
    "1 vset". A row named None is claimed but not shown (it repeats another). A
    reading no group claims goes in a last group, "Other" (no title when it is the
    only one). A yes/no value carries `text`: Open/Closed in a valve
    group, On/Off in a pump group, the row's own words, or Yes/No."""
    flat = dict(_flat_items(status or {}))
    labels, _ = owners()
    declared = getattr(labels.get(str(label).lower()), "READINGS", None)
    try:
        if callable(declared):
            groups = list(declared(str(label).lower(), flat))
        else:
            groups = [(None, None, list((declared or {}).items()))]
    except Exception:                             # a naming mistake must not break the page
        groups = []
    out, claimed = [], set()
    for title, part, rows in groups:
        shown = []
        for row in rows:
            key, name, words = (*row, None)[:3]
            if key in flat and key not in claimed:
                claimed.add(key)
                if name is not None:
                    shown.append(_row(key, name, flat[key], words or _WORDS.get(part)))
        if shown:
            out.append({"title": title, "part": part, "rows": shown})
    rest = [_row(k, k, v, None) for k, v in flat.items() if k not in claimed]
    if rest:
        out.append({"title": "Other" if out else None, "part": None, "rows": rest})
    return out


def _row(key, name, value, words) -> dict:
    row = {"key": key, "name": str(name), "value": value}
    if isinstance(value, bool):
        yes, no = words or ("Yes", "No")
        row["text"] = yes if value else no
    return row


def _flat_items(d: dict, prefix: str = ""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat_items(v, f"{prefix}{k} ")
        else:
            yield f"{prefix}{k}", v


def _flat_keys(d: dict, prefix: str = "") -> list[str]:
    keys = []
    for k, v in d.items():
        if isinstance(v, dict):
            keys += _flat_keys(v, f"{prefix}{k} ")
        else:
            keys.append(f"{prefix}{k}")
    return keys


def describe(words: list[str]) -> dict:
    """For the command box's help card: {cards, rules}. `rules` are the
    prerequisites of the command `words` are, with their status now."""
    from formslab.rscripts.rules import explain

    g = grammar()
    cards = [{**c, "part": card_part(c)} for c in g.describe(words)]
    rules = []
    if cards and cards[0]["complete"]:
        try:
            rules = explain(words[0], g.parse(words))
        except GrammarError:
            pass
    return {"cards": cards, "rules": rules}


def complete(words: list[str]) -> list[Option]:
    """What can come after `words` in a cast command."""
    return grammar().complete(words)


def help_rows() -> list[tuple[str, str, str]]:
    """(script, usage, meaning) for every declared command."""
    labels, _ = owners()
    rows, done = [], set()
    for module in labels.values():
        if id(module) in done:
            continue
        done.add(id(module))
        prefix = "|".join(module.CAST_LABELS)
        rows += [(script_name(module), f"{prefix} {usage}", meaning)
                 for usage, meaning in Grammar(commands(module)).rows()]
    return rows
