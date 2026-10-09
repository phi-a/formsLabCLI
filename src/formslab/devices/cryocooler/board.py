"""
Cryocooler control board.

Layering, from the bottom up::

    lab/pico_board_control.py   MicroPython I2C bridge firmware (on the Pico)
    lab/pico_i2c.py             PC-side transport seam: scan / read / write
    lab/cryo_registers.py       register map + encodings, hardware-free
    lab/CryoBoard.py            board behaviour and state  <- this file
    rScripts/rCryoBoard.py      the only routine that owns a CryoBoard

Everything this class does resolves to the three transport operations, so it
can be exercised against a fake bus with no hardware attached (see
``test/test_cryoboard.py``).

Safety
------
Constructing a ``CryoBoard`` opens no link and writes no register.
:meth:`initialize` programmes the board with its output *off*; the output is
only ever energised by an explicit :meth:`enable_output` (or
``initialize(enabled=True)``). A successful :meth:`scan` never enables
anything -- see :meth:`status` for why the two are deliberately separate.
"""

import json
from pathlib import Path

from formslab.config import usbmap_path

from formslab.devices.cryocooler import registers as regs
from formslab.devices.cryocooler.config import (
    CCV_MAX_V,
    CCV_MIN_V,
    CCVRES_MAX_OHMS,
    CCVRES_MIN_OHMS,
    CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
    CRYO_DEFAULT_RESISTANCE_OHMS,
)
from formslab.devices.cryocooler.pico_i2c import I2CTransportError, PicoI2C, resolve_serial_port


