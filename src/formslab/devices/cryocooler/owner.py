import time
import traceback

from formslab.devices.cryocooler.config import (
    CRYO_PSU_LABEL,
    CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
    CRYO_DEFAULT_RESISTANCE_OHMS,
    CRYO_PSU_CHANNEL,
    CRYO_PSU_COMPONENT,
    CRYO_SUPPLY_CURRENT_A,
    CRYO_SUPPLY_OCP_A,
    CRYO_SUPPLY_OVP_V,
    CRYO_SUPPLY_VOLTAGE_V,
)
from formslab.devices.dp832a.commands import (
    build_psu_channel_request,
    psu_channel_state,
    queue_psu_request,
)


def _init_psu2(forms, r_global):
    readiness = psu_channel_state(
        CRYO_PSU_LABEL,
        CRYO_PSU_CHANNEL,
        on=True,
        voltage=CRYO_SUPPLY_VOLTAGE_V,
        current=CRYO_SUPPLY_CURRENT_A,
    )

    if readiness == "match":
        if not getattr(r_global, "_psu2_ready", False):
            forms.log(
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
            forms.log(
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
    forms.log(f"Configuring CH{CRYO_PSU_CHANNEL} for cryocooler board input...", level="INFO", component=CRYO_PSU_COMPONENT)
    queue_psu_request(
        CRYO_PSU_LABEL,
        build_psu_channel_request(
            CRYO_PSU_CHANNEL,
            ovp=CRYO_SUPPLY_OVP_V,
            ocp=CRYO_SUPPLY_OCP_A,
            protect=True,
            voltage=CRYO_SUPPLY_VOLTAGE_V,
            current=CRYO_SUPPLY_CURRENT_A,
            on=True,
        ),
        update=True,
    )
    r_global._psu2_request_pending = True
    r_global._psu2_ready = False
    return r_global


def _init_cryo_board(forms, r_global):
    if r_global.cryo is not None:
        return r_global
    try:
        try:
            from formslab.devices.cryocooler.board import CryoBoard
        except ModuleNotFoundError as exc:
            forms.log(
                f"Cryocooler board support unavailable: {exc}. "
                "Install the 'lab' extra to enable board control.",
                level="WARNING",
                component="CRYO",
            )
            r_global.cryo = None
            return r_global
        forms.log("Initializing...", level="INFO", component="CRYO")
        time.sleep(1.0)
        r_global.cryo = CryoBoard("cryo_board")
        r_global.cryo.initialize(
            voltage=CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
            resistance=CRYO_DEFAULT_RESISTANCE_OHMS,
            enabled=False,
        )
        forms.log(
            f"Cryocooler board initialized at "
            f"{CRYO_DEFAULT_OUTPUT_VOLTAGE_V:.1f}V / "
            f"{CRYO_DEFAULT_RESISTANCE_OHMS:.0f} ohm (output OFF)",
            level="INFO",
            component="CRYO",
        )
    except Exception as exc:
        tb = traceback.format_exc()
        forms.log(f"Cryocooler board initialization failed: {exc}\n{tb}", level="ERROR", component="CRYO")
        r_global.cryo = None
    return r_global


def _shutdown_cryo_subsystem(forms, r_global, *, close_transport=True, release_handles=True):
    """
    Safely disable the CryoBoard output, power feed, and serial transport.

    This is used by `rCryoBoard` during routine shutdown so the USB-I2C bridge
    is not left busy across simulation runs.
    """
    if r_global.cryo is not None:
        try:
            r_global.cryo.shutdown(close_transport=close_transport)
            forms.log("CryoBoard output disabled", level="INFO", component="CRYO")
        except Exception as exc:
            tb = traceback.format_exc()
            forms.log(f"CryoBoard shutdown failed: {exc}\n{tb}", level="WARNING", component="CRYO")

    queue_psu_request(
        CRYO_PSU_LABEL,
        build_psu_channel_request(CRYO_PSU_CHANNEL, on=False),
        update=True,
    )
    r_global._psu2_ready = False
    r_global._psu2_request_pending = False
    forms.log(
        f"Queued {CRYO_PSU_COMPONENT} CH{CRYO_PSU_CHANNEL} disable for cryocooler board",
        level="INFO",
        component=CRYO_PSU_COMPONENT,
    )

    if release_handles:
        r_global.cryo = None
        r_global._init_attempted = False
        r_global._psu2_ready = False
        r_global._psu2_request_pending = False

    return r_global
