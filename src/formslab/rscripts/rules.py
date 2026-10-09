"""What must be true before a command is sent, declared by the rScript that owns it.

A script that owns CAST labels may declare, beside its COMMANDS:

    RULES = [Rule("hvc", {"vent": True}, (closed("rough"), NO_FAULT), "why")]
    def RULE_STATE(status): ...        # its CAST status block -> {name: value}
    def RULE_EFFECTS(request): ...     # what a request leaves set, for reading plans

(RULES may be a function returning the list, like COMMANDS.) A rule covers the
requests its `when` matches -- the request dict the grammar builds, so `hvc vent
open` and `hvc vent OPEN` are one thing -- and lists the conditions that must hold
first. Conditions share one namespace: a device state (`rough closed`), a value the
script publishes (`Platen at least 10 °C`), and `no fault`.

They are checked twice:

- while a plan is read (`formslab.sequence.rules`): a step that definitely breaks
  a rule is an error and the plan cannot start; one that depends on the state
  before the run is a warning;
- when a command is sent, from the console, the GUI or a plan step (`refusal`),
  against the owner's latest status block. A condition that cannot be checked
  there (the block is old, a value is missing) refuses: the safe answer.

Besides the declared rules, a supply channel the hardware map gives an owner
(devices/dp832a/wiring.py) is refused to everyone else while that owner runs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

STALE_S = 20.0      # a status block older than this cannot prove a condition

_ON = ("open", "on", "start")
_OFF = ("close", "closed", "off", "stop")


@dataclass(frozen=True)
class Cond:
    kind: str                 # "device" | "value" | "fault"
    name: str = ""
    want: object = None       # device: bool; value: (side, limit, unit or None)
    text: str = ""
    live: bool = False        # only checkable when sent (never while a plan is read)
    proof: str = ""           # a plan line that establishes it (`hvc rough close`)


def device(name: str, on: bool, text: str, proof: str = "") -> Cond:
    return Cond("device", name, bool(on), text, proof=proof)


def value(name: str, side: str, limit: float, unit: str | None = None, *, shown: str | None = None,
          live: bool = False, called: str | None = None) -> Cond:
    """`name` above/below `limit` (tested as at least / at most). `unit` (C or K)
    converts from the value's own unit; `shown` is only how the limit is written;
    `called` is the part's name in the text (`Platen at least 10 °C`). Its proof is
    the `until` that waits for it."""
    tail = f" {unit or shown}" if (unit or shown) else ""
    shown_tail = tail.replace(" C", " °C") if unit == "C" else tail
    op = ">=" if side == "above" else "<="
    text = (f"{called} {'at least' if side == 'above' else 'at most'} {limit:g}{shown_tail}" if called
            else f"{name} {op} {limit:g}{tail}")
    proof = "" if live else f"until {name} {op} {limit:g}{' ' + unit if unit else ''}"
    return Cond("value", name, (side, float(limit), unit), text, live, proof)


NO_FAULT = Cond("fault", text="No fault", live=True)


def listed(items) -> str:
    """`a`, `a and b`, `a, b and c`."""
    items = list(items)
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _sentence(s: str) -> str:
    return s[:1].upper() + s[1:] + ("" if s.endswith(".") else ".")


@dataclass(frozen=True)
class Rule:
    label: str
    when: dict | None                       # {request key: True/False/value}; None: every request
    requires: tuple[Cond, ...]
    why: str
    unless: Callable[[dict], bool] | None = None   # requests it never covers

    def covers(self, request: dict) -> bool:
        if not request or (self.unless and self.unless(request)):
            return False
        if self.when is None:
            return True
        return all(k in request and state_of(request[k]) == v for k, v in self.when.items())


def state_of(v):
    """True/False for the words a request uses (open/close, on/off...); else v."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in _ON:
            return True
        if v.lower() in _OFF:
            return False
    return v


def convert(x: float, have: str | None, want: str | None) -> float:
    if want is None or have is None or have == want:
        return x
    if (have, want) == ("K", "C"):
        return x - 273.15
    if (have, want) == ("C", "K"):
        return x + 273.15
    raise ValueError(f"cannot compare a value in {have} with a limit in {want}")


# --- what a script declares --------------------------------------------------------

def rules_for(module, label: str) -> list[Rule]:
    from formslab.rscripts.cast import _declared

    if module is None:
        return []
    return [r for r in _declared(module, "RULES") if r.label.lower() == label.lower()]


def covering(module, label: str, request: dict) -> list[Rule]:
    return [r for r in rules_for(module, label) if r.covers(request)]


def effects(module, request: dict) -> dict | None:
    """{device: state} a request leaves set; None when it may change anything."""
    fn = getattr(module, "RULE_EFFECTS", None)
    if callable(fn):
        return fn(request)
    return {k: v for k, v in ((k, state_of(v)) for k, v in request.items()) if isinstance(v, bool)}


def owned_channels(label: str, request: dict) -> list[tuple[str, dict]]:
    """(channel, {feeds, owner}) for each channel of supply `label` the request
    touches that the hardware map gives an owner."""
    if not label.lower().startswith("psu"):
        return []
    from formslab.devices.dp832a import wiring

    return [(ch, info) for ch in request if str(ch).isdigit()
            if (info := wiring.channel(label, ch)).get("owner")]


# --- the live check ----------------------------------------------------------------

