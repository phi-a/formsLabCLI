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

# Supply feeding the cryocooler board's input. Confirmed on the bench
# 2026-08-29: the board is wired to the Rigol DP832A (psu1) CH1. The previous
# psu2 CH2 entry referred to the FTDI Chipi-X supply, which is not part of
# this setup.
#
# rCryoBoard owns this channel while it runs; do not command psu1 CH1 from a
# plan or the console at the same time.
CRYO_PSU_LABEL = "psu1"
CRYO_PSU_CHANNEL = 1

# Component tag for supply log lines, derived so it cannot drift from the
# label the way the hard-coded "PSU2" strings did.
CRYO_PSU_COMPONENT = CRYO_PSU_LABEL.upper()

# The board's I2C devices come up above roughly 15 V in, but the converter
# cannot be commanded to produce an output until roughly 20 V. 24 V is the
# normal bench condition under which both work. These are current engineering
# figures from bring-up, not electrical specifications.
CRYO_SUPPLY_VOLTAGE_V = 24.0
CRYO_SUPPLY_CURRENT_A = 2.0
CRYO_SUPPLY_OVP_V = 24.5
CRYO_SUPPLY_OCP_A = 2.2

CRYO_I2C_SUPPLY_THRESHOLD_V = 15.0
CRYO_OUTPUT_SUPPLY_THRESHOLD_V = 20.0

CRYO_DEFAULT_OUTPUT_VOLTAGE_V = 17.0
CRYO_DEFAULT_RESISTANCE_OHMS = 266.0

# Band formsLabCLI drives the cryocooler in. Narrower than what the converter can be
# programmed for; this is the range CAST accepts and rCryoBoard enforces.
CCV_MIN_V = 12.0
CCV_MAX_V = 20.0
