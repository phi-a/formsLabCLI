"""The rules (formslab.rscripts.rules) checked while a plan is read.

The steps are walked in order, keeping what the plan itself has made true:

- a device state a command set (`hvc rough close` -> rough closed) until a
  command changes it, or a cycle operation (`hvc vent2atm`, `start`...) makes
  every state unknown again;
- a value an `until` waited for (`until platenT below 60 C`), until the next
  `hold` or command, after which it may have drifted.

A step whose rule the plan definitely breaks (it opened rough two lines up) is an
error. One whose conditions the plan does not establish is a warning: they depend
on the chamber at the start, and are checked live when the step runs. A command
to a supply channel whose owner the plan loads is an error.
"""
from __future__ import annotations

from formslab.rscripts import rules as R
from formslab.sequence.spec import origin_text

# A condition's status at a step: established by the plan, broken by it, not
# known until the run, or checkable only live (no fault, a supply voltage).
OK, BROKEN, UNKNOWN, LIVE = "ok", "broken", "unknown", "live"


def walk(steps, scripts, published: dict):
    """For each command step in `steps` ([(line, Segment)]): (line, findings),
    a finding {why, conditions: [{text, status}]} per rule the step meets."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    units = {k.lower(): u for k, u in published.items()}
    devices: dict[str, tuple[bool, str]] = {}           # name -> (state, where it was set)
    guards: list[tuple[str, str, float]] = []        # (variable, side, limit in its own unit)

    for n, seg in steps:
        if seg.verb == "until":
            p = seg.params
            own = units.get(p["variable"].lower())
            try:
                guards.append((p["variable"].lower(), p["side"], R.convert(p["value"], p["unit"], own)))
            except ValueError:
                pass
            continue
        if seg.verb == "hold":
            guards = []
            continue
        if seg.verb != "command":
            continue
        label, request = seg.params["label"], seg.params["request"]
        module = labels.get(label)
        findings = []

        for ch, info in R.owned_channels(label, request):
            loaded = info["owner"] in scripts
            findings.append({"why": f"{label} ch{ch} feeds the {info.get('feeds') or 'bench'}, and "
                                    f"{info['owner']} drives it while it runs.",
                             "owner": info["owner"],
                             "conditions": [{"text": f"{info['owner']} not loaded",
                                             "status": BROKEN if loaded else OK}]})

        for rule in R.covering(module, label, request):
            conditions = []
            for c in rule.requires:
                if c.live:
                    status = LIVE
                elif c.kind == "device":
                    known = devices.get(c.name)
                    status = UNKNOWN if known is None else OK if known[0] == c.want else BROKEN
                    if status == BROKEN:
                        conditions.append({"text": f"{c.text} ({known[1]} changed it)", "status": status})
                        continue
                else:
                    status = OK if _guarded(c, guards, units) else UNKNOWN
                conditions.append({"text": c.text, "status": status, **({"proof": c.proof} if c.proof else {})})
            findings.append({"why": rule.why, "conditions": conditions})

        if seg.origin:                                   # a step from a block
            for f in findings:
                f["origin"] = origin_text(seg.origin)
        yield n, findings
        left = R.effects(module, request)
        where = f"{origin_text(seg.origin)} at line {n}" if seg.origin else f"line {n}"
        if left is None:
            devices = {}
        else:
            devices.update({k: (v, where) for k, v in left.items()})
        guards = []


def check(steps, scripts, published: dict) -> tuple[list, list]:
    """(errors, warnings) as (line, message) for `steps`, [(line, Segment)]."""
    errors, warnings = [], []
    for n, findings in walk(steps, scripts, published):
        for f in findings:
            broken = [c["text"] for c in f["conditions"] if c["status"] == BROKEN]
            unknown = [c for c in f["conditions"] if c["status"] == UNKNOWN]
            within = f"In {f['origin']}: " if f.get("origin") else ""
            if broken and "owner" in f:
                errors.append((n, within + f["why"].replace(f"{f['owner']} drives it while it runs",
                                                            f"{f['owner']}, loaded here, drives it")))
            elif broken:
                errors.append((n, f"{within}Needs {R.listed(broken)}. {f['why']}"))
            elif unknown:
                proofs = [c["proof"] for c in unknown if c.get("proof")]
                warnings.append((n, f"{within}Checked when the step runs: {R.listed([c['text'] for c in unknown])}."
                                    + (f" To settle it here, add {R.listed(proofs)} before this step."
                                       if proofs else "")))
    return errors, warnings


def at_line(steps, scripts, published: dict, line: int) -> list[dict]:
    """The findings for the command step on `line`, or for every command of the
    block called there ([] for any other line)."""
    return [f for n, findings in walk(steps, scripts, published) if n == line for f in findings]


def _guarded(c, guards, units) -> bool:
    """An `until` just before the step already proved condition `c`."""
    side, limit, unit = c.want
    own = units.get(c.name.lower())
    try:
        want = R.convert(limit, unit, own)
    except ValueError:
        return False
    return any(v == c.name.lower() and s == side and (g >= want if side == "above" else g <= want)
               for v, s, g in guards)
