# # local pull only
# import json
# from datetime import datetime
# from pathlib import Path
# import serial

# CHANNELS = [f"ch{i}" for i in range(16)]

# class RTD16:
#     def __init__(self, label, usbmap=None):
#         if usbmap is None:
#             usbmap = usbmap_path()

#         with usbmap.open("r") as f:
#             cfg = json.load(f)[label]

#         self.label = label
#         self.board_id = cfg["board_id"]
#         self.serial_port = cfg.get("resource") or cfg.get("port")
#         self.baudrate = cfg.get("baud", 115200)  # default fallback

#         self.influx_host = "http://monitor.silicon.cf:8086"
#         self.influx_db = "multichtemp"
#         self.influx_user = "iot"
#         self.influx_pass = "iotcloud"
#         self.measurement = "temperature"

#         self.temps = {ch: float("nan") for ch in CHANNELS}
#         self.times = {ch: None for ch in CHANNELS}

#         self.ser = None  # <-- persistent serial handle

#     def resolve_usb_device(self, port_fragment):
#         import subprocess
#         candidates = list(Path("/dev").glob("ttyACM*")) + list(Path("/dev").glob("ttyUSB*"))
#         for dev in candidates:
#             try:
#                 out = subprocess.check_output(
#                     ["udevadm", "info", "-q", "path", "-n", str(dev)],
#                     text=True
#                 )
#                 if port_fragment in out:
#                     return str(dev)
#             except Exception:
#                 continue
#         raise RuntimeError(f"Could not resolve port fragment '{port_fragment}' to /dev/ttyXXX")

#     def open(self):
#         """Open the serial connection if not already open."""
#         if not self.serial_port:
#             raise RuntimeError(f"No serial 'port' defined for {self.label} in usbmap.json")

#         dev_path = self.serial_port
#         if not dev_path.startswith("/dev"):
#             dev_path = self.resolve_usb_device(self.serial_port)

#         if self.ser is None or not self.ser.is_open:
#             self.ser = serial.Serial(dev_path, baudrate=self.baudrate, timeout=2)

#     def close(self):
#         """Cleanly close the serial connection."""
#         if self.ser and self.ser.is_open:
#             self.ser.close()
#             self.ser = None

#     def read_serial(self):
#         """Read temperatures via the serial link."""
#         self.open()
#         try:
#             self.ser.write(b"GET\n")
#             response = ""
#             while True:
#                 line = self.ser.readline().decode().strip()
#                 if not line:
#                     break
#                 if line.startswith("DATA:"):
#                     response = line[5:]
#                     break

#             if response:
#                 values = [float(v) for v in response.split(",")]
#                 for ch, val in zip(CHANNELS, values):
#                     self.temps[ch] = val
#                     self.times[ch] = datetime.utcnow()
#                 return self.temps
#             else:
#                 raise RuntimeError("No DATA line received over serial.")

#         except Exception as e:
#             raise RuntimeError(f"Serial read failed: {e}")

import json
import time
import logging
from datetime import datetime
from pathlib import Path

from formslab.config import usbmap_path
import serial
import subprocess
import traceback

