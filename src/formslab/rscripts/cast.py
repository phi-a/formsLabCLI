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
RESERVED = ("hold", "until", "log", "load", "record")


def _declared(module, attr: str) -> list:
    value = getattr(module, attr, ())
    return list(value() if callable(value) else value)


def commands(module) -> list[tuple]:
    """A script's (pattern, help, builder) list."""
    return _declared(module, "COMMANDS")


def variables(module) -> list[tuple[str, str | None]]:
    """A script's (name, unit) list of the values it publishes."""
    return [(n, u or None) for n, u in _declared(module, "VARIABLES")]


def script_name(module) -> str:
    return module.__name__.split(".")[-1]


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
    beforehand would be lost. Returns {label, request, state, ok, messages, text},
    where state is "refused" (not sent), "not_taken", "cleared", "taken" or "done",
    and `text` says it in one line."""
    from formslab.console.cast import castutils

    label = label.lower()
    out = {"label": label, "request": request, "messages": []}
    if host is None:
        return {**out, "state": "refused", "ok": False,
                "text": "refused: no run is going, so nothing would apply it; nothing sent"}
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
