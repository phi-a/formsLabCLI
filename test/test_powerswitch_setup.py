from __future__ import annotations

import json
import subprocess
from pathlib import Path

from formslab.console.psu import psucli
from formslab.devices import powerswitch


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_lab_config_owns_the_ps_device(tmp_path, monkeypatch):
    config_path = tmp_path / "usbmap.json"
    config_path.write_text(
        json.dumps({
            "PS": {
                "description": "New lab switch",
                "host": "192.168.8.100",
                "user": "operator",
                "password_env": "TEST_PS_PASSWORD",
                "adapter": "Dedicated Lab NIC",
                "local_ip": "192.168.8.50",
                "prefix_length": 24,
                "outlet_count": 8,
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_PS_PASSWORD", "from-environment")

    config = powerswitch.load_config(config_path)

    assert config.label == "PS"
    assert config.description == "New lab switch"
    assert config.host == "192.168.8.100"
    assert config.password == "from-environment"
    assert config.adapter == "Dedicated Lab NIC"
    assert config.local_ip == "192.168.8.50"


def test_windows_setup_is_persistent_and_has_no_gateway():
    config = powerswitch.PowerSwitchConfig(
        adapter="Dedicated Lab NIC",
        local_ip="192.168.8.50",
        prefix_length=24,
    )

    assert powerswitch.windows_setup_command(config) == [
        "netsh",
        "interface",
        "ipv4",
        "set",
        "address",
        "name=Dedicated Lab NIC",
        "source=static",
        "address=192.168.8.50",
        "mask=255.255.255.0",
        "gateway=none",
        "store=persistent",
    ]


def test_windows_dry_run_inspects_but_does_not_change_adapter(capsys):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _completed(command)

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Dedicated Lab NIC"),
        dry_run=True,
        runner=runner,
    )

    assert ok
    assert calls == [[
        "netsh", "interface", "show", "interface", "name=Dedicated Lab NIC"
    ]]
    assert "[dry-run]" in capsys.readouterr().out


def test_windows_setup_refuses_an_unknown_adapter(capsys):
    def runner(command, **kwargs):
        return _completed(command, returncode=1, stderr="not found")

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Not A Real Adapter"),
        runner=runner,
    )

    assert not ok
    output = capsys.readouterr().out
    assert "Not A Real Adapter" in output
    assert "PS.adapter" in output


def test_setup_failure_explains_windows_elevation(capsys):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return _completed(command)
        return _completed(command, returncode=1, stderr="Access is denied")

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Dedicated Lab NIC"),
        runner=runner,
    )

    assert not ok
    output = capsys.readouterr().out
    assert "Access is denied" in output
    assert "Run as administrator" in output


def test_outlet_range_refuses_before_any_http_request(monkeypatch, capsys):
    monkeypatch.setattr(
        powerswitch,
        "_http_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP called")),
    )

    assert not powerswitch.switch("ON", 9, powerswitch.PowerSwitchConfig())
    assert "outside 1-8" in capsys.readouterr().out


def test_labcli_setup_dispatches_the_python_lab_tool(monkeypatch):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        return _completed(command, stdout="[dry-run] planned\n")

    monkeypatch.setattr(psucli.subprocess, "run", run)

    result = psucli.execute_command(["--setup", "--dry-run"], target="ps")

    command = captured["command"]
    assert command[0] == powerswitch.sys.executable
    assert Path(command[1]).name == "powerswitch.py"
    assert Path(command[1]).parent.name == "devices"   # was lab/ before the split
    assert command[2:] == ["setup", "--dry-run"]
    assert "PowerSwitch network setup completed successfully" in result.content.plain
