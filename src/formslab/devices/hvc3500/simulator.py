"""Fake HVC-3500 for testing the client without a chamber.

Implements the documented command surface from manual appendix 9.1 with a
small physical model: pressure decays toward the vacuum setpoint while the
rough valve is open, temperatures drift toward active zone setpoints, and
valve/pump toggles take effect ~1 s after the reply (as documented).

Knobs for exercising client robustness:
    terminator      reply terminator bytes (default CR; try CRLF or 0x13)
    one_shot        close the connection after every reply
    reply_delay_s   artificial latency before each reply
    interlocks      when True the gate valve refuses to open unless turbo on
"""
from __future__ import annotations

import re
import socket
import socketserver
import threading
import time


class ControllerState:
    def __init__(self) -> None:
        self.mode = "AUTO"
        self.test_status = "IDLE"
        self.recipe = 1
        self.step = 0
        self.vacuum_setpoint = 1.0e-5
        self.vacuum_range = 0.5
        self.vacuum_rate = 0.0
        self.hold_time = 600
        self.test_time = 0.0
        self.pressure = 760.0
        self.pressure_rate = 0.0
        self.fan_rpm = 0
        self.temps = [22.0 + 0.1 * i for i in range(21)]     # degrees
        zones = range(1, 8)                                   # firmware has 7 zones
        # commanded setpoint (what !Zn writes and the HMI shows). ?Zn returns the
        # *effective* setpoint: this value when the zone is active, otherwise the
        # zone's control sensor (bumpless tracking, as on the real controller).
        self.zone_setpoint = {z: 20.0 for z in zones}
        self.zone_rate = {z: 1.0 for z in zones}
        self.zone_range = {z: 1.0 for z in zones}
        self.zone_active = {z: False for z in zones}
        self.devices = {c: False for c in ("OR", "OV", "OF", "O4", "OG", "OP", "OT")}
        self.pending_toggles: list[tuple[float, str]] = []
        self.severity = "N"
        self.mask = 0
        self.cycle_running = False
        self.lock = threading.Lock()
        self._last = time.monotonic()

    def tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        for when, code in list(self.pending_toggles):
            if now >= when:
                self.devices[code] = not self.devices[code]
                self.pending_toggles.remove((when, code))
        if self.devices["OR"] and self.devices["OP"]:
            target = max(self.vacuum_setpoint, 1e-6)
            new = self.pressure * (0.1 ** dt) if self.pressure > target else target
            self.pressure_rate = (new - self.pressure) / dt if dt > 0 else 0.0
            self.pressure = new
        elif self.devices["OV"]:
            new = min(760.0, self.pressure + 200.0 * dt)
            self.pressure_rate = (new - self.pressure) / dt if dt > 0 else 0.0
            self.pressure = new
        else:
            self.pressure_rate = 0.0
        for z, active in self.zone_active.items():
            if active:
                sp = self.zone_setpoint[z]
                idx = z - 1
                step = min(abs(sp - self.temps[idx]), self.zone_rate[z] * 20.0 * dt)
                self.temps[idx] += step if sp > self.temps[idx] else -step
        if self.cycle_running:
            self.test_time += dt


_TEMP = re.compile(r"^T(\d{1,2})$")
_ZONE = re.compile(r"^(Z|ZR|RT|ZS|ZO)([1-7])$")


def _tenths(v: float) -> str:
    return str(int(round(v * 10)))