def holds(c: Cond, state: dict, units: dict) -> bool | None:
    """Whether condition `c` holds in `state`; None when it cannot be told."""
    if c.kind == "fault":
        sev = state.get("fault")
        return None if sev is None else str(sev).upper() != "F"
    v = state.get(c.name)
    if v is None:
        return None
    if c.kind == "device":
        return state_of(v) == c.want
    side, limit, unit = c.want
    try:
        x = convert(float(v), units.get(c.name), unit)
    except (TypeError, ValueError):
        return None
    return x >= limit if side == "above" else x <= limit


def _blocks() -> dict:
    from formslab.console.safefile import read_json
    from formslab.state import cast_state_path

    try:
        data = read_json(cast_state_path())
    except (OSError, ValueError):
        return {}
    return {str(k).lower(): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _fresh(block: dict, now: float) -> bool:
    ts = block.get("timestamp")
    return isinstance(ts, (int, float)) and now - ts <= STALE_S


def live_state(module, block: dict, now: float) -> tuple[dict, str | None]:
    """The owner's state by rule names, from its CAST block; and why it is empty."""
    from formslab.rscripts.cast import labels_of

    fn = getattr(module, "RULE_STATE", None)
    if not callable(fn):
        return {}, None
    if not _fresh(block, now):
        ts = block.get("timestamp")
        label = labels_of(module)[0] if labels_of(module) else "the instrument"
        return {}, (f"Nothing heard from {label} for {_age(now - ts)}" if isinstance(ts, (int, float))
                    else f"Nothing heard from {label} yet")
    return fn(block.get("status") or {}) or {}, None


def _age(s: float) -> str:
    return f"{s:.0f} s" if s < 120 else f"{s / 60:.0f} min" if s < 7200 else f"{s / 3600:.0f} h"


def explain(label: str, request: dict, *, blocks: dict | None = None,
            now: float | None = None) -> list[dict]:
    """For the help card: each rule `request` to `label` meets, {why,
    conditions: [{text, status}]}, status ok, broken or unknown right now."""
    from formslab.rscripts import cast

    label = label.lower()
    now = time.time() if now is None else now
    labels, _ = cast.owners()
    module = labels.get(label)
    blocks = _blocks() if blocks is None else blocks
    out = []
    for ch, info in owned_channels(label, request):
        owner = next((m for m in labels.values() if cast.script_name(m) == info["owner"]), None)
        running = owner is not None and any(_fresh(blocks.get(lb) or {}, now) for lb in cast.labels_of(owner))
        out.append({"why": f"{label} ch{ch} feeds the {info.get('feeds') or 'bench'}, and "
                           f"{info['owner']} drives it while it runs.",
                    "conditions": [{"text": f"{info['owner']} not running",
                                    "status": "broken" if running else "ok"}]})
    rules = covering(module, label, request)
    if rules:
        state, stale = live_state(module, blocks.get(label) or {}, now)
        units = dict(cast.variables(module))
        for rule in rules:
            conditions = []
            for c in rule.requires:
                ok = holds(c, state, units)
                conditions.append({"text": c.text,
                                   "status": "ok" if ok else "broken" if ok is False else "unknown"})
            out.append({"why": rule.why + (f" {_sentence(stale)}" if stale else ""), "conditions": conditions})
    return out


def refusal(label: str, request: dict, *, blocks: dict | None = None, now: float | None = None) -> str | None:
    """Why `request` to `label` must not be sent now, or None."""
    return " ".join(why for why, _ in assess(label, request, blocks=blocks, now=now)) or None


def _only_off(request) -> bool:
    """A supply request that only turns channels off ({"1": {"on": False}})."""
    channels = [v for k, v in (request or {}).items() if str(k).isdigit()]
    return bool(channels) and all(isinstance(v, dict) and v == {"on": False} for v in channels)


def assess(label: str, request: dict, *, blocks: dict | None = None,
           now: float | None = None, ending: bool = False) -> list[tuple[str, bool]]:
    """[(why, definite)] for each rule `request` to `label` would break now.
    `definite` is False when the rule fails only on conditions that cannot be
    told yet (the owner has not reported): a plan step waits for those.
    `ending`: the end script's own step, which may turn off a channel a routine owns."""
    from formslab.rscripts import cast

    label = label.lower()
    now = time.time() if now is None else now
    labels, _ = cast.owners()
    module = labels.get(label)
    blocks = _blocks() if blocks is None else blocks
    reasons = []

    for ch, info in owned_channels(label, request):
        if ending and _only_off(request):
            continue
        owner = next((m for m in labels.values() if cast.script_name(m) == info["owner"]), None)
        if owner is not None and any(_fresh(blocks.get(lb.lower()) or {}, now)
                                     for lb in cast.labels_of(owner)):
            reasons.append((f"{label} ch{ch} feeds the {info.get('feeds') or 'bench'}, and "
                            f"{info['owner']} drives it while it runs.", True))

    rules = covering(module, label, request)
    if rules:
        state, stale = live_state(module, blocks.get(label) or {}, now)
        units = dict(cast.variables(module))
        for rule in rules:
            missing, definite = [], False
            for c in rule.requires:
                ok = holds(c, state, units)
                if ok is False:
                    missing.append(c.text)
                    definite = True
                elif ok is None:
                    missing.append(f"{c.text} (unknown)")
            if missing:
                reasons.append((f"Needs {listed(missing)}. {rule.why}"
                                + (f" {_sentence(stale)}" if stale else ""), definite))
    return reasons
