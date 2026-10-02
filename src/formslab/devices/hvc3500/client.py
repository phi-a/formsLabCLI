"""TCP client for the HVC-3500 ASCII interface.

Design rules (see docs/HVC3500.md):
- every transaction is logged with raw bytes and timestamps
- nothing is retried automatically; a timeout raises and marks the socket dirty
- setpoint writes are verified by reading back
- action writes and toggles require an explicit `confirm=True`
- toggles are read-before, single-send, wait, verify-after
"""
from __future__ import annotations

import json
import socket
import time
from dataclasses import asdict, dataclass
from typing import Callable

from . import protocol as P
from .protocol import ErrorStatus, ProtocolError, Reply


class WriteRefused(Exception):
    """A state-changing write was requested without confirm=True."""


@dataclass
class Transaction:
    t_start: float
    t_end: float
    tx: str
    rx: str
    ok: bool
    error: str | None = None
    reconnected: bool = False

    @property
    def elapsed_ms(self) -> float:
        return (self.t_end - self.t_start) * 1000.0

    def as_json(self) -> str:
        d = asdict(self)
        d["elapsed_ms"] = round(self.elapsed_ms, 1)
        return json.dumps(d)


class HVC3500Client:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float = 2.0,
        persistent: bool = True,
        inter_command_delay: float = 0.05,
        toggle_settle_s: float = 2.0,
        on_transaction: Callable[[Transaction], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.persistent = persistent
        self.inter_command_delay = inter_command_delay
        self.toggle_settle_s = toggle_settle_s
        self.on_transaction = on_transaction
        self.transactions: list[Transaction] = []
        self._sock: socket.socket | None = None
        self._dirty = False
        self._last_tx_time = 0.0

    # ------------------------------------------------------------------ transport
    def connect(self) -> None:
        self.close()
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.settimeout(self.timeout)
        self._dirty = False

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "HVC3500Client":
        if self.persistent:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read_line(self, sock: socket.socket) -> bytes:
        buf = b""
        while True:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError(f"connection closed by controller after {buf!r}")
            if chunk in P.REPLY_TERMINATORS:
                if not buf:
                    continue        # stray LF from a previous CRLF reply; not a frame
                return buf + chunk
            buf += chunk
            if len(buf) > 256:
                raise ProtocolError(f"reply longer than 256 bytes without terminator: {buf!r}")

    def transact(self, command: str) -> Reply:
        """Send one command and return its parsed reply. Never retries."""
        payload = P.encode(command)
        reconnected = False
        gap = self.inter_command_delay - (time.monotonic() - self._last_tx_time)
        if gap > 0:
            time.sleep(gap)
        t0 = time.time()
        raw = b""
        try:
            if not self.persistent or self._sock is None or self._dirty:
                self.connect()
                reconnected = True
            assert self._sock is not None
            self._sock.sendall(payload)
            self._last_tx_time = time.monotonic()
            raw = self._read_line(self._sock)
            line = P.decode_reply(raw)
            reply = P.parse_reply(command, line)
        except (OSError, ProtocolError) as exc:
            self._dirty = True
            self._record(t0, command, raw, False, f"{type(exc).__name__}: {exc}", reconnected)
            raise
        finally:
            if not self.persistent:
                self.close()
        self._record(t0, command, raw, True, None, reconnected)
        return reply

    def _record(self, t0, command, raw, ok, error, reconnected) -> None:
        tx = Transaction(t0, time.time(), command + "<CR>", repr(raw)[2:-1], ok, error, reconnected)
        self.transactions.append(tx)
        if self.on_transaction:
            self.on_transaction(tx)

    # --------------------------------------------------------------------- reads
    def mode(self) -> str:
        return self.transact("?MC").raw

    def test_status(self) -> str:
        """Free text, e.g. 'Recovery/Ready,Stand By,Ready'. On the installed
        firmware the third field becomes 'Holding Temperature' while any zone's
        thermal control is on (observed 2026-09-24 with the HMI Z1 button)."""
        r = self.transact("?TC")
        return r.value if r.value is not None else r.raw

    def thermal_control_active(self) -> bool:
        """True if the controller reports it is holding temperature. The only
        read-only indicator of zone activation found so far; it does not say
        which zone."""
        return "holding temperature" in self.test_status().lower()

    def recipe(self) -> int:
        return int(P.parse_number(self.transact("?TR").value))

    def recipe_step(self) -> int:
        return int(P.parse_number(self.transact("?RS").value))

    def pressure(self) -> float:
        return P.parse_number(self.transact("?VP").value)

    def pressure_rate(self) -> float:
        return P.parse_number(self.transact("?PR").value)

    def vacuum_setpoint(self) -> float:
        return P.parse_number(self.transact("?VS").value)

    def vacuum_range(self) -> float:
        return P.parse_number(self.transact("?VR").value)

    def vacuum_rate(self) -> float:
        return P.parse_number(self.transact("?VD").value)

    def hold_time(self) -> float:
        return P.parse_number(self.transact("?VH").value)

    def test_time(self) -> float:
        return P.parse_number(self.transact("?TT").value)

    def temperature(self, n: int) -> float:
        self._check_index(n, 0, P.MAX_TEMPERATURE_INPUT, "temperature input")
        return P.parse_temperature(self.transact(f"?T{n}").value)

    def zone_setpoint(self, zone: int) -> float:
        """The zone's *effective* setpoint. While thermal control is off the PLC
        slaves this to the control sensor (bumpless tracking); once `!ZSn` is
        active it equals the commanded value from `set_zone_setpoint`."""
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return P.parse_temperature(self.transact(f"?Z{zone}").value)

    def zone_rate(self, zone: int) -> float:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return P.parse_temperature(self.transact(f"?ZR{zone}").value)

    def zone_range(self, zone: int) -> float:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return P.parse_temperature(self.transact(f"?RT{zone}").value)

    def error_status(self) -> ErrorStatus:
        return P.parse_error_status(self.transact("?ES").value)

    def device_state(self, code: str) -> bool:
        """True = open/on. code is one of OR OV OF O4 OG OP OT."""
        if code not in P.DEVICE_CODES:
            raise ValueError(f"unknown device code {code!r}")
        return P.parse_device_state(self.transact(f"?{code}").value)

    def snapshot(self, temperatures=(), zones=(), devices: bool = True,
                 stop: Callable[[], bool] | None = None) -> dict:
        """Read-only status snapshot. Per-item failures are recorded, not raised.

        `stop` is checked before each read; once it returns True the remaining
        reads are skipped and the result carries ``"interrupted": True`` -- so a
        waiting command need not sit behind a whole snapshot."""
        out: dict = {"t": time.time()}

        def take(name, fn):
            if out.get("interrupted") or (stop is not None and stop()):
                out["interrupted"] = True
                return
            try:
                out[name] = fn()
            except (OSError, ProtocolError) as exc:
                out[name] = None
                out.setdefault("errors", {})[name] = f"{type(exc).__name__}: {exc}"

        take("mode", self.mode)
        take("test_status", self.test_status)
        take("pressure", self.pressure)
        take("pressure_rate", self.pressure_rate)
        take("vacuum_setpoint", self.vacuum_setpoint)
        take("recipe", self.recipe)
        take("recipe_step", self.recipe_step)
        take("test_time", self.test_time)

        def es_dict():
            es = self.error_status()
            return {"severity": es.severity, "mask": es.mask,
                    "active": list(es.active), "names": es.names}

        take("error_status", es_dict)
        for n in temperatures:
            take(f"T{n}", lambda n=n: self.temperature(n))
        for z in zones:
            take(f"Z{z}_setpoint", lambda z=z: self.zone_setpoint(z))
            take(f"Z{z}_rate", lambda z=z: self.zone_rate(z))
            take(f"Z{z}_range", lambda z=z: self.zone_range(z))
        if devices:
            for code in P.DEVICE_CODES:
                take(code, lambda c=code: self.device_state(c))
        return out

    # -------------------------------------------------------- setpoint writes
    def _write_setpoint(self, key: str, value_text: str, read_back: Callable[[], float],
                        expected: float, tol: float, *, verify: str = "readback",
                        parse=None) -> float:
        """Write a setpoint and verify it.

        verify="readback": re-query the value and compare (default).
        verify="echo": trust the controller's echo of the stored value. Used
        for zone setpoints, where `?Zn` returns the *effective* setpoint,
        which tracks the control sensor while the zone is idle, not the
        commanded value just written (observed on the HMI 2026-09-24).
        """
        reply = self.transact(f"!{key}:{value_text}")
        if reply.value is None:
            raise ProtocolError(f"setpoint write got bare reply {reply.raw!r}")
        if verify == "echo":
            echoed = (parse or P.parse_number)(reply.value)
            if abs(echoed - expected) > tol:
                raise ProtocolError(f"{key} echoed {echoed} after writing {expected}")
            return echoed
        actual = read_back()
        if abs(actual - expected) > tol:
            raise ProtocolError(f"{key} read back {actual} after writing {expected}")
        return actual

    def set_vacuum_setpoint(self, value: float) -> float:
        return self._write_setpoint("VS", repr(float(value)), self.vacuum_setpoint,
                                    value, abs(value) * 1e-3 + 1e-9)

    def set_vacuum_range(self, value: float) -> float:
        return self._write_setpoint("VR", repr(float(value)), self.vacuum_range,
                                    value, abs(value) * 1e-3 + 1e-9)

    def set_vacuum_rate(self, value: float) -> float:
        return self._write_setpoint("VD", repr(float(value)), self.vacuum_rate,
                                    value, abs(value) * 1e-3 + 1e-9)

    def set_hold_time(self, value: float) -> float:
        return self._write_setpoint("VH", str(int(value)), self.hold_time, int(value), 0.5)

    def select_recipe(self, n: int) -> int:
        self._check_index(n, 1, 20, "recipe")
        return int(self._write_setpoint("TR", str(n), self.recipe, n, 0.5))

    def set_zone_setpoint(self, zone: int, degrees: float) -> float:
        """Set the commanded setpoint for a zone (what the HMI shows and the PLC
        uses once `!ZSn` activates the zone). Verified by the controller's echo;
        `zone_setpoint()` will not reflect it until the zone is active."""
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return self._write_setpoint(f"Z{zone}", P.format_temperature(degrees),
                                    lambda: self.zone_setpoint(zone), round(degrees, 1), 0.05,
                                    verify="echo", parse=P.parse_temperature)

    def set_zone_rate(self, zone: int, deg_per_min: float) -> float:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return self._write_setpoint(f"ZR{zone}", P.format_temperature(deg_per_min),
                                    lambda: self.zone_rate(zone), round(deg_per_min, 1), 0.05)

    def set_zone_range(self, zone: int, degrees: float) -> float:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return self._write_setpoint(f"RT{zone}", P.format_temperature(degrees),
                                    lambda: self.zone_range(zone), round(degrees, 1), 0.05)

    # ---------------------------------------------------------- action writes
    def action(self, code: str, *, confirm: bool = False) -> Reply:
        """Send a non-idempotent command (CS CA RS RO VA FA PS NA CR ZSn ZOn).

        Requires confirm=True. Never retried: a timeout after sending leaves the
        controller state unknown and the caller must read state before deciding
        what to do next.
        """
        base = code.rstrip("0123456789")
        if base not in P.ACTION_WRITES:
            raise ValueError(f"{code!r} is not an action command")
        if not confirm:
            raise WriteRefused(f"!{code} changes process state; pass confirm=True")
        return self.transact(f"!{code}")

    def activate_zone(self, zone: int, *, confirm: bool = False) -> Reply:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return self.action(f"ZS{zone}", confirm=confirm)

    def deactivate_zone(self, zone: int, *, confirm: bool = False) -> Reply:
        self._check_index(zone, 1, P.MAX_ZONES, "zone")
        return self.action(f"ZO{zone}", confirm=confirm)

    def set_device(self, code: str, want_open: bool, *, confirm: bool = False) -> bool:
        """Drive a valve/pump to a desired state using the toggle command.

        Read first; if already in the wanted state send nothing. Otherwise send
        exactly one toggle, wait past the documented ~1 s actuation delay, read
        again and return the verified state. Raises ProtocolError if the state
        did not change (an interlock most likely blocked it).
        """
        if code not in P.DEVICE_CODES:
            raise ValueError(f"unknown device code {code!r}")
        if not confirm:
            raise WriteRefused(f"!{code} toggles the {P.DEVICE_CODES[code]}; pass confirm=True")
        before = self.device_state(code)
        if before == want_open:
            return before
        self.transact(f"!{code}")          # reply reports state at receipt; ignore it
        time.sleep(self.toggle_settle_s)
        after = self.device_state(code)
        if after != want_open:
            raise ProtocolError(
                f"{P.DEVICE_CODES[code]} still {'open' if after else 'closed'} "
                f"{self.toggle_settle_s}s after toggle; interlock likely. Not retrying.")
        return after

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _check_index(n: int, lo: int, hi: int, what: str) -> None:
        if not isinstance(n, int) or not lo <= n <= hi:
            raise ValueError(f"{what} must be an integer {lo}..{hi}, got {n!r}")

    def dump_log(self, path: str) -> None:
        with open(path, "a", encoding="ascii") as f:
            for tx in self.transactions:
                f.write(tx.as_json() + "\n")
