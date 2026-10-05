from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from formslab import config

from formslab.devices.cryocooler.config import (
    CRYO_PSU_CHANNEL,
    CRYO_PSU_LABEL,
    CRYO_SUPPLY_CURRENT_A,
    CRYO_SUPPLY_VOLTAGE_V,
)


# The console's mutable runtime state, in the config directory rather than
# beside the code: site-packages is read-only on a shared lab machine and is
# replaced on upgrade, and CAST state is the last thing that should be lost to
# a `pip install --upgrade` in the middle of a run.
#
# Read through the accessors, not the module constants -- `$FORMSLAB_CONFIG_DIR`
# is read per call so a test (or a second bench) can redirect it.
def cast_state_path() -> Path:
    """Where CAST keeps live device state."""
    return config.state_path("castfile.json")


def ctrl_state_path() -> Path:
    """Where CTRL keeps its command table."""
    return config.state_path("ctrlfile.json")


# The requests the host takes from the ctrl file (host/sequence.py). The console's
# own verbs (plans, run, status...) never pass through it.
CTRL_LABELS = ("end", "pause", "resume", "reset")


def build_default_ctrl_commands() -> dict:
    return {label: {"key": None, "processed": True} for label in CTRL_LABELS}


def build_default_cast_state(now: float | None = None) -> dict:
    timestamp = time.time() if now is None else now
    return {
        "psu1": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {
                "1": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
                "2": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
                "3": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
            },
        },
        "psu2": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {
                "1": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
                "2": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
                "3": {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0},
            },
        },
        "slta": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {
                "SLTARUN": False,
                "running": False,
                "in_umbra": False,
                "mode": "E",
                "exposure": 600,
                "idle": 30,
                "token": None,
                "IMAGEDIR": "default",
            },
        },
        "tc": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {},
        },
        "hvc": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {
                "connected": False,
                "mode": None,
                "test_status": None,
                "pressure": None,
                "pressure_unit": "Torr",
                "vacuum_setpoint": None,
                "recipe": None,
                "recipe_step": None,
                "thermal_control": False,
                "fault_severity": None,
                "faults": "none",
                "platen C": None,
                "platen setpoint C": None,
                "shroud C": None,
                "shroud setpoint C": None,
            },
        },
        "cryo": {
            "timestamp": timestamp,
            "processed": True,
            "request": {},
            "status": {
                "LINK": False,
                "ON": False,
                "PSU": f"{CRYO_PSU_LABEL.upper()} CH{CRYO_PSU_CHANNEL}",
                "PSUON": False,
                "CCVIN": CRYO_SUPPLY_VOLTAGE_V,
                "CCIIN": CRYO_SUPPLY_CURRENT_A,
                "CCVINM": None,
                "CCIINM": None,
                "CCV": None,
                "CCVRES": None,
                "CCVRES#": None,
            },
        },
    }


def ensure_json_file(path: Path, factory: Callable[[], dict]) -> Path:
    if path.exists():
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(factory(), indent=2) + "\n", encoding="utf-8")
    return path


def ensure_runtime_files() -> None:
    """Create any missing state file from its code default, and drop what an
    older version left in them (a CAST block nobody owns, a ctrl entry nobody
    reads), so a stale `tvac` block does not show on every status page."""
    from formslab.console.cast.castutils import DropUnknownBlocks
    from formslab.console.ctrl.ctrlutils import DropUnknownCommands

    ensure_json_file(cast_state_path(), build_default_cast_state)
    ensure_json_file(ctrl_state_path(), build_default_ctrl_commands)
    for tidy in (DropUnknownBlocks, DropUnknownCommands):
        try:
            tidy()
        except OSError:                  # busy right now: the next start tidies it
            pass
