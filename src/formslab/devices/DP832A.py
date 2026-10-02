"""Rigol DP832A power supply: transport drivers and the `PSU` API.

This is the only Rigol PSU module. The console's `psu` tab and the FORMS
routines (through `psu_service`) both drive supplies through `PSU` here.

Two transports sit under it, chosen per resource by `driver_for_resource`:

- `RigolDriverVISA` for native USB and LAN resources (`USB0::...`,
  `TCPIP::...`). pyvisa picks the VISA library itself: NI-VISA when it is
  installed (the Windows benches), otherwise pyvisa-py (Linux and the Pi).
  Set `PYVISA_LIBRARY=@ivi` or `@py` to force one.
- `RigolDriverSerial` for RS232 resources (`ASRLCOM3::INSTR`,
  `ASRL/dev/psu1::INSTR`, `/dev/ttyUSB0`), over pyserial on every platform, so
  a serial supply needs no VISA install at all. `FORMS_PSU_BACKEND=visa` sends
  RS232 through VISA instead.
"""

import os
import re
import threading
import time

from formslab.devices.psu_config import resource_for

CHANNELS = (1, 2, 3)

_FLOAT = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_float(text, default=None):
    """The first number in an instrument reply, or `default` if there is none."""
    match = _FLOAT.search(str(text))
    return float(match.group(0)) if match else default


def parse_floats(text):
    """Every number in an instrument reply, in order."""
    return [float(x) for x in _FLOAT.findall(str(text))]


# --- Transport drivers ------------------------------------------------------


class RigolDriver:
    """What `PSU` needs from a transport: write, query, clear, reconnect, close.

    Subclasses supply `_open`, `_query`, `write`, `_clear` and `close`. The
    retry loop and the identity check are shared here so the transports
    cannot drift apart.
    """

    interface = "UNKNOWN"

    def __init__(self, resource):
        self.resource = resource
        try:
            self._open()
            self._handshake()
        except Exception:
            self.close()
            raise

    def _handshake(self):
        self.write("SYSTEM:REMOTE")
        idn = self.query("*IDN?")
        if "RIGOL" not in idn:
            raise RuntimeError(f"Not a Rigol instrument at {self.resource}: {idn!r}")

    def query(self, cmd, retries=2):
        """Query, retrying once after a failure. The device clear comes only
        before a retry: on the DP832A a clear sent right after a write (e.g.
        `:INST:NSEL`) makes it drop the next reply, so clearing before every
        query cost a full VISA timeout (~6 s) per channel switch."""
        for attempt in range(retries):
            try:
                return self._query(cmd)
            except Exception:
                if attempt == retries - 1:
                    raise
                self.clear()          # flush what the failed attempt left behind
                time.sleep(0.1)

    def clear(self):
        """Flush stale input after a failed query. Best effort, never raises.

        Optional by design: pyvisa-py's USBTMC session (Linux, the Pi) does not
        implement a device clear, and treating that as fatal failed every
        query on the Pi with VI_ERROR_NSUP_OPER although `*IDN?` answered.
        """
        try:
            self._clear()
        except Exception:
            pass

    def reconnect(self):
        self.close()
        self._open()
        self._handshake()


class RigolDriverVISA(RigolDriver):
    """pyvisa transport, for native USB and LAN resources."""

    def __init__(self, resource):
        self.interface = self._interface_of(resource)
        self.rm = None
        self.inst = None
        super().__init__(resource)

    @staticmethod
    def _interface_of(resource):
        resource = resource.upper()
        if resource.startswith("ASRL"):
            return "RS232"
        if resource.startswith("USB"):
            return "USB"
        if resource.startswith("TCPIP"):
            return "LAN"
        return "UNKNOWN"

    def _open(self):
        import pyvisa

        if self.rm is None:
            self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.resource)
        self.inst.write_termination = "\n"
        self.inst.read_termination = "\n"
        self.inst.timeout = 5000

    def _query(self, cmd):
        return self.inst.query(cmd)

    def write(self, cmd):
        self.inst.write(cmd)
        if self.interface == "RS232":
            time.sleep(0.05)  # let the DP832A finish before the next command

    def _clear(self):
        if self.interface == "RS232":
            self._drain()
        else:
            self.inst.clear()

    def _drain(self):
        """Read and discard whatever is waiting on an RS232 line."""
        saved = self.inst.timeout
        self.inst.timeout = 50
        try:
            while True:
                self.inst.read_bytes(256)
        except Exception:
            pass
        finally:
            self.inst.timeout = saved

    def _close_instrument(self):
        try:
            if self.inst is not None:
                self.inst.close()
        except Exception:
            pass
        self.inst = None

    def _close_manager(self):
        try:
            if self.rm is not None:
                self.rm.close()
        except Exception:
            pass
        self.rm = None

    def reconnect(self):
        """Reopen the session. USB and LAN keep the ResourceManager; an RS232
        port gets a fresh one, as the old one keeps the COM port held."""
        self._close_instrument()
        if self.interface == "RS232":
            self._close_manager()
        self._open()
        self._handshake()

    def close(self):
        self._close_instrument()
        self._close_manager()


