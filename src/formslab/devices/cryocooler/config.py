"""
Cryocooler subsystem configuration.

Operating policy for the cryocooler: which PSU channel feeds the board, what
the supply is set to, and the band formsLabCLI is willing to drive the cooler in.

Device-level facts -- I2C addresses, the register map, the DAC and resistance
encodings -- are *not* here. They live in ``cryocooler/registers.py``, and the
resistance helpers below are re-exported from there so there is exactly one
copy of the fit in the tree.
"""

from formslab.devices.cryocooler.registers import (  # noqa: F401  (re-exported for callers)
    CCVRES_CODE_MAX,
    CCVRES_CODE_MIN,
    CCVRES_FIT_BASE_OHMS,
    CCVRES_FIT_STEP_OHMS,
    CCVRES_MAX_OHMS,
    CCVRES_MIN_OHMS,
    ccvres_code_from_ohms,
    ccvres_ohms_from_code,
)

# Supply feeding the cryocooler board's input. The hardware map says which
# (usbmap.json, `"channels"` on the supply: owner rCryoBoard; see
# devices/dp832a/wiring.py); these are the default when it does not. Confirmed
# on the bench 2026-08-29: the board is wired to the Rigol DP832A (psu1) CH1.
#
# rCryoBoard owns this channel while it runs; do not command it from a plan or
# the console at the same time.
CRYO_PSU_LABEL = "psu1"
CRYO_PSU_CHANNEL = 1

# Component tag for supply log lines, derived so it cannot drift from the
# label the way the hard-coded "PSU2" strings did.
CRYO_PSU_COMPONENT = CRYO_PSU_LABEL.upper()


def cryo_supply(r_global=None) -> tuple[str, int]:
    """(supply label, channel) feeding the board, from the hardware map. Kept on
    `r_global` for the run, so the channel cannot change under a running board."""
    from formslab.devices.dp832a.wiring import supply_for

    cached = getattr(r_global, "_supply", None)
    if cached:
        return cached
    supply = supply_for("rCryoBoard", (CRYO_PSU_LABEL, CRYO_PSU_CHANNEL))
    if r_global is not None:
        r_global._supply = supply
    return supply

# The board's I2C devices come up above roughly 15 V in, but the converter
# cannot be commanded to produce an output until roughly 20 V. 24 V is the
# normal bench condition under which both work. These are current engineering
# figures from bring-up, not electrical specifications.
CRYO_SUPPLY_VOLTAGE_V = 24.0
CRYO_SUPPLY_CURRENT_A = 1.0       # the board's supply: 24 V, 1.0 A, protected at 1.25 A (2026-10-07)
CRYO_SUPPLY_OVP_V = 24.5
CRYO_SUPPLY_OCP_A = 1.25

CRYO_I2C_SUPPLY_THRESHOLD_V = 15.0
CRYO_OUTPUT_SUPPLY_THRESHOLD_V = 20.0

# The most the board's input may be given (2026-10-07): `cryo supply` refuses more.
CRYO_SUPPLY_MAX_V = 24.5
CRYO_SUPPLY_MAX_A = 2.0


def supply_settings(r_global=None) -> dict:
    """The board's supply as the run has set it (`cryo supply`), or the defaults above:
    {volts, amps, ovp, ocp}."""
    chosen = getattr(r_global, "supply_set", None)
    return dict(chosen) if chosen else {"volts": CRYO_SUPPLY_VOLTAGE_V, "amps": CRYO_SUPPLY_CURRENT_A,
                                        "ovp": CRYO_SUPPLY_OVP_V, "ocp": CRYO_SUPPLY_OCP_A}

CRYO_DEFAULT_OUTPUT_VOLTAGE_V = 17.0
CRYO_DEFAULT_RESISTANCE_OHMS = 266.0

# Band formsLabCLI drives the cryocooler in: the K508N's input range, 8.5 to 20 V
# (2026-10-07). Narrower than what the converter can be programmed for; this is the
# range CAST accepts and rCryoBoard enforces. The converter's calibration
# (registers.CAL_SLOPE, CAL_OFFSET) was set on the bench; below 12 V it has not been
# checked against a meter.
CCV_MIN_V = 8.5
CCV_MAX_V = 20.0
