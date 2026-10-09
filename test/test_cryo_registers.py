"""
Validation tests for the cryocooler board's register map and encodings.

Truth values come from two documented sources:

* ``python/docs/Cyrocooler_board_docs.pdf`` -- the board's own bring-up notes,
  which give the AD5258 resistance fit and the expected I2C scan result.
* The bench control script the board was commissioned with, which fixes the
  converter addresses, the register numbers, the reference-DAC scale, the
  output-voltage calibration and the enable/disable write sequences.

Nothing here touches hardware.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from formslab.devices.cryocooler import registers as regs
from formslab.devices.cryocooler import config as cryo_config


# --------------------------------------------------------------------------
# Addresses and register numbers
# --------------------------------------------------------------------------


def test_i2c_addresses():
    assert regs.VCONV_ADDR == 0x74
    assert regs.DIGIPOT_ADDR == 0x18
    assert regs.EXPECTED_ADDRESSES == (0x18, 0x74)


def test_expected_scan_result():
    found = [regs.format_address(addr) for addr in regs.EXPECTED_ADDRESSES]
    assert found == ["0x18", "0x74"]


def test_converter_register_map():
    assert (regs.REF_L, regs.REF_M, regs.IOUT_LIMIT, regs.VOUT_SR) == (0x00, 0x01, 0x02, 0x03)
    assert (regs.VOUT_FS, regs.CDC, regs.MODE, regs.STATUS) == (0x04, 0x05, 0x06, 0x07)
    assert regs.VOUT_ES == regs.VOUT_FS
    assert len(regs.CONVERTER_REGISTERS) == 8
    assert set(regs.CONVERTER_REGISTER_NAMES) == set(regs.CONVERTER_REGISTERS)


def test_digipot_register_map():
    assert regs.REG0 == 0x00
    assert regs.REG1 == 0x20
    assert set(regs.DIGIPOT_REGISTER_NAMES) == set(regs.DIGIPOT_REGISTERS)


# --------------------------------------------------------------------------
# Output enable / disable sequences
# --------------------------------------------------------------------------


def test_enable_sequence_matches_bench_script():
    """The three writes, in order, that the commissioned board is known to take."""
    assert regs.OUTPUT_ENABLE_SEQUENCE == (
        (0x05, 0b10100000),
        (0x06, 0b10100000),
        (0x05, 0b11100000),
    )


def test_enable_sequence_clears_ocp_mask_before_raising_oe():
    """OCP_MASK (CDC bit 6) must be low while OE (MODE bit 7) goes 0 -> 1."""
    (_, cdc_first), (mode_reg, mode_value), (_, cdc_last) = regs.OUTPUT_ENABLE_SEQUENCE
    assert cdc_first & 0b01000000 == 0
    assert mode_reg == regs.MODE and mode_value & 0b10000000
    assert cdc_last & 0b01000000


def test_disable_sequence_drops_output_enable():
    assert regs.OUTPUT_DISABLE_SEQUENCE == ((0x06, 0b00100000),)
    (_, value), = regs.OUTPUT_DISABLE_SEQUENCE
    assert value & 0b10000000 == 0


def test_current_limit_disable_clears_enable_bit():
    assert regs.IOUT_LIMIT_DISABLED == 0b01100100
    assert regs.IOUT_LIMIT_DISABLED & 0b10000000 == 0


# --------------------------------------------------------------------------
# Reference DAC
# --------------------------------------------------------------------------


def test_vref_scale():
    assert regs.VREF_BASELINE_V == 45.0e-3
    assert regs.VREF_STEP_V == 1.129e-3
    assert regs.vref_dac_code(regs.VREF_BASELINE_V) == 0


def test_vref_code_matches_bench_formula():
    for v_ref in (0.0564, 0.2, 0.6768, 0.9588, 1.128):
        expected = math.floor((v_ref - 45e-3) / 1.129e-3)
        assert regs.vref_dac_code(v_ref) == expected


def test_vref_code_clamps_to_ten_bits():
    assert regs.vref_dac_code(-5.0) == 0
    assert regs.vref_dac_code(99.0) == regs.DAC_CODE_MAX == 1023


def test_ref_register_bytes_split_lsb_msb():
    assert regs.ref_register_bytes(0) == (0x00, 0x00)
    assert regs.ref_register_bytes(255) == (0xFF, 0x00)
    assert regs.ref_register_bytes(256) == (0x00, 0x01)
    assert regs.ref_register_bytes(1023) == (0xFF, 0x03)


def test_ref_register_bytes_msb_is_two_bits():
    for code in range(0, 1024, 37):
        lsb, msb = regs.ref_register_bytes(code)
        assert 0 <= lsb <= 0xFF
        assert msb <= 0b11
        assert (msb << 8) | lsb == code


def test_vref_round_trip_within_one_step():
    """
    The encoder floors, as the bench script does, so the round trip is exact
    only up to binary representation of the 1.129 mV step. One code of slack.
    """
    for code in (0, 1, 100, 512, 1023):
        assert abs(regs.vref_dac_code(regs.vref_from_code(code)) - code) <= 1


# --------------------------------------------------------------------------
# Output-voltage calibration
# --------------------------------------------------------------------------


def test_calibration_constants_preserved():
    assert regs.CAL_SLOPE == 1.187
    assert regs.CAL_OFFSET == -1.90


def test_feedback_setting_is_the_finest_ratio():
    assert regs.FEEDBACK_RATIOS == (0.2256, 0.1128, 0.0752, 0.0564)
    assert regs.FEEDBACK_INDEX == 3
    assert regs.FEEDBACK_RATIOS[regs.FEEDBACK_INDEX] == min(regs.FEEDBACK_RATIOS)


def test_vref_for_output_matches_bench_formula():
    for v_out in (12.0, 15.0, 17.0, 20.0):
        expected = ((v_out - (-1.90)) / 1.187) * 0.0564
        assert regs.vref_for_output(v_out) == expected


def test_output_code_is_monotonic_over_the_operating_band():
    codes = [regs.output_dac_code(v / 10) for v in range(120, 201)]
    assert codes == sorted(codes)
    assert codes[0] < codes[-1]


def test_output_code_round_trips_within_one_dac_step():
    """One DAC step is ~20 mV at the 0.0564 feedback setting."""
    for v_out in (12.0, 14.5, 17.0, 20.0):
        recovered = regs.output_from_dac_code(regs.output_dac_code(v_out))
        assert recovered <= v_out
        assert v_out - recovered < 0.025


# --------------------------------------------------------------------------
# Digipot resistance
# --------------------------------------------------------------------------


def test_resistance_fit_matches_board_documentation():
    """docs/Cyrocooler_board_docs.pdf programmes 75.3 + 17.43 * D."""
    assert regs.CCVRES_FIT_BASE_OHMS == 75.3
    assert regs.CCVRES_FIT_STEP_OHMS == 17.43
    assert (regs.CCVRES_CODE_MIN, regs.CCVRES_CODE_MAX) == (0, 63)


def test_declared_band_is_narrower_than_the_reachable_range():
    """
    The 62-1120 ohm band predates the measured fit and does not line up with
    it: the fit reaches 75.3-1173.4 ohm. Pinned so the mismatch stays visible
    rather than being rediscovered on the bench. See the note in
    cryo_registers next to the fit constants.
    """
    assert regs.ccvres_ohms_from_code(0) == 75.3
    assert regs.ccvres_ohms_from_code(63) == 1173.39

    # A request at the bottom of the band lands ~13 ohm high.
    assert regs.ccvres_code_from_ohms(regs.CCVRES_MIN_OHMS) == 0
    assert regs.ccvres_ohms_from_code(0) - regs.CCVRES_MIN_OHMS > 13.0

    # A request at the top of the band is met, but codes 61-63 sit beyond it.
    top = regs.ccvres_code_from_ohms(regs.CCVRES_MAX_OHMS)
    assert top == 60
    assert regs.ccvres_ohms_from_code(top) >= regs.CCVRES_MAX_OHMS
    assert regs.ccvres_ohms_from_code(top + 1) > regs.CCVRES_MAX_OHMS


def test_resistance_code_is_nearest_not_floor():
    """FORMS rounds to nearest; the code lands within half a step of the ask."""
    for ohms in (100.0, 266.0, 300.0, 512.5, 900.0):
        code = regs.ccvres_code_from_ohms(ohms)
        error = abs(regs.ccvres_ohms_from_code(code) - ohms)
        assert error <= regs.CCVRES_FIT_STEP_OHMS / 2 + 1e-6


def test_resistance_code_clamps_outside_the_band():
    assert regs.ccvres_code_from_ohms(-100.0) == 0
    assert regs.ccvres_code_from_ohms(1e6) == 63
    assert regs.clamp_ccvres_code(-1) == 0
    assert regs.clamp_ccvres_code(999) == 63


def test_resistance_code_round_trips():
    for code in range(0, 64):
        assert regs.ccvres_code_from_ohms(regs.ccvres_ohms_from_code(code)) == code


# --------------------------------------------------------------------------
# STATUS decoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw, mode", [(0b00, "boost"), (0b01, "buck"), (0b10, "buck-boost")])
def test_status_bits_1_0_are_the_operating_mode(raw, mode):
    """TPS55288 datasheet, register 07h: there is no power-good bit."""
    flags = regs.decode_status(raw)
    assert flags["mode"] == mode and flags["healthy"] and not flags["faulted"]
    assert "pgood" not in flags
    assert regs.status_faults(raw) == []


def test_status_reserved_mode_is_not_healthy():
    assert not regs.decode_status(0b11)["healthy"]


def test_status_protection_flags():
    assert regs.decode_status(0x80)["sc"]
    assert regs.decode_status(0x40)["ocp"]
    assert regs.decode_status(0x20)["ovp"]
    assert regs.status_faults(0xE0) == ["SC", "OCP", "OVP"]
    assert regs.decode_status(0xE3)["faulted"]
    assert not regs.decode_status(0xE3)["healthy"]


def test_status_keeps_the_raw_byte():
    assert regs.decode_status(0x5A)["raw"] == 0x5A


# --------------------------------------------------------------------------
# Scan interpretation
# --------------------------------------------------------------------------


def test_scan_with_both_devices_is_healthy():
    verdict, _ = regs.interpret_scan(["0x18", "0x74"])
    assert verdict == "healthy"


def test_every_address_answering_means_sda_is_stuck_low():
    """
    Observed on the bench 2026-08-29: SDA held low makes all of 0x08-0x77
    acknowledge. That is a wiring fault, not 112 devices.
    """
    everything = [regs.format_address(a) for a in range(0x08, 0x78)]
    verdict, note = regs.interpret_scan(everything)
    assert verdict == "sda_stuck_low"
    assert "SDA" in note


def test_empty_scan_is_silent_not_stuck():
    verdict, note = regs.interpret_scan([])
    assert verdict == "silent"
    assert "15 V" in note


def test_one_missing_device_is_partial():
    verdict, note = regs.interpret_scan(["0x18"])
    assert verdict == "partial"
    assert "0x74" in note


def test_scan_interpretation_accepts_ints_and_mixed_case():
    assert regs.interpret_scan([0x18, "0X74"])[0] == "healthy"


# --------------------------------------------------------------------------
# Configuration re-export
# --------------------------------------------------------------------------


def test_cryo_config_reexports_a_single_fit():
    assert cryo_config.CCVRES_FIT_BASE_OHMS is regs.CCVRES_FIT_BASE_OHMS
    assert cryo_config.ccvres_code_from_ohms is regs.ccvres_code_from_ohms
    assert cryo_config.ccvres_ohms_from_code is regs.ccvres_ohms_from_code


def test_operating_band_is_inside_the_supply_thresholds():
    assert cryo_config.CCV_MIN_V == 8.5                      # the K508N's input range
    assert cryo_config.CCV_MAX_V == 20.0
    assert (
        cryo_config.CRYO_SUPPLY_VOLTAGE_V > cryo_config.CRYO_OUTPUT_SUPPLY_THRESHOLD_V
        > cryo_config.CRYO_I2C_SUPPLY_THRESHOLD_V
    )


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
