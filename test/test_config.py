"""Config and output resolution, and the guarantee that follows from it.

The point of `formslab/config.py` is that an installed console is writable.
Before it, the package wrote runtime state next to its own code and reached
`usbmap.json` through `Path(__file__)` -- fine in an editable checkout, broken
the moment `pip install formslab` puts the code in a read-only site-packages
that is replaced on upgrade. One test here states that as a property over the
whole package rather than trusting each call site.
"""

import json
import os
from pathlib import Path

import pytest

from formslab import config, state


class TestConfigDirectory:

    def test_env_var_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "bench2"))
        assert config.config_dir() == tmp_path / "bench2"

    def test_directory_is_created(self, tmp_path, monkeypatch):
        target = tmp_path / "made" / "on" / "demand"
        monkeypatch.setenv(config.CONFIG_ENV, str(target))
        assert config.config_dir().is_dir()

    def test_defaults_to_home_when_unset(self, monkeypatch):
        """The documented default. Checked without creating it."""
        monkeypatch.delenv(config.CONFIG_ENV, raising=False)
        monkeypatch.setattr(Path, "mkdir", lambda self, **kw: None)
        assert config.config_dir() == Path.home() / ".formslab"

    def test_resolved_per_call_not_per_import(self, tmp_path, monkeypatch):
        """A bench can be switched mid-process; nothing caches the first answer."""
        monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "a"))
        first = config.config_dir()
        monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "b"))
        assert config.config_dir() != first


class TestOutputDirectory:

    def test_env_var_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(config.OUTPUT_ENV, str(tmp_path / "runs"))
        assert config.output_dir() == tmp_path / "runs"

    def test_defaults_to_cwd_outputs(self, tmp_path, monkeypatch):
        monkeypatch.delenv(config.OUTPUT_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        assert config.output_dir() == tmp_path / "outputs"

    def test_run_state_is_per_machine_not_per_folder(self, tmp_path, monkeypatch):
        """The host's lock and log sit in the config dir, so `status` from
        another folder (or an SSH session in ~) still finds the run."""
        monkeypatch.delenv(config.OUTPUT_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        first = config.run_dir()
        (tmp_path / "elsewhere").mkdir()
        monkeypatch.chdir(tmp_path / "elsewhere")
        assert config.run_dir() == first == config.config_dir() / ".run"


class TestUsbmapSeeding:

    def test_first_read_seeds_from_the_packaged_default(self):
        live = config.usbmap_path()
        assert live == config.config_dir() / "usbmap.json"
        assert live.exists()
        assert json.loads(live.read_text(encoding="utf-8")) == json.loads(
            config.default_path("usbmap.json").read_text(encoding="utf-8"))

    def test_an_edited_map_is_not_overwritten(self):
        """The whole reason for a config copy: upgrades must not clobber a bench."""
        live = config.usbmap_path()
        live.write_text(json.dumps({"psu1": {"resource": "EDITED"}}),
                        encoding="utf-8")

        again = config.usbmap_path()

        assert json.loads(again.read_text(encoding="utf-8")) == {
            "psu1": {"resource": "EDITED"}}

    def test_falls_back_to_the_packaged_default_when_unwritable(self, monkeypatch):
        """A read-only home degrades to shipped values, it does not crash."""
        def refuse(*_args, **_kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(config.shutil, "copyfile", refuse)
        assert config.usbmap_path() == config.default_path("usbmap.json")

    def test_the_packaged_default_ships_and_parses(self):
        packaged = config.default_path("usbmap.json")
        assert packaged.exists(), "usbmap.json must ship in the wheel"
        assert isinstance(json.loads(packaged.read_text(encoding="utf-8")), dict)


class TestRuntimeState:

    def test_state_files_land_in_the_config_directory(self):
        assert state.cast_state_path().parent == config.config_dir()
        assert state.ctrl_state_path().parent == config.config_dir()

    def test_ensure_runtime_files_creates_both_from_code_defaults(self):
        state.ensure_runtime_files()

        cast = json.loads(state.cast_state_path().read_text(encoding="utf-8"))
        ctrl = json.loads(state.ctrl_state_path().read_text(encoding="utf-8"))

        assert set(cast) == set(state.build_default_cast_state())
        assert set(ctrl) == set(state.build_default_ctrl_commands())

    def test_existing_state_is_not_reset(self):
        state.ensure_runtime_files()
        path = state.ctrl_state_path()
        path.write_text(json.dumps({"mine": True}), encoding="utf-8")

        state.ensure_runtime_files()

        assert json.loads(path.read_text(encoding="utf-8")) == {"mine": True}


def test_nothing_is_written_inside_the_installed_package():
    """The property Phase 2 exists to establish.

    Exercises the write paths -- seeding the map, creating runtime state,
    touching the output directory -- then asserts none of it landed in the
    package. A regression here means the console breaks on a non-editable
    install, which is exactly the failure that is invisible in a dev checkout.
    """
    before = {p for p in config.PACKAGE_ROOT.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}

    config.usbmap_path()
    state.ensure_runtime_files()
    config.output_dir()

    after = {p for p in config.PACKAGE_ROOT.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts}

    assert after == before, (
        "these were written inside the package: "
        f"{sorted(str(p.relative_to(config.PACKAGE_ROOT)) for p in after - before)}")
