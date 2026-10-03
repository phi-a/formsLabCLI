"""
MicroPython I2C bridge firmware for the cryocooler control board.

The FlatSat PC has no I2C bus of its own. The bridge on this bench is a
Raspberry Pi Pico (USB 2e8a:0005): the PC drives it over USB CDC with
``mpremote``, and the Pico bit-bangs the bus. ``lab/pico_i2c.py`` deploys this
file to the Pico as ``main.py`` and calls the four entry points below.

This firmware is a *transport*, not a controller. It holds no board state and
knows nothing about the converter or the digipot -- no calibration, no
register map, no enable sequence. All of that lives on the PC in
``lab/cryo_registers.py``, where it can be tested without hardware.

Importing this module configures the I2C pins and nothing else. It never
writes to a device, so deploying it cannot disturb a running cryocooler.

Pin assignment
--------------
``SCL_PIN``/``SDA_PIN`` below are only defaults. The PC overrides them from
the ``cryo_board.i2c`` block in ``lab/usbmap.json`` on every connection, so
rewiring the board is a config edit, not a firmware edit.
"""

import machine


SCL_PIN = 22
SDA_PIN = 23
FREQ = 200000

_i2c = None
_config = {"scl": SCL_PIN, "sda": SDA_PIN, "freq": FREQ}


def configure(scl=SCL_PIN, sda=SDA_PIN, freq=FREQ):
    """Bind the I2C bus to a pin pair. Returns the configuration in use."""
    global _i2c
    _config["scl"] = int(scl)
    _config["sda"] = int(sda)
    _config["freq"] = int(freq)
    _i2c = _open_bus()
    return dict(_config)


def config():
    """Return the pin/frequency configuration currently bound."""
    return dict(_config)


def _open_bus():
    return machine.SoftI2C(
        scl=machine.Pin(_config["scl"]),
        sda=machine.Pin(_config["sda"]),
        freq=_config["freq"],
    )


def _bus():
    global _i2c
    if _i2c is None:
        _i2c = _open_bus()
    return _i2c


def _is_enodev(error):
    if "ENODEV" in str(error):
        return True
    args = getattr(error, "args", None)
    return bool(args) and args[0] == 19


def _retry(operation):
    """
    Run an I2C operation, rebuilding the bus once on ENODEV.

    A SoftI2C bus that loses its device mid-transaction stays wedged until it
    is recreated. This recovery is measured behaviour on this board; keep it.
    """
    global _i2c
    try:
        return operation(_bus())
    except OSError as error:
        if not _is_enodev(error):
            raise
        _i2c = _open_bus()
        return operation(_i2c)


def scan():
    """Return the responding I2C addresses as ``["0x18", "0x74"]``."""
    devices = _retry(lambda bus: bus.scan())
    return ["0x%02X" % dev for dev in devices]


def read(addr, reg, nbytes=1):
    """Read ``nbytes`` from ``reg`` on ``addr``. Returns a list of ints."""
    if nbytes < 1:
        return []
    data = _retry(lambda bus: bus.readfrom_mem(addr, reg, nbytes))
    return list(data)


def write(addr, reg, data):
    """
    Write ``data`` to ``reg`` on ``addr``.

    ``data`` is an int or a sequence of ints. Multi-byte payloads go out as one
    burst, which is how the REF_L/REF_M pair must be written.
    """
    if isinstance(data, int):
        payload = bytes([data & 0xFF])
    else:
        payload = bytes([value & 0xFF for value in data])
    _retry(lambda bus: bus.writeto_mem(addr, reg, payload))
    return len(payload)


def emit(value):
    """Print a value in the framed form ``lab.pico_i2c`` parses."""
    print("RESULT:" + repr(value))
