"""
Cryocooler control board register map and encodings.

The board carries two I2C devices, documented in
``python/docs/Cyrocooler_board_docs.pdf``:

* ``0x74`` -- Texas Instruments **TPS55288** buck-boost converter, which sets
  the cryocooler drive voltage (CCVOUT).
* ``0x18`` -- Analog Devices **AD5258BRMZ1** 64-position digital
  potentiometer, 1 kohm end-to-end, which sets the drive resistance (CCVRES).

This module is the single authority for turning an engineering quantity into
bytes on the wire, and back. It imports nothing, holds no state and touches no
hardware, so every encoding below is testable on the FlatSat PC with no board
attached (``test/test_cryo_registers.py``).

The transport that carries these bytes lives in ``lab/pico_i2c.py``; the board
behaviour that sequences them lives in ``lab/CryoBoard.py``.
"""

import math


# --------------------------------------------------------------------------
# I2C addresses
# --------------------------------------------------------------------------

VCONV_ADDR = 0x74
DIGIPOT_ADDR = 0x18

# What a healthy board reports from an I2C scan once its input is above the
# ~15 V at which the devices come up. Note that seeing both addresses proves
# only that the bus is alive -- the converter needs roughly 20 V in before its
# output can actually be enabled. See CryoBoard.status().
EXPECTED_ADDRESSES = (DIGIPOT_ADDR, VCONV_ADDR)


# --------------------------------------------------------------------------
# TPS55288 converter registers
# --------------------------------------------------------------------------

REF_L = 0x00
REF_M = 0x01
IOUT_LIMIT = 0x02
VOUT_SR = 0x03
VOUT_FS = 0x04
CDC = 0x05
MODE = 0x06
STATUS = 0x07

# Historic FORMS/reference-script name for VOUT_FS (0x04), kept so existing
# call sites and notebooks keep resolving.
VOUT_ES = VOUT_FS

CONVERTER_REGISTERS = (
    REF_L,
    REF_M,
    IOUT_LIMIT,
    VOUT_SR,
    VOUT_FS,
    CDC,
    MODE,
    STATUS,
)

CONVERTER_REGISTER_NAMES = {
    REF_L: "REF_L",
    REF_M: "REF_M",
    IOUT_LIMIT: "IOUT_LIMIT",
    VOUT_SR: "VOUT_SR",
    VOUT_FS: "VOUT_FS",
    CDC: "CDC",
    MODE: "MODE",
    STATUS: "STATUS",
}


# --------------------------------------------------------------------------
# AD5258 digipot registers
# --------------------------------------------------------------------------

REG0 = 0x00  # RDAC (volatile wiper position)
REG1 = 0x20  # EEPROM mirror

DIGIPOT_REGISTERS = (REG0, REG1)

DIGIPOT_REGISTER_NAMES = {REG0: "RDAC", REG1: "EEPROM"}

# Instruction byte that copies the live RDAC value into EEPROM. Never issued
# by FORMS -- the wiper is programmed on every run -- but recorded here so the
# bit pattern is not rediscovered from scratch. The part needs ~25 ms after it.
DIGIPOT_STORE_EEPROM = 0b11000000
DIGIPOT_STORE_EEPROM_DELAY_S = 0.025


# --------------------------------------------------------------------------
# Output enable / disable
# --------------------------------------------------------------------------
#
# These byte sequences are load-bearing and measured on hardware; do not
# "tidy" them. The TPS55288 datasheet requires OCP_MASK (CDC bit 6) to be
# clear while OE (MODE bit 7) transitions 0 -> 1, so the enable is three
# writes: clear the mask, raise OE, restore the mask.

OUTPUT_ENABLE_SEQUENCE = (
    (CDC, 0b10100000),
    (MODE, 0b10100000),
    (CDC, 0b11100000),
)

OUTPUT_DISABLE_SEQUENCE = ((MODE, 0b00100000),)

# IOUT_LIMIT with the enable bit (bit 7) cleared: current limiting off, the
# remaining bits left at their reset defaults.
IOUT_LIMIT_DISABLED = 0b01100100


# --------------------------------------------------------------------------
# Reference DAC (REF_L / REF_M)
# --------------------------------------------------------------------------

DAC_CODE_MIN = 0
DAC_CODE_MAX = 1023

VREF_BASELINE_V = 45.0e-3
VREF_STEP_V = 1.129e-3


def vref_dac_code(v_ref):
    """Return the 10-bit REF code that lands closest below ``v_ref`` volts."""
    code = math.floor((float(v_ref) - VREF_BASELINE_V) / VREF_STEP_V)
    return clamp_dac_code(code)