class RigolDriverSerial(RigolDriver):
    """pyserial transport, for RS232. Needs no VISA library."""

    interface = "RS232"

    def __init__(self, resource):
        self.device_path = self._parse_device_path(resource)
        self.ser = None
        super().__init__(resource)

    @staticmethod
    def _parse_device_path(resource):
        """The OS port name inside a VISA resource string, or the path itself.

        ASRLCOM3::INSTR        ->  COM3
        ASRL/dev/psu2::INSTR   ->  /dev/psu2
        /dev/ttyUSB0           ->  /dev/ttyUSB0
        """
        if resource.startswith("ASRL") and "::" in resource:
            return resource[4:resource.index("::")]
        if resource.startswith("/dev/"):
            return resource
        raise ValueError(f"Cannot parse device path from: {resource!r}")

    def _open(self):
        import serial

        self.ser = serial.Serial(self.device_path, baudrate=9600, timeout=5.0)

    def _query(self, cmd):
        self.ser.write(cmd.encode() + b"\n")
        # The DP832A echoes commands over RS232; skip the echo.
        for _ in range(3):
            line = self.ser.readline().decode(errors="ignore").strip()
            if not line:
                raise TimeoutError(f"No response to {cmd!r}")
            if line != cmd:
                return line
        raise TimeoutError(f"Only got echo for {cmd!r}")

    def write(self, cmd):
        self.ser.write(cmd.encode() + b"\n")
        time.sleep(0.05)  # let the DP832A finish before the next command

    def _clear(self):
        self.ser.reset_input_buffer()

    def close(self):
        try:
            if self.ser is not None and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass
        self.ser = None


def driver_for_resource(resource):
    """The transport class for a resource string.

    USB and LAN always go through VISA. RS232 uses pyserial unless
    `FORMS_PSU_BACKEND=visa` asks for VISA.
    """
    forced = os.environ.get("FORMS_PSU_BACKEND", "").strip().lower() == "visa"
    if forced or resource.upper().startswith(("USB", "TCPIP")):
        return RigolDriverVISA
    return RigolDriverSerial


# --- The supply -------------------------------------------------------------


