# --- rCryoBoard: the cryocooler control board (the K508N's drive) ---
#
# Owns the board through the Pico I2C bridge (docs/CRYOCOOLER.md): the converter's
# output enable and voltage (cryo on|off, cryo ccv) and the variable resistor
# (cryo ccvres, cryo code), with its supply, psu1 CH1, asked of rPSU. Each request
# on CAST "cryo" is answered: done once the board has applied it, or refused with
# why, so a plan step stops the run on a refusal. The board's state is published
# for plans to wait on, and recorded: CRYO_LINK, CRYO_ON, CRYO_OK, CRYO_CCV, CRYO_RES,
# CRYO_SUPPLY_V.
import os
import time

from formslab.console.cast.castutils import ReportResult, TakeCommand, UpdateStatus
from formslab.rscripts import RScriptControl
from formslab.devices.cryocooler.owner import _init_cryo_board, _init_psu2, _shutdown_cryo_subsystem
from formslab.devices.cryocooler.config import (
    CCV_MAX_V,
    CCV_MIN_V,
    CRYO_OUTPUT_SUPPLY_THRESHOLD_V,
    CRYO_SUPPLY_MAX_A,
    CRYO_SUPPLY_MAX_V,
    ccvres_ohms_from_code,
    cryo_supply,
    supply_settings,
)
from formslab.devices.dp832a.commands import read_psu_channel_status


name = os.path.splitext(os.path.basename(__file__))[0]

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("cryo",)
RESULT_LABELS = ("cryo",)   # each request is answered: done, or refused with why
SHUTDOWN_BEFORE = ("rPSU",)  # the board lets go before its supply's owner does
READY_WAIT_S = 30.0         # how long a request waits for the supply and the board's link
STATUS_INTERVAL = 5.0       # seconds between reads of the board for the published values

def _wired_to():
    """(supply, channel) the hardware map says the board is wired to."""
    return cryo_supply()


def _supply(psu, ch, volts, amps, ovp, ocp):
    """`cryo supply`: the board's supply channel, as the hardware map has it, and its
    settings, the protection at or above them."""
    from formslab.rscripts.grammar import GrammarError

    channel = int(ch[2:])
    label, wired = _wired_to()
    if (psu, channel) != (label, wired):
        raise GrammarError(f"this powers the board from {psu} ch{channel}, but the hardware map says the "
                           f"cryocooler board is wired to {label} ch{wired} (usbmap.json, {label} channels)")
    if ovp < volts:
        raise GrammarError(f"the over-voltage protection, {ovp:g} V, is below the supply's {volts:g} V")
    if ocp < amps:
        raise GrammarError(f"the over-current protection, {ocp:g} A, is below the supply's {amps:g} A")
    return {"supply": {"psu": psu, "channel": channel, "volts": volts, "amps": amps, "ovp": ovp, "ocp": ocp}}


_V, _A = f"{CRYO_OUTPUT_SUPPLY_THRESHOLD_V:g}..{CRYO_SUPPLY_MAX_V:g} V", f"0.1..{CRYO_SUPPLY_MAX_A:g} A"

COMMANDS = [
    (f"supply <supply:psu1|psu2> <channel:ch1|ch2|ch3> at <volts:number {_V}> V <amps:number {_A}> A "
     f"protect <ovp:number {_V}> V <ocp:number {_A}> A",
     f"""Set the board's supply and its protection
     The supply channel the board is wired to, as the hardware map has it: a channel
     the map does not give the cryocooler board is refused, so a block cannot power
     the wrong one. Then the voltage, {CRYO_OUTPUT_SUPPLY_THRESHOLD_V:g} to {CRYO_SUPPLY_MAX_V:g} V, and current limit, at most
     {CRYO_SUPPLY_MAX_A:g} A, then the over-voltage and over-current protection, each at or above
     them. The cooler cannot run from less than {CRYO_OUTPUT_SUPPLY_THRESHOLD_V:g} V in. Sent to the
     channel at once unless the board is shut down, and at each cryo startup.""", _supply),
    (f"ccv <volts:number {CCV_MIN_V:g}..{CCV_MAX_V:g} V>", f"""Set the cryocooler voltage
     The board's output to the cooler, {CCV_MIN_V:g} to {CCV_MAX_V:g} V: the K508N's input
     range. It is applied while the output is on.""", lambda v: {"voltage": v}),
    ("on|off", """Turn the cryocooler output on or off
     On needs the board's supply at 20 V or more: below that the converter cannot
     produce an output. This is checked before the command is sent.""",
     lambda s: {"enabled": s == "on"}),
    ("ccvres <ohms:number 62..1120>", """Set the variable resistor in ohms
     The board's variable resistor, 62 to 1120 ohms, set to the nearest of its 64
     steps; it sets the K508N's fixed-point temperature control. Which resistance
     gives which temperature has not been measured. cryo code sets the step itself.
     The output is dropped while the resistor moves and restored after.""",
     lambda r: {"resistance": r}),
    ("code <step:integer 0..63>", """Set the variable resistor step
     The resistor's step, 0 to 63.""", lambda n: {"code": n}),
    ("startup|shutdown|update", """Start, stop or read the board
     startup turns its supply channel on and initializes the board with the output
     off. shutdown turns the output off, then the supply channel. update reads the
     board now.""",
     lambda w: {w: True}),
]
VARIABLES = [("CRYO_LINK", "bool"), ("CRYO_ON", "bool"), ("CRYO_OK", "bool"),
             ("CRYO_CCV", "V"), ("CRYO_RES", "ohm"), ("CRYO_SUPPLY_V", "V")]


