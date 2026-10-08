# --- rLACO: the LACO chamber (UIUC, HVC-3500 controller) ---
#
# Owns the chamber for the run through `formslab.devices.hvc3500.laco.LACO`:
#   - applies every request on CAST "hvc" as soon as it arrives (each loop),
#   - reads the whole chamber every poll_interval_s (tvac_bench.json; ~38
#     queries, ~6 s), publishes kelvin scalars (chamberP, <zone>T,
#     target_<zone>, <zone>_effSP, HVC_<sensor>) and the CAST "hvc" status block,
#   - right after a request, reads just pressure, faults, valves and pumps
#     (~1.5 s) so the cast tab shows the effect quickly,
#   - appends the full reading to outputs/LACO.jsonl every LOG_INTERVAL s,
#   - holds a zone at a thermocouple (`hvc platen 40 at TC01`): once a minute it
#     moves the zone's setpoint toward what brings the thermocouple to 40 °C.
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
import math
import os
import time
from datetime import datetime, timezone

from formslab.config import output_dir
from formslab.console.cast.castutils import CommandPending, ReportResult, TakeCommand, UpdateStatus
from formslab.devices.hvc3500.laco import LACO, OPERATIONS, PUMPS, VALVES, _flag
from formslab.rscripts import C2K

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "hvc"
RETRY_INTERVAL = 30.0      # seconds between connect attempts while unreachable
HOLD_EVERY = 60.0          # seconds between a held zone's setpoint moves
HOLD_STALE = 120.0         # a thermocouple older than this stops the moves

# --- console commands --------------------------------------------------------------

CAST_LABELS = (LABEL,)
RESULT_LABELS = (LABEL,)   # each request is answered: done, ok or refused with the controller's reason


