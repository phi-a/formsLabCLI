"""PSU entries from the shared lab USB map."""

import json
import sys
from pathlib import Path

from formslab.config import usbmap_path


def load_usbmap() -> dict:
    """Load the lab hardware map."""
    with usbmap_path().open(encoding="utf-8") as stream:
        return json.load(stream)


def enabled_psu_labels() -> tuple[str, ...]:
    """Return configured PSU labels that are enabled for operator control."""
    config = load_usbmap()
    return tuple(
        label
        for label, entry in config.items()
        if label.lower().startswith("psu")
        and isinstance(entry, dict)
        and entry.get("enabled", True)
    )


def resource_for(label: str, *, platform: str | None = None) -> str:
    """Resolve a PSU resource for the current operating system."""
    config = load_usbmap()
    entry = config.get(label)
    if not isinstance(entry, dict):
        raise ValueError(f"No USB mapping for PSU tag {label!r} in {usbmap_path()}")

    platform = platform or sys.platform
    resource_key = "resource_windows" if platform == "win32" else "resource"
    resource = entry.get(resource_key) or entry.get("resource")
    if not resource:
        raise ValueError(
            f"No valid {resource_key!r} or 'resource' for tag {label!r} "
            f"in {usbmap_path()}"
        )
    return str(resource)