class PSU:
    """A three-channel Rigol DP832A, addressed by its `usbmap.json` label.

    The session opens on first use, so building a `PSU` for a supply that is
    unplugged does not fail. `connect()` opens it explicitly (and reopens it if
    already open); `disconnect()` hands the front panel back to the operator.
    """

    def __init__(self, label):
        self.label = label
        self.resource = label if "::" in label else resource_for(label)
        self.driver = None
        self.state = {}

        self._last_channel = None
        self._lock = threading.Lock()
        self._time = 0.0
        self._cooldown = 3.0  # seconds between full status reads
        self._verbose_cooldown = os.environ.get(
            "FORMS_PSU_VERBOSE_COOLDOWN", ""
        ).strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def configure(cls, label):
        """A connected PSU with every channel off and at 0 V / 0 A."""
        psu = cls(label)
        for ch in CHANNELS:
            psu.off(ch)
            psu.set(ch, 0.0, 0.0)
        return psu

    # --- Session ---

    @property
    def connected(self):
        return self.driver is not None

    def connect(self):
        if self.driver is not None:
            self.disconnect()
        self.driver = driver_for_resource(self.resource)(self.resource)
        self._last_channel = None

    def disconnect(self):
        """Return the front panel to local and close the session."""
        if self.driver is None:
            return
        try:
            self.driver.write("SYSTEM:LOCAL")
        except Exception:
            pass
        self.driver.close()
        self.driver = None
        self._last_channel = None

    close = disconnect

    def reconnect(self):
        """Reopen a session that has stopped answering."""
        self._last_channel = None
        if self.driver is None:
            self.connect()
        else:
            self.driver.reconnect()

    def _ensure_connected(self):
        if self.driver is None:
            self.connect()

    # --- Commands ---

    def idn(self):
        self._ensure_connected()
        return self.driver.query("*IDN?")

    def _select(self, ch):
        self._ensure_connected()
        if self._last_channel != ch:
            self.driver.write(f":INST:NSEL {ch}")
            self._last_channel = ch
            time.sleep(0.05)

    def set(self, ch, v, c):
        self._select(ch)
        self.driver.write(f":APPL CH{ch},{v},{c}")

    def setOVCP(self, ch, ovp=None, ocp=None, enable=True):
        self._select(ch)
        state = "ON" if enable else "OFF"
        ovp = ovp if ovp is not None else 0.001
        ocp = ocp if ocp is not None else 0.001
        self.driver.write(f":VOLT:PROT {ovp}")
        self.driver.write(f":VOLT:PROT:STAT CH{ch},{state}")
        self.driver.write(f":CURR:PROT {ocp}")
        self.driver.write(f":CURR:PROT:STAT CH{ch},{state}")

    def on(self, ch):
        self._ensure_connected()
        self.driver.write(f":OUTP CH{ch},ON")

    def off(self, ch):
        self._ensure_connected()
        self.driver.write(f":OUTP CH{ch},OFF")

    def alloff(self):
        for ch in CHANNELS:
            self.off(ch)

    def shutdown(self):
        """Turn every channel off and set it to 0 V / 0 A.

        Returns False instead of raising, so a routine's shutdown path can log
        the failure and carry on. The session stays open; call `disconnect()`
        to release the front panel.
        """
        try:
            for ch in CHANNELS:
                self.off(ch)
                self.set(ch, 0.0, 0.0)
            return True
        except Exception as e:
            print(f"[{self.label}] shutdown failed: {e}")
            return False

    # --- Readback ---

    def read(self, ch):
        """The programmed (voltage, current) for a channel."""
        self._select(ch)
        parts = [t for t in self.driver.query(":APPL?").split(",") if t.strip()]
        return parse_float(parts[0]), parse_float(parts[1])

    def measure_all(self, ch, retries=3, settle=0.05):
        """Measured (V, I, P) for a channel, or (None, None, None) on failure.

        One `:MEAS:ALL?` rather than separate voltage and current queries, so
        the three values describe the same instant.
        """
        last_exc = None
        for _ in range(retries):
            try:
                self._select(ch)
                time.sleep(settle)
                resp = self.driver.query(f":MEAS:ALL? CH{ch}")
                vals = parse_floats(resp)[:3]
                if len(vals) < 2:
                    raise ValueError(f"unparseable reply {resp!r}")
                if len(vals) == 2:
                    vals.append(vals[0] * vals[1])
                return tuple(vals)
            except Exception as e:
                last_exc = e
                if self.driver is not None:
                    self.driver.clear()
                time.sleep(0.1)
        print(f"[{self.label}] MEAS:ALL failed on CH{ch}: {last_exc}")
        return None, None, None

    def measure(self, ch, retries=3, settle=0.05):
        """Measured (V, I) for a channel, or (None, None) on failure."""
        v, i, _ = self.measure_all(ch, retries=retries, settle=settle)
        return v, i

    def _query_channel(self, ch):
        """One channel's output state, setpoints and measurements."""
        self._select(ch)
        # Name the channel: a bare `:OUTP?` gets no reply on this DP832A (firmware
        # 00.01.19) until the VISA timeout and a retry -- ~6 s per channel.
        is_on = self.driver.query(f":OUTP? CH{ch}").strip().upper() in {"1", "ON"}

        vset = cset = None
        for attempt in range(3):
            raw = self.driver.query(":APPL?")
            parts = [s.strip() for s in raw.split(",") if s.strip()]
            if raw.strip().upper() not in {"ON", "OFF"} and len(parts) >= 2:
                vset = parse_float(parts[0])
                cset = parse_float(parts[1])
                if vset is not None and cset is not None:
                    break
            if attempt == 2:
                raise ValueError(f"malformed :APPL? reply for CH{ch}: {raw!r}")
            self.driver.clear()
            time.sleep(0.1)

        return {
            "on": is_on,
            "vset": vset,
            "cset": cset,
            "vmeas": parse_float(self.driver.query(":MEAS:VOLT?")),
            "cmeas": parse_float(self.driver.query(":MEAS:CURR?")),
        }

    def update(self, channels=CHANNELS):
        """Refresh `self.state` from the instrument, at most once per cooldown."""
        self._ensure_connected()
        with self._lock:
            wait = self._time + self._cooldown - time.time()
            if wait > 0:
                if self._verbose_cooldown:
                    print(f"[{self.label}] Waiting {wait:.2f}s for update cooldown...")
                time.sleep(wait)

            for ch in channels:
                try:
                    self.state[ch] = self._query_channel(ch)
                except Exception as e:
                    print(f"[{self.label}] CH{ch} query failed: {e}")
                    self.state[ch] = dict.fromkeys(("on", "vset", "cset", "vmeas", "cmeas"))
                time.sleep(0.05)

            self._time = time.time()

    def status(self) -> dict:
        self.update()
        return self.state.copy()

    # --- Helpers ---

    @staticmethod
    def safe_lt(a, b):
        """a < b, and False when `a` is not a number (a failed reading)."""
        return isinstance(a, (int, float)) and a < b

    @staticmethod
    def safe_gt(a, b):
        """a > b, and False when `a` is not a number (a failed reading)."""
        return isinstance(a, (int, float)) and a > b
