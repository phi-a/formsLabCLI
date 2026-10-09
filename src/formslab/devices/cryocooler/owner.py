import time
import traceback

from formslab.devices.cryocooler.config import (
    CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
    CRYO_DEFAULT_RESISTANCE_OHMS,
    cryo_supply,
    supply_settings,
)
from formslab.devices.dp832a.commands import (
    build_psu_channel_request,
    psu_channel_state,
    queue_psu_request,
    read_psu_channel_status,
)


def _init_psu2(run, r_global):
    """Bring the board's supply to its settings (supply_settings): ready once rPSU
    reports the channel on at them. `_supply_changed` (cryo supply) sends them
    again even when the channel already matches, for the protection limits."""
    CRYO_PSU_LABEL, CRYO_PSU_CHANNEL = cryo_supply(r_global)
    CRYO_PSU_COMPONENT = CRYO_PSU_LABEL.upper()
    supply = supply_settings(r_global)
    readiness = psu_channel_state(
        CRYO_PSU_LABEL,
        CRYO_PSU_CHANNEL,
        on=True,
        voltage=supply["volts"],
        current=supply["amps"],
    )
    if getattr(r_global, "_supply_changed", False):
        readiness = "mismatch"
        r_global._supply_changed = False
        r_global._psu2_request_pending = False

    if readiness == "match":
        if not getattr(r_global, "_psu2_ready", False):
            run.log(
                f"{CRYO_PSU_COMPONENT} CH{CRYO_PSU_CHANNEL} supply ready for cryocooler board",
                level="INFO",
                component=CRYO_PSU_COMPONENT,
            )
        r_global._psu2_ready = True
        r_global._psu2_request_pending = False
        r_global._psu2_status_unknown_reported = False
        return r_global

    if readiness == "unknown":
        if not getattr(r_global, "_psu2_status_unknown_reported", False):
            run.log(
                f"{CRYO_PSU_COMPONENT} CH{CRYO_PSU_CHANNEL} telemetry is unavailable; preserving last-known cryocooler supply state",
                level="WARNING",
                component=CRYO_PSU_COMPONENT,
            )
            r_global._psu2_status_unknown_reported = True
        return r_global

    if getattr(r_global, "_psu2_request_pending", False):
        r_global._psu2_ready = False
        return r_global

    r_global._psu2_status_unknown_reported = False
    run.log(f"Configuring CH{CRYO_PSU_CHANNEL} for cryocooler board input...", level="INFO", component=CRYO_PSU_COMPONENT)
    queue_psu_request(
        CRYO_PSU_LABEL,
        build_psu_channel_request(
            CRYO_PSU_CHANNEL,
            ovp=supply["ovp"],
            ocp=supply["ocp"],
            protect=True,
            voltage=supply["volts"],
            current=supply["amps"],
            on=True,
        ),
        update=True,
    )
    r_global._psu2_request_pending = True
    r_global._psu2_ready = False
    return r_global


INIT_RETRY_S = 10.0     # seconds between attempts to bring up a board that failed


def diagnose(exc, supply: dict) -> str:
    """Why the board did not come up, in words, with what its supply reads."""
    text = str(exc)
    if "ETIMEDOUT" in text or "Errno 110" in text:
        why = ("the I2C clock line stayed low (ETIMEDOUT): the board side has no power, "
               "or its pull-ups are not reaching the Pico")
    elif "ENODEV" in text or "Errno 19" in text or "EIO" in text or "Errno 5" in text:
        why = "nothing answered on the I2C bus (ENODEV): the board's chips are unpowered or not connected"
    elif "could not open port" in text or "FileNotFoundError" in text or "PermissionError" in text:
        why = "the Pico's serial port did not open: is it plugged in, and its port right in usbmap.json?"
    else:
        last = next((ln for ln in reversed(text.strip().splitlines()) if ln.strip()), type(exc).__name__)
        why = f"{type(exc).__name__}: {last.strip()}"
    volts, amps = supply.get("vmeas"), supply.get("cmeas")
    if volts is not None and amps is not None:
        why += f"; its supply reads {float(volts):.1f} V, {float(amps) * 1000:.0f} mA"
        if float(volts) > 15 and float(amps) < 0.010:
            why += (", too little current for a powered board: check the cable from the "
                    "supply to the board, and the board's input")
    return why


