# --- rTVAC: the Rigol/RTD bench chamber ---
#
# Reads the shroud RTDs (RTD16: PY = mean of ch0-2, MY = mean of ch14-15) and the
# SMTC08 thermocouples (TC01..TC16, kelvin), and holds each shroud at its target
# with a PI heater loop on PSU1 (MY on CH1, PY on CH2). CAST "tvac" requests:
#
#   {"PY": 240.0}, {"MY": 240.0}       shroud targets, kelvin
#   {"startup": true}                  re-initialise PSU1 (both channels on at 0 V)
#   {"shutdown": true}                 both heater channels off, 0 V / 0 A
#
# On host stop, rShutdown does the same as "shutdown": an uncontrolled heater
# must not be left on.
import os
from statistics import mean

from formslab.config import output_dir
from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.devices.shroud import HeaterController
from formslab.devices.tvacutils import _init, _init_psu, _update_tvac
from formslab.rscripts import C2K, RScriptControl

# --- Constants ---
name = os.path.splitext(os.path.basename(__file__))[0]
TVACLOG = output_dir() / "TVAC.json"
# --- Encapsulated State ---
class rGlobal:
    disable = False
    useInitialize = False
    useHold = True
    useTick = False
    TICK_INTERVAL = 3
    HOLD_INTERVAL = 5
    LOG_INTERVAL = 30.0

    psu1 = None
    rtd1 = None
    smtcA = None
    smtcB = None

    scrtl = None
    sctrlconfig = False
    _hardware_initialized = False
    _last_log_time = 0.0

    shutdown = False
    startup = False

    tvaclog = TVACLOG

    # — New counters —
    rI   = 0
    rtdERROR   = 0

rg = rGlobal


def _ensure_variables(forms):
    """Register TVAC variables on first run if not already present."""
    def _scalar(name, unit, value=None):
        if forms.get_variable(name) is None:
            kwargs = {"unit": unit, "overwrite": False}
            if value is not None:
                kwargs["value"] = value
            forms.types.scalar(name, **kwargs)

    _scalar("target_PYs", "K", value=240.0)
    _scalar("target_MYs", "K", value=240.0)
    _scalar("PYsT", "K")
    _scalar("MYsT", "K")
    for i in range(1, 17):
        _scalar(f"TC{i:02d}", "K")


def _scalar_var(forms, name, unit, value=None):
    """Return a registered scalar, recreating it if the registry lost it."""
    var = forms.get_variable(name)
    if var is not None:
        return var
    kwargs = {"unit": unit, "overwrite": False}
    if value is not None:
        kwargs["value"] = value
    return forms.types.scalar(name, **kwargs)


