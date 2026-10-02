"""HVC-3500 ASCII protocol: framing, parsing and fault decoding.

Everything here is pure (no sockets) so it can be unit-tested and reused by a
serial transport later. Source: HVC 3500 Manual rev A16, appendix 9.1 and
section 7.

Framing (manual 9.1.1):
    reads start with '?', writes start with '!'
    every command and reply ends with a carriage return
    unknown command -> 'ER<CR>'

The manual prints the terminator as 0x13; ASCII CR is 0x0D. We send 0x0D and
accept 0x0D, 0x0A or 0x13 when reading so the first live test cannot hang on a
framing guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CR = b"\r"
REPLY_TERMINATORS = (b"\r", b"\n", b"\x13")
ERROR_REPLY = "ER"

# Section 7 fault/warning numbers -> bit position in the ?ES UINT32 mask.
FAULT_NAMES: dict[int, str] = {
    0: "Abort",
    1: "Evac/Rough timeout",
    2: "Vent-to-atmosphere timeout",
    3: "Fill-to-atmosphere timeout",
    4: "High-vacuum warning",
    5: "Soft overtemperature",
    6: "Gauge disconnected",
    16: "Control air low",
    17: "Hard overtemperature (Watlow reset required)",
    18: "Gate valve not opening",
    19: "Gate valve not closing",
    20: "Chiller communications",
    21: "PLC/HMI to I/O communications",
    22: "High-vacuum enclosure communications",
    23: "Turbo fault",
    24: "Door open / massive leak",
    25: "Foreline pressure",
    26: "Chiller response",
    27: "Watlow communications",
    28: "Thermocouple card communications",
}

# The manual says three zones; the installed LACO firmware shows seven
# (zone 1 = platen, zone 2 = shroud, 3-7 monitor-only). Observed 2026-09-17.
MAX_ZONES = 7
MAX_TEMPERATURE_INPUT = 20

# Device codes for ?Ox / !Ox valve and pump commands.
DEVICE_CODES: dict[str, str] = {
    "OR": "rough valve",
    "OV": "vent valve",
    "OF": "fill valve",
    "O4": "foreline valve",
    "OG": "high-vacuum gate valve",
    "OP": "vacuum pump",
    "OT": "turbo pump",
}

# Write classes. The client treats them differently:
#   setpoint writes are idempotent and are verified by reading back
#   action writes change process state and are never retried
#   toggle writes flip a valve/pump and are read-before / verify-after
SETPOINT_WRITES = ("TR", "VS", "VR", "VD", "VH", "Z", "ZR", "RT", "FN")
ACTION_WRITES = ("CS", "CA", "RS", "RO", "VA", "FA", "PS", "NA", "CR", "ZS", "ZO")
TOGGLE_WRITES = tuple(DEVICE_CODES)

_COMMAND_RE = re.compile(r"^[?!][A-Z][A-Z0-9]{0,3}(:[-0-9.eE+]{1,16})?$")


class ProtocolError(Exception):
    """The controller answered, but not with something we can use."""


@dataclass(frozen=True)
class Reply:
    """A decoded reply line."""

    command: str        # what we sent, without terminator, e.g. '?VP'
    raw: str            # reply line without terminator, e.g. 'VP:7.5E-03'
    key: str | None     # 'VP' for 'VP:...', None for bare 'AUTO'
    value: str | None   # text after the first ':' (stripped), or None

    @property
    def is_error(self) -> bool:
        return self.raw == ERROR_REPLY


@dataclass(frozen=True)
class ErrorStatus:
    severity: str                     # 'N', 'W' or 'F'
    mask: int                         # raw UINT32
    active: tuple[int, ...] = field(default_factory=tuple)

    @property
    def names(self) -> list[str]:
        return [FAULT_NAMES.get(n, f"unknown bit {n}") for n in self.active]

    @property
    def ok(self) -> bool:
        return self.severity == "N" and self.mask == 0


def encode(command: str) -> bytes:
    """Validate a command string and append the carriage return."""
    if not _COMMAND_RE.match(command):
        raise ValueError(f"not a valid HVC-3500 command: {command!r}")
    return command.encode("ascii") + CR


def command_key(command: str) -> str:
    """'!Z1:855' -> 'Z1', '?VP' -> 'VP'. Used to match reply to request."""
    return command[1:].split(":", 1)[0]


def decode_reply(raw: bytes) -> str:
    """Strip any accepted terminator(s) and decode as ASCII."""
    text = raw
    while text and text[-1:] in REPLY_TERMINATORS:
        text = text[:-1]
    try:
        return text.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"non-ASCII reply {raw!r}") from exc


def parse_reply(command: str, raw_line: str) -> Reply:
    """Split a reply into key/value and check it belongs to `command`.

    Raises ProtocolError on 'ER' or a reply whose key does not match the
    command; callers decide whether that is fatal.
    """
    line = raw_line.strip()
    if line == ERROR_REPLY:
        raise ProtocolError(f"controller returned ER for {command!r}")
    if ":" in line:
        key, value = line.split(":", 1)
        # installed software pads some keys ('Z 1: 193', 'ZR 1: 0'); drop inner spaces
        reply = Reply(command, line, "".join(key.split()), value.strip())
    else:
        reply = Reply(command, line, None, None)
    expected = command_key(command)
    if command == "?MC":
        # installed software replies 'Manual' (mixed case); manual documents 'MANUAL'
        if line.upper() not in ("AUTO", "MANUAL"):
            raise ProtocolError(f"?MC expected AUTO/MANUAL, got {line!r}")
        return Reply(command, line.upper(), None, None)
    if reply.key is not None and reply.key != expected:
        raise ProtocolError(f"reply {line!r} does not match {command!r}")
    if reply.key is None and command != "?TC":
        # bare acknowledgements echo the command body, e.g. '!CS' -> 'CS'.
        # Installed software (project Rev8.06, v1.41.213) pads zone acks and
        # answers both '!ZSn' and '!ZOn' with 'ZS n' - the zone's status label,
        # not the command (observed 2026-09-24; HMI confirmed the zone went off).
        got = "".join(line.split())
        accepted = {expected}
        if expected.startswith("ZO"):
            accepted.add("ZS" + expected[2:])
        if got not in accepted:
            raise ProtocolError(f"reply {line!r} does not match {command!r}")
    return reply


_NUMBER_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def parse_number(value: str | None) -> float:
    """Pressure/time fields may be decimal or scientific notation."""
    if value is None or not _NUMBER_RE.match(value.strip()):
        raise ProtocolError(f"not a number: {value!r}")
    return float(value.strip())


def parse_temperature(value: str | None) -> float:
    """Temperatures are integers in tenths: '855' -> 85.5."""
    if value is None or not re.match(r"^[-+]?\d+$", value.strip()):
        raise ProtocolError(f"not a tenths integer: {value!r}")
    return int(value.strip()) / 10.0


def format_temperature(degrees: float) -> str:
    """85.5 -> '855'. Rounds to the nearest tenth."""
    return str(int(round(degrees * 10)))


def parse_error_status(value: str | None) -> ErrorStatus:
    """'W: 0000065536' -> ErrorStatus('W', 65536, (16,))."""
    if value is None:
        raise ProtocolError("empty ?ES value")
    parts = [p.strip() for p in value.split(":")]
    if len(parts) != 2 or parts[0] not in ("N", "W", "F") or not parts[1].isdigit():
        raise ProtocolError(f"unexpected ?ES value: {value!r}")
    mask = int(parts[1])
    if mask >= 1 << 32:
        raise ProtocolError(f"?ES mask exceeds UINT32: {mask}")
    active = tuple(bit for bit in range(32) if mask & (1 << bit))
    return ErrorStatus(parts[0], mask, active)


def parse_device_state(value: str | None) -> bool:
    """'O' -> True (open/on), 'C' -> False (closed/off)."""
    if value is None or value.strip() not in ("O", "C"):
        raise ProtocolError(f"unexpected valve/pump state: {value!r}")
    return value.strip() == "O"
