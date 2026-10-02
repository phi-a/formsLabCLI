import os

from formslab.console.cast.castutils import ReadCommand, UpdateStatus
from formslab.rscripts import RScriptControl
from formslab.devices.cryoutils import _init_cryo_board, _init_psu2, _shutdown_cryo_subsystem
from formslab.devices.cryo_config import (
    CRYO_PSU_LABEL,
    CRYO_PSU_CHANNEL,
    CRYO_SUPPLY_CURRENT_A,
    CRYO_SUPPLY_VOLTAGE_V,
    ccvres_ohms_from_code,
)
from formslab.devices.psu_command_utils import read_psu_channel_status


from formslab.rscripts.cast import CastUsage, choice, integer, number

name = os.path.splitext(os.path.basename(__file__))[0]

# --- console commands (see formslab.rscripts.cast) ---------------------------------

CAST_LABELS = ("cryo",)
CAST_HELP = [
    ("cryo ccv <V>", "Cryocooler output voltage, 12-20 V"),
    ("cryo on|off", "Cryocooler output"),
    ("cryo ccvres <ohm>", "Variable resistor, 62-1120 ohm (nearest 6-bit code)"),
    ("cryo code <0-63>", "Variable resistor by code"),
    ("cryo startup|shutdown|update", "Bring the board up / down, or read it now"),
]


def cast_request(label, words):
    usage = "cryo ccv <V> | on | off | ccvres <ohm> | code <n> | startup | shutdown | update"
    if len(words) == 1 and words[0].lower() in ("startup", "shutdown", "update"):
        return {words[0].lower(): True}
    if len(words) == 1 and words[0].lower() in ("on", "off"):
        return {"enabled": choice(words[0], ("on", "off"), "cryo")}
    if len(words) == 2:
        verb = words[0].lower()
        if verb == "ccv":
            return {"voltage": number(words[1], "cryo voltage V", 12, 20)}
        if verb == "ccvres":
            return {"resistance": number(words[1], "CCVRES ohm", 62, 1120)}
        if verb == "code":
            return {"code": integer(words[1], "CCVRES code", 0, 63)}
    raise CastUsage(usage)


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

def _refresh_status(forms, r_global):
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
            forms.log(
                f"Cryocooler status read failed: {exc}",
                level="WARNING",
                component="CRYO",
            )
            status["LINK"] = False
            status["ERR"] = str(exc)

    UpdateStatus("cryo", status)


def _ensure_initialized(forms, r_global):
    _init_psu2(forms, r_global)
    if not getattr(r_global, "_psu2_ready", False):
        return False
    _init_cryo_board(forms, r_global)
    return r_global.cryo is not None


def _apply_request(forms, r_global, request):
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
            forms.log(
                f"Invalid cryo {field_name} request: {value}",
                level="WARNING",
                component="CRYO",
            )
            return True

    if enabled is not None and not isinstance(enabled, bool):
        forms.log(
            f"Invalid cryo enabled request: {enabled}",
            level="WARNING",
            component="CRYO",
        )
        return True

    if shutdown:
        r_global._shutdown_latch = True
        _shutdown_cryo_subsystem(forms, r_global, close_transport=False, release_handles=False)
        _refresh_status(forms, r_global)
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
            forms.log(
                "Cryocooler is shutdown-latched; ignoring "
                + ", ".join(blocked)
                + " request until 'startup cryo' is issued",
                level="WARNING",
                component="CRYO",
            )
            _refresh_status(forms, r_global)
            return True

    needs_init = (
        startup
        or update
        or voltage is not None
        or resistance is not None
        or enabled is not None
    )
    if needs_init:
        if not _ensure_initialized(forms, r_global):
            _refresh_status(forms, r_global)
            return False
        if startup:
            r_global._shutdown_latch = False
            forms.log("Cryocooler subsystem initialized", component="CRYO")

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
                forms.log(f"Cryocooler updated: {', '.join(parts)}", component="CRYO")
            except Exception as exc:
                forms.log(f"Failed to update cryocooler: {exc}", level="ERROR", component="CRYO")

    _refresh_status(forms, r_global)
    return True


def rScript(forms):
    global rg

    if rg.disable:
        return

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

    if not rg._init_attempted:
        rg._init_attempted = True
    if not rg._shutdown_latch and (not rg._psu2_ready or rg.cryo is None):
        _ensure_initialized(forms, rg)
        _refresh_status(forms, rg)

    request = ReadCommand("cryo")
    if request:
        rg._pending_request = _merge_request(rg._pending_request, request)

    if rg._pending_request and _apply_request(forms, rg, rg._pending_request):
        rg._pending_request = None


def rShutdown(forms):
    """Release CryoBoard hardware when the host stops."""
    global rg
    _shutdown_cryo_subsystem(forms, rg, close_transport=True, release_handles=True)
    rg._pending_request = None
    _refresh_status(forms, rg)

