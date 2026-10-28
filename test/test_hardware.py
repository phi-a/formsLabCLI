"""USB devices found one way, `formslab.hardware`: each map entry matched to
what is plugged in, with the serial ports and VISA instruments faked."""
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


def map_path():
    return config.config_dir() / config.USBMAP_NAME


def write_map(entries):
    map_path().write_text(json.dumps(entries), encoding="utf-8")


def by_label(survey):
    return {e["label"]: e for e in survey["entries"]}


RIGOL = "USB0::0x1AB1::0x0E11::DP8B279M00280::0::INSTR"
CH340 = dict(vid=0x1a86, pid=0x7523)
PICO = dict(vid=0x2e8a, pid=0x0005)


# --- finding a device -----------------------------------------------------------------

def test_each_entry_is_found_by_the_sturdiest_thing_the_map_says(bench):
    write_map({
        "psu1": {"usb": "1ab1:0e11", "serial": "DP8B279M00280"},
        "SMTC08_A": {"usb": "1a86:7523", "path": "1-4.3.1"},
        "cryo_board": {"usb": "2e8a:0005", "serial": "E664"},
        "bridge": {"address": "/dev/ttyUSB0"},
    })
    bench(ports=[port("/dev/ttyUSB2", **CH340, location="1-4.3.1:1.0"),
                 port("/dev/ttyACM0", **PICO, serial="E664", location="1-4.3.3:1.0"),
                 port("/dev/ttyUSB0", 0x0403, 0x6001, "FT1", "1-4.4:1.0")],
          visa=[RIGOL])
    s = hardware.survey()
    found = {k: (e["status"], e["by"], e["address"]) for k, e in by_label(s).items()}
    assert found == {"psu1": ("found", "serial number", RIGOL),
                     "SMTC08_A": ("found", "USB path", "/dev/ttyUSB2"),
                     "cryo_board": ("found", "serial number", "/dev/ttyACM0"),
                     "bridge": ("found", "address", "/dev/ttyUSB0")}
    assert all(d["label"] for d in s["plugged"]) and s["errors"] == []
    assert hardware.address("SMTC08_A") == "/dev/ttyUSB2"
    assert hardware.address("psu1") == RIGOL                    # only in the VISA listing


def test_the_same_map_entry_finds_the_device_on_windows(bench):
    write_map({"cryo_board": {"usb": "2e8a:0005", "serial": "E664"},
               "SMTC08_A": {"usb": "1a86:7523", "path": "1-2.3.2"}})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.4:x.0"),
                 port("COM3", **CH340, location="1-2.3.2:x.0")])
    assert hardware.address("cryo_board") == "COM5"
    assert hardware.address("SMTC08_A") == "COM3"


def test_a_place_holds_only_the_kind_of_device_the_map_says(bench):
    write_map({"SMTC08_A": {"usb": "1a86:7523", "path": "1-2.3.2"}})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.3.2:x.0")])   # a Pico in its slot
    with pytest.raises(RuntimeError) as e:
        hardware.address("SMTC08_A")
    assert str(e.value) == ("SMTC08_A wants 1a86:7523 at USB path 1-2.3.2; plugged in: "
                            "COM5 (2e8a:0005 serial E664). See labcli hardware.")


def test_a_usb_id_is_never_a_guess_between_two_of_a_kind(bench):
    write_map({"RTD1": {"usb": "2e8a:0005", "path": "1-4.4"},
               "cryo_board": {"usb": "2e8a:0005", "path": "1-4.3.3"}})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.4")])   # another computer's hub numbers
    s = hardware.survey()
    assert {e["status"] for e in s["entries"]} == {"missing"}
    assert s["plugged"][0]["label"] is None                         # listed as not in the map, for a person to say
    with pytest.raises(RuntimeError):
        hardware.address("cryo_board")


