"""Per-bench profile for an HVC-3500 chamber.

Endpoint, units, zone and sensor numbering are properties of one installed
chamber, not of the driver, and the ASCII protocol cannot report units or
mappings. They live in ``<config_dir>/tvac_bench.json``: seeded from the
packaged default on first read (the same pattern as ``usbmap.json``), then
edited by the operator and kept across upgrades.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from formslab.config import config_dir, default_path

PROFILE_NAME = "tvac_bench.json"


@dataclass
class BenchProfile:
    host: str
    port: int
    timeout_s: float = 3.0
    poll_interval_s: float = 5.0
    pressure_unit: str = "Torr"
    temperature_unit: str = "C"
    zones: dict[str, int] = field(default_factory=dict)        # "platen" -> 1
    sensors: dict[str, int] = field(default_factory=dict)      # "platen_ctrl" -> 2
    limits: dict[str, float] = field(default_factory=dict)     # "platen_setpoint_max_c" -> 200
    ascii_in_recipe: bool = False

    def zone(self, name: str) -> int:
        try:
            return int(self.zones[name])
        except KeyError:
            raise KeyError(f"zone {name!r} not in profile; have {sorted(self.zones)}") from None

    def setpoint_bounds(self, zone_name: str) -> tuple[float, float]:
        lo = self.limits.get(f"{zone_name}_setpoint_min_c", -180.0)
        hi = self.limits.get(f"{zone_name}_setpoint_max_c", 200.0)
        return float(lo), float(hi)

    @classmethod
    def from_dict(cls, d: dict) -> "BenchProfile":
        conn = d.get("connection", {})
        units = d.get("units", {})
        return cls(
            host=conn["host"],
            port=int(conn["port"]),
            timeout_s=float(conn.get("timeout_s", 3.0)),
            poll_interval_s=float(conn.get("poll_interval_s", 5.0)),
            pressure_unit=units.get("pressure", "Torr"),
            temperature_unit=units.get("temperature", "C"),
            zones={k: int(v) for k, v in d.get("zones", {}).items()},
            sensors={k: int(v) for k, v in d.get("sensors", {}).items()},
            limits={k: float(v) for k, v in d.get("limits", {}).items()},
            ascii_in_recipe=bool(d.get("hmi", {}).get("enable_ascii_in_recipe", False)),
        )


def profile_path() -> Path:
    """The live profile, seeded from the packaged default if absent."""
    live = config_dir() / PROFILE_NAME
    if not live.exists():
        shutil.copyfile(default_path(PROFILE_NAME), live)
    return live


def load_profile(path: Path | None = None) -> BenchProfile:
    with open(path or profile_path(), "r", encoding="utf-8") as f:
        return BenchProfile.from_dict(json.load(f))
