# --- rPSU: the Rigol DP832A supplies (psu1, psu2) ---
#
# Owns the supplies: applies CAST requests written to "psu1" / "psu2" and
# publishes their status there. The request grammar, per channel "1".."3":
#
#   {"1": {"voltage": 5.0, "current": 0.1}}    setpoints (both required)
#   {"1": {"on": true}}                         output on / off
#   {"1": {"ovp": 6.0, "ocp": 0.2, "protect": true}}
#   {"update": true}                            refresh the status now
#
# Each supply's readings are published as PSU<n>_CH<c>_V / _I / _ON, so a
# run's CSV has the supplies beside everything else.
# On host stop, rShutdown turns off every channel this run switched on.
# Supplies disabled in usbmap.json are not opened.
import math
import os
import time

from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.devices.dp832a.config import enabled_psu_labels
from formslab.devices.dp832a.service import get_psu
from formslab.rscripts import RScriptControl

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("psu1", "psu2")
_CH = "<channel:ch1|ch2|ch3>"
COMMANDS = [
    (f"{_CH} set <volts:number 0..32 V> <amps:number 0..3.2 A>", """Set a channel's voltage and current limit
     The channel's output voltage, 0 to 32 V, and the most current it may supply, 0 to
     3.2 A. Its output is switched on and off separately. A channel the hardware map
     gives to an rScript, such as psu1 ch1 for the cryocooler board, is refused while
     that rScript runs.""",
     lambda ch, v, a: {ch[2:]: {"voltage": v, "current": a}}),
    (f"{_CH} on|off", """Turn a channel's output on or off
     Switches the channel's output, at its setpoints.""", lambda ch, s: {ch[2:]: {"on": s == "on"}}),
    (f"{_CH} protect <max_volts:number 0.01..33 V> <max_amps:number 0.001..3.3 A>",
     """Turn on over-voltage and over-current protection
     The supply cuts the channel off above the maximum voltage or current. Set them
     a little above the setpoints.""",
     lambda ch, v, a: {ch[2:]: {"ovp": v, "ocp": a, "protect": True}}),
    (f"{_CH} protect off", """Turn protection off
     The channel then has no over-voltage or over-current cut-off.""",
     lambda ch: {ch[2:]: {"protect": False}}),
    ("update", """Read the supply now
     Refreshes its readings without waiting for the next poll.""", {"update": True}),
]
_READINGS = {"on": "output", "vset": "set (V)", "cset": "limit (A)", "vmeas": "(V)", "cmeas": "(A)",
             "ovp": "OVP (V)", "ocp": "OCP (A)", "protect": "protection"}


def STATUS_LABELS(label, key):
    """`1 vset` -> `CH1 set (V) - cryocooler board`: what the hardware map says it feeds."""
    from formslab.devices.dp832a.wiring import channel

    ch, _, reading = key.partition(" ")
    if not ch.isdigit() or reading not in _READINGS:
        return {"error": "Error"}.get(key)
    feeds = channel(label, ch).get("feeds")
    return f"CH{ch} {_READINGS[reading]}" + (f" - {feeds}" if feeds else "")


VARIABLES = [(f"PSU{n}_CH{c}_{q}", unit) for n in (1, 2) for c in (1, 2, 3)
             for q, unit in (("V", "V"), ("I", "A"), ("ON", None))]


name = os.path.splitext(os.path.basename(__file__))[0]

PSU2_KEEPALIVE_INTERVAL = 10.0   # seconds between keep-alive queries when idle
PSU2_KEEPALIVE_BACKOFF  = 60.0   # max interval when PSU2 is persistently unreachable
PSU1_POLL_INTERVAL      = 5.0    # seconds between PSU1 readbacks published as scalars


class rGlobal:
    TICK_INTERVAL = 1.0

    _vars_initialized = False
    _status_initialized = False
    psu1 = None
    psu2 = None
    _psu2_keepalive_time      = 0.0
    _psu2_keepalive_fails     = 0      # consecutive all-null results
    _psu1_poll_time = 0.0
    _switched_on = set()                # (label, channel) turned on by this run


rg = rGlobal


def _init(run, r_global):
    if r_global._vars_initialized:
        return r_global

    try:
        enabled = enabled_psu_labels()
    except Exception as exc:
        run.log(f"usbmap unreadable, no PSU opened: {exc}", level="ERROR", component=name)
        enabled = ()

    for label in ("psu1", "psu2"):
        if label not in enabled:
            run.log(f"{label.upper()} disabled in usbmap; not opened", component=name)
            continue
        try:
            setattr(r_global, label, get_psu(label))
            run.log(f"{label.upper()} connected", component=name)
        except Exception as exc:
            run.log(f"{label.upper()} init failed: {exc}", level="ERROR", component=name)

    r_global._vars_initialized = True
    return r_global


def _publish_status(run, label, psu):
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
            run.log(
                f"{label.upper()} telemetry returned all-null — "
                "VISA link may be down; preserving previous CAST state",
                level="WARNING",
                component=name,
            )
            return False
        UpdateStatus(label, state)
        _publish_channels(run, label, state)
        return True
    except Exception as exc:
        run.log(
            f"{label.upper()} status update failed: {exc}",
            level="WARNING",
            component=name,
        )
        return False


def _publish_channels(run, label, state):
    """A supply's measured volts/amps and output state per channel, as
    PSU<n>_CH<c>_V / _I / _ON. A channel whose query failed is NaN, so the CSV
    shows the gap rather than a stale number."""
    prefix = label.upper()
    for ch, s in state.items():
        s = s if isinstance(s, dict) else {}
        for suffix, key, unit in (("V", "vmeas", "V"), ("I", "cmeas", "A")):
            value = s.get(key)
            run.publish(f"{prefix}_CH{ch}_{suffix}", math.nan if value is None else float(value), unit)
        on = s.get("on")
        run.publish(f"{prefix}_CH{ch}_ON", math.nan if on is None else float(bool(on)))


