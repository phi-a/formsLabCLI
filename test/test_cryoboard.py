"""
Validation tests for CryoBoard against a recording fake I2C bus.

These assert on the exact bytes the board layer puts on the wire, so the
commissioned register sequences stay intact while the code above them moves.
No hardware, no serial port, no mpremote.
"""

import sys
from pathlib import Path

import pytest

from formslab import config
from formslab.devices.cryocooler import registers as regs
from formslab.devices.cryocooler.board import CryoBoard


def _usbmap():
    """The live map, seeded from the packaged default into the test config dir.

    Resolved per call rather than at import: `conftest` redirects
    `$FORMSLAB_CONFIG_DIR` per test, so a module-level constant would capture
    whichever directory happened to exist at collection time.
    """
    return config.usbmap_path()


class FakeI2C:
    """A PicoI2C-shaped bus that records traffic and answers from a register file."""

    def __init__(self, present=(0x18, 0x74), status=0b00000001):
        self.present = list(present)
        self.status = status                    # 01: buck mode, no fault, as 24 V in to 12 V out
        self.registers = {}                     # (address, register) -> last byte written
        self.writes = []
        self.scans = 0
        self.opened = False
        self.closed = False
        self.deployed = False

    # transport surface used by CryoBoard
    def open(self, soft_reset=False):
        self.opened = True

    def close(self):
        self.closed = True

    def deploy_firmware(self):
        self.deployed = True
        self.opened = True

    def scan(self):
        self.scans += 1
        return [regs.format_address(addr) for addr in self.present]

    def read_register(self, address, register, nbytes=1):
        if address == regs.VCONV_ADDR and register == regs.STATUS:
            return bytes([self.status])
        return bytes([self.registers.get((address, register), 0)] * nbytes)

    def write_register(self, address, register, data):
        payload = [data] if isinstance(data, int) else list(data)
        self.writes.append((address, register, payload))
        for k, byte in enumerate(payload):
            self.registers[(address, register + k)] = byte
        return len(payload)

    # helpers for assertions
    def converter_writes(self):
        return [(reg, payload) for addr, reg, payload in self.writes if addr == regs.VCONV_ADDR]

    def digipot_writes(self):
        return [(reg, payload) for addr, reg, payload in self.writes if addr == regs.DIGIPOT_ADDR]


def make_board(**kwargs):
    bus = FakeI2C(**kwargs)
    board = CryoBoard("cryo_board", config_path=_usbmap(), transport=bus)
    return board, bus


# --------------------------------------------------------------------------
# Construction is inert
# --------------------------------------------------------------------------


def test_construction_writes_nothing_and_opens_nothing():
    board, bus = make_board()
    assert bus.writes == []
    assert bus.scans == 0
    assert not bus.deployed
    assert board.state["enabled"] is False
    assert board.state["connected"] is False


def test_scan_does_not_enable_the_output():
    board, bus = make_board()
    assert board.scan() == ["0x18", "0x74"]
    assert bus.writes == []
    assert board.state["enabled"] is False


# --------------------------------------------------------------------------
# Configuration comes from the USB map
# --------------------------------------------------------------------------


def test_i2c_pins_are_configuration_not_code():
    """GP17/GP16, confirmed by a bench pull-up sweep. Not the ESP32 22/23."""
    board, _ = make_board()
    assert (board.scl_pin, board.sda_pin) == (17, 16)
    assert board.i2c_freq == 100000


def test_stuck_bus_is_reported_and_status_is_not_read():
    """
    Every address answering means SDA is held low. The converter appears
    'present' on such a bus, so reading STATUS would report noise as telemetry.
    """
    board, bus = make_board(present=tuple(range(0x08, 0x78)))
    state = board.status()
    assert state["bus"] == "sda_stuck_low"
    assert state["output_on"] is None and state["conversion"] is None
    assert state["output_healthy"] is False


