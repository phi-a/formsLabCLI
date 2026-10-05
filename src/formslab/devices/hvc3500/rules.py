"""The chamber's prerequisites, as rules rLACO declares (see formslab.rscripts.rules).

The PLC enforces its own interlocks and refuses what breaks them; these say the
same things before a command is sent, in words, so a plan that would be refused
is caught while it is written and the command box can say why. Thresholds come
from the bench profile (tvac_bench.json `limits`).
"""
from __future__ import annotations

from formslab.rscripts.rules import NO_FAULT, Rule, device, value

from .laco import OPERATIONS, PART_NAMES, PUMPS, VALVES, _flag
from .profile import BenchProfile


# A condition on a part, in the chamber screen's name, with the command that sets it.
def closed(v): return device(v, False, f"{PART_NAMES[v]} closed", f"hvc {v} close")
def opened(v): return device(v, True, f"{PART_NAMES[v]} open", f"hvc {v} open")
def on(p): return device(p, True, f"{PART_NAMES[p]} on", f"hvc {p} on")
def off(p): return device(p, False, f"{PART_NAMES[p]} off", f"hvc {p} off")


def _safe(request: dict) -> bool:
    """A request that only makes the chamber safer: valves closed, `stop`, zones
    off, `closeall`, `reset`, `abort`. Allowed even with a fault."""
    def ok(k, v):
        if k in VALVES:
            return _flag(v, ("open", "close")) is False
        if k.endswith("_control"):
            return _flag(v, ("on", "off")) is False
        return k in ("stop_pumping", "close_all", "reset", "abort") and v is True
    return bool(request) and all(ok(k, v) for k, v in request.items())


def laco_rules(profile: BenchProfile) -> list[Rule]:
    lim = profile.limits
    lo, hi = float(lim.get("min_vent_temp_c", 10.0)), float(lim.get("max_vent_temp_c", 60.0))
    window = tuple(c for z in profile.zones
                   for c in (value(f"{z}T", "above", lo, "C", called=z.capitalize()),
                             value(f"{z}T", "below", hi, "C", called=z.capitalize())))
    sealed = (closed("rough"), closed("gate"), *window)     # (a fault is the last rule's, for every command)
    venting = (f"Air may only come in with the chamber sealed from the pumps and every zone "
               f"between {lo:g} and {hi:g} °C.")
    gate = (on("turbo"), opened("foreline"))
    crossover = lim.get("high_vac_crossover_torr")
    if crossover:
        gate += (value("chamberP", "below", crossover, shown=profile.pressure_unit, called="Chamber pressure"),)
    return [
        Rule("hvc", {"vent": True}, sealed, venting),
        Rule("hvc", {"fill": True}, sealed, venting),
        Rule("hvc", {"rough": True},
             (off("turbo"), closed("vent"), closed("fill"), closed("foreline"), closed("gate")),
             "The vacuum pump must not pull on a chamber open to air or to the turbo side, and a "
             "running turbo pump must not be left without its backing."),
        Rule("hvc", {"pump": False}, (closed("rough"), closed("foreline"), off("turbo")),
             "Stopping the vacuum pump with a valve open to it lets air back in, so hvc stop "
             "closes the vacuum valve first."),
        Rule("hvc", {"foreline": True}, (closed("rough"),),
             "The vacuum pump serves either the chamber, through the vacuum valve, or the turbo "
             "pump, through the foreline valve, not both."),
        Rule("hvc", {"foreline": False}, (off("turbo"),),
             "The foreline valve is a running turbo pump's only backing, so the turbo stops first."),
        Rule("hvc", {"turbo": True}, (opened("foreline"),),
             "The turbo pump needs the vacuum pump behind it, through the foreline valve."),
        Rule("hvc", {"gate": True}, gate,
             "The gate valve joins the chamber to the turbo pump, which must be running, backed, "
             "and take over only below the crossover pressure."),
        Rule("hvc", None, (NO_FAULT,),
             "During a fault of severity F, only closing valves, stop, zones off, closeall, reset "
             "and abort are allowed until it is cleared.",
             unless=_safe),
    ]


def laco_effects(request: dict) -> dict | None:
    """What a request leaves set, for reading a plan; None when it may change
    anything (a cycle operation runs the controller's own valve sequence)."""
    if any(request.get(k) is True for k in (*OPERATIONS, "start", "abort", "reset")) \
            or "recipe_run" in request:
        return None
    out = {}
    if request.get("stop_pumping") is True:
        out.update(rough=False, pump=False)
    for name in (*VALVES, *PUMPS):
        if name in request:
            v = _flag(request[name], ("open", "close") if name in VALVES else ("on", "off"))
            if v is not None:
                out[name] = v
    return out


def laco_state(status: dict) -> dict:
    """rLACO's CAST block by rule names: devices as they are, `platenT`/`shroudT`
    in K (as published), `chamberP`, and the fault severity."""
    s = {name: status.get(name) for name in (*VALVES, *PUMPS)}
    s["chamberP"] = status.get("pressure")
    s["fault"] = status.get("fault_severity")
    for key, t in status.items():
        if key.endswith(" C") and not key.endswith(" setpoint C"):
            s[f"{key[:-2]}T"] = None if t is None else t + 273.15
    return s