def COMMANDS():
    """Every hvc command. Zone names and setpoint limits come from this bench's
    tvac_bench.json, so the list is built on use, not at import. Each help is a
    summary line, then details (docs/WRITING.md)."""
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    gain, band = _hold_tuning(profile)
    sensors = "|".join(_holdable(profile))
    cmds = []
    for z, n in profile.zones.items():
        lo, hi = profile.setpoint_bounds(z)
        cmds += [
            (f"{z} <temperature:number {lo:g}..{hi:g} C>", f"""Set the {z} temperature
             The temperature the {z} controls to, from {lo:g} to {hi:g} °C: the limits in
             this bench's tvac_bench.json. A value outside them is refused before it is
             sent. It takes effect while the {z}'s thermal control is on, and the {z}
             moves toward it at its ramp rate. The controller reads it back to confirm.
             It ends a hold at a thermocouple. Controller command: !Z{n}.""",
             lambda c, z=z: {z: c}),
            (f"{z} <temperature:number {lo:g}..{hi:g} C> at <sensor:{sensors}>",
             f"""Hold a thermocouple at a temperature
             The controller holds the {z} at its setpoint with its own sensor. This moves
             that setpoint once a minute until the thermocouple given reads the
             temperature, then keeps it there: each minute by {gain:g} times the
             difference, never more than {band:g} °C from the temperature (tvac_bench.json
             limits). It moves only while thermal control is on, so turn the {z} on too.
             A thermocouple with no reading for two minutes stops the moves until it
             reports again. A plain {z} setpoint, {z} off or the end of the run ends the
             hold, and the {z} keeps the last setpoint. The reading is only as right as
             the thermocouple's type: an SMTC08 board converts with the type set on it.""",
             lambda c, s, z=z: {z: c, f"{z}_at": s}),
            (f"{z} on|off", f"""Turn {z} thermal control on or off
             On, the controller heats or cools the {z} toward its setpoint. Off, it does
             neither, and the {z}'s effective setpoint follows its own temperature.
             Controller command: !ZS{n}, !ZO{n}.""",
             lambda s, z=z: {f"{z}_control": s == "on"}),
            (f"{z} rate <rate:number 0.. C/min>", f"""Set the {z} ramp rate
             How fast the {z}'s setpoint moves, in °C per minute.
             Controller command: !ZR{n}.""",
             lambda r, z=z: {f"{z}_rate": r}),
            (f"{z} range <range:number 0.. C>", f"""Set the {z} control range
             The {z}'s temperature control range, in °C. Its exact effect is in the
             HVC-3500 manual and is not documented here. Controller command: !RT{n}.""",
             lambda r, z=z: {f"{z}_range": r}),
        ]
    unit = profile.pressure_unit
    ops = "|".join(op for op in OPERATIONS if op != "close_all")
    return cmds + [
        (f"vacuum <pressure:number 0.. {unit}>", f"""Set the pressure setpoint
         The pressure the controller's own vacuum process aims for, in {unit}. Whether
         the valve and pump commands use it is not documented here.
         Controller command: !VS.""", lambda p: {"vacuum": p}),
        (f"vacuum range <pressure:number 0.. {unit}>", f"""Set the pressure control range
         A pressure, in {unit}, used by the controller's vacuum process. Its exact effect
         is not documented here. Controller command: !VR.""", lambda p: {"vacuum_range": p}),
        ("vacuum rate <rate:number 0..>", """Set the pressure rate control
         A setting of the controller's vacuum process. Its unit and exact effect are not
         documented here. Controller command: !VD.""", lambda r: {"vacuum_rate": r}),
        ("hold <seconds:number 0.. s>", """Set the vacuum process hold time
         How long the controller's vacuum process holds, in seconds. This is not the
         plan step hold, which waits in the plan. Controller command: !VH.""",
         lambda t: {"hold_s": t}),
        ("recipe <recipe:integer 1..20>", """Choose a stored recipe
         Which of the controller's stored recipes, 1 to 20, hvc recipe start runs.
         Controller command: !TR.""", lambda n: {"recipe": n}),
        ("recipe start|stop", """Start or stop the chosen recipe
         A recipe runs the chamber by itself. Until it ends, its valves, pumps and zones
         are the controller's, and a plan treats their states as unknown.
         Controller command: !RS, !RO.""", lambda w: {"recipe_run": w == "start"}),
        ("start", """Start the cycle or continue a held step
         What it does depends on the controller: it starts a cycle, or continues a recipe
         held at a step. It is never repeated automatically. Controller command: !CS.""",
         {"start": True}),
        ("abort", """Abort the running cycle
         Ends the cycle and sends the controller through its recovery sequence. Allowed
         during a fault. Controller command: !CA.""", {"abort": True}),
        ("reset", """Reset the controller
         The controller homes and recovers. This is how a fault is cleared once its cause
         is fixed; a hard over-temperature also needs a physical reset at the Watlow
         controller. Allowed during a fault. Controller command: !CR.""", {"reset": True}),
        (f"<operation:{ops}>", """Run a cycle vacuum operation
         It acts only inside a running cycle. vent2atm vents to atmosphere, fill2atm fills
         to atmosphere with the process gas, and purge purges the system. The controller
         runs its own valve sequence, so a plan treats every valve and pump as unknown
         afterwards. Controller command: !VA, !FA, !PS.""", lambda op: {op: True}),
        ("closeall", """Close all valves in a running cycle
         Allowed during a fault. Controller command: !NA.""", {"close_all": True}),
        (f"<valve:{'|'.join(VALVES)}> open|close", """Open or close a valve
         rough is the vacuum valve: it joins the chamber to the vacuum pump. vent lets
         air in. fill lets in the process gas. foreline joins the turbo pump's exhaust to
         the vacuum pump. gate joins the chamber to the turbo pump. The valve is read
         first and switched only if it must change, then read again about a second later
         to verify. Closing a valve is always allowed, even during a fault.
         Controller command: !OR, !OV, !OF, !O4, !OG.""",
         lambda v, a: {v: a}),
        (f"<pump:{'|'.join(PUMPS)}> on|off", """Turn a pump on or off
         pump is the vacuum pump, which roughs the chamber. The controller wants it
         running for 10 s before the vacuum or foreline valve opens. turbo is the turbo
         pump. It needs the foreline valve open and the foreline pressure at or below
         0.2 Torr. To stop the vacuum pump, hvc stop closes the vacuum valve first.
         Controller command: !OP, !OT.""",
         lambda p, s: {p: s}),
        ("stop", """Stop roughing safely
         Closes the vacuum valve, then stops the vacuum pump, verifying each. A run that
         started pumping does this itself when it ends.""",
         {"stop_pumping": True}),
    ]


def _hold_tuning(profile):
    """(gain per minute, band °C) of a hold at a thermocouple, from the profile."""
    lim = profile.limits
    return float(lim.get("hold_at_gain_per_min", 0.05)), float(lim.get("hold_at_band_c", 15.0))