def test_a_usb_id_finds_the_one_device_of_its_kind(bench):
    write_map({"cryo_board": {"usb": "2e8a:0005", "path": "1-4.3.3"}})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.4")])
    e = by_label(hardware.survey())["cryo_board"]
    assert (e["status"], e["by"], e["address"]) == ("found", "USB id", "COM5")


def test_a_serial_number_in_the_map_must_match(bench):
    write_map({"psu1": {"usb": "1ab1:0e11", "serial": "DP8B279M00280"}})
    bench(visa=["USB0::0x1AB1::0x0E11::DP8OTHER0001::0::INSTR"])   # the same model, another unit
    s = hardware.survey()
    assert by_label(s)["psu1"]["status"] == "missing"
    assert by_label(s)["psu1"]["wants"] == "1ab1:0e11 serial number DP8B279M00280"


def test_an_entry_not_in_the_map_or_off_is_a_value_error(bench):
    write_map({"psu2": {"enabled": False, "address": "COM4"}})
    bench()
    with pytest.raises(ValueError, match="not in the hardware map"):
        hardware.address("SMTC08_B")
    with pytest.raises(ValueError, match="off"):
        hardware.address("psu2")


def test_an_address_is_used_as_it_is(bench):
    write_map({"psu1": {"address": "TCPIP::192.0.2.7::INSTR"}})
    bench()
    assert hardware.address("psu1") == "TCPIP::192.0.2.7::INSTR"
    assert by_label(hardware.survey())["psu1"]["status"] == "network"


def test_network_devices_and_entries_with_nothing_to_find_by(bench):
    write_map({"PS": {"host": "192.168.0.100"}, "RTD2": {"description": "not wired yet"},
               "_comment": "not an entry"})
    bench()
    got = {k: (e["status"], e["wants"]) for k, e in by_label(hardware.survey()).items()}
    assert got == {"PS": ("network", "network 192.168.0.100"), "RTD2": ("none", "")}


def test_what_cannot_be_listed_is_said_and_the_rest_still_listed(bench, monkeypatch):
    write_map({"psu2": {"enabled": False, "address": "COM4"}})
    bench(ports=[port("COM4")])

    def no_visa():
        raise ValueError("Could not locate a VISA implementation")
    monkeypatch.setattr(pyvisa, "ResourceManager", no_visa)
    s = hardware.survey()
    assert s["errors"] == ["VISA instruments: Could not locate a VISA implementation"]
    assert by_label(s)["psu2"]["status"] == "found" and by_label(s)["psu2"]["enabled"] is False


def test_a_device_not_in_the_map_says_what_to_put_there(bench):
    write_map({})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.4:x.0"),
                 port("COM3", **CH340, location="1-2.3.2:x.0"), port("COM1")])
    assert [d["entry"] for d in hardware.survey()["plugged"]] == [
        '"usb": "2e8a:0005", "serial": "E664"', '"usb": "1a86:7523", "path": "1-2.3.2"', '"address": "COM1"']


# --- the old form, converted once ----------------------------------------------------

