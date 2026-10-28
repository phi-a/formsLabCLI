"""What is plugged in, beside the hardware map: `formslab.hardware`, with the
serial ports and VISA instruments faked."""
import json
from types import SimpleNamespace

import pytest
import pyvisa
from serial.tools import list_ports

from formslab import config, hardware


def port(device, vid=None, pid=None, serial=None, location=None, description="USB Serial Device"):
    return SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial,
                           location=location, description=description)


@pytest.fixture
def bench(monkeypatch):
    """Sets what is plugged in: serial ports, and VISA resource strings."""
    def plug(ports=(), visa=()):
        monkeypatch.setattr(list_ports, "comports", lambda: list(ports))
        monkeypatch.setattr(pyvisa, "ResourceManager", lambda: SimpleNamespace(
            list_resources=lambda query: tuple(visa), close=lambda: None))
    return plug


def write_map(entries):
    (config.config_dir() / config.USBMAP_NAME).write_text(json.dumps(entries), encoding="utf-8")


def by_label(survey):
    return {e["label"]: e for e in survey["entries"]}


RIGOL = "USB0::0x1AB1::0x0E11::DP8B279M00280::0::INSTR"


def test_each_entry_is_found_by_the_sturdiest_thing_the_map_says(bench, monkeypatch):
    monkeypatch.setattr(hardware.sys, "platform", "linux")
    write_map({
        "psu1": {"resource": "USB0::0x1AB1::0x0E11::DP8B279M00280::INSTR"},
        "SMTC08_A": {"resource": "/dev/ttyUSB2", "hub_location": "1-4.3", "hub_port": 1},
        "cryo_board": {"port": "1-4.3.3", "vendor_id": "2e8a", "product_id": "0005"},
        "bridge": {"interface_id": "1-4.4:1.0", "vendor_id": "0403", "product_id": "6001"},
    })
    bench(ports=[port("/dev/ttyUSB2", 0x1a86, 0x7523, location="1-4.3.1:1.0"),
                 port("/dev/ttyACM0", 0x2e8a, 0x0005, "E664", "1-4.3.3:1.0"),
                 port("/dev/ttyUSB0", 0x0403, 0x6001, "FT1", "1-4.4:1.0")],
          visa=[RIGOL])
    s = hardware.survey()
    found = {k: (e["status"], e["by"], e["address"]) for k, e in by_label(s).items()}
    assert found == {"psu1": ("found", "serial number", RIGOL),
                     "SMTC08_A": ("found", "port name", "/dev/ttyUSB2"),
                     "cryo_board": ("found", "USB path", "/dev/ttyACM0"),
                     "bridge": ("found", "USB path", "/dev/ttyUSB0")}
    assert all(d["label"] for d in s["plugged"]) and s["errors"] == []


def test_windows_takes_resource_windows(bench, monkeypatch):
    monkeypatch.setattr(hardware.sys, "platform", "win32")
    write_map({"SMTC08_A": {"resource": "/dev/ttyUSB2", "resource_windows": "COM3"}})
    bench(ports=[port("COM3", 0x1a86, 0x7523, location="1-2.3.2:x.0")])
    assert by_label(hardware.survey())["SMTC08_A"]["address"] == "COM3"


def test_a_usb_id_is_never_a_guess_between_two_of_a_kind(bench):
    write_map({"RTD1": {"port": "1-4.4", "vendor_id": "2e8a", "product_id": "0005"},
               "cryo_board": {"port": "1-4.3.3", "vendor_id": "2e8a", "product_id": "0005"}})
    bench(ports=[port("COM5", 0x2e8a, 0x0005, "E664", "")])        # Windows: another computer's hub numbers
    s = hardware.survey()
    assert {e["status"] for e in s["entries"]} == {"missing"}
    assert s["plugged"][0]["label"] is None                         # listed as not in the map, for a person to say


def test_a_usb_id_finds_the_one_device_of_its_kind(bench):
    write_map({"cryo_board": {"port": "1-4.3.3", "vendor_id": "2e8a", "product_id": "0005"}})
    bench(ports=[port("COM5", 0x2e8a, 0x0005, "E664", "")])
    e = by_label(hardware.survey())["cryo_board"]
    assert (e["status"], e["by"], e["address"]) == ("found", "USB id", "COM5")


def test_a_serial_number_in_the_map_must_match(bench):
    write_map({"psu1": {"resource": "USB0::0x1AB1::0x0E11::DP8B279M00280::INSTR"}})
    bench(visa=["USB0::0x1AB1::0x0E11::DP8OTHER0001::0::INSTR"])   # the same model, another unit
    s = hardware.survey()
    assert by_label(s)["psu1"]["status"] == "missing"
    assert by_label(s)["psu1"]["wants"] == "serial number DP8B279M00280"


def test_network_devices_and_hubs_are_not_looked_for(bench):
    write_map({"PS": {"host": "192.168.0.100"},
               "slta": {"host_ip": "192.168.133.101", "port": "1-4.3.4"},
               "hub1": {"hub_location": "1-4", "hub_port": 3, "vendor_id": "2109", "product_id": "2817"},
               "_comment": "not an entry"})
    bench()
    got = {k: (e["status"], e["wants"]) for k, e in by_label(hardware.survey()).items()}
    assert got == {"PS": ("network", "network 192.168.0.100"), "slta": ("network", "network 192.168.133.101"),
                   "hub1": ("none", "")}


def test_what_cannot_be_listed_is_said_and_the_rest_still_listed(bench, monkeypatch):
    write_map({"psu2": {"enabled": False, "resource": "COM4"}})
    bench(ports=[port("COM4")])

    def no_visa():
        raise ValueError("Could not locate a VISA implementation")
    monkeypatch.setattr(pyvisa, "ResourceManager", no_visa)
    s = hardware.survey()
    assert s["errors"] == ["VISA instruments: Could not locate a VISA implementation"]
    assert by_label(s)["psu2"]["status"] == "found" and by_label(s)["psu2"]["enabled"] is False
