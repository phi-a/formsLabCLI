# --- rSLTA: the sLTA camera ---
#
# Captures on a forced request ({"image": true} on CAST "slta"), or, with
# SLTARUN on, whenever the run says the spacecraft is in umbra. Umbra comes from
# three variables on the run -- InUmbra (0/1), UmbraDuration and
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

# --- Constants ---
name = os.path.splitext(os.path.basename(__file__))[0]

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("slta",)
COMMANDS = [
    ("image", "Capture one image now", {"image": True}),
    ("run on|off", "Umbra-triggered captures (SLTARUN)", lambda s: {"SLTARUN": s == "on"}),
    ("exposure <s:integer 1..3600 s>", "Exposure time", lambda t: {"exposure": t}),
    ("exposure auto", "Exposure from the umbra duration", {"exposureAuto": True}),
    ("idle <s:integer 1..600 s>", "Idle polling interval", lambda t: {"idle": t}),
    ("nsamp <n:integer 1..1000>", "Samples per pixel", lambda n: {"nsamp": n}),
    ("clear <s:integer 0..600 s>", "Clear dwell", lambda t: {"clear": t}),
    ("version v1|v2", "Driver version", lambda v: {"version": v}),
    ("imagedir <name:text>", "Image subdirectory (no spaces); `default` resets it",
     lambda n: {"IMAGEDIR": n}),
    ("startup|shutdown", "Power the camera supply up / down", lambda w: {w: True}),
]
VARIABLES = []

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

def _value(run, var_name):
    try:
        return None if run.get(var_name) is None else float(run.get(var_name))
    except (TypeError, ValueError):
        return None


def _umbra(run):
    """(in umbra, umbra duration s, time remaining s) from the run's
    variables; (None, None, None) while nothing publishes them."""
    flag = _value(run, "InUmbra")
    return (None if flag is None else bool(flag),
            _value(run, "UmbraDuration"), _value(run, "UmbraTimeRemaining"))


