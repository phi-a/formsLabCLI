import math
import os
import time

from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from forms.utils.rScripts import RScriptControl
from formslab.devices.psu_service import get_psu


name = os.path.splitext(os.path.basename(__file__))[0]

PSU2_KEEPALIVE_INTERVAL = 10.0   # seconds between keep-alive queries when idle
PSU2_KEEPALIVE_BACKOFF  = 60.0   # max interval when PSU2 is persistently unreachable


class rGlobal:
    disable = False
    useInitialize = False
    useHold = False
    useTick = True
    TICK_INTERVAL = 1.0

    _vars_initialized = False
    _status_initialized = False
    psu1 = None
    psu2 = None
    _psu2_keepalive_time      = 0.0
    _psu2_keepalive_fails     = 0      # consecutive all-null results
    _psu2_var_missing_reported = False


rg = rGlobal


def _init(forms, r_global):
    if r_global._vars_initialized:
        return r_global

    try:
        r_global.psu1 = get_psu("psu1")
    except Exception as exc:
        forms.log(f"PSU1 init failed: {exc}", level="ERROR", component=name)

    try:
        r_global.psu2 = get_psu("psu2")
    except Exception as exc:
        forms.log(f"PSU2 init failed: {exc}", level="ERROR", component=name)

    r_global._vars_initialized = True
    return r_global


def _publish_status(forms, label, psu):
    """Publish PSU telemetry to CAST. Returns True on success, False when the
    VISA link appears to be down (all channels null)."""
    if psu is None:
        UpdateStatus(label, {"error": f"{label.upper()} not initialized"})
        return False

    try:
        state = psu.status()
        # If every queried channel came back null, the VISA link is down.
        # Preserve the previous CAST state so downstream routines keep
        # their last-known-good readings instead of seeing "unknown".
        if state and all(
            isinstance(v, dict) and v.get("vset") is None
            for v in state.values()
        ):
            forms.log(
                f"{label.upper()} telemetry returned all-null — "
                "VISA link may be down; preserving previous CAST state",
                level="WARNING",
                component=name,
            )
            return False
        UpdateStatus(label, state)
        return True
    except Exception as exc:
        forms.log(
            f"{label.upper()} status update failed: {exc}",
            level="WARNING",
            component=name,
        )
        return False


def _publish_psu2_variable(forms, psu):
    """Write the PSU2 [[v1,i1],[v2,i2]] tensor to the recorded variable.
    Uses psu.state if available; emits NaN rows when PSU2 is not initialized."""
    nan = math.nan

    if psu is None or not psu.state:
        tensor = [[nan, nan], [nan, nan]]
    else:
        tensor = []
        for ch in (1, 2):
            s = psu.state.get(ch, {})
            v = s.get("vmeas")
            i = s.get("cmeas")
            tensor.append([
                float(v) if v is not None else nan,
                float(i) if i is not None else nan,
            ])

    psu2_var = forms.get_variable("PSU2")
    if psu2_var is not None:
        psu2_var.set(tensor)
        rg._psu2_var_missing_reported = False
    elif not rg._psu2_var_missing_reported:
        forms.log(
            "Variable PSU2 is not registered; skipping PSU2 tensor publish",
            level="WARNING",
            component=name,
        )
        rg._psu2_var_missing_reported = True


def _handle_channel_request(forms, label, psu, channel_text, channel_request):
    if not isinstance(channel_request, dict):
        return False
    if psu is None:
        forms.log(f"{label.upper()} not initialized — ignoring CH{channel_text} request", level="WARNING", component=name)
        return False

    channel = int(channel_text)
    changed = False

    try:
        if (
            "ovp" in channel_request
            or "ocp" in channel_request
            or "protect" in channel_request
            or "protect_enabled" in channel_request
        ):
            ovp = channel_request.get("ovp")
            ocp = channel_request.get("ocp")
            protect = channel_request.get("protect", channel_request.get("protect_enabled"))
            if protect is not None and not isinstance(protect, bool):
                raise ValueError("protect must be a boolean")
            psu.setOVCP(
                channel,
                ovp=None if ovp is None else float(ovp),
                ocp=None if ocp is None else float(ocp),
                enable=True if protect is None else bool(protect),
            )
            forms.log(
                f"{label.upper()} CH{channel} protection updated"
                + (
                    f" (ovp={float(ovp):.2f}V, ocp={float(ocp):.3f}A, "
                    f"enabled={True if protect is None else bool(protect)})"
                    if ovp is not None or ocp is not None or protect is not None
                    else ""
                ),
                component=name,
            )
            changed = True

        if "voltage" in channel_request or "current" in channel_request:
            if "voltage" not in channel_request or "current" not in channel_request:
                raise ValueError("set requires both voltage and current")
            voltage = float(channel_request["voltage"])
            current = float(channel_request["current"])
            psu.set(channel, voltage, current)
            forms.log(
                f"{label.upper()} CH{channel} set to {voltage:.2f}V, {current:.3f}A",
                component=name,
            )
            changed = True

        if "on" in channel_request:
            if channel_request["on"]:
                psu.on(channel)
                forms.log(f"{label.upper()} CH{channel} turned ON", component=name)
            else:
                psu.off(channel)
                forms.log(f"{label.upper()} CH{channel} turned OFF", component=name)
            changed = True
    except Exception as exc:
        forms.log(
            f"{label.upper()} CH{channel} command failed: {exc}",
            level="ERROR",
            component=name,
        )

    return changed


