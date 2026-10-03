"""Digital Loggers PowerSwitch: outlet control over HTTP, and the network setup
the lab PC needs to reach it.

    driver.py     the operations (switch, cycle, check_reachability, setup_*)
                  and the command line
    __main__.py   python -m formslab.devices.powerswitch status | on | off | cycle | setup

The PSU tab runs the command line (`ps` target); see docs/POWERSWITCH.md.
"""
from .driver import (
    PowerSwitchConfig, check_reachability, cycle, load_config, main, setup_network, switch,
)

__all__ = ["PowerSwitchConfig", "check_reachability", "cycle", "load_config", "main",
           "setup_network", "switch"]
