"""The Rigol PSU module against fake transports: no instrument, no VISA.

The bench test for the real supply is `test_DP832A.py`, run by name.
"""

import pytest

from formslab.devices import DP832A
from formslab.devices.DP832A import (
    PSU,
    RigolDriverSerial,
    RigolDriverVISA,
    driver_for_resource,
    parse_float,
    parse_floats,
)

IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B224001812,00.01.16"
USB = "USB0::6833::3601::DP8B224001812::0::INSTR"


# --- Transport selection ---


@pytest.mark.parametrize(
    "resource",
    [USB, "USB0::0x1AB1::0x0E11::DP8B279M00280::INSTR", "TCPIP0::192.0.2.5::INSTR"],
)
def test_usb_and_lan_resources_use_visa(resource):
    assert driver_for_resource(resource) is RigolDriverVISA


@pytest.mark.parametrize("resource", ["ASRLCOM3::INSTR", "ASRL/dev/psu1::INSTR", "/dev/ttyUSB0"])
def test_rs232_resources_use_pyserial(resource):
    assert driver_for_resource(resource) is RigolDriverSerial


def test_backend_override_sends_rs232_through_visa(monkeypatch):
    monkeypatch.setenv("FORMS_PSU_BACKEND", "visa")

    assert driver_for_resource("ASRLCOM3::INSTR") is RigolDriverVISA


@pytest.mark.parametrize(
    "resource, path",
    [("ASRLCOM3::INSTR", "COM3"), ("ASRL/dev/psu2::INSTR", "/dev/psu2"), ("/dev/ttyUSB0", "/dev/ttyUSB0")],
)
def test_serial_driver_finds_the_port_on_either_os(resource, path):
    assert RigolDriverSerial._parse_device_path(resource) == path


# --- Transports ---


class _NoClearUSB:
    """A pyvisa-py USBTMC session, as on the Pi: answers queries, cannot clear."""

    def clear(self):
        raise NotImplementedError("clear not supported by this session")

    def query(self, cmd):
        return IDN


def test_visa_query_survives_a_backend_without_clear():
    driver = RigolDriverVISA.__new__(RigolDriverVISA)  # no instrument to open
    driver.inst = _NoClearUSB()
    driver.interface = "USB"

    assert driver.query("*IDN?") == IDN


class _EchoingPort:
    """A DP832A on RS232, which echoes each command before answering."""

    is_open = True

    def __init__(self):
        self.lines = []

    def write(self, data):
        cmd = data.decode().strip()
        self.lines += [cmd, IDN] if cmd.endswith("?") else []

    def readline(self):
        return (self.lines.pop(0) + "\n").encode() if self.lines else b""

    def reset_input_buffer(self):
        self.lines.clear()

    def close(self):
        self.is_open = False


def test_serial_query_skips_the_echo(monkeypatch):
    monkeypatch.setattr(RigolDriverSerial, "_open", lambda self: setattr(self, "ser", _EchoingPort()))
    monkeypatch.setattr(DP832A.time, "sleep", lambda s: None)

    driver = RigolDriverSerial("ASRLCOM3::INSTR")

    assert driver.query("*IDN?") == IDN


def test_failed_identity_check_closes_the_port(monkeypatch):
    port = _EchoingPort()
    port.write = lambda data: port.lines.append("SOMEONE ELSE,X1") if data.endswith(b"?\n") else None
    monkeypatch.setattr(RigolDriverSerial, "_open", lambda self: setattr(self, "ser", port))
    monkeypatch.setattr(DP832A.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="Not a Rigol"):
        RigolDriverSerial("ASRLCOM3::INSTR")
    assert not port.is_open


# --- PSU ---


