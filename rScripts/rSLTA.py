# --- rSLTA: the sLTA camera ---
#
# Captures on a forced request ({"image": true} on CAST "slta"), or, with
# SLTARUN on, whenever the run says the spacecraft is in umbra. Umbra comes from
# three variables on the handle -- InUmbra (0/1), UmbraDuration and
# UmbraTimeRemaining (s) -- which an eclipse profile computed by FORMS provides.
# Until something publishes them, SLTARUN captures nothing.
import os,time,math
import traceback
from pathlib import Path
from formslab.rscripts import (
    RScriptControl, rTaskRegister, rTaskRunning, rTaskStart, rTaskStop, set_logger,
)
from formslab.devices.slta.imaging import capture
from formslab.console.cast.castutils import UpdateStatus, ReadCommand, ReadStatus
from formslab.devices.slta.routine import _init, _init_psu
from formslab.devices.slta.exposure import ExposureManager
from formslab.devices.dp832a.commands import (
    build_psu_channel_request,
    queue_psu_request,
    wait_for_psu_channel,
)
from formslab.rscripts.cast import CastUsage, choice, integer

# --- Constants ---
name = os.path.splitext(os.path.basename(__file__))[0]

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("slta",)
CAST_HELP = [
    ("slta image", "Capture one image now"),
    ("slta run on|off", "Umbra-triggered captures (SLTARUN)"),
    ("slta exposure <s>|auto", "Exposure 1-3600 s, or auto (umbra-based)"),
    ("slta idle <s>", "Idle polling interval, 1-600 s"),
    ("slta nsamp <n>", "Samples per pixel, 1-1000"),
    ("slta clear <s>", "Clear dwell, 0-600 s"),
    ("slta version v1|v2", "Driver version"),
    ("slta imagedir <name>|default", "Image subdirectory (no spaces)"),
    ("slta startup|shutdown", "Power the camera supply up / down"),
]


def cast_request(label, words):
    usage = "slta image | run on|off | exposure <s>|auto | idle <s> | nsamp <n> | clear <s> | version v1|v2 | imagedir <name> | startup | shutdown"
    if len(words) == 1:
        w = words[0].lower()
        if w == "image":
            return {"image": True}
        if w in ("startup", "shutdown"):
            return {w: True}
    if len(words) == 2:
        w, v = words[0].lower(), words[1]
        if w == "run":
            return {"SLTARUN": choice(v, ("on", "off"), "slta run")}
        if w == "exposure":
            return {"exposureAuto": True} if v.lower() == "auto" else {"exposure": integer(v, "exposure s", 1, 3600)}
        if w == "idle":
            return {"idle": integer(v, "idle s", 1, 600)}
        if w == "nsamp":
            return {"nsamp": integer(v, "nsamp", 1, 1000)}
        if w == "clear":
            return {"clear": integer(v, "clear s", 0, 600)}
        if w == "version" and v.lower() in ("v1", "v2"):
            return {"version": v.lower()}
        if w == "imagedir":
            return {"IMAGEDIR": v}
    raise CastUsage(usage)
# --- Encapsulated State ---
class rGlobal:
    disable = False
    useInitialize = False
    useHold = True
    useTick = False
    HOLD_INTERVAL = 3
    TICK_INTERVAL = 5
    _vars_initialized = False
    psu2            = None
    register        = False
    running         = False
    umbra           = None
    umbraDuration   = None
    exposure        = 10
    idle            = 30
    ImageToken      = None
    exposureMgr     = None
    SLTARUN         = False
    shutdown        = False
    startup         = False
    cmd             = {'mode': 'E', 'exposure': 600, 'idle': 30, 'nsamp': 1, 'clear': 30, 'version': 'v1', 'TK': None, 'IMAGEDIR': None}
    TC              = 'TC01'
    _psu2_ready = False
    _psu2_request_pending = False
    _psu2_status_unknown_reported = False
rg = rGlobal


def _power_on_slta_channel():
    queue_psu_request(
        "psu2",
        build_psu_channel_request(1, on=True),
        update=True,
    )
    wait_for_psu_channel("psu2", 1, on=True, timeout=5.0)


def _power_off_slta_channel():
    queue_psu_request(
        "psu2",
        build_psu_channel_request(1, on=False),
        update=True,
    )
    wait_for_psu_channel("psu2", 1, on=False, timeout=5.0)


def _queue_psu2_shutdown():
    queue_psu_request(
        "psu2",
        build_psu_channel_request(1, on=False),
        update=True,
    )