def SENSORS():
    """Its temperature readings (cast.sensors): the controller's thermocouples and
    each zone's control sensor; not the setpoints, which are also in K."""
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    return [f"HVC_{sensor}" for sensor in profile.sensors] + [f"{z}T" for z in profile.zones]


def _holdable(profile):
    """What a zone can be held at, named as a plan's conditions name it: every
    temperature another routine publishes (rSMTC08's TC01...), then the
    controller's own thermocouples."""
    from formslab.rscripts import cast

    mine = set(SENSORS())
    return [s for s in cast.sensors() if s not in mine] + [f"HVC_{sensor}" for sensor in profile.sensors]


def READS(request):
    """The values a request reads, so a plan holding a zone at TC01 must load
    the routine that publishes it."""
    return [v for k, v in request.items() if k.endswith("_at")]


def PARTS():
    """Which kind of part each command word names: shown as an icon beside the word
    (valve, pump, zone, setting). Cycle words (start, abort, stop...) name none."""
    from formslab.devices.hvc3500 import load_profile

    out = {v: "valve" for v in VALVES}
    out.update({p: "pump" for p in PUMPS})
    out.update({z: "zone" for z in load_profile().zones})
    out.update({w: "setting" for w in ("vacuum", "hold", "recipe")})
    return out


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


def READINGS(label, status):
    """The status page's groups, in the chamber screen's names (laco.PART_NAMES)."""
    from formslab.devices.hvc3500 import load_profile
    from formslab.devices.hvc3500.laco import PART_NAMES

    unit = status.get("pressure_unit") or "Torr"
    zones = []
    for z in load_profile().zones:
        zones += [(f"{z} C", f"{z.capitalize()} (°C)"), (f"{z} setpoint C", f"{z.capitalize()} setpoint (°C)"),
                  (f"{z} held at", f"{z.capitalize()} held at")]
    zones.append(("thermal_control", "Holding temperature", ("On", "Off")))
    zoned = {k for k, *_ in zones}
    sensors = [(k, f"{k[:-2]} (°C)") for k in status if k.endswith(" C") and k not in zoned]
    return [
        ("Chamber", None, [("connected", "Connected"), ("error", "Error"), ("mode", "Mode"),
                           ("test_status", "Test status"), ("pressure", f"Chamber pressure ({unit})"),
                           ("pressure_unit", None), ("fault_severity", "Fault severity"),
                           ("faults", "Faults")]),
        ("Valves", "valve", [(v, PART_NAMES[v]) for v in VALVES]),
        ("Pumps", "pump", [(p, PART_NAMES[p]) for p in PUMPS]),
        ("Zones", "zone", zones),
        ("Thermocouples", None, sensors),
        ("Pressure settings", "setting", [("vacuum_setpoint", f"Pressure setpoint ({unit})")]),
        ("Recipe", None, [("recipe", "Recipe"), ("recipe_step", "Recipe step")]),
    ]


# The chamber's states as on/off values a plan can wait on: each valve (open),
# each pump (on), and the controller's one flag for thermal control on either zone.
STATES = {"VentValve": "vent", "FillValve": "fill", "GateValve": "gate", "RoughValve": "rough",
          "ForelineValve": "foreline", "VacuumPump": "pump", "TurboPump": "turbo",
          "HoldingTemperature": "thermal_control"}


def VARIABLES():
    from formslab.devices.hvc3500 import load_profile

    profile = load_profile()
    out = [("chamberP", profile.pressure_unit)]
    out += [(f"HVC_{sensor}", "K") for sensor in profile.sensors]
    for z in profile.zones:
        out += [(f"{z}T", "K"), (f"{z}_effSP", "K"), (f"target_{z}", "K")]
    return out + [(v, "bool") for v in STATES]


def _publish_states(run, cast):
    """The valves, pumps and thermal control from the last report, as STATES."""
    for var, key in STATES.items():
        if cast.get(key) is not None:
            run.publish(var, 1 if cast[key] else 0, "bool")


# --- the routine -------------------------------------------------------------------

class rGlobal:
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
    holds = {}                 # zone -> its hold at a thermocouple (_take_holds)


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
    rg.cast = {**status.as_cast(), **_hold_status(laco)}
    _publish_states(run, rg.cast)
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
    _publish_states(run, rg.cast)
    UpdateStatus(LABEL, rg.cast)
    if q.get("pressure") is not None:
        run.publish("chamberP", q["pressure"], laco.profile.pressure_unit)