OLD_MAP = {
    "PS": {"description": "Digital Loggers PowerSwitch", "host": "192.168.0.100"},
    "psu1": {"description": "Rigol DP832A", "enabled": True,
             "resource": "USB0::0x1AB1::0x0E11::DP8B279M00280::INSTR",
             "resource_windows": "USB0::0x1AB1::0x0E11::DP8B279M00280::INSTR",
             "serial_number": "DP8B279M00280", "vendor_id": "1ab1", "product_id": "0e11",
             "hub_location": "1-4", "hub_port": 2, "port": "1-4.2", "interface_id": "1-4.2:1.0",
             "windows_hub_port": 3, "windows_location_path": "USB(2)#USB(3)#USB(3)", "baud": 9600,
             "recovery_method": "hubpower", "channels": {"1": {"feeds": "cryocooler board"}}},
    "psu2": {"description": "FTDI Chipi-X", "enabled": False, "resource": "ASRL/dev/psu2::INSTR",
             "hub_location": "1-4", "hub_port": 1, "port": "1-4.1", "interface_id": "1-4.1:1.0"},
    "slta": {"mac_address": "d0:37:45:2a:f8:f2", "host_ip": "192.168.133.101", "port": "1-4.3.4"},
    "hub1": {"hub_location": "1-4", "hub_port": 3, "vendor_id": "2109", "product_id": "2817"},
    "RTD1": {"description": "RTD board 1", "port": "1-4.4", "interface_id": "1-4.4:1.0",
             "vendor_id": "2e8a", "product_id": "0005", "board_id": "01"},
    "cryo_board": {"description": "Cryo", "port": "1-4.3.3", "interface_id": "1-4.3.3:1.0",
                   "baud": 115200, "auto_deploy": True, "firmware_version": "2026.08.29.1",
                   "i2c": {"scl_pin": 17}, "vendor_id": "2e8a", "product_id": "0005"},
    "SMTC08_A": {"description": "SMTC08", "resource": "/dev/ttyUSB2", "resource_windows": "COM3",
                 "baud": 9600, "hub_location": "1-4.3", "hub_port": 1},
}


def test_an_old_map_is_converted_once_with_what_is_plugged_in(bench, monkeypatch):
    monkeypatch.setattr(hardware.sys, "platform", "win32")
    write_map(OLD_MAP)
    bench(ports=[port("COM3", **CH340, location="1-2.3.2:x.0"),
                 port("COM5", **PICO, serial="E664", location="1-2.4:x.0")], visa=[RIGOL])
    assert hardware.load() == {
        "PS": OLD_MAP["PS"],
        "psu1": {"description": "Rigol DP832A", "usb": "1ab1:0e11", "serial": "DP8B279M00280",
                 "enabled": True, "baud": 9600, "channels": {"1": {"feeds": "cryocooler board"}}},
        "psu2": {"description": "FTDI Chipi-X", "path": "1-4.1", "enabled": False},
        "RTD1": {"description": "RTD board 1", "path": "1-4.4", "usb": "2e8a:0005", "board_id": "01"},
        "cryo_board": {"description": "Cryo", "path": "1-4.3.3", "usb": "2e8a:0005", "baud": 115200,
                       "i2c": {"scl_pin": 17}},                 # two Picos wanted: left for a person to say
        "SMTC08_A": {"description": "SMTC08", "path": "1-2.3.2", "usb": "1a86:7523", "baud": 9600},
    }
    old = map_path().with_name("usbmap.json.old")
    assert json.loads(old.read_text(encoding="utf-8")) == OLD_MAP
    assert json.loads(map_path().read_text(encoding="utf-8")) == hardware.load()
    assert hardware.address("SMTC08_A") == "COM3"


def test_an_old_entry_with_a_serial_number_takes_the_device_s_own(bench):
    write_map({"cryo_board": {"port": "1-4.3.3", "vendor_id": "2e8a", "product_id": "0005"}})
    bench(ports=[port("COM5", **PICO, serial="E664", location="1-2.4:x.0")])   # the one Pico: found by its id
    assert hardware.load()["cryo_board"] == {"usb": "2e8a:0005", "serial": "E664"}


def test_a_map_in_the_new_form_is_never_rewritten(bench):
    write_map({"SMTC08_A": {"usb": "1a86:7523", "path": "1-2.3.2"}})
    before = map_path().read_text(encoding="utf-8")
    bench(ports=[port("COM9", **CH340, location="1-9:x.0")])
    hardware.load()
    hardware.survey()
    assert map_path().read_text(encoding="utf-8") == before
    assert not map_path().with_name("usbmap.json.old").exists()


def test_the_shipped_map_is_in_the_new_form():
    raw = json.loads(config.default_path(config.USBMAP_NAME).read_text(encoding="utf-8"))
    assert not hardware._old(raw)
