# --- rLACO: the LACO chamber (UIUC, HVC-3500 controller) ---
#
# Owns the chamber for the run through `formslab.devices.laco.LACO`:
#   - applies every request on CAST "hvc" as soon as it arrives (each loop),
#   - reads the chamber every poll_interval_s (tvac_bench.json) and right after
#     a request, publishes kelvin scalars (chamberP, <zone>T, target_<zone>,
#     <zone>_effSP, HVC_<sensor>) and the CAST "hvc" status block,
#   - appends the full reading to outputs/LACO.jsonl every LOG_INTERVAL s.
#
# The console commands are declared below (CAST_HELP / cast_request): typing
# `hvc vent open` in the cast panel writes {"vent": "open"} to CAST "hvc", and
# this script applies it. `LACO.apply` documents the whole request grammar.
#
# On host stop: if this run started pumping (pump on or rough valve opened) and
# it is still pumping, rShutdown ends it (rough closed, pump off). Everything
# else keeps its state -- the PLC holds temperatures, other valves stay as they
# are. Then the connection is released (the controller takes one client).
import json
import os
import time
from datetime import datetime, timezone

from formslab.config import output_dir
from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.devices.laco import LACO, OPERATIONS, PUMPS, VALVES
from formslab.rscripts import C2K, RScriptControl
from formslab.rscripts.cast import CastUsage, choice, integer, number

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "hvc"

# --- console commands --------------------------------------------------------------

CAST_LABELS = (LABEL,)
CAST_HELP = [
    ("hvc platen <C>", "Platen setpoint, C (refused outside the profile limits)"),
    ("hvc shroud <C>", "Shroud setpoint, C"),
    ("hvc <zone> on|off", "Thermal control for a zone (!ZS/!ZO)"),
    ("hvc <zone> rate <C/min>", "Zone rate setpoint"),
    ("hvc <zone> range <C>", "Zone control range"),
    ("hvc vacuum <P>", "Vacuum setpoint (profile pressure unit)"),
    ("hvc vacuum range <P>", "Vacuum control range"),
    ("hvc vacuum rate <value>", "Vacuum rate control"),
    ("hvc hold <s>", "Hold time"),
    ("hvc recipe <n>", "Select recipe n"),
    ("hvc recipe start|stop", "Run / stop the selected recipe (!RS/!RO)"),
    ("hvc start", "Start the cycle, or continue a held step (!CS)"),
    ("hvc abort", "Abort the running cycle (!CA)"),
    ("hvc reset", "Reset the controller; starts its recovery (!CR)"),
    ("hvc vent2atm|fill2atm|purge|closeall", "Cycle vacuum operation (!VA/!FA/!PS/!NA); "
                                             "acts only inside a running cycle"),
    ("hvc rough|vent|fill|foreline|gate open|close", "A valve: read first, verified; PLC interlocks apply"),
    ("hvc pump|turbo on|off", "A pump: read first, verified; PLC interlocks apply"),
    ("hvc stop", "End pumping: rough valve closed, then pump off"),
]


def cast_request(label, words):
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    if not words:
        raise CastUsage("hvc <command>; `help` lists them")
    w, rest = words[0].lower(), words[1:]
    n = len(rest)

    if w in profile.zones:
        lo, hi = profile.setpoint_bounds(w)
        if n == 1 and rest[0].lower() in ("on", "off"):
            return {f"{w}_control": rest[0].lower() == "on"}
        if n == 1:
            return {w: number(rest[0], f"{w} setpoint C", lo, hi)}
        if n == 2 and rest[0].lower() in ("rate", "range"):
            return {f"{w}_{rest[0].lower()}": number(rest[1], f"{w} {rest[0]}", 0)}
        raise CastUsage(f"hvc {w} <C> | on | off | rate <C/min> | range <C>")
    if w == "vacuum":
        if n == 1:
            return {"vacuum": number(rest[0], "vacuum setpoint", 0)}
        if n == 2 and rest[0].lower() in ("range", "rate"):
            return {f"vacuum_{rest[0].lower()}": number(rest[1], f"vacuum {rest[0]}", 0)}
        raise CastUsage("hvc vacuum <P> | range <P> | rate <value>")
    if w == "hold" and n == 1:
        return {"hold_s": number(rest[0], "hold time s", 0)}
    if w == "recipe" and n == 1:
        if rest[0].lower() in ("start", "stop"):
            return {"recipe_run": rest[0].lower() == "start"}
        return {"recipe": integer(rest[0], "recipe", 1, 20)}
    if w in ("start", "abort", "reset") and n == 0:
        return {w: True}
    op = "close_all" if w == "closeall" else w
    if op in OPERATIONS and n == 0:
        return {op: True}
    if w in VALVES and n == 1:
        return {w: "open" if choice(rest[0], ("open", "close"), w) else "close"}
    if w in PUMPS and n == 1:
        return {w: "on" if choice(rest[0], ("on", "off"), w) else "off"}
    if w == "stop" and n == 0:
        return {"stop_pumping": True}
    raise CastUsage(f"unknown hvc command {' '.join(words)!r}; `help` lists them")


