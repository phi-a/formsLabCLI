from __future__ import annotations

import json
import subprocess
from pathlib import Path

from formslab.console.psu import psucli
from formslab.devices.powerswitch import driver as powerswitch


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


def _windows_runner(calls, *, dhcp=False, addresses=(), found=True, apply_error=""):
    """Fake Windows: the state query answers as an adapter would; changes are recorded."""
    def runner(command, **kwargs):
        calls.append(command)
        if command[0] == "powershell":
            if not found:
                return _completed(command, returncode=1, stderr="No MSFT_NetIPInterface objects found")
            state = {"dhcp": "Enabled" if dhcp else "Disabled", "addresses": list(addresses)}
            return _completed(command, stdout=json.dumps(state))
        if apply_error:
            return _completed(command, returncode=1, stderr=apply_error)
        return _completed(command)
    return runner


def test_windows_setup_adds_beside_the_other_addresses():
    config = powerswitch.PowerSwitchConfig(
        adapter="Dedicated Lab NIC",
        local_ip="192.168.8.50",
        prefix_length=24,
    )

    assert powerswitch.windows_setup_command(config) == [
        "netsh",
        "interface",
        "ipv4",
        "add",
        "address",
        "name=Dedicated Lab NIC",
        "address=192.168.8.50",
        "mask=255.255.255.0",
        "store=persistent",
    ]


def test_windows_setup_on_dhcp_is_static_persistent_and_has_no_gateway():
    config = powerswitch.PowerSwitchConfig(
        adapter="Dedicated Lab NIC",
        local_ip="192.168.8.50",
        prefix_length=24,
    )

    assert powerswitch.windows_setup_command(config, dhcp=True) == [
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


def test_windows_state_query_quotes_the_adapter_name():
    command = powerswitch.windows_state_command("Bob's NIC")

    assert command[0] == "powershell"
    assert "-InterfaceAlias 'Bob''s NIC'" in command[-1]


def test_windows_setup_keeps_the_chamber_address(capsys):
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Ethernet 2", local_ip="192.168.0.50"),
        runner=_windows_runner(calls, addresses=["10.1.2.50"]),
    )

    assert ok
    assert calls[-1][3:5] == ["add", "address"]
    assert "keeps 10.1.2.50" in capsys.readouterr().out


def test_windows_setup_replaces_only_a_dhcp_address():
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Ethernet 2"),
        runner=_windows_runner(calls, dhcp=True, addresses=["169.254.1.2"]),
    )

    assert ok
    assert calls[-1][3:5] == ["set", "address"]


def test_windows_setup_with_the_address_present_changes_nothing(capsys):
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Ethernet 2", local_ip="192.168.0.50"),
        runner=_windows_runner(calls, addresses=["192.168.0.50", "10.1.2.50"]),
    )

    assert ok
    assert [c[0] for c in calls] == ["powershell"]
    assert "nothing to change" in capsys.readouterr().out


def test_windows_dry_run_inspects_but_does_not_change_adapter(capsys):
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Dedicated Lab NIC"),
        dry_run=True,
        runner=_windows_runner(calls),
    )

    assert ok
    assert [c[0] for c in calls] == ["powershell"]
    assert "[dry-run]" in capsys.readouterr().out


def test_windows_setup_refuses_an_unknown_adapter(capsys):
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Not A Real Adapter"),
        runner=_windows_runner(calls, found=False),
    )

    assert not ok
    output = capsys.readouterr().out
    assert "Not A Real Adapter" in output
    assert "PS.adapter" in output


def test_setup_failure_explains_windows_elevation(capsys):
    calls = []

    ok = powerswitch.setup_windows(
        powerswitch.PowerSwitchConfig(adapter="Dedicated Lab NIC"),
        runner=_windows_runner(calls, apply_error="Access is denied"),
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
    assert command[1:3] == ["-m", "formslab.devices.powerswitch"]
    assert command[3:] == ["setup", "--dry-run"]
    assert "PowerSwitch network setup completed successfully" in result.content.plain
