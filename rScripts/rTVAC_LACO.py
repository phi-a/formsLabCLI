# --- rTVAC_LACO: LACO chamber via the HVC-3500 ASCII/TCP link ---
#
# Reads the chamber every HOLD_INTERVAL seconds, publishes FORMS scalars and
# a CAST "hvc" status block, and applies CAST requests written to "hvc":
#
#   {"platen": 25.0}            zone setpoint, degrees C (clamped to profile limits)
#   {"shroud": -20.0}
#   {"platen_control": true}    activate / deactivate a zone's thermal source
#   {"shroud_control": false}
#   {"vacuum": 1e-3}            vacuum setpoint, profile pressure unit
#   {"start": true}             !CS  start / continue held recipe step
#   {"abort": true}             !CA
#   {"vent": true}              !VA  vent to atmosphere (PLC checks vent temps)
#
# Not exposed: raw valve/pump toggles. The PLC sequences those.
#
# Installed-firmware note: !Zn sets the *commanded* setpoint (persisted, shown on
# the HMI, used once <zone>_control is true). ?Zn returns the *effective*
# setpoint, which tracks the control sensor while the zone is idle. So the
# "<zone> setpoint C" status field equals the sensor until control is on.
import os
from pathlib import Path

from forms.utils.rScripts import RScriptControl
from forms.bricks.thermal.radiation import C2K
from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.config import output_dir
from formslab.devices.hvc3500 import HVC3500Client, ProtocolError, load_profile
from formslab.devices.tvacutils import _update_tvac

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "hvc"


class rGlobal:
    disable = False
    useHold = True
    HOLD_INTERVAL = 5
    LOG_INTERVAL = 30.0

    profile = None
    client = None
    tvaclog = Path(output_dir()) / "TVAC_LACO.json"
    _last_log_time = 0.0
    _connected = False
    rI = 0
    readERROR = 0


rg = rGlobal


def _ensure_variables(forms, profile):
    def _scalar(nm, unit, value=None):
        if forms.get_variable(nm) is None:
            kwargs = {"unit": unit, "overwrite": False}
            if value is not None:
                kwargs["value"] = value
            forms.types.scalar(nm, **kwargs)

    _scalar("chamberP", profile.pressure_unit)
    for zone_name in profile.zones:
        _scalar(f"{zone_name}T", "K")
        _scalar(f"target_{zone_name}", "K")
        _scalar(f"{zone_name}_effSP", "K")
    for sensor_name in profile.sensors:
        _scalar(f"HVC_{sensor_name}", "K")


def _set(forms, nm, value, unit):
    var = forms.get_variable(nm)
    if var is None:
        var = forms.types.scalar(nm, unit=unit, overwrite=False)
    var.set(value=value, unit=unit)


def _connect(forms):
    if rg.profile is None:
        rg.profile = load_profile()
        rg.HOLD_INTERVAL = rg.profile.poll_interval_s
        _ensure_variables(forms, rg.profile)
    if rg.client is None:
        rg.client = HVC3500Client(rg.profile.host, rg.profile.port, timeout=rg.profile.timeout_s)
    if not rg._connected:
        try:
            rg.client.connect()
            rg._connected = True
            forms.log(f"HVC-3500 connected {rg.profile.host}:{rg.profile.port}", component=name)
        except OSError as e:
            forms.log(f"HVC-3500 connect failed: {e}", level="ERROR", component=name)
    return rg._connected


def _apply_requests(forms, request: dict):
    c, p = rg.client, rg.profile
    for zone_name in p.zones:
        zone = p.zone(zone_name)
        val = request.get(zone_name)
        if isinstance(val, (int, float)):
            lo, hi = p.setpoint_bounds(zone_name)
            target = min(max(float(val), lo), hi)
            if target != val:
                forms.log(f"{zone_name} setpoint {val} clamped to {target} C", level="WARNING", component=name)
            try:
                got = c.set_zone_setpoint(zone, target)
                _set(forms, f"target_{zone_name}", C2K(got), "K")
                forms.log(f"{zone_name} setpoint -> {got} C", component=name)
            except (OSError, ProtocolError) as e:
                forms.log(f"{zone_name} setpoint write: {e}", level="ERROR", component=name)
        ctl = request.get(f"{zone_name}_control")
        if isinstance(ctl, bool):
            try:
                if ctl:
                    c.activate_zone(zone, confirm=True)
                else:
                    c.deactivate_zone(zone, confirm=True)
                forms.log(f"{zone_name} thermal control {'ON' if ctl else 'OFF'}", component=name)
            except (OSError, ProtocolError) as e:
                forms.log(f"{zone_name} control {ctl}: {e}", level="ERROR", component=name)
    vac = request.get("vacuum")
    if isinstance(vac, (int, float)):
        try:
            forms.log(f"vacuum setpoint -> {c.set_vacuum_setpoint(float(vac))} {p.pressure_unit}", component=name)
        except (OSError, ProtocolError) as e:
            forms.log(f"vacuum setpoint write: {e}", level="ERROR", component=name)
    for key, code in (("start", "CS"), ("abort", "CA"), ("vent", "VA")):
        if request.get(key) is True:
            try:
                c.action(code, confirm=True)
                forms.log(f"!{code} sent ({key})", component=name)
            except (OSError, ProtocolError) as e:
                forms.log(f"!{code} ({key}): {e}", level="ERROR", component=name)


