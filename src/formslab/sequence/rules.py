"""The rules (formslab.rscripts.rules) checked while a plan is read.

The steps are walked in order, keeping what the plan itself has made true:

- a device state a command set (`hvc rough close` -> rough closed) until a
  command changes it, or a cycle operation (`hvc vent2atm`, `start`...) makes
  every state unknown again;
- a value an `until` waited for (`until platenT < 60 C`), until the next
  `hold` or command, after which it may have drifted.

A `when` rule may send its command at any moment after its line, so what that
command sets is not known from there on; its own prerequisites are checked
when it fires (a supply channel another routine drives is still an error here).

A loop is walked as its second pass would find things (see `walk`), so a step
that a later step in the same loop undoes is caught.

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


def _tree(steps):
    """[(line, Segment)] with each loop gathered: a `repeat` becomes (line, Segment,
    [its body as a tree]); its `end` is dropped. Unpaired lines are already errors;
    here a stray `end` is ignored and an open loop runs to the last step."""
    root: list = []
    stack = [root]
    for n, seg in steps:
        if seg.verb == "repeat":
            body: list = []
            stack[-1].append((n, seg, body))
            stack.append(body)
        elif seg.verb == "end":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append((n, seg))
    return root


def _join(a: dict, b: dict) -> dict:
    """What is known either way: a state both agree on."""
    return {k: v for k, v in a.items() if k in b and b[k][0] == v[0]}


def walk(steps, scripts, published: dict):
    """For each command step in `steps` ([(line, Segment)]): (line, findings),
    a finding {why, conditions: [{text, status}]} per rule the step meets.

    A loop's body is walked twice: the first pass from the state before it, the
    second from the end of the first, as a later pass finds things. Each step is
    reported with the worse of the two. After a loop that runs at least once
    (`times`, `until end`), the state is the end of a pass; after one that may run
    none (`until`), what that and the state before agree on, and its condition is
    proved, as an `until` proves it."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    units = {k.lower(): u for k, u in published.items()}
    out: list = []
    volatile: dict = {}            # device -> the states `when` rules may set it to, at any time
    anything = []                  # a `when` that may change every device (a cycle operation)

    def forget(devices):
        """What is still known, given the rules armed so far: a state no rule may change."""
        if anything:
            return {}
        return {k: v for k, v in devices.items() if not volatile.get(k, set()) - {v[0]}}

    def guard(p):
        """What a wait proves after it: a number past its limit. Not an on/off
        value (no rule needs one), and not a wait that may have gone on without it."""
        side = {"<": "below", "<=": "below", ">": "above", ">=": "above"}.get(p["op"])
        if side is None or p.get("go_on"):
            return []
        own = units.get(p["variable"].lower())
        try:
            return [(p["variable"].lower(), side, R.convert(p["value"], p["unit"], own))]
        except ValueError:
            return []

    def run(items, devices, guards, sink):
        for item in items:
            if len(item) == 3:                          # a loop
                n, seg, body = item
                first = [] if sink is not None else None
                end1, _ = run(body, dict(devices), [], first)
                if seg.params.get("times") == 1:
                    found, end = first, end1
                else:
                    second = [] if sink is not None else None
                    end, _ = run(body, dict(end1), [], second)
                    found = _worse(first, second) if sink is not None else None
                if sink is not None:
                    sink.extend(found)
                if seg.params.get("until"):              # it may run no pass
                    devices, guards = _join(_join(devices, end1), end), guard(seg.params["until"])
                else:
                    devices, guards = end, []
                continue
            devices, guards = step(item, devices, guards, sink)
            devices = forget(devices)
        return devices, guards

    def step(item, devices, guards, sink):
        n, seg = item
        if seg.verb == "until":
            return devices, guards + guard(seg.params)
        if seg.verb == "hold":
            return devices, []
        rule = seg.verb == "when"
        if rule and seg.params["do"]["verb"] == "command":
            label, request = seg.params["do"]["label"], seg.params["do"]["request"]
        elif seg.verb == "command":
            label, request = seg.params["label"], seg.params["request"]
        else:
            return devices, guards
        module = labels.get(label)
        findings = []

        for ch, info in R.owned_channels(label, request):
            loaded = info["owner"] in scripts
            findings.append({"why": f"{label} ch{ch} feeds the {info.get('feeds') or 'bench'}, and "
                                    f"{info['owner']} drives it while it runs.",
                             "owner": info["owner"],
                             "conditions": [{"text": f"{info['owner']} not loaded",
                                             "status": BROKEN if loaded else OK}]})

        for covering in R.covering(module, label, request):
            conditions = []
            for c in covering.requires:
                if c.live or rule:                          # a rule's: checked when it fires
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
            findings.append({"why": covering.why, "conditions": conditions})

        if seg.origin:                                   # a step from a block
            for f in findings:
                f["origin"] = origin_text(seg.origin)
        if sink is not None:
            sink.append((n, findings))
        left = R.effects(module, request)
        if rule:                                            # it may act at any time from here
            if left is None:
                anything.append(n)
            else:
                for k, v in left.items():
                    volatile.setdefault(k, set()).add(v)
            return devices, guards
        where = f"{origin_text(seg.origin)} at line {n}" if seg.origin else f"line {n}"
        if left is None:
            return {}, []
        return {**devices, **{k: (v, where) for k, v in left.items()}}, []

    run(_tree(steps), {}, [], out)
    return out


