# --- rLACO: LACO chamber control and monitor ---
#
# The default rScript for the UIUC LACO chamber. Runs without FORMS.
#
# Every POLL_INTERVAL seconds (wall clock) it applies any CAST request written
# to "hvc", reads the chamber through `formslab.devices.laco.LACO`, publishes
# kelvin scalars on the forms handle, updates the CAST "hvc" status block and
# appends to the disk log. Requests the "hvc" block accepts:
#
#   {"platen": 25.0}            zone setpoint, degrees C (clamped to profile limits)
#   {"shroud": -20.0}
#   {"platen_control": true}    thermal control on / off for a zone
#   {"shroud_control": false}
#   {"vacuum": 1e-3}            vacuum setpoint, profile pressure unit
#   {"start": true}             start / continue a held recipe step
#   {"abort": true}
#   {"vent": true}              vent to atmosphere (the PLC checks vent temps)
#
# Not exposed: raw valve/pump toggles. The PLC sequences those.
#
# Installed-firmware note: the commanded setpoint is what the HMI shows and the
# PLC uses once <zone>_control is true; the *effective* setpoint tracks the
# control sensor while the zone is idle. `target_<zone>` holds the commanded
# value, `<zone>_effSP` the effective one.
import os
from pathlib import Path

from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.config import output_dir
from formslab.devices.laco import LACO
from formslab.devices.tvacutils import _update_tvac
from formslab.rscripts import C2K, RScriptControl

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "hvc"


class rGlobal:
    disable = False
    POLL_INTERVAL = 5          # replaced by the profile's poll_interval_s
    LOG_INTERVAL = 30.0

    laco = None
    tvaclog = Path(output_dir()) / "LACO.json"
    _last_log_time = 0.0
    readERROR = 0


rg = rGlobal


def _ensure_variables(forms, laco):
    p = laco.profile

    def _scalar(nm, unit):
        if forms.get_variable(nm) is None:
            forms.types.scalar(nm, unit=unit, overwrite=False)

    _scalar("chamberP", p.pressure_unit)
    for zone_name in laco.zones:
        _scalar(f"{zone_name}T", "K")
        _scalar(f"target_{zone_name}", "K")
        _scalar(f"{zone_name}_effSP", "K")
    for sensor_name in p.sensors:
        _scalar(f"HVC_{sensor_name}", "K")


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
        _ensure_variables(forms, rg.laco)
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
    p = laco.profile
    if status.pressure is not None:
        _set(forms, "chamberP", status.pressure, p.pressure_unit)
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


def rScript(forms):
    if rg.disable:
        return
    if RScriptControl(forms, name).tick(seconds=rg.POLL_INTERVAL):
        return
    laco = _chamber(forms)
    if laco is None:
        return

    request = ReadCommand(label=LABEL)
    if request:
        for level, message in laco.apply(request):
            forms.log(message, level=level, component=name)

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
    _update_tvac(forms, rg, status.as_record())
