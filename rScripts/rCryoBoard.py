import os

from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.rscripts import RScriptControl
from formslab.devices.cryocooler.owner import _init_cryo_board, _init_psu2, _shutdown_cryo_subsystem
from formslab.devices.cryocooler.config import (
    CRYO_SUPPLY_CURRENT_A,
    CRYO_SUPPLY_VOLTAGE_V,
    ccvres_ohms_from_code,
    cryo_supply,
)
from formslab.devices.dp832a.commands import read_psu_channel_status


name = os.path.splitext(os.path.basename(__file__))[0]

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("cryo",)
COMMANDS = [
    ("ccv <V:number 12..20 V>", """Cryocooler output voltage
     The board's converter output to the cooler, 12 to 20 V (the band formsLabCLI
     drives it in). Applied while the output is on.""", lambda v: {"voltage": v}),
    ("on|off", """Cryocooler output
     on needs the board's supply at 20 V or more: below that the converter cannot
     produce an output (checked before it is sent).""", lambda s: {"enabled": s == "on"}),
    ("ccvres <ohm:number 62..1120>", """Variable resistor (nearest 6-bit code)
     The board's variable resistor in ohms, set to the nearest of its 64 steps
     (`cryo code` sets the step itself).""", lambda r: {"resistance": r}),
    ("code <n:integer 0..63>", """Variable resistor by code
     The resistor's step, 0 to 63.""", lambda n: {"code": n}),
    ("startup|shutdown|update", """Bring the board up / down, or read it now
     startup: its supply channel on, the board initialized with the output off.
     shutdown: output off, then the supply channel off. update: read the board now.""",
     lambda w: {w: True}),
]
VARIABLES = []


def RULES():
    from formslab.devices.cryocooler.config import CRYO_OUTPUT_SUPPLY_THRESHOLD_V as V
    from formslab.rscripts.rules import Rule, value

    return [Rule("cryo", {"enabled": True}, (value("supplyV", "above", V, shown="V", live=True),),
                 f"the board's converter cannot produce an output below about {V:g} V in")]


def RULE_STATE(status):
    return {"supplyV": status.get("CCVINM")}


class rGlobal:
    disable = False
    useInitialize = False
    useHold = False
    useTick = True
    TICK_INTERVAL = 1.0

    cryo = None
    _init_attempted = False
    _psu2_ready = False
    _psu2_request_pending = False
    _psu2_status_unknown_reported = False
    _pending_request = None
    _shutdown_latch = False


rg = rGlobal


def _merge_request(existing, incoming):
    if not existing:
        return dict(incoming)
    merged = dict(existing)
    merged.update(incoming)
    return merged

def _refresh_status(run, r_global):
    CRYO_PSU_LABEL, CRYO_PSU_CHANNEL = cryo_supply(r_global)
    status = {
        "LINK": r_global.cryo is not None,
        "ON": False,
        "PSU": f"{CRYO_PSU_LABEL.upper()} CH{CRYO_PSU_CHANNEL}",
        "PSUON": False,
        "CCVIN": CRYO_SUPPLY_VOLTAGE_V,
        "CCIIN": CRYO_SUPPLY_CURRENT_A,
        "CCVINM": None,
        "CCIINM": None,
        "CCV": None,
        "CCVRES": None,
        "CCVRES#": None,
    }

    try:
        ch2 = read_psu_channel_status(CRYO_PSU_LABEL, CRYO_PSU_CHANNEL)
        status.update(
            {
                "PSUON": ch2.get("on", False),
                "CCVIN": ch2.get("vset", CRYO_SUPPLY_VOLTAGE_V),
                "CCIIN": ch2.get("cset", CRYO_SUPPLY_CURRENT_A),
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

    UpdateStatus("cryo", status)


def _ensure_initialized(run, r_global):
    _init_psu2(run, r_global)
    if not getattr(r_global, "_psu2_ready", False):
        return False
    _init_cryo_board(run, r_global)
    return r_global.cryo is not None


def _apply_request(run, r_global, request):
    if not request:
        return True

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
            return True

    if enabled is not None and not isinstance(enabled, bool):
        run.log(
            f"Invalid cryo enabled request: {enabled}",
            level="WARNING",
            component="CRYO",
        )
        return True

    if shutdown:
        r_global._shutdown_latch = True
        _shutdown_cryo_subsystem(run, r_global, close_transport=False, release_handles=False)
        _refresh_status(run, r_global)
        return True

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
            return True

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
            return False
        if startup:
            r_global._shutdown_latch = False
            run.log("Cryocooler subsystem initialized", component="CRYO")

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
            except Exception as exc:
                run.log(f"Failed to update cryocooler: {exc}", level="ERROR", component="CRYO")

    _refresh_status(run, r_global)
    return True


def rScript(run):
    global rg

    if rg.disable:
        return

    try:
        control = RScriptControl(run, name)
        if rg.useInitialize:
            control.initialize()
        if rg.useHold:
            control.hold(seconds=1.0)
        if rg.useTick:
            control.tick(seconds=rg.TICK_INTERVAL)
        if control:
            return
    except Exception as exc:
        run.log(f"RScriptControl exception: {exc}", level="ERROR", component=name)
        return

    if not rg._init_attempted:
        rg._init_attempted = True
    if not rg._shutdown_latch and (not rg._psu2_ready or rg.cryo is None):
        _ensure_initialized(run, rg)
        _refresh_status(run, rg)

    request = ReadCommand("cryo")
    if request:
        rg._pending_request = _merge_request(rg._pending_request, request)

    if rg._pending_request and _apply_request(run, rg, rg._pending_request):
        rg._pending_request = None


def rShutdown(run):
    """Release CryoBoard hardware when the host stops."""
    global rg
    _shutdown_cryo_subsystem(run, rg, close_transport=True, release_handles=True)
    rg._pending_request = None
    _refresh_status(run, rg)