def test_present_explains_a_silent_bus():
    board, _ = make_board(present=())
    found = board.present()
    assert found["bus"] == "silent"
    assert not found["converter"] and not found["digipot"]


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError):
        CryoBoard("not_a_device", config_path=_usbmap())


# --------------------------------------------------------------------------
# Discovery and health
# --------------------------------------------------------------------------


def test_present_reports_both_devices():
    board, _ = make_board()
    found = board.present()
    assert found["addresses"] == ["0x18", "0x74"]
    assert found["converter"] and found["digipot"]


def test_present_reports_a_missing_converter():
    board, _ = make_board(present=(0x18,))
    found = board.present()
    assert found["digipot"] and not found["converter"]


def test_status_separates_link_health_from_output_health():
    """Bus alive and the converter answering, its output off: not yet ok."""
    board, _ = make_board(status=0b00000001)
    state = board.status()
    assert state["connected"] is True
    assert state["i2c_devices"] == ["0x18", "0x74"]
    assert state["converter_present"] and state["digipot_present"]
    assert state["output_on"] is False and state["conversion"] == "buck"
    assert state["output_healthy"] is False
    assert state["faults"] == []


def test_status_reports_the_output_on_from_the_converters_own_enable_bit():
    board, _ = make_board(status=0b00000001)
    board.enable_output()
    state = board.status()
    assert state["output_on"] is True and state["conversion"] == "buck"
    assert state["output_healthy"] is True and state["faulted"] is False


def test_status_reports_latched_faults():
    board, _ = make_board(status=0b01000001)
    board.enable_output()
    state = board.status()
    assert state["faults"] == ["OCP"]
    assert state["faulted"] is True
    assert state["output_healthy"] is False


def test_status_without_a_converter_does_not_invent_flags():
    board, _ = make_board(present=(0x18,))
    state = board.status()
    assert state["converter_present"] is False
    assert state["output_on"] is None
    assert state["output_healthy"] is False


def test_read_status_decodes_the_register():
    board, _ = make_board(status=0x20)
    assert board.read_status()["ovp"] is True


def test_read_registers_dumps_both_devices():
    board, _ = make_board()
    dump = board.read_registers()
    assert set(dump["converter"]) == set(regs.CONVERTER_REGISTER_NAMES.values())
    assert set(dump["digipot"]) == {"RDAC", "EEPROM"}


# --------------------------------------------------------------------------
# Register sequences
# --------------------------------------------------------------------------


def test_enable_output_writes_the_commissioned_sequence():
    board, bus = make_board()
    board.enable_output()
    assert bus.converter_writes() == [
        (regs.CDC, [0b10100000]),
        (regs.MODE, [0b10100000]),
        (regs.CDC, [0b11100000]),
    ]
    assert board.state["enabled"] is True


def test_disable_output_writes_the_commissioned_sequence():
    board, bus = make_board()
    board.state["enabled"] = True
    board.disable_output()
    assert bus.converter_writes() == [(regs.MODE, [0b00100000])]
    assert board.state["enabled"] is False


def test_disable_output_reaches_hardware_even_when_state_says_off():
    """A fresh object believes the output is off; the board may disagree."""
    board, bus = make_board()
    assert board.state["enabled"] is False
    board.disable_output()
    assert bus.converter_writes() == [(regs.MODE, [0b00100000])]


def test_set_output_voltage_writes_ref_as_one_burst_then_feedback():
    board, bus = make_board()
    board.set_output_voltage(17.0)
    code = regs.output_dac_code(17.0)
    lsb, msb = regs.ref_register_bytes(code)
    assert bus.converter_writes() == [
        (regs.REF_L, [lsb, msb]),
        (regs.VOUT_FS, [regs.FEEDBACK_INDEX]),
    ]
    assert board.state["output_voltage_v"] == 17.0


def test_set_resistance_writes_the_wiper_code():
    board, bus = make_board()
    board.set_resistance(300.0)
    code = regs.ccvres_code_from_ohms(300.0)
    assert bus.digipot_writes() == [(regs.REG0, [code])]
    assert board.state["resistance_code"] == code
    assert board.state["resistance_ohms"] == regs.ccvres_ohms_from_code(code)