class Simulator:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, terminator: bytes = b"\r",
                 one_shot: bool = False, reply_delay_s: float = 0.0, interlocks: bool = True) -> None:
        self.state = ControllerState()
        self.terminator = terminator
        self.one_shot = one_shot
        self.reply_delay_s = reply_delay_s
        self.interlocks = interlocks
        sim = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                buf = b""
                while True:
                    try:
                        b = self.request.recv(1)
                    except OSError:
                        return          # client went away mid-command; not an error
                    if not b:
                        return
                    if b in (b"\r", b"\n", b"\x13"):
                        if not buf:
                            continue
                        reply = sim.handle_command(buf.decode("ascii", "replace"))
                        if sim.reply_delay_s:
                            time.sleep(sim.reply_delay_s)
                        self.request.sendall(reply.encode("ascii") + sim.terminator)
                        buf = b""
                        if sim.one_shot:
                            return
                    else:
                        buf += b

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.host = host
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "Simulator":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self) -> "Simulator":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----------------------------------------------------------- protocol
    def handle_command(self, cmd: str) -> str:
        s = self.state
        with s.lock:
            s.tick()
            if len(cmd) < 2 or cmd[0] not in "?!":
                return "ER"
            body = cmd[1:]
            key, _, val = body.partition(":")
            try:
                return self._read(key) if cmd[0] == "?" else self._write(key, val)
            except (ValueError, KeyError):
                return "ER"

    def _read(self, key: str) -> str:
        s = self.state
        simple = {
            "MC": lambda: s.mode,
            # installed firmware: third field reads 'Holding Temperature' while any zone is active
            "TC": lambda: f"TC:{s.test_status},Stand By," + ("Holding Temperature" if any(s.zone_active.values()) else "Ready"),
            "TR": lambda: f"TR:{s.recipe}",
            "RS": lambda: f"RS:{s.step}",
            "VS": lambda: f"VS:{s.vacuum_setpoint:.6E}",
            "VR": lambda: f"VR:{s.vacuum_range:.3f}",
            "VD": lambda: f"VD:{s.vacuum_rate:.3f}",
            "VH": lambda: f"VH:{int(s.hold_time)}",
            "TT": lambda: f"TT:{int(s.test_time)}",
            "VP": lambda: f"VP:{s.pressure:.7E}",
            "PR": lambda: f"PR:{s.pressure_rate:.4E}",
            "ES": lambda: f"ES: {s.severity}: {s.mask:010d}",
        }
        if key in simple:
            return simple[key]()
        if key in s.devices:
            return f"{key}: {'O' if s.devices[key] else 'C'}"
        m = _TEMP.match(key)
        if m and int(m.group(1)) <= 20:
            return f"{key}:{_tenths(s.temps[int(m.group(1))])}"
        m = _ZONE.match(key)
        if m:
            kind, z = m.group(1), int(m.group(2))
            if kind == "Z":
                effective = s.zone_setpoint[z] if s.zone_active[z] else s.temps[z - 1]
                return f"Z {z}: {_tenths(effective)}"        # padded key, as installed
            table = {"ZR": s.zone_rate, "RT": s.zone_range}
            if kind in table:
                return f"{key}:{_tenths(table[kind][z])}"
        raise ValueError(key)

    def _write(self, key: str, val: str) -> str:
        s = self.state
        if key == "CS":
            s.cycle_running = True
            s.test_status = "RUNNING"
            s.step = max(s.step, 1)
            return "CS"
        if key == "CA":
            s.cycle_running = False
            s.test_status = "ABORTED"
            s.mask |= 1 << 0
            s.severity = "F"
            return "CA"
        if key == "CR":
            s.cycle_running = False
            s.test_status = "IDLE"
            s.mask = 0
            s.severity = "N"
            s.step = 0
            return "CR"
        if key in ("RS", "RO"):
            s.cycle_running = key == "RS"
            return key
        if key == "VA":
            s.devices["OV"] = True
            return "VA"
        if key == "FA":
            s.devices["OF"] = True
            return "FA"
        if key in ("PS", "NA"):
            for c in ("OR", "OV", "OF", "O4", "OG"):
                s.devices[c] = False
            return key
        if key == "TR":
            n = int(val)
            if not 1 <= n <= 20:
                raise ValueError(val)
            s.recipe = n
            return f"TR:{n}"
        if key == "VS":
            s.vacuum_setpoint = float(val)
            return f"VS:{s.vacuum_setpoint:.6E}"
        if key == "VR":
            s.vacuum_range = float(val)
            return f"VR:{s.vacuum_range:.3f}"
        if key == "VD":
            s.vacuum_rate = float(val)
            return f"VD:{s.vacuum_rate:.3f}"
        if key == "VH":
            s.hold_time = int(val)
            return f"VH:{s.hold_time}"
        if key == "FN":
            s.fan_rpm = int(val)
            return f"FN:{s.fan_rpm}"
        if key in s.devices:
            state_now = "O" if s.devices[key] else "C"
            if self.interlocks and key == "OG" and not s.devices["OG"] and not s.devices["OT"]:
                return f"{key}: {state_now}"     # interlock: reply, no change
            if self.interlocks and key == "OP" and s.devices["OP"] and (s.devices["OR"] or s.devices["O4"]):
                return f"{key}: {state_now}"     # pump cannot stop with rough/foreline open
            s.pending_toggles.append((time.monotonic() + 1.0, key))
            return f"{key}: {state_now}"
        m = _ZONE.match(key)
        if m:
            kind, z = m.group(1), int(m.group(2))
            if kind == "Z":
                s.zone_setpoint[z] = int(val) / 10.0
                return f"Z {z}: {_tenths(s.zone_setpoint[z])}"   # echoes the commanded value
            if kind == "ZR":
                s.zone_rate[z] = int(val) / 10.0
                return f"{key}:{_tenths(s.zone_rate[z])}"
            if kind == "RT":
                s.zone_range[z] = int(val) / 10.0
                return f"{key}:{_tenths(s.zone_range[z])}"
            if kind == "ZS":
                s.zone_active[z] = True
                return key
            if kind == "ZO":
                s.zone_active[z] = False
                return key
        raise ValueError(key)


def main(argv=None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Run a fake HVC-3500 ASCII/TCP controller")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20256)
    ap.add_argument("--terminator", choices=["cr", "crlf", "x13"], default="cr")
    ap.add_argument("--one-shot", action="store_true", help="close after each reply")
    ap.add_argument("--delay", type=float, default=0.0, help="reply latency seconds")
    a = ap.parse_args(argv)
    term = {"cr": b"\r", "crlf": b"\r\n", "x13": b"\x13"}[a.terminator]
    sim = Simulator(a.host, a.port, terminator=term, one_shot=a.one_shot, reply_delay_s=a.delay)
    sim.start()
    print(f"simulator listening on {sim.host}:{sim.port} (terminator={a.terminator}, one_shot={a.one_shot})")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()


if __name__ == "__main__":
    main()
