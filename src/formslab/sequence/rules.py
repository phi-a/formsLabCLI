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


def check(steps, scripts, published: dict) -> tuple[list, list]:
    """(errors, warnings) as (line, message) for `steps`, [(line, Segment)]."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    units = {k.lower(): u for k, u in published.items()}
    devices: dict[str, tuple[bool, int]] = {}
    guards: list[tuple[str, str, float]] = []        # (variable, side, limit in its own unit)
    errors, warnings = [], []

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

        for ch, info in R.owned_channels(label, request):
            if info["owner"] in scripts:
                errors.append((n, f"{label} ch{ch} feeds the {info.get('feeds') or 'bench'}; "
                                  f"{info['owner']}, loaded here, drives it"))

        for rule in R.covering(module, label, request):
            broken, unknown = [], []
            for c in rule.requires:
                if c.live:
                    continue
                if c.kind == "device":
                    known = devices.get(c.name)
                    if known is None:
                        unknown.append(c.text)
                    elif known[0] != c.want:
                        broken.append(f"{c.text} (line {known[1]} changed it)")
                elif not _guarded(c, guards, units):
                    unknown.append(c.text)
            if broken:
                errors.append((n, f"needs {', '.join(broken)}: {rule.why}"))
            elif unknown:
                warnings.append((n, f"needs {', '.join(unknown)}, which this plan does not "
                                    f"establish; checked when the step runs. Why: {rule.why}"))

        left = R.effects(module, request)
        if left is None:
            devices = {}
        else:
            devices.update({k: (v, n) for k, v in left.items()})
        guards = []
    return errors, warnings


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
