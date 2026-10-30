import json

from formslab import config
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
                "psu1": {"enabled": True, "address": "ASRLCOM3::INSTR"},
                "psu2": {"enabled": False, "address": "ASRL/dev/psu2::INSTR"},
                "PS": {"host": "192.0.2.1"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_a_psu_opens_what_the_map_finds(monkeypatch):
    from formslab.devices.dp832a import driver

    _write_map()
    opened = []
    monkeypatch.setattr(driver, "driver_for_resource", lambda r: opened.append(r) or (lambda r: object()))
    psu = driver.PSU("psu1")                      # building one finds nothing yet: unplugged is fine
    assert opened == []
    psu.connect()
    assert opened == ["ASRLCOM3::INSTR"]


def test_disabled_psu_is_not_exposed():
    _write_map()

    assert psu_config.enabled_psu_labels() == ("psu1",)
