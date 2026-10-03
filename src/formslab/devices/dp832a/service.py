"""Shared PSU instance access for FORMS routines."""

from typing import Dict

from formslab.devices.dp832a.driver import PSU


_PSU_CACHE: Dict[str, PSU] = {}


def get_psu(label: str) -> PSU:
    """Return a shared PSU instance for the given label."""
    # Routines share PSU objects through this module-level cache instead of
    # passing references to each other directly.
    psu = _PSU_CACHE.get(label)
    if psu is None:
        # Keep first-touch behavior minimal. Individual subsystems such as
        # TVAC, SLTA, and CryoBoard are responsible for channel-specific
        # setup, and the generic PSU.configure() bootstrap can re-emit legacy
        # Rigol protection commands that some instruments reject.
        psu = PSU(label)
        # Connect now, before caching, so a routine learns at setup that its
        # supply is missing rather than at its first command.
        psu.connect()
        _PSU_CACHE[label] = psu
    return psu