def _publish_gap(run, label):
    """The supply could not be read: NaN for every channel."""
    _publish_channels(run, label, {ch: {} for ch in (1, 2, 3)})


def _handle_channel_request(run, label, psu, channel_text, channel_request):
    if not isinstance(channel_request, dict):
        return False
    if psu is None:
        run.log(f"{label.upper()} not initialized — ignoring CH{channel_text} request", level="WARNING", component=name)
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
            run.log(
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
            run.log(
                f"{label.upper()} CH{channel} set to {voltage:.2f}V, {current:.3f}A",
                component=name,
            )
            changed = True

        if "on" in channel_request:
            if channel_request["on"]:
                psu.on(channel)
                rg._switched_on.add((label, channel))
                run.log(f"{label.upper()} CH{channel} turned ON", component=name)
            else:
                psu.off(channel)
                rg._switched_on.discard((label, channel))
                run.log(f"{label.upper()} CH{channel} turned OFF", component=name)
            changed = True
    except Exception as exc:
        run.log(
            f"{label.upper()} CH{channel} command failed: {exc}",
            level="ERROR",
            component=name,
        )

    return changed


def _handle_psu_request(run, label, psu, request):
    """Process a CAST request for a PSU. Returns True if a status refresh was performed."""
    if not request:
        return False

    refresh = False
    changed = False

    if request.get("shutdown"):
        run.log(f"{label.upper()} shutdown requested via CAST — ignored (use psucli)", level="WARNING", component=name)

    update = request.get("update")
    if isinstance(update, bool):
        refresh = update
    elif update is not None:
        run.log(
            f"Invalid {label} update request: {update}",
            level="WARNING",
            component=name,
        )

    for channel_text in ("1", "2", "3"):
        changed = _handle_channel_request(
            run,
            label,
            psu,
            channel_text,
            request.get(channel_text),
        ) or changed

    if refresh or changed:
        _publish_status(run, label, psu)
        return True

    return False


def rScript(run):
    global rg


    rg = _init(run, rg)

    try:
        control = RScriptControl(run, name)
        control.tick(seconds=rg.TICK_INTERVAL)
        if control:
            return
    except Exception as exc:
        run.log(f"RScriptControl exception: {exc}", level="ERROR", component=name)
        return

    if not rg._status_initialized:
        _publish_status(run, "psu1", rg.psu1)
        _publish_status(run, "psu2", rg.psu2)
        rg._status_initialized = True

    psu1_refreshed = _handle_psu_request(run, "psu1", rg.psu1, ReadCommand("psu1"))
    psu2_refreshed = _handle_psu_request(run, "psu2", rg.psu2, ReadCommand("psu2"))

    now = time.time()
    if psu1_refreshed:
        rg._psu1_poll_time = now
    elif rg.psu1 is not None and now - rg._psu1_poll_time >= PSU1_POLL_INTERVAL:
        _publish_status(run, "psu1", rg.psu1)
        rg._psu1_poll_time = now

    if rg.psu2 is None:
        return          # PSU2 not opened (disabled in usbmap, or failed): no keep-alive
    # Keep-alive: poll PSU2 periodically when idle so the serial link doesn't
    # go stale (PSU2 can go quiet for hours).
    # Back off exponentially when PSU2 is persistently unreachable so a dead
    # unit doesn't hammer the serial bus every 10 s.
    if psu2_refreshed:
        rg._psu2_keepalive_time  = now
        rg._psu2_keepalive_fails = 0
    else:
        interval = min(
            PSU2_KEEPALIVE_INTERVAL * (2 ** rg._psu2_keepalive_fails),
            PSU2_KEEPALIVE_BACKOFF,
        )
        if now - rg._psu2_keepalive_time >= interval:
            ok = _publish_status(run, "psu2", rg.psu2)
            rg._psu2_keepalive_time = now
            if ok:
                rg._psu2_keepalive_fails = 0
            else:
                rg._psu2_keepalive_fails = min(rg._psu2_keepalive_fails + 1, 3)
                _publish_gap(run, "psu2")
                if rg._psu2_keepalive_fails >= 2 and rg.psu2 is not None:
                    try:
                        rg.psu2.reconnect()
                        run.log("PSU2 reconnected", component=name)
                        rg._psu2_keepalive_fails = 0
                        if _publish_status(run, "psu2", rg.psu2):
                            rg._psu2_keepalive_time = time.time()
                    except Exception as exc:
                        run.log(
                            f"PSU2 reconnect failed: {exc}",
                            level="ERROR",
                            component=name,
                        )


def rShutdown(run):
    """Turn off every channel this run switched on, then hand the front panels
    back. Channels the run found on, or that an operator switched on from the
    console, are left as they are."""
    for label, channel in sorted(rg._switched_on):
        psu = getattr(rg, label, None)
        if psu is None:
            continue
        try:
            psu.off(channel)
            run.log(f"{label.upper()} CH{channel} turned OFF at shutdown", component=name)
        except Exception as exc:
            run.log(f"{label.upper()} CH{channel} OFF at shutdown FAILED: {exc}",
                      level="ERROR", component=name)
    rg._switched_on.clear()
    for label in ("psu1", "psu2"):
        psu = getattr(rg, label, None)
        if psu is not None:
            try:
                _publish_status(run, label, psu)
                psu.disconnect()
            except Exception as exc:
                run.log(f"{label.upper()} release failed: {exc}", level="WARNING", component=name)