CHANNELS = [f"ch{i}" for i in range(16)]
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class RTD16:
    def __init__(self, label, usbmap=None):
        if usbmap is None:
            usbmap = usbmap_path()

        with usbmap.open("r") as f:
            self.cfg = json.load(f)[label]

        self.label = label
        self.board_id = self.cfg["board_id"]
        self.serial_port = self.cfg.get("resource") or self.cfg.get("port")
        self.baudrate = self.cfg.get("baud", 115200)
        self.device_path = None

        self.temps = {ch: float("nan") for ch in CHANNELS}
        self.times = {ch: None for ch in CHANNELS}
        self.ser = None
        self.read_counter = 0

    def _resolve_by_path_symlink(self, target):
        by_path = Path("/dev/serial/by-path")
        if not by_path.exists():
            return None

        for entry in sorted(by_path.iterdir()):
            try:
                if target in entry.name:
                    return str(entry.resolve())
            except OSError:
                continue
        return None

    def _resolve_by_sysfs(self, target):
        tty_paths = sorted(Path("/sys/class/tty").glob("ttyACM*"))
        tty_paths += sorted(Path("/sys/class/tty").glob("ttyUSB*"))

        for tty in tty_paths:
            try:
                if target in str((tty / "device").resolve()):
                    return str(Path("/dev") / tty.name)
            except OSError:
                continue
        return None

    def _resolve_by_udev(self, target):
        candidates = sorted(Path("/dev").glob("ttyACM*")) + sorted(Path("/dev").glob("ttyUSB*"))
        for dev in candidates:
            try:
                out = subprocess.check_output(
                    ["udevadm", "info", "-q", "path", "-n", str(dev)],
                    text=True,
                )
                if target in out:
                    return str(dev)
            except Exception:
                continue
        return None

    def resolve_usb_device(self, port_fragment=None):
        if (port_fragment and str(port_fragment).upper().startswith("COM")
                and str(port_fragment)[3:].isdigit()):
            return str(port_fragment)
        if port_fragment and str(port_fragment).startswith("/dev"):
            return str(port_fragment)

        targets = []
        interface_id = self.cfg.get("interface_id")
        if interface_id:
            targets.append(interface_id)

        port_value = port_fragment or self.cfg.get("port")
        if port_value:
            targets.append(str(port_value))

        hub_location = self.cfg.get("hub_location")
        hub_port = self.cfg.get("hub_port")
        if hub_location and hub_port:
            targets.append(f"{hub_location}.{hub_port}")

        seen = set()
        for target in targets:
            if not target or target in seen:
                continue
            seen.add(target)

            for resolver in (
                self._resolve_by_path_symlink,
                self._resolve_by_sysfs,
                self._resolve_by_udev,
            ):
                dev_path = resolver(target)
                if dev_path:
                    return dev_path

        details = ", ".join(seen) if seen else repr(port_fragment)
        raise RuntimeError(f"Could not resolve RTD16 USB path for {details}")

    def open(self):
        if not self.serial_port:
            raise RuntimeError(f"No serial 'port' defined for {self.label} in usbmap.json")

        dev_path = self.serial_port
        if not dev_path.startswith("/dev"):
            dev_path = self.resolve_usb_device(dev_path)
        self.device_path = dev_path

        if self.ser is None or not self.ser.is_open:
            logging.info(f"Opening serial connection to {dev_path}")
            self.ser = serial.Serial(dev_path, baudrate=self.baudrate, timeout=2)

    def close(self):
        if self.ser and self.ser.is_open:
            logging.info("Closing serial connection")
            self.ser.close()
            self.ser = None

    def _do_read(self, max_retries=3):
        """
        Low-level attempt at reading data from RTD board.
        Will retry a few times if no valid DATA line is received.
        """

        for attempt in range(1, max_retries + 1):
            try:
                self.ser.reset_input_buffer()
                time.sleep(0.1)
                self.ser.write(b"GET\n")

                response = ""
                while True:
                    line = self.ser.readline().decode(errors="ignore").strip()
                    if not line:
                        logging.warning("RTD returned empty line (timeout?)")
                        break

                    logging.debug(f"RTD raw line (attempt {attempt}): '{line}'")

                    if line.startswith("DATA:"):
                        response = line[5:]
                        break

                if response:
                    values = [float(v) for v in response.split(",")]
                    for ch, val in zip(CHANNELS, values):
                        self.temps[ch] = val
                        self.times[ch] = datetime.utcnow()
                    return self.temps

                else:
                    logging.warning(f"No DATA line received (attempt {attempt}/{max_retries})")

            except Exception as e:
                logging.error(f"Exception in _do_read (attempt {attempt}): {e}", exc_info=True)

            # wait briefly before retrying
            time.sleep(0.5)

        # If we reach here, all attempts failed
        raise RuntimeError(f"No DATA line received after {max_retries} retries.")

    def read_serial(self):
        time.sleep(0.5)
        # Periodic forced reset to avoid stale handles
        if self.ser and self.ser.is_open:
            self.read_counter += 1
            if self.read_counter > 100:
                logging.info("Forcing periodic serial reconnect")
                self.close()
                self.read_counter = 0
        try:
            self.open()
            return self._do_read()
        except Exception as e:
            logging.warning(f"Read failed once: {e}")
            logging.debug(traceback.format_exc())

            # Retry
            self.close()
            time.sleep(1)
            try:
                self.device_path = None
                self.open()
                return self._do_read()
            except Exception as e2:
                logging.error("Retry failed", exc_info=True)  # full traceback
                self.close()
                raise