# === Register capture cycle callback once ===
def _task(stop_event, cmd: dict) -> None:
    try:
        capture(cmd=cmd, power_on=_power_on_slta_channel, power_off=_power_off_slta_channel)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
    finally:
        rg.cmd['mode'] = 'E'
        if rg.exposureMgr:
            rg.exposureMgr.release()
        UpdateStatus(label="slta", status={"running": False, "token": None})

def _value(forms, var_name):
    var = forms.get_variable(var_name)
    try:
        return None if var is None else float(var.value)
    except (TypeError, ValueError):
        return None


def _umbra(forms):
    """(in umbra, umbra duration s, time remaining s) from the handle's
    variables; (None, None, None) while nothing publishes them."""
    flag = _value(forms, "InUmbra")
    return (None if flag is None else bool(flag),
            _value(forms, "UmbraDuration"), _value(forms, "UmbraTimeRemaining"))


# --- rScript Entry Point ---
def rScript(forms):
    global rg
    if rg.disable: return
    else: _init(forms, rg)
    # === Refactored execution control ===
    try:
        r = RScriptControl(forms, name)
        if rg.useInitialize: r.initialize()
        if rg.useHold:       r.hold(seconds=rg.HOLD_INTERVAL)
        if rg.useTick:       r.tick(seconds=rg.TICK_INTERVAL)
        if r: return
        else: 
            if rg.useHold: rg.useHold = False 
    except Exception as e:
        forms.log(f"❌ RScriptControl exception: {e}", level="ERROR", component="rSLTA")
        return

    _init_psu(forms, rg)
    
    # === Initialize Tasks
    set_logger(lambda msg: forms.log(msg, component="TASK"))
    # === Register capture cycle callback once ===
    if not rg.register:
            rTaskRegister("slta", _task)
            rg.register = True

    # === Read and Handle CAST commands ===
    request1 = ReadCommand(label="slta")

    if request1:
    # Exposure (CLI override or auto)
        try:
            # Check for auto mode first
            if request1.get("exposureAuto"):
                if rg.exposureMgr:
                    rg.exposureMgr.clearOverride()
                forms.log("Exposure set to AUTO (umbra-based)", component="rSLTA")
            else:
                val = request1.get("exposure")
                if isinstance(val, int):
                    rg.cmd['exposure'] = val
                    if rg.exposureMgr:
                        rg.exposureMgr.setOverride(val)
                    forms.log(f"Exposure override set to {val}s via CAST", component="rSLTA")
                elif val is not None:
                    raise ValueError("exposure must be an integer")
        except Exception as e:
            forms.log(f"Invalid exposure in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Idle time
        try:
            val = request1.get("idle")
            if isinstance(val, int):
                rg.cmd['idle'] = val
                forms.log(f"Idle updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("idle must be an integer")
        except Exception as e:
            forms.log(f"Invalid idle time in request: {val} — {e}", level="WARNING", component="rSLTA")

        # NSAMP
        try:
            val = request1.get("nsamp")
            if isinstance(val, int):
                rg.cmd['nsamp'] = val
                forms.log(f"NSAMP updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("nsamp must be an integer")
        except Exception as e:
            forms.log(f"Invalid nsamp in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Clear dwell time
        try:
            val = request1.get("clear")
            if isinstance(val, int):
                rg.cmd['clear'] = val
                forms.log(f"Clear updated to {val}s via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("clear must be an integer")
        except Exception as e:
            forms.log(f"Invalid clear in request: {val} — {e}", level="WARNING", component="rSLTA")

        # SLTA version (v1 or v2)
        try:
            val = request1.get("version")
            if isinstance(val, str) and val in ("v1", "v2"):
                rg.cmd['version'] = val
                forms.log(f"SLTA version set to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("version must be 'v1' or 'v2'")
        except Exception as e:
            forms.log(f"Invalid version in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Image directory
        try:
            val = request1.get("IMAGEDIR")
            if val == "default":
                rg.cmd['IMAGEDIR'] = None
                forms.log("Image directory reset to default via CAST request", component="rSLTA")
            elif isinstance(val, str):
                rg.cmd['IMAGEDIR'] = val
                forms.log(f"Image directory updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("Image directory must be a string or 'default'")
        except Exception as e:
            forms.log(f"Invalid image directory in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Boolean requests
        for attr in ("shutdown", "startup", "SLTARUN"):
            val = request1.get(attr)
            if val is None:
                continue
            try:
                if not isinstance(val, bool):
                    raise ValueError(f"{attr} must be a boolean")
                setattr(rg, attr, val)
                forms.log(f"{attr.capitalize()} request: {val}", level="INFO", component="rSLTA")
            except Exception as e:
                forms.log(f"Invalid {attr} request: {val} — {e}", level="WARNING", component="rSLTA")

    # Raw PSU2 CAST ownership now lives in rPSU.


    # === If Startup or Shutdown Logic ===
    if rg.shutdown:
        _queue_psu2_shutdown()
        forms.log("Queued PSU2 shutdown.", component="rSLTA")
        UpdateStatus(label="psu2", status=ReadStatus("psu2"))
        rg.shutdown = False
        rg._psu2_ready = False
        rg._psu2_request_pending = False
        rg._psu2_status_unknown_reported = False
    elif rg.startup:
        _init_psu(forms,rg)
        rg.startup = False

    # === Token Logic ===
    if request1 and request1.get("image", False):
        forms.log("Forced image request detected", component="rSLTA")
        token = rg.ImageToken.force()
        rg.cmd['mode'] = "F"
    elif rg.SLTARUN:
        rg.umbra, rg.umbraDuration, _ = _umbra(forms)
        if rg.exposureMgr:
            rg.exposureMgr.update(rg.umbraDuration)
        token = rg.ImageToken.update(rg.umbra)
        if token:
            durationStr = f"duration={rg.umbraDuration:.0f}s" if rg.umbraDuration and not math.isnan(rg.umbraDuration) else "duration=unknown"
            forms.log(f"Umbra-triggered token | {durationStr}", component="rSLTA")
    else: token = None

    # Update Task Status
    rg.running = rTaskRunning("slta")

    # === Abort if leaving umbra (unless forced) ===
    if not rg.umbra and rg.running and not (rg.cmd['mode'] == 'F'):
        forms.log("Exiting umbra — aborting capture", component="rSLTA")
        rTaskStop("slta")  # Signal the thread to stop and wait for it to clean up
        _power_off_slta_channel()
        rg.running = False
        if rg.exposureMgr:
            rg.exposureMgr.release()

    # === Start capture if token is valid ===
    if token and (rg.umbra or rg.cmd['mode'] == 'F'):
        if not rg._psu2_ready:
            forms.log(
                "PSU2 is not ready for SLTA capture yet — waiting for rPSU to apply configuration",
                level="INFO",
                component="rSLTA",
            )
            token = None
        else:
        # Lock exposure for this sequence
            if rg.exposureMgr:
                lockedExp = rg.exposureMgr.lock()
                rg.cmd['exposure'] = lockedExp
            forms.log(
                f"Starting capture | exposure={rg.cmd['exposure']}s | token={token}",
                component="rSLTA"
            )
            try:
                val = forms.get_variable("TC01").value
                rg.cmd["TK"] = int(round(val)) if val is not None else None
            except Exception as e:
                forms.log(f"Failed to read or round{rg.TC}: {e}", level="WARNING", component="rSLTA")
                rg.cmd["TK"] = None
           
            if rTaskStart("slta", cmd=rg.cmd):
                UpdateStatus(label="slta", status={"running": True, "token": token})
            else:
                forms.log(
                    "Capture cycle start failed — task already running",
                    level="WARNING", component="rSLTA"
                )
                if rg.exposureMgr:
                    rg.exposureMgr.release()  # Release lock if start failed
    elif token:
        forms.log("Token available but outside umbra and no forced capture — skipping", component="rSLTA")

    # === SLTA Status Update ===
    expStatus = rg.exposureMgr.status() if rg.exposureMgr else {}
    timeRemaining = _umbra(forms)[2] if rg.SLTARUN else None
    UpdateStatus(
        label="slta",
        status = {
            "SLTARUN": rg.SLTARUN,
            "running": rg.running,
            "in_umbra": rg.umbra,
            "umbraDuration": rg.umbraDuration,
            "umbraTimeRemaining": timeRemaining,
            "mode": rg.cmd['mode'],
            "exposure": rg.cmd['exposure'],
            "exposureComputed": expStatus.get('computed'),
            "exposureLocked": expStatus.get('locked', False),
            "exposureOverride": expStatus.get('override'),
            "idle": rg.cmd['idle'],
            "nsamp": rg.cmd['nsamp'],
            "clear": rg.cmd['clear'],
            "version": rg.cmd['version'],
            "token": token,
            "IMAGEDIR": rg.cmd['IMAGEDIR'],
        }
    )