_RANK = {OK: 0, LIVE: 1, UNKNOWN: 2, BROKEN: 3}


def _worse(first, second):
    """Two passes' findings for the same steps, each condition as the worse of the two."""
    out = []
    for (n, a), (_, b) in zip(first, second):
        merged = []
        for fa, fb in zip(a, b):
            conditions = [ca if _RANK[ca["status"]] > _RANK[cb["status"]] else cb
                          for ca, cb in zip(fa["conditions"], fb["conditions"])]
            merged.append({**fb, "conditions": conditions})
        out.append((n, merged))
    return out


NOT_STARTED = "has no value yet"
ADD_START = "Add one before this step, or the wait can only time out."


def _waits_before_start(steps, scripts) -> list[tuple[int, str]]:
    """(line, message) for each wait on a value that its rScript publishes only
    once a request has started it (STARTED_BY: rOrbit's `orbit follow`, `orbit
    replay`), where the plan has sent no such request yet: that wait can only
    time out. A `repeat until` reads before each pass, so a body that starts it
    is in time for the second read."""
    from formslab.rscripts import cast

    labels, _ = cast.owners()
    starts = {}                                          # variable -> (label, script, request words)
    for label, module in labels.items():
        words = tuple(getattr(module, "STARTED_BY", ()))
        if words and cast.script_name(module) in scripts:
            for variable, _unit in cast.variables(module):
                starts[variable.lower()] = (label, cast.script_name(module), words)
    out: list = []

    def unstarted(variable, started):
        hit = starts.get(variable.lower())
        return hit if hit and hit[0] not in started else None

    def read(n, seg, variable, started):
        if hit := unstarted(variable, started):
            label, script, words = hit
            how = " or ".join(f"`{label} {w}`" for w in words)
            within = f"In {origin_text(seg.origin)}: " if seg.origin else ""
            out.append((n, f"{within}{variable} {NOT_STARTED}: {script} publishes it only after {how}. "
                           f"{ADD_START}"))

    def run(items, started):
        for item in items:
            if len(item) == 3:                           # a loop
                n, seg, body = item
                cond = seg.params.get("until")
                after = run(body, started)
                if cond:
                    read(n, seg, cond["variable"], after)
                # A pass runs unless a `repeat until` is met at once, which a value
                # not published yet cannot be.
                if not cond or unstarted(cond["variable"], started):
                    started = after
                continue
            n, seg = item
            if seg.verb == "until":
                read(n, seg, seg.params["variable"], started)
            elif seg.verb == "command":
                label, request = seg.params["label"], seg.params["request"]
                if any(h[0] == label and isinstance(request, dict) and any(w in request for w in h[2])
                       for h in starts.values()):
                    started = started | {label}
        return started

    if starts:
        run(_tree(steps), frozenset())
    return out


def check(steps, scripts, published: dict) -> tuple[list, list]:
    """(errors, warnings) as (line, message) for `steps`, [(line, Segment)]."""
    errors, warnings = list(_waits_before_start(steps, scripts)), []
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