def _handle_psu_request(forms, label, psu, request):
    """Process a CAST request for a PSU. Returns True if a status refresh was performed."""
    if not request:
        return False

    refresh = False
    changed = False

    if request.get("shutdown"):
        forms.log(f"{label.upper()} shutdown requested via CAST — ignored (use psucli)", level="WARNING", component=name)

    update = request.get("update")
    if isinstance(update, bool):
        refresh = update
    elif update is not None:
        forms.log(
            f"Invalid {label} update request: {update}",
            level="WARNING",
            component=name,
        )

    for channel_text in ("1", "2", "3"):
        changed = _handle_channel_request(
            forms,
            label,
            psu,
            channel_text,
            request.get(channel_text),
        ) or changed

    if refresh or changed:
        _publish_status(forms, label, psu)
        return True

    return False


def rScript(forms):
    global rg

    if rg.disable:
        return

    rg = _init(forms, rg)

    try:
        control = RScriptControl(forms, name)
        if rg.useInitialize:
            control.initialize()
        if rg.useHold:
            control.hold(seconds=1.0)
        if rg.useTick:
            control.tick(seconds=rg.TICK_INTERVAL)
        if control:
            return
    except Exception as exc:
        forms.log(f"RScriptControl exception: {exc}", level="ERROR", component=name)
        return

    if not rg._status_initialized:
        _publish_status(forms, "psu1", rg.psu1)
        _publish_status(forms, "psu2", rg.psu2)
        rg._status_initialized = True

    _handle_psu_request(forms, "psu1", rg.psu1, ReadCommand("psu1"))
    psu2_refreshed = _handle_psu_request(forms, "psu2", rg.psu2, ReadCommand("psu2"))

    if psu2_refreshed:
        _publish_psu2_variable(forms, rg.psu2)

    # Keep-alive: poll PSU2 periodically when idle so the serial link doesn't
    # go stale (PSU1 stays alive via TVAC commands; PSU2 can go quiet for hours).
    # Back off exponentially when PSU2 is persistently unreachable so a dead
    # unit doesn't hammer the serial bus every 10 s.
    now = time.time()
    if psu2_refreshed:
        rg._psu2_keepalive_time  = now
        rg._psu2_keepalive_fails = 0
    else:
        interval = min(
            PSU2_KEEPALIVE_INTERVAL * (2 ** rg._psu2_keepalive_fails),
            PSU2_KEEPALIVE_BACKOFF,
        )
        if now - rg._psu2_keepalive_time >= interval:
            ok = _publish_status(forms, "psu2", rg.psu2)
            rg._psu2_keepalive_time = now
            if ok:
                rg._psu2_keepalive_fails = 0
                _publish_psu2_variable(forms, rg.psu2)
            else:
                rg._psu2_keepalive_fails = min(rg._psu2_keepalive_fails + 1, 3)
                _publish_psu2_variable(forms, rg.psu2)  # emit NaN rows on failure
                if rg._psu2_keepalive_fails >= 2 and rg.psu2 is not None:
                    try:
                        rg.psu2.reconnect()
                        forms.log("PSU2 reconnected", component=name)
                        rg._psu2_keepalive_fails = 0
                        if _publish_status(forms, "psu2", rg.psu2):
                            _publish_psu2_variable(forms, rg.psu2)
                            rg._psu2_keepalive_time = time.time()
                    except Exception as exc:
                        forms.log(
                            f"PSU2 reconnect failed: {exc}",
                            level="ERROR",
                            component=name,
                        )