def vref_from_code(code):
    """Return the reference voltage a REF code produces, in volts."""
    return VREF_BASELINE_V + clamp_dac_code(code) * VREF_STEP_V


def clamp_dac_code(code):
    return max(DAC_CODE_MIN, min(DAC_CODE_MAX, int(code)))


def ref_register_bytes(code):
    """
    Split a 10-bit REF code into the (REF_L, REF_M) payload.

    The pair is written as one two-byte burst starting at REF_L; the converter
    auto-increments into REF_M.
    """
    value = clamp_dac_code(code)
    return (value & 0xFF, (value >> 8) & 0xFF)


# --------------------------------------------------------------------------
# Feedback divider (VOUT_FS) and output-voltage calibration
# --------------------------------------------------------------------------

# Internal feedback ratios selectable through VOUT_FS bits 1:0.
FEEDBACK_RATIOS = (0.2256, 0.1128, 0.0752, 0.0564)

# FORMS drives the cryocooler on the 0.0564 setting for the whole 12-20 V
# band: it is the only ratio that covers the band on one setting, at ~20 mV
# per DAC step.
FEEDBACK_INDEX = 3

# Measured correction between commanded and delivered output voltage. Both the
# reference bench script and the deployed FORMS firmware carry these same two
# numbers, so they are treated as current until a fresh calibration run says
# otherwise.
CAL_SLOPE = 1.187
CAL_OFFSET = -1.90


def vref_for_output(v_out, feedback_index=FEEDBACK_INDEX):
    """Return the reference voltage that yields ``v_out`` at the output."""
    v_corrected = (float(v_out) - CAL_OFFSET) / CAL_SLOPE
    return v_corrected * FEEDBACK_RATIOS[int(feedback_index)]


def output_dac_code(v_out, feedback_index=FEEDBACK_INDEX):
    """Return the REF code that commands ``v_out`` volts at the output."""
    return vref_dac_code(vref_for_output(v_out, feedback_index))


def output_from_dac_code(code, feedback_index=FEEDBACK_INDEX):
    """Inverse of :func:`output_dac_code`, for readback and diagnostics."""
    v_corrected = vref_from_code(code) / FEEDBACK_RATIOS[int(feedback_index)]
    return v_corrected * CAL_SLOPE + CAL_OFFSET


# --------------------------------------------------------------------------
# Digipot resistance (CCVRES)
# --------------------------------------------------------------------------
#
# Two fits for the same part appear in the project's history:
#
#   62.06 + 16.81 * D   -- the AD5258 datasheet ideal (1 kohm / 64 taps plus
#                          nominal wiper resistance). Used by the original
#                          bench script cryo_control_example.py.
#   75.30 + 17.43 * D   -- the fit in docs/Cyrocooler_board_docs.pdf, taken
#                          from resistance measured across this board.
#
# FORMS uses the measured fit, which is what the board documentation's own
# listing programs and what cryo_config has published since the cryocooler
# work landed.
#
# Two things about it are UNRESOLVED and deliberately left as they are:
#
# 1. The documentation derives the code with floor(), FORMS with round().
#    round() is kept -- it is what the shipped code does, and it lands nearest
#    the requested value -- but a request is therefore satisfied to within half
#    a step (~8.7 ohm) either side, not always from below.
#
# 2. The declared 62-1120 ohm band predates the measured fit; it matches the
#    datasheet-ideal one exactly (62.06 + 16.81 * 63 = 1121.1). Under the
#    measured fit the reachable range is 75.3-1173.4 ohm, so a request at the
#    bottom of the band returns ~13 ohm high, and codes 61-63 are unreachable
#    through ccvres_code_from_ohms(). The band is left alone because it is what
#    CAST advertises and what rCryoBoard enforces; reach the top codes with
#    CryoBoard.set_resistance_code() if they are ever needed.

CCVRES_CODE_MIN = 0
CCVRES_CODE_MAX = 63
CCVRES_FIT_BASE_OHMS = 75.3
CCVRES_FIT_STEP_OHMS = 17.43

CCVRES_MIN_OHMS = 62.0
CCVRES_MAX_OHMS = 1120.0


def clamp_ccvres_code(code):
    return max(CCVRES_CODE_MIN, min(CCVRES_CODE_MAX, int(code)))