def _init_cryo_board(run, r_global):
    if r_global.cryo is not None:
        return r_global
    if time.monotonic() < getattr(r_global, "_init_retry_at", 0.0):
        return r_global
    try:
        try:
            from formslab.devices.cryocooler.board import CryoBoard
        except ModuleNotFoundError as exc:
            run.log(
                f"Cryocooler board support unavailable: {exc}. "
                "Install the 'lab' extra to enable board control.",
                level="WARNING",
                component="CRYO",
            )
            r_global.cryo = None
            return r_global
        run.log("Initializing...", level="INFO", component="CRYO")
        time.sleep(1.0)
        r_global.cryo = CryoBoard("cryo_board")
        r_global.cryo.initialize(
            voltage=CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
            resistance=CRYO_DEFAULT_RESISTANCE_OHMS,
            enabled=False,
        )
        run.log(
            f"Cryocooler board initialized at "
            f"{CRYO_DEFAULT_OUTPUT_VOLTAGE_V:.1f}V / "
            f"{CRYO_DEFAULT_RESISTANCE_OHMS:.0f} ohm (output OFF)",
            level="INFO",
            component="CRYO",
        )
        r_global._init_error, r_global._init_failures = None, 0
    except Exception as exc:
        label, channel = cryo_supply(r_global)
        why = diagnose(exc, read_psu_channel_status(label, channel))
        failures = getattr(r_global, "_init_failures", 0) + 1
        if failures == 1:                        # the whole story once; then one line each time
            run.log(f"Cryocooler board did not start: {why}\n{traceback.format_exc()}",
                    level="ERROR", component="CRYO")
        else:
            run.log(f"Cryocooler board did not start (try {failures}): {why}", level="ERROR", component="CRYO")
        if r_global.cryo is not None:            # release the Pico, so the next try can open it
            try:
                r_global.cryo.close()
            except Exception:
                pass
        r_global.cryo = None
        r_global._init_error, r_global._init_failures = why, failures
        r_global._init_retry_at = time.monotonic() + INIT_RETRY_S
    return r_global


def _shutdown_cryo_subsystem(run, r_global, *, close_transport=True, release_handles=True):
    """
    Safely disable the CryoBoard output, power feed, and serial transport.

    This is used by `rCryoBoard` during routine shutdown so the USB-I2C bridge
    is not left busy across simulation runs.
    """
    CRYO_PSU_LABEL, CRYO_PSU_CHANNEL = cryo_supply(r_global)
    CRYO_PSU_COMPONENT = CRYO_PSU_LABEL.upper()
    # A board whose supply is already off (the end script cut it) cannot answer:
    # only the link is released, and no request goes to a supply already off.
    powered = read_psu_channel_status(CRYO_PSU_LABEL, CRYO_PSU_CHANNEL).get("on") is not False
    if r_global.cryo is not None:
        try:
            if powered:
                r_global.cryo.shutdown(close_transport=close_transport)
                run.log("CryoBoard output disabled", level="INFO", component="CRYO")
            else:
                if close_transport:
                    r_global.cryo.close()
                run.log("CryoBoard already unpowered: link released", level="INFO", component="CRYO")
        except Exception as exc:
            tb = traceback.format_exc()
            run.log(f"CryoBoard shutdown failed: {exc}\n{tb}", level="WARNING", component="CRYO")

    if powered:
        queue_psu_request(
            CRYO_PSU_LABEL,
            build_psu_channel_request(CRYO_PSU_CHANNEL, on=False),
            update=True,
        )
        run.log(
            f"Queued {CRYO_PSU_COMPONENT} CH{CRYO_PSU_CHANNEL} disable for cryocooler board",
            level="INFO",
            component=CRYO_PSU_COMPONENT,
        )
    r_global._psu2_ready = False
    r_global._psu2_request_pending = False

    if release_handles:
        r_global.cryo = None
        r_global._init_attempted = False
        r_global._psu2_ready = False
        r_global._psu2_request_pending = False
        r_global._supply = None             # the next start reads the map again

    return r_global