# --- the routine -------------------------------------------------------------------

class rGlobal:
    disable = False
    POLL_INTERVAL = 5          # replaced by the profile's poll_interval_s
    LOG_INTERVAL = 30.0

    laco = None
    log_path = None
    _last_log_time = 0.0
    readERROR = 0
    started_pumping = False    # this run turned the pump on or opened the rough valve


rg = rGlobal


def _set(forms, nm, value, unit):
    var = forms.get_variable(nm)
    if var is None:
        var = forms.types.scalar(nm, unit=unit, overwrite=False)
    var.set(value=value, unit=unit)


def _chamber(forms):
    """The one LACO object, connected. None (and a status update) if it cannot connect."""
    if rg.laco is None:
        rg.laco = LACO()
        rg.POLL_INTERVAL = rg.laco.profile.poll_interval_s
    if not rg.laco.connected:
        try:
            rg.laco.connect()
            forms.log(f"HVC-3500 connected {rg.laco.endpoint}", component=name)
        except OSError as e:
            forms.log(f"HVC-3500 connect failed: {e}", level="ERROR", component=name)
            UpdateStatus(LABEL, {"connected": False})
            return None
    return rg.laco


def _publish(forms, laco, status):
    if status.pressure is not None:
        _set(forms, "chamberP", status.pressure, laco.profile.pressure_unit)
    for sensor_name, t in status.sensors.items():
        if t is not None:
            _set(forms, f"HVC_{sensor_name}", C2K(t), "K")
    for z in status.zones.values():
        if z.temperature_c is not None:
            _set(forms, f"{z.name}T", C2K(z.temperature_c), "K")
        if z.effective_setpoint_c is not None:
            _set(forms, f"{z.name}_effSP", C2K(z.effective_setpoint_c), "K")
        if z.target_c is not None:
            _set(forms, f"target_{z.name}", C2K(z.target_c), "K")


def _log(forms, record):
    now = time.time()
    if now - rg._last_log_time < rg.LOG_INTERVAL:
        return
    rg._last_log_time = now
    rg.log_path = rg.log_path or output_dir() / "LACO.jsonl"
    entry = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    try:
        with rg.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError as e:
        forms.log(f"LACO log write failed: {e}", level="WARNING", component=name)


def _read(forms, laco):
    try:
        status = laco.status()
    except (OSError, ValueError) as e:
        rg.readERROR += 1
        forms.log(f"HVC-3500 read failed: {e}", level="ERROR", component=name)
        UpdateStatus(LABEL, {"connected": False})
        return
    if status.errors:
        forms.log(f"HVC-3500 partial read: {status.errors}", level="WARNING", component=name)
    _publish(forms, laco, status)
    UpdateStatus(LABEL, status.as_cast())
    _log(forms, status.as_record())


def rScript(forms):
    if rg.disable:
        return
    request = ReadCommand(label=LABEL)
    due = not RScriptControl(forms, name).tick(seconds=rg.POLL_INTERVAL)
    if not (request or due):
        return
    laco = _chamber(forms)
    if laco is None:
        if request:
            forms.log(f"request {request} dropped: chamber not connected",
                      level="ERROR", component=name)
        return
    if request:
        for level, message in laco.apply(request):
            forms.log(message, level=level, component=name)
            if level == "INFO" and message in ("pump verified on", "rough verified open"):
                rg.started_pumping = True
            if level == "INFO" and (message == "pump verified off"
                                    or message.startswith("stop_pumping:")):
                rg.started_pumping = False
    _read(forms, laco)


def rShutdown(forms):
    """End pumping this run started, then release the controller's connection
    (it takes one client at a time)."""
    if rg.laco is None:
        return
    if rg.started_pumping:
        try:
            done = rg.laco.stop_pumping()
            forms.log(f"pumping this run started was ended: {', '.join(done) or 'already stopped'}",
                      component=name)
        except Exception as e:
            forms.log(f"could not end pumping at shutdown: {e} -- check the HMI",
                      level="ERROR", component=name)
    rg.laco.close()
