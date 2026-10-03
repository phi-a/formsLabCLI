"""
PC-side I2C transport for the cryocooler control board.

The FlatSat PC has no native I2C bus and there is no USB-I2C dongle on this
bench. The bridge is a Raspberry Pi Pico (USB 2e8a:0005) running MicroPython,
driven from the PC over USB CDC with ``mpremote``; the board's own
documentation (``docs/Cyrocooler_board_docs.pdf``) describes exactly this
arrangement. ``lab/pico_board_control.py`` is the firmware it deploys.

This module is the seam. Everything above it -- ``lab/CryoBoard.py`` and the
encodings in ``lab/cryo_registers.py`` -- speaks only the three operations
below, so dropping in a native USB-I2C adapter later means writing one more
class with ``scan``/``read_register``/``write_register`` and nothing else
changes.

``mpremote`` is imported lazily, inside :meth:`PicoI2C.open`, so the modules
above can be imported and tested on a machine that has no lab extras
installed.
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path


DEFAULT_FIRMWARE = Path(__file__).resolve().parent / "pico_board_control.py"

# Framing emitted by the firmware's ``emit()`` helper. The firmware also
# prints nothing else, but a MicroPython traceback or a stray boot banner can
# share the stream, so results are matched rather than assumed.
RESULT_PREFIX = "RESULT:"


class I2CTransportError(RuntimeError):
    """Raised when the bridge itself fails -- not when a device is silent."""


def resolve_serial_port(
    interface_id=None,
    vendor_id=None,
    product_id=None,
    serial_number=None,
    explicit=None,
):
    """
    Find the serial device node for the USB-I2C bridge.

    Every path here either identifies the bridge positively or raises. It
    never falls back to "the first serial port that looks about right":
    writing converter registers over the wrong device node is worse than not
    connecting at all.

    Resolution order:

    1. ``explicit`` -- a port named outright in the USB map (``port_windows``),
       used unchanged.
    2. Linux -- walk ``/sys/class/tty`` for the USB ``interface_id``, which
       pins the bridge to a physical hub port.
    3. Windows -- match USB vendor/product id through pyserial, narrowed by
       ``serial_number`` when the map supplies one. Windows has no stable
       by-path equivalent, so an ambiguous match raises and lists what it
       found rather than picking one. This matters here: the RTD boards are
       the same 2e8a:0005 Pico as the cryo bridge.
    """
    if explicit:
        return str(explicit)

    if sys.platform.startswith("win"):
        return _resolve_windows(vendor_id, product_id, serial_number)

    if not interface_id:
        raise I2CTransportError("No USB interface id configured for the cryo bridge")
    candidates = sorted(Path("/sys/class/tty").glob("ttyACM*"))
    candidates += sorted(Path("/sys/class/tty").glob("ttyUSB*"))
    for tty in candidates:
        try:
            if interface_id in str((tty / "device").resolve()):
                return str(Path("/dev") / tty.name)
        except OSError:
            continue
    raise I2CTransportError(f"Could not resolve USB-I2C bridge interface {interface_id}")


def _resolve_windows(vendor_id, product_id, serial_number):
    try:
        from serial.tools import list_ports
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise I2CTransportError(
            "pyserial is required to resolve the cryo bridge on Windows; "
            "install the 'lab' extra"
        ) from exc

    if vendor_id is None or product_id is None:
        raise I2CTransportError(
            "No vendor_id/product_id configured for the cryo bridge; Windows "
            "cannot resolve it by hub path. Set 'port_windows' in usbmap.json."
        )
    want_vid = int(str(vendor_id), 16)
    want_pid = int(str(product_id), 16)

    matches = [
        port
        for port in list_ports.comports()
        if port.vid == want_vid and port.pid == want_pid
    ]
    if serial_number:
        matches = [
            port
            for port in matches
            if (port.serial_number or "").upper() == str(serial_number).upper()
        ]

    if not matches:
        detail = f"{want_vid:04x}:{want_pid:04x}"
        if serial_number:
            detail += f" serial {serial_number}"
        raise I2CTransportError(f"No USB-I2C bridge found matching {detail}")
    if len(matches) > 1:
        found = ", ".join(
            f"{port.device} (serial {port.serial_number})" for port in matches
        )
        raise I2CTransportError(
            f"Several {want_vid:04x}:{want_pid:04x} devices are attached: {found}. "
            "Pin the bridge with 'serial_number' or 'port_windows' in usbmap.json."
        )
    return matches[0].device


class PicoI2C:
    """
    An I2C bus reached through the MicroPython bridge.

    Constructing this object opens nothing and writes nothing. The serial link
    is established on first use, or explicitly through :meth:`open`.
    """

    def __init__(
        self,
        device_path,
        baudrate=115200,
        boot_wait=2.0,
        scl_pin=22,
        sda_pin=23,
        freq=200000,
        firmware_path=None,
    ):
        self.device_path = device_path
        self.baudrate = int(baudrate)
        self.boot_wait = float(boot_wait)
        self.scl_pin = int(scl_pin)
        self.sda_pin = int(sda_pin)
        self.freq = int(freq)
        self.firmware_path = Path(firmware_path) if firmware_path else DEFAULT_FIRMWARE
        self._transport = None
        self._deployed = False

    # -- link -------------------------------------------------------------

    @property
    def is_open(self):
        return self._transport is not None

    def open(self, soft_reset=False):
        if self._transport is None:
            try:
                from mpremote.transport_serial import SerialTransport
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise I2CTransportError(
                    "mpremote is required to reach the cryocooler board's USB-I2C "
                    "bridge; install the 'lab' extra"
                ) from exc
            self._transport = SerialTransport(
                self.device_path, baudrate=self.baudrate, wait=0
            )
            time.sleep(self.boot_wait)
        self._transport.enter_raw_repl(soft_reset=soft_reset)
        return self._transport

    def close(self):
        transport, self._transport = self._transport, None
        self._deployed = False
        if transport is not None:
            transport.close()

    def deploy_firmware(self):
        """Copy the bridge firmware onto the Pico and bind the configured pins."""
        if not self.firmware_path.exists():
            raise I2CTransportError(f"Bridge firmware not found: {self.firmware_path}")
        self.open(soft_reset=True)
        self._transport.fs_writefile("main.py", self.firmware_path.read_bytes())
        time.sleep(0.2)
        self._deployed = False
        self._configure()

    def _configure(self):
        """Bind the bus to the configured pins. Reads and writes nothing."""
        self._call(
            f"main.configure(scl={self.scl_pin}, sda={self.sda_pin}, freq={self.freq})"
        )
        self._deployed = True

    def _ensure_ready(self):
        """
        Make sure the bridge has firmware and the right pins bound.

        A bridge that has never been flashed cannot answer ``main.configure``,
        so the firmware is deployed on demand. That is safe to do at any time:
        the firmware writes to no device on import, so recovering a blank
        bridge cannot disturb the cryocooler.
        """
        if self._deployed:
            return
        self.open()
        try:
            self._configure()
        except Exception:
            self.deploy_firmware()

    # -- framed calls -----------------------------------------------------

    def _call(self, expression):
        """Evaluate ``expression`` on the bridge and return its Python value."""
        self.open()
        script = f"import main\nmain.emit({expression})\n"
        raw = self._transport.exec(script).decode("utf-8", errors="ignore")
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith(RESULT_PREFIX):
                return ast.literal_eval(line[len(RESULT_PREFIX) :].strip())
        raise I2CTransportError(
            f"No result from USB-I2C bridge for {expression!r}: {raw.strip()!r}"
        )

    # -- bus operations ---------------------------------------------------

    def scan(self):
        """Return responding addresses as ``["0x18", "0x74"]``."""
        self._ensure_ready()
        return list(self._call("main.scan()"))

    def read_register(self, address, register, nbytes=1):
        """Return ``nbytes`` from ``register`` on ``address`` as a bytes object."""
        self._ensure_ready()
        data = self._call(f"main.read({address:#04x}, {register:#04x}, {int(nbytes)})")
        return bytes(data)

    def write_register(self, address, register, data):
        """Write an int or sequence of ints to ``register`` on ``address``."""
        self._ensure_ready()
        payload = [int(data) & 0xFF] if isinstance(data, int) else [
            int(value) & 0xFF for value in data
        ]
        return self._call(f"main.write({address:#04x}, {register:#04x}, {payload!r})")

    def bridge_config(self):
        """Return the pin/frequency configuration the bridge is bound to."""
        self._ensure_ready()
        return dict(self._call("main.config()"))
