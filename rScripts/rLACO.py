# --- rLACO: the LACO chamber (UIUC, HVC-3500 controller) ---
#
# Owns the chamber for the run through `formslab.devices.hvc3500.laco.LACO`:
#   - applies every request on CAST "hvc" as soon as it arrives (each loop),
#   - reads the whole chamber every poll_interval_s (tvac_bench.json; ~38
#     queries, ~6 s), publishes kelvin scalars (chamberP, <zone>T,
#     target_<zone>, <zone>_effSP, HVC_<sensor>) and the CAST "hvc" status block,
#   - right after a request, reads just pressure, faults, valves and pumps
#     (~1.5 s) so the cast tab shows the effect quickly,
#   - appends the full reading to outputs/LACO.jsonl every LOG_INTERVAL s.
#
# The console commands are declared below (COMMANDS): typing `hvc vent open`
# in the cast tab writes {"vent": "open"} to CAST "hvc", and this script applies
# it. `LACO.apply` documents the whole request grammar.
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
from formslab.console.cast.castutils import CommandPending, ReportResult, TakeCommand, UpdateStatus
from formslab.devices.hvc3500.laco import LACO, OPERATIONS, PUMPS, VALVES
from formslab.rscripts import C2K

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "hvc"
RETRY_INTERVAL = 30.0      # seconds between connect attempts while unreachable

# --- console commands --------------------------------------------------------------

CAST_LABELS = (LABEL,)
RESULT_LABELS = (LABEL,)   # each request is answered: done, ok or refused with the controller's reason


def COMMANDS():
    """Every hvc command. Zone names and setpoint limits come from this bench's
    tvac_bench.json, so the list is built on use, not at import."""
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    cmds = []
    for z in profile.zones:
        lo, hi = profile.setpoint_bounds(z)
        cmds += [
            (f"{z} <C:number {lo:g}..{hi:g} C>", f"{z} setpoint (refused outside the profile limits)",
             lambda c, z=z: {z: c}),
            (f"{z} on|off", f"{z} thermal control (!ZS/!ZO)", lambda s, z=z: {f"{z}_control": s == "on"}),
            (f"{z} rate <rate:number 0.. C/min>", f"{z} rate setpoint", lambda r, z=z: {f"{z}_rate": r}),
            (f"{z} range <range:number 0.. C>", f"{z} control range", lambda r, z=z: {f"{z}_range": r}),
        ]
    unit = profile.pressure_unit
    ops = "|".join(op for op in OPERATIONS if op != "close_all")
    return cmds + [
        (f"vacuum <P:number 0.. {unit}>", "Vacuum setpoint", lambda p: {"vacuum": p}),
        (f"vacuum range <P:number 0.. {unit}>", "Vacuum control range", lambda p: {"vacuum_range": p}),
        ("vacuum rate <rate:number 0..>", "Vacuum rate control", lambda r: {"vacuum_rate": r}),
        ("hold <s:number 0.. s>", "Hold time", lambda t: {"hold_s": t}),
        ("recipe <n:integer 1..20>", "Select recipe n", lambda n: {"recipe": n}),
        ("recipe start|stop", "Run / stop the selected recipe (!RS/!RO)",
         lambda w: {"recipe_run": w == "start"}),
        ("start", "Start the cycle, or continue a held step (!CS)", {"start": True}),
        ("abort", "Abort the running cycle (!CA)", {"abort": True}),
        ("reset", "Reset the controller; starts its recovery (!CR)", {"reset": True}),
        (f"<operation:{ops}>", "Cycle vacuum operation (!VA/!FA/!PS); acts only inside a running cycle",
         lambda op: {op: True}),
        ("closeall", "Close all valves in a running cycle (!NA)", {"close_all": True}),
        (f"<valve:{'|'.join(VALVES)}> open|close", "A valve: read first, verified; PLC interlocks apply",
         lambda v, a: {v: a}),
        (f"<pump:{'|'.join(PUMPS)}> on|off", "A pump: read first, verified; PLC interlocks apply",
         lambda p, s: {p: s}),
        ("stop", "End pumping: rough valve closed, then pump off", {"stop_pumping": True}),
    ]


def RULES():
    """What each command needs first (devices/hvc3500/rules.py), thresholds from
    this bench's tvac_bench.json."""
    from formslab.devices.hvc3500 import load_profile
    from formslab.devices.hvc3500.rules import laco_rules

    return laco_rules(load_profile())


def RULE_STATE(status):
    from formslab.devices.hvc3500.rules import laco_state

    return laco_state(status)


def RULE_EFFECTS(request):
    from formslab.devices.hvc3500.rules import laco_effects

    return laco_effects(request)


def VARIABLES():
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    out = [("chamberP", profile.pressure_unit)]
    out += [(f"HVC_{sensor}", "K") for sensor in profile.sensors]
    for z in profile.zones:
        out += [(f"{z}T", "K"), (f"{z}_effSP", "K"), (f"target_{z}", "K")]
    return out


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
    retry_at = 0.0             # next connect attempt while unreachable (time.monotonic)
    cast = {}                  # the last CAST "hvc" status block published
    next_full = 0.0            # when the next full read is due (time.monotonic)
    connect_error = None       # last connect failure, logged once