def test_resistance_change_drops_a_live_output_first():
    """The wiper must never move under load."""
    board, bus = make_board()
    board.enable_output()
    bus.writes.clear()
    board.set_resistance(500.0)
    ops = bus.writes
    assert ops[0] == (regs.VCONV_ADDR, regs.MODE, [0b00100000])
    assert ops[1][0] == regs.DIGIPOT_ADDR
    assert [(reg, payload) for addr, reg, payload in ops[2:] if addr == regs.VCONV_ADDR] == [
        (regs.CDC, [0b10100000]),
        (regs.MODE, [0b10100000]),
        (regs.CDC, [0b11100000]),
    ]
    assert board.state["enabled"] is True


def test_resistance_change_on_a_disabled_board_leaves_it_disabled():
    board, bus = make_board()
    board.set_resistance(500.0)
    assert bus.converter_writes() == []
    assert board.state["enabled"] is False


def test_disable_current_limit_clears_the_enable_bit():
    board, bus = make_board()
    board.disable_current_limit()
    assert bus.converter_writes() == [(regs.IOUT_LIMIT, [regs.IOUT_LIMIT_DISABLED])]


# --------------------------------------------------------------------------
# Initialization safety
# --------------------------------------------------------------------------


def test_initialize_leaves_the_output_off_by_default():
    board, bus = make_board()
    board.initialize(voltage=17.0, resistance=266.0)
    assert bus.deployed
    assert board.state["enabled"] is False
    modes = [payload for reg, payload in bus.converter_writes() if reg == regs.MODE]
    assert modes == [[0b00100000]]


def test_initialize_only_enables_when_asked():
    board, bus = make_board()
    board.initialize(voltage=12.0, resistance=266.0, enabled=True)
    assert board.state["enabled"] is True
    assert bus.converter_writes()[-3:] == [
        (regs.CDC, [0b10100000]),
        (regs.MODE, [0b10100000]),
        (regs.CDC, [0b11100000]),
    ]


def test_shutdown_disables_the_output_and_releases_the_link():
    board, bus = make_board()
    board.enable_output()
    bus.writes.clear()
    board.shutdown()
    assert bus.converter_writes() == [(regs.MODE, [0b00100000])]
    assert bus.closed
    assert board.state["enabled"] is False


def test_shutdown_is_safe_without_a_transport():
    board, _ = make_board()
    board.transport = None
    assert board.shutdown()["enabled"] is False


# --------------------------------------------------------------------------
# Range checking
# --------------------------------------------------------------------------


@pytest.mark.parametrize("volts", [8.4, 20.1, 0.0, -5.0, 100.0])
def test_out_of_band_voltage_is_rejected_before_any_write(volts):
    board, bus = make_board()
    with pytest.raises(ValueError):
        board.set_output_voltage(volts)
    assert bus.writes == []


@pytest.mark.parametrize("ohms", [61.9, 1120.1, -10.0])
def test_out_of_band_resistance_is_rejected_before_any_write(ohms):
    board, bus = make_board()
    with pytest.raises(ValueError):
        board.set_resistance(ohms)
    assert bus.writes == []


@pytest.mark.parametrize("volts", [12.0, 17.0, 20.0])
def test_band_edges_are_accepted(volts):
    board, _ = make_board()
    assert board.set_output_voltage(volts)["output_voltage_v"] == volts


def test_update_with_nothing_to_do_writes_nothing():
    board, bus = make_board()
    board.update()
    assert bus.writes == []


def test_raw_setters_bypass_the_calibration_but_still_clamp():
    board, bus = make_board()
    board.set_output_voltage_raw(2000)
    assert bus.converter_writes()[0] == (regs.REF_L, [0xFF, 0x03])
    assert board.state["output_dac_code"] == 1023
    bus.writes.clear()
    board.set_resistance_code(99)
    assert bus.digipot_writes() == [(regs.REG0, [63])]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# --------------------------------------------------------------------------
