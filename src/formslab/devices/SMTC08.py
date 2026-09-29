
from pymodbus.client import ModbusSerialClient
from pathlib import Path
import inspect
import sys

from formslab.config import usbmap_path
import subprocess
import os
import time
import json

# pymodbus 3.10 renamed the per-request unit keyword from `slave` to
# `device_id`; the dependency is unpinned, so ask the installed version.
_UNIT_KW = ("device_id"
            if "device_id" in inspect.signature(ModbusSerialClient.read_input_registers).parameters
            else "slave")


def _s16(val):
    """Registers are unsigned on the wire; temperatures and mV are signed."""
    return val - 65536 if val > 32767 else val


class SMTC08:
    """
    Class for interfacing with the Sequent Microsystems 8-Thermocouple DAQ via MODBUS RTU over RS485.

    Parameters:
    -----------
    port : str
        Serial port name (e.g., 'COM7' on Windows, '/dev/ttyUSB0' on Linux)
    slave : int
        MODBUS slave address of the board (default: 1)
    baudrate : int
        Baudrate for serial communication (default: 9600)

    Notes:
    ------
    - Temperature readings are in tenths of degrees Celsius.
    - Thermocouple type codes:
        0=B, 1=E, 2=J, 3=K, 4=N, 5=R, 6=S, 7=T (default recommended: 7 for Type T)
    """

    def __init__(self, label="SMTC08", slave=1, config_path=None, port=None):
        self.hubmap = self.get_config(label, config_path)

        # Resolve port: explicit override, then 'resource_windows' on Windows,
        # then 'resource', then the udev 'port' fragment
        if port is None and sys.platform == "win32":
            port = self.hubmap.get("resource_windows")
        port = port or self.hubmap.get("resource")
        if port and (port.startswith("/dev/") or
                     (port.upper().startswith("COM") and port[3:].isdigit())):
            self.port = port
        else:
            port_fragment = self.hubmap.get("port")
            if port_fragment:
                self.port = self.resolve_usb_port_path(port_fragment)
            else:
                raise RuntimeError(f"✗ No 'resource' or 'port' defined in usbmap for {label}")

        baudrate = self.hubmap.get("baud", 9600)
        self.slave = slave
        self.client = ModbusSerialClient(
            port=self.port,
            baudrate=baudrate,
            stopbits=1,
            bytesize=8,
            parity='N',
            timeout=1
        )
        if not self.client.connect():
            raise IOError(f"Could not connect to port {self.port}")
        
    @staticmethod
    def get_config(name, path=None):
        if path is None:
            path = usbmap_path()
        else:
            path = Path(path).resolve()

        if not path.exists():
            raise FileNotFoundError(f"USB map not found: {path}")
        
        with path.open("r") as f:
            config = json.load(f)
        
        if name not in config:
            raise ValueError(f"Device '{name}' not found in USB map.")
        
        return config[name]

    def _read_input(self, address, count):
        return self.client.read_input_registers(
            address=address, count=count, **{_UNIT_KW: self.slave})

    def read_temp(self, channel):
        """
        Read temperature from a single channel.

        Parameters:
        -----------
        channel : int
            Channel number (1–8)

        Returns:
        --------
        float
            Temperature in °C
        """
        if not 1 <= channel <= 8:
            raise ValueError("Channel must be 1–8")
        reg_addr = channel - 1
        result = self._read_input(reg_addr, 1)
        if result.isError():
            raise IOError(f"MODBUS error reading channel {channel}: {result}")
        return _s16(result.registers[0]) / 10.0

    def read_all(self):
        """
        Read all 8 thermocouple channels.

        Returns:
        --------
        list of float
            List of 8 temperatures in °C
        """
        result = self._read_input(0, 8)
        if result.isError():
            raise IOError(f"MODBUS error reading all channels: {result}")
        return [_s16(val) / 10.0 for val in result.registers]  # 0.1 °C

    def read_all_mv(self):
        """
        Read all 8 thermocouple channels in millivolts via MODBUS.

        Returns:
        --------
        list of float
            List of 8 thermocouple voltages in mV
        """
        result = self._read_input(8, 8)
        if result.isError():
            raise IOError(f"MODBUS error reading millivolt registers: {result}")
        return [_s16(val) / 1000.0 for val in result.registers]  # µV → mV
    
    def read_all_polytemp(self):
        """
        Read all thermocouple voltages and convert to °C using NIST Type T polynomial.

        Returns:
        --------
        list of float
            List of converted temperatures from mV
        """
        def type_t_mv_to_c(mv):
            if mv < 0:
                c = [0.0, 25.173462, -1.1662878, -1.0833638,
                    -0.8977354, -0.37342377, -0.086632643,
                    -0.010450598, -0.00051920577]
            else:
                c = [0.0, 25.08355, 0.07860106, -0.2503131e-3,
                    0.0831527e-4, -0.01228034e-6, 0.0009804036e-8,
                    -0.0000441303e-10, 0.0000115924e-13, -0.0000013309e-16]
            return sum(c[i] * mv**i for i in range(len(c)))

        mvs = self.read_all_mv()
        return [type_t_mv_to_c(mv) for mv in mvs]

    def close(self):
        """Close MODBUS connection."""
        self.client.close()

    def resolve_usb_port_path(self, port_fragment):
        import subprocess
        from pathlib import Path
        candidates = list(Path("/dev").glob("ttyUSB*")) + list(Path("/dev").glob("ttyACM*"))
        for dev in candidates:
            try:
                path = subprocess.check_output(["udevadm", "info", "-q", "path", "-n", str(dev)], text=True)
                if port_fragment in path:
                    return str(dev)
            except Exception:
                continue
        raise RuntimeError(f"✗ Could not resolve port fragment '{port_fragment}' to a /dev/ttyXXX")

    
    def usbreset(self):
        """
        Reset the USB device using the method in usbmap.json and re-establish MODBUS connection.
        """
        method = self.hubmap.get("recovery_method", "usbreset")

        if method == "usbreset":
            self._reset_by_usbreset()
        elif method == "hubpower":
            self._reset_by_hub()
        elif method == "rebind":
            self._reset_by_rebind()
        else:
            raise RuntimeError(f"✗ Unknown recovery method '{method}'")

        # Post-reset recovery
        print("⏳ Waiting for device to reinitialize...")
        time.sleep(5)

        print("↻ Reconnecting MODBUS client...")
        self.client.connect()

        # Validate connection with retry loop
        for attempt in range(5):
            result = self._read_input(0, 1)
            if not result.isError():
                print(f"✅ Reconnected successfully on attempt {attempt+1}")
                return
            else:
                print(f"Retry {attempt+1}: No response (waiting 2s)")
                time.sleep(2)

        raise IOError("✗ Failed to re-establish MODBUS communication after USB reset")


    def _reset_by_rebind(self):
        interface = self.hubmap.get("interface_id")
        if not interface:
            raise RuntimeError("✗ Missing 'interface_id' for rebind")
        print(f"→ Rebinding FTDI driver on {interface}")
        subprocess.run(["sudo", "tee", "/sys/bus/usb/drivers/ftdi_sio/unbind"],
                       input=(interface + "\n").encode(), check=True)
        time.sleep(1)
        subprocess.run(["sudo", "tee", "/sys/bus/usb/drivers/ftdi_sio/bind"],
                       input=(interface + "\n").encode(), check=True)
        time.sleep(1)
        
    def _reset_by_hub(self):
        loc = self.hubmap.get("hub_location")
        port = self.hubmap.get("hub_port")
        if not loc or not port:
            raise RuntimeError("✗ Missing 'hub_location' or 'hub_port'")
        print(f"→ Power cycling hub port {port} on {loc}")
        subprocess.run(["uhubctl", "-l", loc, "-p", str(port), "-a", "off"], check=True)
        time.sleep(2)
        subprocess.run(["uhubctl", "-l", loc, "-p", str(port), "-a", "on"], check=True)
        time.sleep(2)

if __name__ == "__main__":
    smtc = SMTC08("SMTC08")
    temps = smtc.read_all()
    for i, t in enumerate(temps, 1):
        print(f"Channel {i}: {t:.1f} °C")
    smtc.close()
