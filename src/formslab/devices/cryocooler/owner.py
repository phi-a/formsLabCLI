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


def _init_cryo_board(run, r_global):
    if r_global.cryo is not None:
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
    except Exception as exc:
        tb = traceback.format_exc()
        run.log(f"Cryocooler board initialization failed: {exc}\n{tb}", level="ERROR", component="CRYO")
        r_global.cryo = None
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
