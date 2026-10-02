"""LACO VC/HVC-3500 thermal-vacuum controller: ASCII over TCP.

The controller's PLC owns interlocks, valve sequencing, thermal limits and
recovery. This driver only sends documented ASCII commands and reads state
back; it never reimplements sequencing. docs/HVC3500.md has the protocol and
its installed-firmware quirks. `python -m formslab.devices.hvc3500` is the
commissioning tool (cli.py).

    protocol.py   framing, parsing, fault decoding (pure)
    client.py     TCP client with guarded writes and a transaction log
    simulator.py  fake controller for tests and dry runs
    profile.py    per-bench profile (endpoint, units, zone/sensor map)
    cli.py        probe / snapshot / watch / raw / guarded writes / discover / simulate
"""
from .client import HVC3500Client, Transaction, WriteRefused
from .profile import BenchProfile, load_profile
from .protocol import ErrorStatus, ProtocolError, Reply

__all__ = [
    "HVC3500Client", "Transaction", "WriteRefused",
    "BenchProfile", "load_profile",
    "ErrorStatus", "ProtocolError", "Reply",
]