def ccvres_code_from_ohms(ohms):
    """Return the 6-bit wiper code nearest to ``ohms``."""
    code = round((float(ohms) - CCVRES_FIT_BASE_OHMS) / CCVRES_FIT_STEP_OHMS)
    return clamp_ccvres_code(code)


def ccvres_ohms_from_code(code):
    """Return the resistance a wiper code produces, in ohms."""
    value = clamp_ccvres_code(code)
    return round(CCVRES_FIT_BASE_OHMS + value * CCVRES_FIT_STEP_OHMS, 2)


# --------------------------------------------------------------------------
# STATUS register
# --------------------------------------------------------------------------

STATUS_SC = 0x80
STATUS_OCP = 0x40
STATUS_OVP = 0x20
STATUS_PGOOD = 0x02
STATUS_INTVREF = 0x01

STATUS_FAULT_MASK = STATUS_SC | STATUS_OCP | STATUS_OVP


def decode_status(value):
    """
    Decode a STATUS byte into named flags.

    ``faulted`` is true when any protection flag is latched; ``healthy`` means
    the converter reports a good output off a ready internal reference and no
    fault. Both describe the *converter*, not the I2C link -- a board whose
    input sits between ~15 V and ~20 V answers on the bus and still reports an
    unhealthy output.
    """
    raw = int(value) & 0xFF
    flags = {
        "raw": raw,
        "sc": bool(raw & STATUS_SC),
        "ocp": bool(raw & STATUS_OCP),
        "ovp": bool(raw & STATUS_OVP),
        "pgood": bool(raw & STATUS_PGOOD),
        "intvref": bool(raw & STATUS_INTVREF),
    }
    flags["faulted"] = bool(raw & STATUS_FAULT_MASK)
    flags["healthy"] = flags["pgood"] and flags["intvref"] and not flags["faulted"]
    return flags


def status_faults(value):
    """Return the latched protection flag names, most severe first."""
    raw = int(value) & 0xFF
    names = []
    if raw & STATUS_SC:
        names.append("SC")
    if raw & STATUS_OCP:
        names.append("OCP")
    if raw & STATUS_OVP:
        names.append("OVP")
    return names


def format_address(address):
    """Render an I2C address the way a scan reports it."""
    return "0x%02X" % int(address)


def parse_address(address):
    """
    Accept an address as an int or as any hex spelling and return the int.

    Scan results cross a serial link as text, so ``"0x18"``, ``"0X18"`` and
    ``24`` all have to mean the same device.
    """
    if isinstance(address, int):
        return address
    return int(str(address).strip(), 16)


def normalize_addresses(addresses):
    """Return a sorted, de-duplicated list of ints from a scan result."""
    return sorted({parse_address(value) for value in addresses})


# A scan that reports this many devices is not reporting devices. The 7-bit
# space between the reserved ranges is 0x08-0x77, and when SDA is held low
# every one of those addresses appears to acknowledge.
STUCK_BUS_ADDRESS_COUNT = 32


def interpret_scan(addresses):
    """
    Say what a scan result actually means.

    An I2C scan has more failure modes than "found" and "not found", and they
    point at different things:

    ``healthy``
        Both expected devices answered and nothing else did.
    ``sda_stuck_low``
        Nearly every address acknowledged. No bus has 100 devices on it -- SDA
        is being held low, so every address reads as an ACK. Look at wiring,
        not at the converter.
    ``silent``
        Nothing answered. Either the board is below the ~15 V at which its
        devices come up, or the bus is not connected to the pins configured in
        usbmap.json.
    ``partial``
        Somebody answered, but not the expected pair.

    Returns ``(verdict, note)``.
    """
    found = normalize_addresses(addresses)
    missing = [addr for addr in EXPECTED_ADDRESSES if addr not in found]

    if len(found) >= STUCK_BUS_ADDRESS_COUNT:
        return (
            "sda_stuck_low",
            f"{len(found)} addresses acknowledged; SDA is held low. Check the "
            "SDA line for a short to ground or a device holding the bus.",
        )
    if not found:
        return (
            "silent",
            "No device answered. Check board input voltage (devices need "
            "roughly 15 V) and the I2C pins configured in usbmap.json.",
        )
    if not missing and len(found) == len(EXPECTED_ADDRESSES):
        return ("healthy", "Converter and digipot both answered.")
    if missing:
        names = ", ".join(format_address(addr) for addr in missing)
        return ("partial", f"Expected device(s) missing: {names}.")
    return (
        "partial",
        "Unexpected device(s) on the bus: "
        + ", ".join(format_address(addr) for addr in found),
    )