def READINGS(label, status):
    return [
        ("Board", None, [("ERR", "Problem"), ("LINK", "Board link", ("Up", "Down")), ("ON", "Output", ("On", "Off")),
                         ("OK", "Output on, no fault", ("Yes", "No")), ("MODE", "Converter mode"),
                         ("FAULTS", "Faults"), ("LASTFAULT", "Last fault"),
                         ("CCV", "Cooler voltage (V)"), ("CCVRES", "Variable resistor (ohm)"),
                         ("CCVRES#", "Resistor step")]),
        ("Supply", None, [("PSU", "Supply channel"), ("PSUON", "Supply output", ("On", "Off")),
                          ("CCVIN", "Set (V)"), ("CCIIN", "Limit (A)"), ("CCVINM", "Measured (V)"),
                          ("CCIINM", "Measured (A)")]),
    ]


def RULES():
    from formslab.devices.cryocooler.config import CRYO_OUTPUT_SUPPLY_THRESHOLD_V as V
    from formslab.rscripts.rules import Rule, value

    return [Rule("cryo", {"enabled": True},
                 (value("supplyV", "above", V, shown="V", live=True, called="Board supply"),),
                 f"The board's converter cannot produce an output with less than about {V:g} V in.")]


def RULE_STATE(status):
    return {"supplyV": status.get("CCVINM")}


class rGlobal:
    TICK_INTERVAL = 1.0

    cryo = None
    _init_attempted = False
    _psu2_ready = False
    _psu2_request_pending = False
    _psu2_status_unknown_reported = False
    _pending_request = None
    _pending_ids = []           # the senders of the request being applied, to answer
    _pending_since = None       # when it arrived (time.monotonic)
    _shutdown_latch = False
    _next_status = 0.0          # when the board is next read for the published values
    _last_fault = None          # the last fault the converter reported, and when (it clears them on read)
    _was_on = False             # the converter's output was on at the last read


rg = rGlobal


def _merge_request(existing, incoming):
    if not existing:
        return dict(incoming)
    merged = dict(existing)
    merged.update(incoming)
    return merged

def _refresh_status(run, r_global):
    CRYO_PSU_LABEL, CRYO_PSU_CHANNEL = cryo_supply(r_global)
    supply = supply_settings(r_global)
    status = {
        "LINK": r_global.cryo is not None,
        "ON": False,
        "PSU": f"{CRYO_PSU_LABEL.upper()} CH{CRYO_PSU_CHANNEL}",
        "PSUON": False,
        "CCVIN": supply["volts"],
        "CCIIN": supply["amps"],
        "CCVINM": None,
        "CCIINM": None,
        "CCV": None,
        "CCVRES": None,
        "CCVRES#": None,
        "OK": None,
        "MODE": None,
        "FAULTS": None,
        "LASTFAULT": getattr(r_global, "_last_fault", None),
        "ERR": getattr(r_global, "_init_error", None),
    }

    try:
        ch2 = read_psu_channel_status(CRYO_PSU_LABEL, CRYO_PSU_CHANNEL)
        status.update(
            {
                "PSUON": ch2.get("on", False),
                "CCVIN": ch2.get("vset", supply["volts"]),
                "CCIIN": ch2.get("cset", supply["amps"]),
                "CCVINM": ch2.get("vmeas"),
                "CCIINM": ch2.get("cmeas"),
            }
        )
    except Exception as exc:
        status["ERR"] = str(exc)

    if r_global.cryo is not None:
        try:
            board = r_global.cryo.status()
            status.update(
                {
                    "LINK": True,
                    "ON": board.get("enabled", False),
                    "CCV": board.get("output_voltage_v"),
                    "CCVRES": board.get("resistance_ohms"),
                    "CCVRES#": board.get("resistance_code"),
                    "OK": board.get("output_healthy"),
                    "MODE": board.get("conversion"),
                    "FAULTS": ", ".join(board.get("faults") or []) or "none",
                }
            )
        except Exception as exc:
            run.log(
                f"Cryocooler status read failed: {exc}",
                level="WARNING",
                component="CRYO",
            )
            status["LINK"] = False
            status["ERR"] = str(exc)

    _watch_output(run, r_global, status)
    UpdateStatus("cryo", status)
    _publish(run, status)
    r_global._next_status = time.monotonic() + STATUS_INTERVAL