# --- a zone held at a thermocouple ---------------------------------------------------

def _take_holds(run, laco, request):
    """Take each `<zone>_at` out of a request and start that zone's hold. A plain
    setpoint, or thermal control off, for a held zone ends its hold. Returns
    what is left for the controller."""
    request = dict(request)
    for z in laco.profile.zones:
        sensor = request.pop(f"{z}_at", None)
        if sensor is not None and z in request:
            target = float(request[z])
            rg.holds[z] = {"sensor": sensor, "target": target, "setpoint": target,
                           "next": time.monotonic() + HOLD_EVERY, "waiting": None}
            run.log(f"{z} held at {sensor} {target:g} C", component=name)
        elif z in rg.holds and (z in request or _flag(request.get(f"{z}_control"), ("on", "off")) is False):
            run.log(f"{z} no longer held at {rg.holds.pop(z)['sensor']}", component=name)
    return request


def _reading(run, sensor):
    """(°C, None) for a current reading of `sensor`, or (None, why not)."""
    var = run.variable(sensor)
    if var is None or var.value is None or math.isnan(float(var.value)):
        return None, f"no reading from {sensor}"
    if (age := time.monotonic() - var.updated) > HOLD_STALE:
        return None, f"{sensor} has not reported for {age:.0f} s"
    return float(var.value) - 273.15, None


def _hold(run, laco):
    """Each held zone, once a minute while thermal control is on: move its setpoint
    by gain x (temperature - reading), within the band around the temperature."""
    gain, band = _hold_tuning(laco.profile)
    now = time.monotonic()
    for z, h in rg.holds.items():
        if now < h["next"]:
            continue
        h["next"] = now + HOLD_EVERY
        reading, why = _reading(run, h["sensor"])
        why = why or (None if rg.cast.get("thermal_control") else "thermal control is off")
        if why:
            if why != h["waiting"]:
                run.log(f"{z} hold at {h['sensor']} waiting: {why}", level="WARNING", component=name)
            h["waiting"] = why
            continue
        h["waiting"] = None
        before = round(h["setpoint"], 1)
        h["setpoint"] += gain * HOLD_EVERY / 60.0 * (h["target"] - reading)
        h["setpoint"] = min(max(h["setpoint"], h["target"] - band), h["target"] + band)
        if round(h["setpoint"], 1) != before:
            for level, message in laco.apply({z: round(h["setpoint"], 1)}):
                run.log(f"{message} ({h['sensor']} {reading:.1f} C)", level=level, component=name)
    rg.cast.update(_hold_status(laco))


def _hold_status(laco):
    """`<zone> held at` for the status page: the thermocouple and temperature, or None."""
    out = {}
    for z in laco.profile.zones:
        h = rg.holds.get(z)
        out[f"{z} held at"] = None if h is None else (
            f"{h['sensor']} {h['target']:g} °C" + (f", waiting: {h['waiting']}" if h["waiting"] else ""))
    return out


def rScript(run):
    request, ids = TakeCommand(label=LABEL)
    now = time.monotonic()
    due = now >= rg.next_full
    hold_due = any(now >= h["next"] for h in rg.holds.values())
    if not (request or due or hold_due):
        return
    laco = _chamber(run)
    if laco is None:
        if request:
            run.log(f"request {request} dropped: chamber not connected",
                      level="ERROR", component=name)
            ReportResult(LABEL, ids, False, ["chamber not connected"])
        return
    if request:
        request = _take_holds(run, laco, request)
        rg.cast.update(_hold_status(laco))
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
    if hold_due:
        _hold(run, laco)
        UpdateStatus(LABEL, rg.cast)
    if due and _read(run, laco):
        rg.next_full = time.monotonic() + rg.POLL_INTERVAL


def rShutdown(run):
    """End pumping this run started, then release the controller's connection
    (it takes one client at a time)."""
    if rg.laco is None:
        return
    for z, h in rg.holds.items():
        run.log(f"{z} hold at {h['sensor']} ended with the run; setpoint left at "
                f"{h['setpoint']:.1f} C", component=name)
    rg.holds.clear()
    if rg.started_pumping:
        try:
            done = rg.laco.stop_pumping()
            run.log(f"pumping this run started was ended: {', '.join(done) or 'already stopped'}",
                      component=name)
        except Exception as e:
            run.log(f"could not end pumping at shutdown: {e} -- check the HMI",
                      level="ERROR", component=name)
    rg.laco.close()