class _FakeDriver:
    """Records writes; answers queries from a table."""

    def __init__(self, resource, replies=None):
        self.resource = resource
        self.writes = []
        self.replies = replies or {}
        self.closed = False

    def write(self, cmd):
        self.writes.append(cmd)

    def query(self, cmd):
        reply = self.replies.get(cmd, "")
        if isinstance(reply, Exception):
            raise reply
        return reply

    def clear(self):
        pass

    def reconnect(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def opened(monkeypatch):
    """Patch the transport; returns the list of drivers the PSU opened."""
    drivers = []
    replies = {}

    def factory(resource):
        driver = _FakeDriver(resource, replies)
        drivers.append(driver)
        return driver

    monkeypatch.setattr(DP832A, "driver_for_resource", lambda resource: factory)
    monkeypatch.setattr(DP832A.time, "sleep", lambda s: None)
    return drivers, replies


def test_psu_opens_nothing_until_first_used(opened):
    drivers, _ = opened

    psu = PSU(USB)
    assert drivers == [] and not psu.connected

    psu.on(1)
    assert len(drivers) == 1 and drivers[0].writes == [":OUTP CH1,ON"]


def test_disconnect_returns_the_panel_to_local(opened):
    drivers, _ = opened
    psu = PSU(USB)
    psu.connect()

    psu.disconnect()

    assert drivers[0].writes[-1] == "SYSTEM:LOCAL"
    assert drivers[0].closed and not psu.connected


def test_channel_is_reselected_after_reconnect(opened):
    drivers, _ = opened
    psu = PSU(USB)
    psu.set(2, 5.0, 0.1)
    psu.connect()  # a new session: the old channel selection is not assumed

    psu.set(2, 5.0, 0.1)

    assert drivers[1].writes[0] == ":INST:NSEL 2"


def test_shutdown_zeroes_every_channel_and_keeps_the_session(opened):
    drivers, _ = opened
    psu = PSU(USB)

    assert psu.shutdown() is True
    for ch in (1, 2, 3):
        assert f":OUTP CH{ch},OFF" in drivers[0].writes
        assert f":APPL CH{ch},0.0,0.0" in drivers[0].writes
    assert psu.connected


def test_shutdown_reports_failure_instead_of_raising(monkeypatch):
    def unplugged(resource):
        raise OSError("could not open port")

    monkeypatch.setattr(DP832A, "driver_for_resource", lambda resource: unplugged)

    assert PSU(USB).shutdown() is False


def test_measure_reads_one_atomic_reply(opened):
    _, replies = opened
    replies[":MEAS:ALL? CH1"] = "12.001,0.502,6.023"

    assert PSU(USB).measure_all(1) == (12.001, 0.502, 6.023)


def test_measure_returns_none_when_the_supply_is_silent(opened):
    assert PSU(USB).measure(1, retries=2) == (None, None)


def test_status_reports_each_channel(opened):
    _, replies = opened
    replies.update({
        ":OUTP? CH1": "ON",
        ":APPL?": "12.000,1.500",
        ":MEAS:VOLT?": "11.998",
        ":MEAS:CURR?": "0.250",
    })

    state = PSU(USB).status()

    assert state[1] == {"on": True, "vset": 12.0, "cset": 1.5, "vmeas": 11.998, "cmeas": 0.25}


def test_status_marks_a_failed_channel_unknown(opened):
    _, replies = opened
    replies[":OUTP? CH2"] = TimeoutError("no reply")

    state = PSU(USB).status()

    assert state[2] == dict.fromkeys(("on", "vset", "cset", "vmeas", "cmeas"))


def test_number_parsing():
    assert parse_float(" 11.998V") == 11.998
    assert parse_float("no numbers", default=0) == 0
    assert parse_floats("12.0,1.5e-1,-3") == [12.0, 0.15, -3.0]


def test_safe_comparisons_treat_a_failed_reading_as_false():
    assert PSU.safe_lt(1.0, 2.0) and not PSU.safe_lt(None, 2.0)
    assert PSU.safe_gt(3.0, 2.0) and not PSU.safe_gt(None, 2.0)