# The Pico's own pull-ups, for a board whose resistors were removed
# --------------------------------------------------------------------------

FIRMWARE = Path(__file__).resolve().parents[1] / "src" / "formslab" / "devices" / "cryocooler" / "pico_board_control.py"


def _firmware(made):
    import types

    class Pin:
        OPEN_DRAIN, PULL_UP = "open_drain", "pull_up"

        def __init__(self, n, mode=None, pull=None):
            made.append((n, mode, pull))

    class SoftI2C:
        def __init__(self, scl, sda, freq):
            made.append(("bus", freq))

    namespace = {}
    sys.modules["machine"] = types.SimpleNamespace(Pin=Pin, SoftI2C=SoftI2C)
    try:
        exec(compile(FIRMWARE.read_text(encoding="utf-8"), "main.py", "exec"), namespace)
    finally:
        sys.modules.pop("machine", None)
    return namespace


def test_the_firmware_turns_on_the_picos_pull_ups_only_when_asked():
    made = []
    fw = _firmware(made)
    fw["configure"](scl=17, sda=16, freq=20000, pull=True)
    assert made == [(17, "open_drain", "pull_up"), (16, "open_drain", "pull_up"), ("bus", 20000)]
    made.clear()
    fw["configure"](scl=17, sda=16, freq=100000)
    assert made == [(17, None, None), (16, None, None), ("bus", 100000)]


def test_pullup_internal_in_the_map_reaches_the_pico(monkeypatch, tmp_path):
    import json
    from formslab.devices.cryocooler import pico_i2c
    live = json.loads(config.usbmap_path().read_text(encoding="utf-8"))
    live["cryo_board"]["i2c"].update(pullup="internal", freq=20000)
    path = tmp_path / "usbmap.json"
    path.write_text(json.dumps(live), encoding="utf-8")
    board = CryoBoard("cryo_board", config_path=path)
    assert board.i2c_pull is True and board.i2c_freq == 20000
    calls = []
    bridge = pico_i2c.PicoI2C("COM9", scl_pin=17, sda_pin=16, freq=20000, pull=True)
    monkeypatch.setattr(bridge, "_call", lambda expression: calls.append(expression))
    bridge._configure()
    assert calls == ["main.configure(scl=17, sda=16, freq=20000, pull=True)"]
    assert CryoBoard("cryo_board", config_path=_usbmap()).i2c_pull is False   # the board's own, by default


# --------------------------------------------------------------------------
# An output the converter turned off by itself
# --------------------------------------------------------------------------

def _drop(bus):
    """What the converter holds after a trip or a reset: its output off."""
    bus.registers[(regs.VCONV_ADDR, regs.MODE)] = 0b00100000


def test_on_after_the_converter_dropped_the_output_turns_it_on_again():
    board, bus = make_board()
    board.initialize(voltage=12.0, resistance=270.0)
    board.enable_output()
    _drop(bus)
    assert board.status()["output_on"] is False
    bus.writes.clear()
    board.update(enabled=True)                          # the cache said "on": it must not be trusted
    assert board.status()["output_on"] is True
    written = [reg for _, reg, _ in bus.writes]
    assert regs.IOUT_LIMIT in written and regs.REF_L in written   # its settings back first, a reset loses them
    assert written.index(regs.REF_L) < written.index(regs.MODE)


def test_on_when_already_on_writes_the_enable_and_nothing_else():
    board, bus = make_board()
    board.initialize(voltage=12.0, resistance=270.0)
    board.enable_output()
    bus.writes.clear()
    board.update(enabled=True)
    assert regs.REF_L not in [reg for _, reg, _ in bus.writes]


def test_off_always_reaches_the_converter():
    board, bus = make_board()
    board.initialize(voltage=12.0, resistance=270.0)
    bus.registers[(regs.VCONV_ADDR, regs.MODE)] = 0b10100000      # on, though never commanded here
    board.update(enabled=False)
    assert board.status()["output_on"] is False
