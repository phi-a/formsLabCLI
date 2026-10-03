from formslab.devices.slta.exposure import ExposureManager
import time,math,uuid
from typing import Optional
from formslab.devices.dp832a.commands import (
    build_psu_channel_request,
    psu_channel_state,
    queue_psu_request,
)
# --- Helper Functions ---
def _init(run,rGlobal):
    if rGlobal._vars_initialized:
        return
    _init_psu(run, rGlobal)
    rGlobal._vars_initialized = True
    rGlobal.ImageToken = ImageToken()
    rGlobal.exposureMgr = ExposureManager(defaultExposure=600)
    rGlobal.measure = True
    return

def _init_psu(run, rGlobal):
    readiness = psu_channel_state("psu2", 1, voltage=12.0, current=2.0)

    if readiness == "match":
        if not getattr(rGlobal, "_psu2_ready", False):
            run.log("SLTA supply ready on PSU2 CH1", level="INFO", component="PSU2")
        rGlobal._psu2_ready = True
        rGlobal._psu2_request_pending = False
        rGlobal._psu2_status_unknown_reported = False
        return

    if readiness == "unknown":
        if not getattr(rGlobal, "_psu2_status_unknown_reported", False):
            run.log(
                "PSU2 CH1 telemetry is unavailable; preserving last-known SLTA configuration",
                level="WARNING",
                component="PSU2",
            )
            rGlobal._psu2_status_unknown_reported = True
        return

    if getattr(rGlobal, "_psu2_request_pending", False):
        rGlobal._psu2_ready = False
        return

    rGlobal._psu2_status_unknown_reported = False
    run.log("Setting PSU2 CH1 for SLTA...", level="INFO", component="PSU2")
    queue_psu_request(
        "psu2",
        build_psu_channel_request(1, ovp=12.5, ocp=2.1, protect=True, voltage=12.0, current=2.0),
        update=True,
    )
    rGlobal._psu2_request_pending = True
    rGlobal._psu2_ready = False
    return

# measurement helper functions
# Ensure rg.measuredIV exists and is ordered by channel id
def _init_measured_struct(rg):
    if not getattr(rg, "measuredIV", None):
        rg.measuredIV = {1: (math.nan, math.nan), 2: (math.nan, math.nan)}
    if not getattr(rg, "measuredP", None):
        rg.measuredP  = {1: math.nan, 2: math.nan}

def _plausible(v, i):
    # conservative DP832A envelope; tweak if you need
    return (
        v is not None and i is not None and
        0.0 <= v <= 32.0 and 0.0 <= i <= 5.2
    )        

# === Image Token Generator ===
class ImageToken:
    """
    Generates a one‐shot token on Umbra entry (sunlit→umbra), and
    hands it out exactly once per entry. Clears on Umbra exit.
    """
    def __init__(self):
        self._prev_in_umbra = False
        self._token = None
        self._consumed = True

    def update(self, in_umbra: bool) -> Optional[str]:
        # 1) on Umbra entry: reset consumed, make new token
        if in_umbra and not self._prev_in_umbra:
            self._token    = f"{int(time.time())}_{uuid.uuid4().hex}"
            self._consumed = False

        # 2) on Umbra exit: clear everything
        elif not in_umbra and self._prev_in_umbra:
            self._token    = None
            self._consumed = True

        self._prev_in_umbra = in_umbra

        # 3) only hand out the token once per entry
        if self._token and not self._consumed:
            self._consumed = True
            return self._token
        return None
    
    def force(self) -> str:
        """Forces creation of a new token immediately."""
        self._token = f"manual_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        self._consumed = True  # Consume immediately
        return self._token
    
# === Handle sLTA CAST Commands ===
def HandleSLTARequest(run, request: dict, rg, label: str) -> object:
    """Update rg object based on incoming SLTA request block."""
    if not request:
        return
    
    if label == 'slta':
    # Exposure
        try:
            val = request.get("exposure")
            if isinstance(val, int):
                rg.cmd['exposure'] = val
                run.log(f"Exposure updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("exposure must be an integer")
        except Exception as e:
            run.log(f"Invalid exposure in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Idle time
        try:
            val = request.get("idle")
            if isinstance(val, int):
                rg.cmd['idle'] = val
                run.log(f"Idle updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("idle must be an integer")
        except Exception as e:
            run.log(f"Invalid idle time in request: {val} — {e}", level="WARNING", component="rSLTA")

        # NSAMP
        try:
            val = request.get("nsamp")
            if isinstance(val, int):
                rg.cmd['nsamp'] = val
                run.log(f"NSAMP updated to {val} via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("nsamp must be an integer")
        except Exception as e:
            run.log(f"Invalid nsamp in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Clear dwell time
        try:
            val = request.get("clear")
            if isinstance(val, int):
                rg.cmd['clear'] = val
                run.log(f"Clear updated to {val}s via CAST request", component="rSLTA")
            elif val is not None:
                raise ValueError("clear must be an integer")
        except Exception as e:
            run.log(f"Invalid clear in request: {val} — {e}", level="WARNING", component="rSLTA")

        # Image directory
        try:
            val = request.get("IMAGEDIR")
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
            val = request.get(attr)
            if val is None:
                continue
            try:
                if not isinstance(val, bool):
                    raise ValueError(f"{attr} must be a boolean")
                setattr(rg, attr, val)
                run.log(f"{attr.capitalize()} request: {val}", level="INFO", component="rSLTA")
            except Exception as e:
                run.log(f"Invalid {attr} request: {val} — {e}", level="WARNING", component="rSLTA")
        return

    # Special case: PSU update
    if label == "psu2":
        try:
            val = request.get("update")
            if isinstance(val, bool):
                rg.update = val
                run.log(f"Update request: {val}", level="INFO", component="rSLTA")
            elif val is not None:
                raise ValueError("update must be a boolean")
        except Exception as e:
            run.log(f"Invalid update request: {val} — {e}", level="WARNING", component="rSLTA")
    return
