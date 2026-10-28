"""PSU entries from the hardware map; `formslab.hardware` finds their ports."""

from formslab import hardware


def enabled_psu_labels() -> tuple[str, ...]:
    """Return configured PSU labels that are enabled for operator control."""
    config = hardware.load()
    return tuple(
        label
        for label, entry in config.items()
        if label.lower().startswith("psu")
        and isinstance(entry, dict)
        and entry.get("enabled", True)
    )