def _watch_output(run, r_global, status):
    """Keep the last fault the converter reported, since reading clears it, and
    say when its output went off without being told to."""
    faults = status.get("FAULTS")
    if faults and faults != "none":
        r_global._last_fault = f"{faults} at {time.strftime('%H:%M:%S')}"
        status["LASTFAULT"] = r_global._last_fault
        run.log(f"converter fault: {faults}", level="WARNING", component="CRYO")
    if r_global._was_on and status.get("ON") and status.get("OK") is False:
        run.log("the converter's output went off without cryo off "
                f"({'last fault ' + r_global._last_fault if r_global._last_fault else 'no fault seen'}): "
                "cryo on turns it on again", level="WARNING", component="CRYO")
    r_global._was_on = bool(status.get("OK"))


def _publish(run, status):
    """The board's state as the run's values, for plans to wait on."""
    for name, key in (("CRYO_LINK", "LINK"), ("CRYO_ON", "ON"), ("CRYO_OK", "OK")):
        run.publish(name, 1 if status.get(key) else 0, "bool")
    for name, key, unit in (("CRYO_CCV", "CCV", "V"), ("CRYO_RES", "CCVRES", "ohm"),
                            ("CRYO_SUPPLY_V", "CCVINM", "V")):
        if status.get(key) is not None:
            run.publish(name, float(status[key]), unit)


def _not_ready(r_global):
    """Why the board cannot take a request yet."""
    label, channel = cryo_supply(r_global)
    supply = supply_settings(r_global)
    if not getattr(r_global, "_psu2_ready", False):
        return (f"its supply, {label} CH{channel}, is not at {supply['volts']:g} V "
                f"{supply['amps']:g} A (is rPSU loaded?)")
    return "the board did not start: " + (getattr(r_global, "_init_error", None)
                                         or "it did not answer through the Pico (is it connected?)")


def _ensure_initialized(run, r_global):
    _init_psu2(run, r_global)
    if not getattr(r_global, "_psu2_ready", False):
        return False
    _init_cryo_board(run, r_global)
    return r_global.cryo is not None


