"""Where the console's per-bench configuration and mutable state live.

An installed package cannot hold what the console writes: `pip install formslab`
puts the code in site-packages, which is read-only on a shared lab machine and
wiped on upgrade. Anything the console *writes* has to leave the package.

Three locations, by what the thing actually is:

* **Package** -- code and the data that ships with it: command tables, the
  Pico firmware, the default `usbmap.json`. Read-only, replaced on upgrade.
* **Config** (``$FORMSLAB_CONFIG_DIR``, else ``~/.formslab``) -- per-bench and
  per-user: the live `usbmap.json`, CTRL's command table, CAST's device state.
  Survives upgrades, differs between benches, and is the directory an operator
  edits or backs up.
* **Output** (``$FORMSLAB_OUTPUT_DIR``, else ``<cwd>/outputs``) -- run
  products: logs, captured frames, temperature histories. Working-directory
  relative so a bench session's artifacts land where it was started.

`usbmap.json` is the one file with a shipped default *and* a live copy. First
read seeds the config copy from the packaged one, so a fresh install comes up
with a usable map that the operator can then edit for their bench without an
upgrade overwriting it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

CONFIG_ENV = "FORMSLAB_CONFIG_DIR"
OUTPUT_ENV = "FORMSLAB_OUTPUT_DIR"

# Data that ships in the wheel. Never written to.
PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULTS_DIR = PACKAGE_ROOT / "defaults"

USBMAP_NAME = "usbmap.json"


def config_dir() -> Path:
    """The per-bench configuration directory, created if absent.

    ``$FORMSLAB_CONFIG_DIR`` wins, so a test (or a second bench on one machine)
    can point somewhere else without touching the operator's real setup.
    """
    root = os.environ.get(CONFIG_ENV)
    path = Path(root).expanduser() if root else Path.home() / ".formslab"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir() -> Path:
    """Where run products land, created if absent."""
    root = os.environ.get(OUTPUT_ENV)
    path = (Path(root).expanduser() if root
            else Path.cwd() / "outputs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir() -> Path:
    """What is running on this machine: the host's lock, event log and output,
    in ``<config>/.run``. Per machine, not per working directory, so `status`
    and `end` find the host from any folder or SSH session."""
    path = config_dir() / ".run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(name: str) -> Path:
    """A mutable state file in the config directory.

    The file itself is not created here -- callers own their own defaults
    (`formslab.state` regenerates CTRL and CAST from code), and an absent file
    is a meaningful state for the IPC files, which are empty until written.
    """
    return config_dir() / name


def default_path(name: str) -> Path:
    """The packaged default for `name`."""
    return DEFAULTS_DIR / name


def usbmap_path() -> Path:
    """The live lab hardware map, seeded from the packaged default.

    Every driver resolves the map through here rather than through its own
    ``Path(__file__).with_name("usbmap.json")``, so an operator edits one file
    and the PSU, cryo board, RTD reader, thermocouple reader and PowerSwitch
    all see the same bench.

    Falls back to the packaged default if the config directory cannot be
    written -- a read-only home should degrade to shipped values rather than
    crash a console that is only being used to read a temperature.
    """
    live = config_dir() / USBMAP_NAME
    if live.exists():
        return live

    packaged = default_path(USBMAP_NAME)
    try:
        shutil.copyfile(packaged, live)
    except OSError:
        return packaged
    return live
