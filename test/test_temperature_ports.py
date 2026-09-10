"""Port configuration works without Linux discovery or connected hardware."""

import importlib
import json
from unittest.mock import Mock

import pytest

from formslab.config import usbmap_path


@pytest.mark.parametrize("port", ["COM7", "COM123", "/dev/ttyUSB0"])
def test_smtc_opens_configured_port(monkeypatch, port):
    module = importlib.import_module("formslab.devices.SMTC08")
    usbmap_path().write_text(json.dumps({"SMTC08_A": {"resource": port}}))
    client = Mock()
    monkeypatch.setattr(module, "ModbusSerialClient", client)

    device = module.SMTC08("SMTC08_A")

    assert device.port == port
    assert client.call_args.kwargs["port"] == port
    client.return_value.connect.assert_called_once_with()


@pytest.mark.parametrize("port", ["COM7", "COM123", "/dev/ttyACM0"])
def test_rtd_opens_configured_port(monkeypatch, port):
    module = importlib.import_module("formslab.devices.RTD16")
    usbmap_path().write_text(json.dumps({
        "RTD1": {"resource": port, "board_id": "01", "interface_id": "old-hub"}
    }))
    serial = Mock()
    monkeypatch.setattr(module.serial, "Serial", serial)

    device = module.RTD16("RTD1")
    device.open()

    assert device.device_path == port
    serial.assert_called_once_with(port, baudrate=115200, timeout=2)