# --- rScript Entry Point ---
def rScript(forms):
    global rg
    # === rScript Controls & CUstom Initializations ===
    if rg.disable:
        return
    _ensure_variables(forms)
    rg = _init(forms, rg)
    rg.rI += 1
    try:
        r = RScriptControl(forms, name)
        if rg.useInitialize: r.initialize()
        if rg.useHold:       r.hold(seconds=rg.HOLD_INTERVAL)
        if rg.useTick:       r.tick(seconds=rg.TICK_INTERVAL)
        if r: return
        else: 
            if rg.useHold: rg.useHold = False 
    except Exception as e:
        forms.log(f"RScriptControl exception: {e}", level="ERROR", component="rTVAC")
        return
    # === Read CAST command ===
    request2 = ReadCommand(label="tvac")
    # TVAC startup or shutdown
    if request2:
        for attr in ("shutdown", "startup"):
            val = request2.get(attr)
            if val is None:
                continue
            if isinstance(val, bool):
                setattr(rg, attr, val)
                forms.log(f"{attr.capitalize()} request: {getattr(rg, attr)}", level="INFO", component="rTVAC")
            else:
                forms.log(f"Invalid {attr} request: {val} — must be bool", level="WARNING", component="rTVAC")

    # TVAC shroud temperature request
    if request2:
        for key in ("PY", "MY"):
            val = request2.get(key)
            if isinstance(val, (int, float)):
                var_name = f"target_{'PYs' if key == 'PY' else 'MYs'}"
                try:
                    var = forms.get_variable(var_name)
                    if var is None:
                        forms.log(f"{var_name} not yet registered — ignoring setpoint {val:.2f} K", level="WARNING", component="rTVAC")
                    else:
                        var.set(value=val, unit="K")
                        forms.log(f"Updated shroud setpoint: {var_name} = {val:.2f} K", component="rTVAC")
                except Exception as e:
                    forms.log(f"Failed to set {var_name}: {e}", level="ERROR", component="rTVAC")
            elif val is not None:
                forms.log(f"Ignoring invalid {key} request: must be float", level="WARNING", component="rTVAC")
    # === If Startup or Shutdown ===
    if rg.shutdown:
        if rg.rtd1:
            rg.rtd1.close()
        if rg.psu1:
            ok = rg.psu1.shutdown()
            if ok:
                status = rg.psu1.status()
                forms.log("psu1 shutdown complete.", component="rTVAC")
            else:
                status = rg.psu1.state
                forms.log("Shutdown failed during PSU commands", level="ERROR", component="rTVAC")
        else:
            status = {"error": "psu1 is None"}
        UpdateStatus(label="psu1", status=status)
        rg.shutdown = False
    elif rg.startup:
        _init_psu(forms,rg)
        rg.startup = False

    # === Sensor Readings ===
    # RTD readings
    try:
        Ys = rg.rtd1.read_serial()
        _scalar_var(forms, "PYsT", "K").set(value=mean([Ys["ch0"], Ys["ch1"], Ys["ch2"]]), unit="K")
        _scalar_var(forms, "MYsT", "K").set(value=mean([Ys["ch14"], Ys["ch15"]]), unit="K")
    except Exception as e:
        rg.rtdERROR += 1
        Ys = None
        # === RTD-error summary every 1000 invocations ===
    if rg.rI % 1000 == 0:
        forms.log(
            f"RTD read failed {rg.rtdERROR} times in the last 1000 iterations",
            level="WARNING", component="rTVAC"
        )
        rg.rtdERROR = 0
    # SMTC readings
    temps_k = []
    # SMTC08_A (TC01-TC08)
    try:
        temps_c = rg.smtcA.read_all() if rg.smtcA else []
        temps_k_a = [C2K(t) for t in temps_c]
        for idx, t in enumerate(temps_k_a, 1):
            try:
                _scalar_var(forms, f"TC{idx:02d}", "K").set(value=t, unit="K")
            except Exception as inner_e:
                forms.log(f"Failed to set TC{idx:02d}: {inner_e}", level="WARNING", component="rTVAC")
        temps_k.extend(temps_k_a)
    except Exception as e:
        forms.log(f"SMTC08_A read failed: {e}", level="ERROR", component="rTVAC")

    # SMTC08_B (TC09-TC16)
    try:
        temps_c = rg.smtcB.read_all() if rg.smtcB else []
        temps_k_b = [C2K(t) for t in temps_c]
        for idx, t in enumerate(temps_k_b, 9):
            try:
                _scalar_var(forms, f"TC{idx:02d}", "K").set(value=t, unit="K")
            except Exception as inner_e:
                forms.log(f"Failed to set TC{idx:02d}: {inner_e}", level="WARNING", component="rTVAC")
        temps_k.extend(temps_k_b)
    except Exception as e:
        forms.log(f"SMTC08_B read failed: {e}", level="ERROR", component="rTVAC")

    # Shroud Controller
    if not rg.sctrlconfig and Ys is not None:

        rg.scrtl = HeaterController(
            psu=rg.psu1,
            forms=forms,
            channel_map={"PYsT": 2, "MYsT": 1},
            target_vars={"PYsT": "target_PYs", "MYsT": "target_MYs"},
            kp=3.0,
            ki=0.1,
            vlim=28.0,
            clim=1.9,
        )
        rg.sctrlconfig = True
    else:
        if rg.scrtl:
            rg.scrtl.update()

    # Log to disk
    tvac_data = {
        "TC": temps_k,
        "PYsT": _scalar_var(forms, "PYsT", "K").value,
        "MYsT": _scalar_var(forms, "MYsT", "K").value,
    }
    if Ys is not None:
        tvac_data["PYlist"] = [Ys["ch0"], Ys["ch1"], Ys["ch2"]]
        tvac_data["MYlist"] = [Ys["ch14"], Ys["ch15"]]
    _update_tvac(forms, rg, tvac_data)

    # === TVAC Status Update ===
    tvac_status = {
        "target PY Shroud": _scalar_var(forms, "target_PYs", "K", value=240.0).value,
        "target MY Shroud": _scalar_var(forms, "target_MYs", "K", value=240.0).value,
        "PYsT": _scalar_var(forms, "PYsT", "K").value,
        "MYsT": _scalar_var(forms, "MYsT", "K").value,
        "TC08": temps_k,
    }
    if Ys is not None:
        tvac_status["PYlist"] = [Ys["ch0"], Ys["ch1"], Ys["ch2"]]
        tvac_status["MYlist"] = [Ys["ch14"], Ys["ch15"]]
    UpdateStatus("tvac", tvac_status)


def rShutdown(forms):
    """Heater channels off at 0 V / 0 A, whatever ended the run."""
    if rg.psu1 is None:
        return
    if rg.psu1.shutdown():
        forms.log("PSU1 heater channels off at shutdown", component=name)
    else:
        forms.log("PSU1 heater shutdown FAILED -- check the supply", level="ERROR", component=name)
    if rg.rtd1:
        try:
            rg.rtd1.close()
        except Exception:
            pass