def _apply_request(run, r_global, request):
    """Apply a request: (done, ok, messages). Not done while the supply or the
    board's link is still coming up; the caller waits, within READY_WAIT_S."""
    if not request:
        return True, True, []

    said = []
    supply = request.get("supply")
    if supply is not None:                       # cryo supply: the settings, sent at once
        label, wired = cryo_supply(r_global)
        if (supply.get("psu"), supply.get("channel")) != (label, wired):
            return True, False, [f"the board is wired to {label} ch{wired}, not "
                                 f"{supply.get('psu')} ch{supply.get('channel')} (usbmap.json)"]
        if supply != getattr(r_global, "supply_set", None):
            r_global.supply_set = dict(supply)
            r_global._supply_changed = True
        if r_global._shutdown_latch:
            said.append("supply settings kept for the next cryo startup")
        else:
            _init_psu2(run, r_global)
            if not getattr(r_global, "_psu2_ready", False):
                return False, False, [_not_ready(r_global)]
            said.append(f"supply {supply['volts']:g} V {supply['amps']:g} A, protected at "
                        f"{supply['ovp']:g} V {supply['ocp']:g} A")

    update = request.get("update")
    startup = request.get("startup")
    shutdown = request.get("shutdown")
    voltage = request.get("voltage", request.get("CCVOUT", request.get("CCV")))
    resistance = request.get("resistance", request.get("CCVRES"))
    code = request.get("code", request.get("resistance_code", request.get("CCVRES#")))
    enabled = request.get("enabled", request.get("CCOUT"))

    if code is not None and resistance is None:
        try:
            resistance = ccvres_ohms_from_code(int(code))
        except Exception:
            resistance = None

    for field_name, value in (
        ("update", update),
        ("startup", startup),
        ("shutdown", shutdown),
    ):
        if value is not None and not isinstance(value, bool):
            run.log(
                f"Invalid cryo {field_name} request: {value}",
                level="WARNING",
                component="CRYO",
            )
            return True, False, [f"{field_name}: expected true or false, got {value!r}"]

    if enabled is not None and not isinstance(enabled, bool):
        run.log(
            f"Invalid cryo enabled request: {enabled}",
            level="WARNING",
            component="CRYO",
        )
        return True, False, [f"enabled: expected true or false, got {enabled!r}"]

    if shutdown:
        r_global._shutdown_latch = True
        _shutdown_cryo_subsystem(run, r_global, close_transport=False, release_handles=False)
        _refresh_status(run, r_global)
        return True, True, ["cooler output off, then its supply"]

    if r_global._shutdown_latch and not startup:
        blocked = []
        if update:
            blocked.append("update")
        if voltage is not None:
            blocked.append("voltage")
        if resistance is not None:
            blocked.append("resistance")
        if enabled is not None:
            blocked.append("enabled")

        if blocked:
            run.log(
                "Cryocooler is shutdown-latched; ignoring "
                + ", ".join(blocked)
                + " request until 'startup cryo' is issued",
                level="WARNING",
                component="CRYO",
            )
            _refresh_status(run, r_global)
            return True, False, [f"the board is shut down: send cryo startup before {', '.join(blocked)}"]

    needs_init = (
        startup
        or update
        or voltage is not None
        or resistance is not None
        or enabled is not None
    )
    if needs_init:
        if not _ensure_initialized(run, r_global):
            _refresh_status(run, r_global)
            if getattr(r_global, "_init_failures", 0) >= 2:      # not a slow start: it failed, twice
                return True, False, [_not_ready(r_global)]
            return False, False, [_not_ready(r_global)]
        if startup:
            r_global._shutdown_latch = False
            run.log("Cryocooler subsystem initialized", component="CRYO")
    said += ["board ready"] if startup else []

    if (
        update
        or voltage is not None
        or resistance is not None
        or enabled is not None
    ):
        if voltage is not None:
            voltage = float(voltage)
        if resistance is not None:
            resistance = float(resistance)

        if voltage is not None or resistance is not None or enabled is not None:
            try:
                r_global.cryo.update(
                    voltage=voltage,
                    resistance=resistance,
                    enabled=enabled,
                )

                parts = []
                if voltage is not None:
                    parts.append(f"voltage={voltage:.2f} V")
                if resistance is not None:
                    parts.append(f"resistance={resistance:.1f} ohm")
                if enabled is not None:
                    parts.append("enabled" if enabled else "disabled")
                run.log(f"Cryocooler updated: {', '.join(parts)}", component="CRYO")
                said.append("cryocooler " + ", ".join(parts))
            except Exception as exc:
                run.log(f"Failed to update cryocooler: {exc}", level="ERROR", component="CRYO")
                _refresh_status(run, r_global)
                return True, False, [f"the board refused it: {exc}"]

    _refresh_status(run, r_global)
    return True, True, said or ["board read"]


def rScript(run):
    global rg


    try:
        control = RScriptControl(run, name)
        control.tick(seconds=rg.TICK_INTERVAL)
        if control:
            return
    except Exception as exc:
        run.log(f"RScriptControl exception: {exc}", level="ERROR", component=name)
        return

    if not rg._init_attempted:
        rg._init_attempted = True
    ending = getattr(run, "ending", False)       # the run is ending: never bring the board back up
    if not ending and not rg._shutdown_latch and (not rg._psu2_ready or rg.cryo is None):
        _ensure_initialized(run, rg)
        _refresh_status(run, rg)

    request, ids = TakeCommand("cryo")
    if request:
        rg._pending_request = _merge_request(rg._pending_request, request)
        rg._pending_ids = list(rg._pending_ids) + list(ids)
        rg._pending_since = rg._pending_since or time.monotonic()

    if rg._pending_request:
        done, ok, messages = _apply_request(run, rg, rg._pending_request)
        if not done and time.monotonic() - rg._pending_since >= READY_WAIT_S:
            done, ok, messages = True, False, [f"not done within {READY_WAIT_S:g} s: {messages[0]}"]
        if done:
            ReportResult("cryo", rg._pending_ids, ok, messages)
            if not ok:
                run.log(f"cryo request refused: {'; '.join(messages)}", level="WARNING", component="CRYO")
            rg._pending_request, rg._pending_ids, rg._pending_since = None, [], None
    elif time.monotonic() >= rg._next_status:
        _refresh_status(run, rg)


def rShutdown(run):
    """Release CryoBoard hardware when the host stops."""
    global rg
    _shutdown_cryo_subsystem(run, rg, close_transport=True, release_handles=True)
    if rg._pending_request:
        ReportResult("cryo", rg._pending_ids, False, ["the run ended first"])
    rg._pending_request, rg._pending_ids, rg._pending_since = None, [], None
    _refresh_status(run, rg)