# --- rScript Entry Point ---
def rScript(run):
    global rg
    if rg.disable: return
    else: _init(run, rg)
    # === Refactored execution control ===
    try:
        r = RScriptControl(run, name)
        if rg.useInitialize: r.initialize()
        if rg.useHold:       r.hold(seconds=rg.HOLD_INTERVAL)
        if rg.useTick:       r.tick(seconds=rg.TICK_INTERVAL)
        if r: return
        else: 
            if rg.useHold: rg.useHold = False 
    except Exception as e:
        run.log(f"❌ RScriptControl exception: {e}", level="ERROR", component="rSLTA")
        return

    _init_psu(run, rg)
    
    # === Initialize Tasks
    set_logger(lambda msg: run.log(msg, component="TASK"))
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
                run.log("Exposure set to AUTO (umbra-based)", component="rSLTA")
            else:
                val = request1.get("exposure")
                if isinstance(val, int):
                    rg.cmd['exposure'] = val
                    if rg.exposureMgr:
                        rg.exposureMgr.setOverride(val)
                    run.log(f"Exposure override set to {val}s via CAST", component="rSLTA")
                elif val is not None:
                    raise ValueError("exposure must be an integer")
        except Exception as e:
            run.log(f"Invalid exposure in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Idle time
        try:
            val = request1.get("idle")
            if isinstance(val, int):
                rg.cmd['idle'] = val
                run.log(f"Idle updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("idle must be an integer")
        except Exception as e:
            run.log(f"Invalid idle time in request: {val} — {e}", level="WARNING", component="rSLTA")

        # NSAMP
        try:
            val = request1.get("nsamp")
            if isinstance(val, int):
                rg.cmd['nsamp'] = val
                run.log(f"NSAMP updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("nsamp must be an integer")
        except Exception as e:
            run.log(f"Invalid nsamp in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Clear dwell time
        try:
            val = request1.get("clear")
            if isinstance(val, int):
                rg.cmd['clear'] = val
                run.log(f"Clear updated to {val}s via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("clear must be an integer")
        except Exception as e:
            run.log(f"Invalid clear in request: {val} — {e}", level="WARNING", component="rSLTA")

        # SLTA version (v1 or v2)
        try:
            val = request1.get("version")
            if isinstance(val, str) and val in ("v1", "v2"):
                rg.cmd['version'] = val
                run.log(f"SLTA version set to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("version must be 'v1' or 'v2'")
        except Exception as e:
            run.log(f"Invalid version in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Image directory
        try:
            val = request1.get("IMAGEDIR")
            if val == "default":
                rg.cmd['IMAGEDIR'] = None
                run.log("Image directory reset to default via CAST request", component="rSLTA")
            elif isinstance(val, str):
                rg.cmd['IMAGEDIR'] = val
                run.log(f"Image directory updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("Image directory must be a string or 'default'")
        except Exception as e:
            run.log(f"Invalid image directory in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Boolean requests
        for attr in ("shutdown", "startup", "SLTARUN"):
            val = request1.get(attr)
            if val is None:
                continue
            try:
                if not isinstance(val, bool):
                    raise ValueError(f"{attr} must be a boolean")
                setattr(rg, attr, val)
                run.log(f"{attr.capitalize()} request: {val}", level="INFO", component="rSLTA")
            except Exception as e:
                run.log(f"Invalid {attr} request: {val} — {e}", level="WARNING", component="rSLTA")

    # Raw PSU2 CAST ownership now lives in rPSU.


    # === If Startup or Shutdown Logic ===
    if rg.shutdown:
        _queue_psu2_shutdown()
        run.log("Queued PSU2 shutdown.", component="rSLTA")
        UpdateStatus(label="psu2", status=ReadStatus("psu2"))
        rg.shutdown = False
        rg._psu2_ready = False
        rg._psu2_request_pending = False
        rg._psu2_status_unknown_reported = False
    elif rg.startup:
        _init_psu(run,rg)
        rg.startup = False

    # === Token Logic ===
    if request1 and request1.get("image", False):
        run.log("Forced image request detected", component="rSLTA")
        token = rg.ImageToken.force()
        rg.cmd['mode'] = "F"
    elif rg.SLTARUN:
        rg.umbra, rg.umbraDuration, _ = _umbra(run)
        if rg.exposureMgr:
            rg.exposureMgr.update(rg.umbraDuration)
        token = rg.ImageToken.update(rg.umbra)
        if token:
            durationStr = f"duration={rg.umbraDuration:.0f}s" if rg.umbraDuration and not math.isnan(rg.umbraDuration) else "duration=unknown"
            run.log(f"Umbra-triggered token | {durationStr}", component="rSLTA")
    else: token = None

    # Update Task Status
    rg.running = rTaskRunning("slta")

    # === Abort if leaving umbra (unless forced) ===
    if not rg.umbra and rg.running and not (rg.cmd['mode'] == 'F'):
        run.log("Exiting umbra — aborting capture", component="rSLTA")
        rTaskStop("slta")  # Signal the thread to stop and wait for it to clean up
        _power_off_slta_channel()
        rg.running = False
        if rg.exposureMgr:
            rg.exposureMgr.release()

    # === Start capture if token is valid ===
    if token and (rg.umbra or rg.cmd['mode'] == 'F'):
        if not rg._psu2_ready:
            run.log(
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
            run.log(
                f"Starting capture | exposure={rg.cmd['exposure']}s | token={token}",
                component="rSLTA"
            )
            try:
                val = run.get("TC01")
                rg.cmd["TK"] = int(round(val)) if val is not None else None
            except Exception as e:
                run.log(f"Failed to read or round{rg.TC}: {e}", level="WARNING", component="rSLTA")
                rg.cmd["TK"] = None
           
            if rTaskStart("slta", cmd=rg.cmd):
                UpdateStatus(label="slta", status={"running": True, "token": token})
            else:
                run.log(
                    "Capture cycle start failed — task already running",
                    level="WARNING", component="rSLTA"
                )
                if rg.exposureMgr:
                    rg.exposureMgr.release()  # Release lock if start failed
    elif token:
        run.log("Token available but outside umbra and no forced capture — skipping", component="rSLTA")

    # === SLTA Status Update ===
    expStatus = rg.exposureMgr.status() if rg.exposureMgr else {}
    timeRemaining = _umbra(run)[2] if rg.SLTARUN else None
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