def rScript(forms):
    global rg
    if rg.disable:
        return
    rg.rI += 1
    try:
        r = RScriptControl(forms, name)
        if rg.useHold:
            r.hold(seconds=rg.HOLD_INTERVAL)
        if r:
            return
    except Exception as e:
        forms.log(f"RScriptControl exception: {e}", level="ERROR", component=name)
        return

    if not _connect(forms):
        UpdateStatus(LABEL, {"connected": False})
        return
    c, p = rg.client, rg.profile

    request = ReadCommand(label=LABEL)
    if request:
        _apply_requests(forms, request)

    # --- Read-only snapshot -------------------------------------------------
    try:
        snap = c.snapshot(temperatures=sorted(set(p.sensors.values())),
                          zones=sorted(set(p.zones.values())))
    except (OSError, ProtocolError) as e:
        rg.readERROR += 1
        rg._connected = False
        forms.log(f"HVC-3500 read failed: {e}", level="ERROR", component=name)
        UpdateStatus(LABEL, {"connected": False})
        return
    if "errors" in snap:
        forms.log(f"HVC-3500 partial read: {snap['errors']}", level="WARNING", component=name)

    # --- FORMS scalars (kelvin, like the rest of the bench) -----------------
    if snap.get("pressure") is not None:
        _set(forms, "chamberP", snap["pressure"], p.pressure_unit)
    for sensor_name, n in p.sensors.items():
        t = snap.get(f"T{n}")
        if t is not None:
            _set(forms, f"HVC_{sensor_name}", C2K(t), "K")
    for zone_name, z in p.zones.items():
        ctrl = p.sensors.get(f"{zone_name}_ctrl")
        if ctrl is not None and snap.get(f"T{ctrl}") is not None:
            _set(forms, f"{zone_name}T", C2K(snap[f"T{ctrl}"]), "K")
        # ?Zn is the *effective* setpoint (sensor-tracking while idle); keep it
        # separate from target_<zone>, which holds the last commanded value.
        if snap.get(f"Z{z}_setpoint") is not None:
            _set(forms, f"{zone_name}_effSP", C2K(snap[f"Z{z}_setpoint"]), "K")

    # --- CAST status --------------------------------------------------------
    es = snap.get("error_status") or {}
    status = {
        "connected": True,
        "mode": snap.get("mode"),
        "test_status": snap.get("test_status"),
        "pressure": snap.get("pressure"),
        "pressure_unit": p.pressure_unit,
        "vacuum_setpoint": snap.get("vacuum_setpoint"),
        "recipe": snap.get("recipe"),
        "recipe_step": snap.get("recipe_step"),
        "thermal_control": "holding temperature" in str(snap.get("test_status", "")).lower(),
        "fault_severity": es.get("severity"),
        "faults": ", ".join(es.get("names", [])) or "none",
    }
    for zone_name, z in p.zones.items():
        ctrl = p.sensors.get(f"{zone_name}_ctrl")
        status[f"{zone_name} C"] = snap.get(f"T{ctrl}") if ctrl is not None else None
        status[f"{zone_name} setpoint C"] = snap.get(f"Z{z}_setpoint")
    for code, label in (("OR", "rough"), ("OV", "vent"), ("OF", "fill"), ("O4", "foreline"),
                        ("OG", "gate"), ("OP", "pump"), ("OT", "turbo")):
        if code in snap:
            status[label] = bool(snap[code])
    UpdateStatus(LABEL, status)

    # --- Disk log -----------------------------------------------------------
    _update_tvac(forms, rg, {k: v for k, v in snap.items() if k != "t"})