class CryoBoard:
    def __init__(self, label="cryo_board", config_path=None, transport=None):
        self.config = self.get_config(label, config_path)
        self.label = label
        self.baudrate = int(self.config.get("baud", 115200))
        self.boot_wait = float(self.config.get("boot_wait", 2.0))
        self.firmware_file = self.config.get("firmware_file", "pico_board_control.py")
        i2c_config = self.config.get("i2c", {})
        self.scl_pin = int(i2c_config.get("scl_pin", 22))
        self.sda_pin = int(i2c_config.get("sda_pin", 23))
        self.i2c_freq = int(i2c_config.get("freq", 200000))
        # "pullup": "internal" -- the Pico's own pull-ups, for a board without its resistors
        self.i2c_pull = str(i2c_config.get("pullup", "board")).lower() == "internal"
        self.device_path = None
        self.transport = transport
        self.state = {
            "connected": False,
            "enabled": False,
            "output_voltage_v": None,
            "resistance_ohms": None,
            "resistance_code": None,
        }

    @staticmethod
    def get_config(name, path=None):
        config_path = (
            Path(path).resolve()
            if path
            else usbmap_path()
        )
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        if name not in config:
            raise ValueError(f"Device '{name}' not found in USB map")
        return config[name]

    def _firmware_path(self):
        path = Path(self.firmware_file)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        if not path.exists():
            raise FileNotFoundError(f"Firmware file not found: {path}")
        return path

    def _resolve_device_path(self):
        try:
            return resolve_serial_port(
                interface_id=self.config.get("interface_id") or self.config.get("port"),
                vendor_id=self.config.get("vendor_id"),
                product_id=self.config.get("product_id"),
                serial_number=self.config.get("serial_number"),
                explicit=self.config.get("port_windows"),
            )
        except I2CTransportError as exc:
            raise RuntimeError(f"Could not resolve {self.label} interface: {exc}") from exc

    @staticmethod
    def _normalize_voltage(voltage):
        value = float(voltage)
        if not CCV_MIN_V <= value <= CCV_MAX_V:
            raise ValueError(
                f"Cryocooler output voltage must be between {CCV_MIN_V:g} V "
                f"and {CCV_MAX_V:g} V"
            )
        return value

    @staticmethod
    def _normalize_resistance(resistance):
        value = float(resistance)
        if not CCVRES_MIN_OHMS <= value <= CCVRES_MAX_OHMS:
            raise ValueError(
                f"Cryocooler resistance must be between {CCVRES_MIN_OHMS:g} "
                f"and {CCVRES_MAX_OHMS:g} ohms"
            )
        return value

    # -- link -------------------------------------------------------------

    def open(self):
        if self.transport is None:
            self.device_path = self._resolve_device_path()
            self.transport = PicoI2C(
                self.device_path,
                baudrate=self.baudrate,
                boot_wait=self.boot_wait,
                scl_pin=self.scl_pin,
                sda_pin=self.sda_pin,
                freq=self.i2c_freq,
                firmware_path=self._firmware_path(),
                pull=self.i2c_pull,
            )
        self.transport.open()
        self.state["connected"] = True
        self.state["port"] = self.device_path
        return self.transport

    def close(self):
        if self.transport is not None:
            try:
                self.transport.close()
            finally:
                self.state["connected"] = False
                self.state["port"] = None
        self.transport = None

    def deploy_firmware(self):
        self.open()
        self.transport.deploy_firmware()

    def _bus(self):
        if self.transport is None:
            self.open()
        return self.transport

    # -- diagnostics ------------------------------------------------------

    def scan(self):
        """
        Report the addresses answering on the board's I2C bus.

        A healthy board with more than roughly 15 V on its input returns::

            ['0x18', '0x74']

        This is a communication check only. It says nothing about whether the
        converter can drive an output -- that needs roughly 20 V in, and is
        reported by :meth:`read_status`.
        """
        return list(self._bus().scan())

    def present(self):
        """
        Return which of the two expected devices answered, and what the scan
        result means -- see :func:`cryo_registers.interpret_scan`. A scan that
        reports a hundred devices is a wiring fault, not a hundred devices.
        """
        found = regs.normalize_addresses(self.scan())
        verdict, note = regs.interpret_scan(found)
        return {
            "addresses": [regs.format_address(addr) for addr in found],
            "converter": regs.VCONV_ADDR in found,
            "digipot": regs.DIGIPOT_ADDR in found,
            "bus": verdict,
            "note": note,
        }

    def read_status(self):
        """
        Read and decode the converter's STATUS register.

        Returns the flag dict from :func:`cryo_registers.decode_status`, which
        distinguishes a latched protection fault (SC/OCP/OVP) from a converter
        that is simply not producing a good output yet.
        """
        raw = self._bus().read_register(regs.VCONV_ADDR, regs.STATUS)
        return regs.decode_status(raw[0])

    def read_registers(self):
        """Dump both devices' registers, for bring-up and fault diagnosis."""
        bus = self._bus()
        converter = {}
        for reg in regs.CONVERTER_REGISTERS:
            value = bus.read_register(regs.VCONV_ADDR, reg)[0]
            converter[regs.CONVERTER_REGISTER_NAMES[reg]] = value
        digipot = {}
        for reg in regs.DIGIPOT_REGISTERS:
            value = bus.read_register(regs.DIGIPOT_ADDR, reg)[0]
            digipot[regs.DIGIPOT_REGISTER_NAMES[reg]] = value
        return {"converter": converter, "digipot": digipot}

    # -- board control ----------------------------------------------------

    def disable_current_limit(self):
        """Clear the converter's output current-limit enable bit."""
        self._bus().write_register(
            regs.VCONV_ADDR, regs.IOUT_LIMIT, regs.IOUT_LIMIT_DISABLED
        )

    def _write_output_voltage(self, volts):
        code = regs.output_dac_code(volts)
        lsb, msb = regs.ref_register_bytes(code)
        bus = self._bus()
        # REF_L and REF_M go out as one burst; the converter auto-increments.
        bus.write_register(regs.VCONV_ADDR, regs.REF_L, [lsb, msb])
        bus.write_register(regs.VCONV_ADDR, regs.VOUT_FS, regs.FEEDBACK_INDEX)
        self.state["output_voltage_v"] = float(volts)
        self.state["output_dac_code"] = code
        return code

    def _converter_on(self):
        """The converter's own output-enable bit (MODE bit 7)."""
        mode = self._bus().read_register(regs.VCONV_ADDR, regs.MODE)[0]
        return bool(mode & regs.MODE_OE)

    def _write_resistance(self, ohms):
        code = regs.ccvres_code_from_ohms(ohms)
        self._bus().write_register(regs.DIGIPOT_ADDR, regs.REG0, code)
        self.state["resistance_code"] = code
        self.state["resistance_ohms"] = regs.ccvres_ohms_from_code(code)
        return code

    def _write_enabled(self, enabled):
        bus = self._bus()
        sequence = (
            regs.OUTPUT_ENABLE_SEQUENCE if enabled else regs.OUTPUT_DISABLE_SEQUENCE
        )
        for register, value in sequence:
            bus.write_register(regs.VCONV_ADDR, register, value)
        self.state["enabled"] = bool(enabled)

    def set_output_voltage_raw(self, code):
        """Programme the reference DAC directly, bypassing the calibration."""
        code = regs.clamp_dac_code(code)
        lsb, msb = regs.ref_register_bytes(code)
        bus = self._bus()
        bus.write_register(regs.VCONV_ADDR, regs.REF_L, [lsb, msb])
        bus.write_register(regs.VCONV_ADDR, regs.VOUT_FS, regs.FEEDBACK_INDEX)
        self.state["output_dac_code"] = code
        self.state["output_voltage_v"] = round(regs.output_from_dac_code(code), 3)
        return code

    def set_resistance_code(self, code):
        """Programme the digipot wiper directly, bypassing the fit."""
        code = regs.clamp_ccvres_code(code)
        self._bus().write_register(regs.DIGIPOT_ADDR, regs.REG0, code)
        self.state["resistance_code"] = code
        self.state["resistance_ohms"] = regs.ccvres_ohms_from_code(code)
        return self.status()

    def initialize(
        self,
        voltage=CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
        resistance=CRYO_DEFAULT_RESISTANCE_OHMS,
        enabled=False,
    ):
        """
        Deploy the bridge firmware and programme the board, output off.

        The output is left disabled unless ``enabled=True`` is passed
        explicitly, so bringing the subsystem up never energises a cryocooler
        by itself.
        """
        voltage = self._normalize_voltage(voltage)
        resistance = self._normalize_resistance(resistance)
        self.deploy_firmware()
        self.disable_current_limit()
        self._write_enabled(False)
        self._write_resistance(resistance)
        self._write_output_voltage(voltage)
        if enabled:
            self._write_enabled(True)
        return self.status()

    def update(self, voltage=None, resistance=None, enabled=None):
        """
        Apply any combination of voltage, resistance and enable state.

        A resistance change on a live output drops the output first and
        restores it afterwards if it was requested to stay on, so the wiper
        never moves under load.

        On and off are decided from the converter, not from what was last
        commanded: an output the converter turned off by itself (a trip, or a
        reset that also lost its settings) is turned on again by `enabled=True`,
        with its voltage and current limit written again first.
        """
        if voltage is None and resistance is None and enabled is None:
            return self.status()

        current_enabled = bool(self.state.get("enabled", False))
        if enabled is not None:
            current_enabled = self._converter_on()
            if enabled and not current_enabled and self.state.get("enabled"):
                # Commanded on, but off on the converter: it may have reset, so
                # what it should hold goes back before the output does.
                self.disable_current_limit()
                if voltage is None and self.state.get("output_voltage_v") is not None:
                    voltage = self.state["output_voltage_v"]
        target_enabled = current_enabled if enabled is None else bool(enabled)

        if resistance is not None:
            resistance = self._normalize_resistance(resistance)
        if voltage is not None:
            voltage = self._normalize_voltage(voltage)

        if resistance is not None and current_enabled:
            self._write_enabled(False)
            current_enabled = False

        if resistance is not None:
            self._write_resistance(resistance)

        if voltage is not None:
            self._write_output_voltage(voltage)

        if target_enabled != current_enabled:
            self._write_enabled(target_enabled)

        return self.status()

    def status(self, probe=True):
        """
        Report both halves of board health.

        Communication health -- ``connected``, ``i2c_devices``,
        ``converter_present``, ``digipot_present`` -- comes from an I2C scan.
        Output health comes from the converter itself: ``output_on`` is its own
        output-enable bit (MODE), ``conversion`` its operating mode and
        ``faulted``/``faults`` its latched protection flags (STATUS);
        ``output_healthy`` is the output on with no fault. The converter reports
        no power-good, so whether the voltage is right takes a meter. They are
        separate on purpose: between roughly 15 V and 20 V of board input the
        devices answer on the bus while the converter still cannot drive an output.

        Transport failures propagate; a device that simply does not answer is
        reported in the returned dict.
        """
        if probe:
            bus = self._bus()
            self.state["connected"] = True
            if self.device_path:
                self.state["port"] = self.device_path
            found = regs.normalize_addresses(bus.scan())
            verdict, note = regs.interpret_scan(found)
            self.state["i2c_devices"] = [regs.format_address(addr) for addr in found]
            self.state["converter_present"] = regs.VCONV_ADDR in found
            self.state["digipot_present"] = regs.DIGIPOT_ADDR in found
            self.state["bus"] = verdict
            self.state["bus_note"] = note
            # On a stuck bus every address "answers", the converter included.
            # Reading STATUS there would return noise dressed up as telemetry.
            if self.state["converter_present"] and verdict != "sda_stuck_low":
                raw = bus.read_register(regs.VCONV_ADDR, regs.STATUS)[0]
                mode = bus.read_register(regs.VCONV_ADDR, regs.MODE)[0]
                flags = regs.decode_status(raw)
                output_on = bool(mode & regs.MODE_OE)
                self.state.update(
                    {
                        "status_raw": flags["raw"],
                        "output_on": output_on,
                        "conversion": flags["mode"],
                        "faulted": flags["faulted"],
                        "faults": regs.status_faults(raw),
                        "output_healthy": output_on and flags["healthy"],
                    }
                )
            else:
                self.state.update(
                    {
                        "status_raw": None,
                        "output_on": None,
                        "conversion": None,
                        "faulted": None,
                        "faults": [],
                        "output_healthy": False,
                    }
                )
        return dict(self.state)

    def set_output_voltage(self, volts):
        return self.update(voltage=volts)

    def set_resistance(self, ohms):
        return self.update(resistance=ohms)

    def enable_output(self):
        return self.update(enabled=True)

    def disable_output(self):
        """
        Drop the converter output.

        Unlike the other setters this never consults cached state: a fresh
        CryoBoard believes the output is off, so a state-aware disable would
        be a no-op against a board that is actually running. A disable must
        always reach the hardware.
        """
        self._write_enabled(False)
        return self.status()

    def shutdown(self, close_transport=True):
        """
        Disable the cryocooler output and optionally release the serial link.

        Safe to call more than once; intended for end-of-run cleanup.
        """
        try:
            if self.transport is not None:
                self._write_enabled(False)
        finally:
            self.state["enabled"] = False
            if close_transport:
                self.close()
        return dict(self.state)
