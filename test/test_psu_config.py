import json

from rich.text import Text

from formslab import config
from formslab.console.sessions.base import CLIResult
from formslab.devices.dp832a import config as psu_config


def _write_map():
    """Write a bench map into the test's own config directory.

    Goes through `config.config_dir()` rather than monkeypatching a module
    constant: the constant is gone, and this exercises the resolution an
    operator actually gets. `conftest` redirects the directory per test.
    """
    path = config.config_dir() / "usbmap.json"
    path.write_text(
        json.dumps(
            {
                "psu1": {
                    "enabled": True,
                    "resource": "ASRL/dev/psu1::INSTR",
                    "resource_windows": "ASRLCOM3::INSTR",
                },
                "psu2": {
                    "enabled": False,
                    "resource": "ASRL/dev/psu2::INSTR",
                },
                "PS": {"host": "192.0.2.1"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_psu_resource_selects_windows_mapping():
    _write_map()

    assert psu_config.resource_for("psu1", platform="win32") == "ASRLCOM3::INSTR"
    assert psu_config.resource_for("psu1", platform="linux") == "ASRL/dev/psu1::INSTR"


def test_disabled_psu_is_not_exposed():
    _write_map()

    assert psu_config.enabled_psu_labels() == ("psu1",)



def test_psu_help_omits_disabled_psu():
    from formslab.console.psu import psucli

    text = psucli.help_panel().content.plain
    assert "psu1" in text
    assert "psu2" not in text


def test_psu_session_returns_only_command_result(monkeypatch):
    from formslab.console.psu import psucli
    from formslab.console.sessions.psu import PSUSession

    monkeypatch.setattr(psucli, "psus", lambda: {"psu1": object()})
    monkeypatch.setattr(
        psucli,
        "execute_command",
        lambda parts, target=None: CLIResult(Text(f"result for {target}")),
    )

    result = PSUSession().handle("--status")

    assert result.content.plain == "result for psu1"

