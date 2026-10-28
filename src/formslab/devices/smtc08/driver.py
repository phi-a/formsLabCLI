from pymodbus.client import ModbusSerialClient
import inspect

from formslab import hardware

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

    def __init__(self, label="SMTC08", slave=1, port=None):
        # The port named, or the one `formslab.hardware` finds for the map entry
        self.hubmap = hardware.entry(label)
        self.port = port or hardware.address(label, self.hubmap)

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


if __name__ == "__main__":
    smtc = SMTC08("SMTC08_A")
    temps = smtc.read_all()
    for i, t in enumerate(temps, 1):
        print(f"Channel {i}: {t:.1f} °C")
    smtc.close()
