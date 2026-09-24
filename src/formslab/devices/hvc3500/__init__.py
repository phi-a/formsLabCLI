"""LACO VC/HVC-3500 thermal-vacuum controller: ASCII over TCP.

The controller's PLC owns interlocks, valve sequencing, thermal limits and
recovery. This driver only sends documented ASCII commands and reads state
back; it never reimplements sequencing. See the notes in the `tvac` repo
(`Notebook/HVC3500_REMOTE_INTERFACE.md`) for the protocol and its
installed-firmware quirks.

    protocol.py   framing, parsing, fault decoding (pure)
    client.py     TCP client with guarded writes and a transaction log
    simulator.py  fake controller for tests and dry runs
    profile.py    per-bench profile (endpoint, units, zone/sensor map)
"""
from .client import HVC3500Client, Transaction, WriteRefused
from .profile import BenchProfile, load_profile
from .protocol import ErrorStatus, ProtocolError, Reply

__all__ = [
    "HVC3500Client", "Transaction", "WriteRefused",
    "BenchProfile", "load_profile",
    "ErrorStatus", "ProtocolError", "Reply",
]