rg = rGlobal


def _chamber(run):
    """The one LACO object, connected. None (and a status update) if it cannot
    connect. While unreachable it retries every RETRY_INTERVAL s, not every
    poll: each attempt blocks the host loop for the connect timeout."""
    if rg.laco is None:
        rg.laco = LACO()
        rg.POLL_INTERVAL = rg.laco.profile.poll_interval_s
    if not rg.laco.connected:
        if time.monotonic() < rg.retry_at:
            return None
        try:
            rg.laco.connect()
            run.log(f"HVC-3500 connected {rg.laco.endpoint}", component=name)
            rg.connect_error = None
        except OSError as e:
            rg.retry_at = time.monotonic() + RETRY_INTERVAL
            msg = f"HVC-3500 connect to {rg.laco.endpoint} failed: {e}"
            if msg != rg.connect_error:      # log each new failure once
                run.log(f"{msg}; retrying every {RETRY_INTERVAL:g} s",
                          level="ERROR", component=name)
                rg.connect_error = msg
            UpdateStatus(LABEL, {"connected": False, "error": str(e)})
            return None
    return rg.laco


def _publish(run, laco, status):
    if status.pressure is not None:
        run.publish("chamberP", status.pressure, laco.profile.pressure_unit)
    for sensor_name, t in status.sensors.items():
        if t is not None:
            run.publish(f"HVC_{sensor_name}", C2K(t), "K")
    for z in status.zones.values():
        if z.temperature_c is not None:
            run.publish(f"{z.name}T", C2K(z.temperature_c), "K")
        if z.effective_setpoint_c is not None:
            run.publish(f"{z.name}_effSP", C2K(z.effective_setpoint_c), "K")
        if z.target_c is not None:
            run.publish(f"target_{z.name}", C2K(z.target_c), "K")


def _log(run, record):
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
        run.log(f"LACO log write failed: {e}", level="WARNING", component=name)


def _read(run, laco) -> bool:
    """The full read. Abandoned (False) as soon as a command is waiting, so
    the command is applied first; the read is then retried."""
    try:
        status = laco.status(interrupt=lambda: CommandPending(LABEL))
    except (OSError, ValueError) as e:
        rg.readERROR += 1
        run.log(f"HVC-3500 read failed: {e}", level="ERROR", component=name)
        UpdateStatus(LABEL, {"connected": False})
        return True
    if status is None:
        return False
    if status.errors:
        run.log(f"HVC-3500 partial read: {status.errors}", level="WARNING", component=name)
    _publish(run, laco, status)
    rg.cast = status.as_cast()
    UpdateStatus(LABEL, rg.cast)
    _log(run, status.as_record())
    return True


def _quick(run, laco):
    """Right after a command: pressure, faults, valves and pumps (~1.5 s),
    merged into the last full reading. The full read keeps its schedule."""
    try:
        q = laco.quick_status()
    except (OSError, ValueError) as e:
        run.log(f"HVC-3500 quick read failed: {e}", level="WARNING", component=name)
        return
    rg.cast = {**rg.cast, **q}
    UpdateStatus(LABEL, rg.cast)
    if q.get("pressure") is not None:
        run.publish("chamberP", q["pressure"], laco.profile.pressure_unit)


def rScript(run):
    if rg.disable:
        return
    request, ids = TakeCommand(label=LABEL)
    due = time.monotonic() >= rg.next_full
    if not (request or due):
        return
    laco = _chamber(run)
    if laco is None:
        if request:
            run.log(f"request {request} dropped: chamber not connected",
                      level="ERROR", component=name)
            ReportResult(LABEL, ids, False, ["chamber not connected"])
        return
    if request:
        said = []
        try:
            for level, message in laco.apply(request):
                run.log(message, level=level, component=name)
                said.append((level, message))
                if level == "INFO" and message in ("pump verified on", "rough verified open"):
                    rg.started_pumping = True
                if level == "INFO" and (message == "pump verified off"
                                        or message.startswith("stop_pumping:")):
                    rg.started_pumping = False
        except Exception as e:
            ReportResult(LABEL, ids, False, [f"{type(e).__name__}: {e}"])
            raise
        # The quick re-read comes first, so the next plan step sees the new state.
        _quick(run, laco)
        errors = [m for level, m in said if level == "ERROR"]
        ReportResult(LABEL, ids, not errors, errors or [m for _, m in said])
    if due and _read(run, laco):
        rg.next_full = time.monotonic() + rg.POLL_INTERVAL


def rShutdown(run):
    """End pumping this run started, then release the controller's connection
    (it takes one client at a time)."""
    if rg.laco is None:
        return
    if rg.started_pumping:
        try:
            done = rg.laco.stop_pumping()
            run.log(f"pumping this run started was ended: {', '.join(done) or 'already stopped'}",
                      component=name)
        except Exception as e:
            run.log(f"could not end pumping at shutdown: {e} -- check the HMI",
                      level="ERROR", component=name)
    rg.laco.close()
