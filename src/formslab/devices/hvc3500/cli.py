"""Command-line tool for the HVC-3500: commissioning, diagnosis, raw access.

    python -m formslab.devices.hvc3500 probe                    first discriminating test
    python -m formslab.devices.hvc3500 snapshot                 read-only status as JSON
    python -m formslab.devices.hvc3500 watch --interval 5       CSV logging loop
    python -m formslab.devices.hvc3500 raw "?VP"                one read command
    python -m formslab.devices.hvc3500 lifecycle                persistent vs one-shot check
    python -m formslab.devices.hvc3500 set-zone 1 25.0          guarded setpoint write
    python -m formslab.devices.hvc3500 set-vacuum 1e-4          guarded setpoint write
    python -m formslab.devices.hvc3500 device OR open --confirm guarded valve/pump toggle
    python -m formslab.devices.hvc3500 discover --subnet 10.1.2 --port 1   TCP scan of a /24
    python -m formslab.devices.hvc3500 simulate --port 20256    fake controller

Host, port, units and the zone/sensor names come from the bench profile
(`$FORMSLAB_CONFIG_DIR/tvac_bench.json`, or --profile PATH); --host/--port
override. Every command writes a JSONL transaction log to <output>/hvc3500/.

The controller takes one TCP client at a time: stop a running host (`run tvac`,
a plan) first. For operation, use the console's cast tab (`hvc ...`); this tool
is for commissioning and diagnosis.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import socket
import subprocess
import sys
import time

from . import protocol as P
from .client import HVC3500Client, ProtocolError, Transaction, WriteRefused


def _log_dir() -> str:
    from formslab.config import output_dir
    path = output_dir() / "hvc3500"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _log_path(name: str) -> str:
    return os.path.join(_log_dir(), f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.jsonl")


def _print_tx(tx: Transaction) -> None:
    status = "ok " if tx.ok else "ERR"
    extra = f"  {tx.error}" if tx.error else ""
    print(f"  {status} {tx.elapsed_ms:7.1f} ms  tx={tx.tx!s:<14} rx={tx.rx}{extra}")


def load_config(path: str | None) -> dict:
    """The bench profile as a dict: --profile PATH, else the live tvac_bench.json."""
    from .profile import profile_path
    try:
        with open(path or profile_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        if path:
            sys.exit(f"cannot read profile {path}: {exc}")
        return {}


def make_client(a, log_name: str) -> HVC3500Client:
    cfg = load_config(getattr(a, "profile", None))
    conn = cfg.get("connection", {})
    host = a.host or conn.get("host")
    port = a.port or conn.get("port")
    if not host or not port:
        sys.exit("need --host and --port (or a bench profile with connection host/port)")
    log = open(_log_path(log_name), "a", encoding="ascii")

    def on_tx(tx: Transaction) -> None:
        log.write(tx.as_json() + "\n")
        log.flush()
        if not a.quiet:
            _print_tx(tx)

    print(f"endpoint {host}:{port}  timeout={a.timeout}s  persistent={not a.one_shot}  log={log.name}",
          file=sys.stderr)   # keep stdout clean so `snapshot --quiet` is pure JSON
    c = HVC3500Client(host, int(port), timeout=a.timeout, persistent=not a.one_shot,
                      on_transaction=on_tx)
    c.config = cfg  # type: ignore[attr-defined]
    return c


# ----------------------------------------------------------------- commands
def cmd_probe(a) -> int:
    """Manual/notes 'first discriminating test': ?MC then a few reads."""
    c = make_client(a, "probe")
    print("step 1: TCP connect")
    try:
        c.connect()
    except OSError as exc:
        print(f"  FAIL connect: {exc}")
        print("  -> wrong IP/port, network path, firewall, or the CPU TCP service is not listening")
        return 2
    print("  connected")
    print("step 2: send ?MC<CR> (expect AUTO or MANUAL)")
    try:
        mode = c.mode()
    except socket.timeout:
        print("  FAIL no reply within timeout")
        print("  -> TCP is open but framing/terminator/command lifecycle is wrong; try --one-shot")
        return 3
    except ProtocolError as exc:
        print(f"  FAIL {exc}")
        print("  -> service reachable; the installed software answered differently than documented")
        return 4
    print(f"  mode = {mode}")
    print("step 3: read-only status queries")
    for name, fn in (("test_status", c.test_status), ("pressure", c.pressure),
                     ("error_status", c.error_status)):
        try:
            print(f"  {name} = {fn()}")
        except (OSError, ProtocolError) as exc:
            print(f"  {name} FAILED: {exc}")
    print("step 4: unknown command must return ER")
    try:
        c.transact("?QQ")
        print("  unexpected: controller accepted ?QQ")
    except ProtocolError as exc:
        print(f"  ok: {exc}")
    except OSError as exc:
        print(f"  no reply to unknown command: {exc}")
    c.close()
    print("PROBE PASSED: controller answers documented reads")
    return 0


def cmd_lifecycle(a) -> int:
    """Does the controller keep a TCP session open across commands?"""
    c = make_client(a, "lifecycle")
    print("test A: 5 x ?MC on one persistent connection")
    c.persistent = True
    ok_a = True
    try:
        for _ in range(5):
            c.mode()
    except (OSError, ProtocolError) as exc:
        ok_a = False
        print(f"  persistent session failed: {exc}")
    c.close()
    print(f"  persistent: {'OK' if ok_a else 'NOT supported'}")
    print("test B: 5 x ?MC with connect/close per command")
    c.persistent = False
    ok_b = True
    try:
        for _ in range(5):
            c.mode()
    except (OSError, ProtocolError) as exc:
        ok_b = False
        print(f"  one-shot failed: {exc}")
    print(f"  one-shot: {'OK' if ok_b else 'NOT supported'}")
    times = [t.elapsed_ms for t in c.transactions if t.ok]
    if times:
        print(f"  reply time: min {min(times):.1f} ms  max {max(times):.1f} ms")
    return 0 if (ok_a or ok_b) else 1


def cmd_raw(a) -> int:
    if a.command.startswith("!") and not a.allow_write:
        sys.exit("refusing a write command without --allow-write")
    c = make_client(a, "raw")
    try:
        r = c.transact(a.command)
        print(f"reply: {r.raw!r}  key={r.key!r} value={r.value!r}")
    except (OSError, ProtocolError) as exc:
        print(f"failed: {exc}")
        return 1
    finally:
        c.close()
    return 0


def _snapshot_args(c):
    cfg = getattr(c, "config", {})
    temps = [int(v) for v in cfg.get("sensors", {}).values()]
    zones = [int(v) for v in cfg.get("zones", {}).values()]
    return temps, zones


def _label(c, snap: dict) -> dict:
    """Add logical names from config next to the raw T#/Z# keys."""
    cfg = getattr(c, "config", {})
    for name, n in cfg.get("sensors", {}).items():
        if f"T{n}" in snap:
            snap[f"temp.{name}"] = snap[f"T{n}"]
    for name, z in cfg.get("zones", {}).items():
        if f"Z{z}_setpoint" in snap:
            snap[f"zone.{name}.setpoint"] = snap[f"Z{z}_setpoint"]
    units = cfg.get("units", {})
    if units:
        snap["units"] = units
    return snap


def cmd_snapshot(a) -> int:
    c = make_client(a, "snapshot")
    temps, zones = _snapshot_args(c)
    with c:
        snap = _label(c, c.snapshot(temps, zones))
    print(json.dumps(snap, indent=2))
    return 0 if "errors" not in snap else 1


def cmd_watch(a) -> int:
    c = make_client(a, "watch")
    temps, zones = _snapshot_args(c)
    a.quiet = True
    path = os.path.join(_log_dir(), f"{time.strftime('%Y%m%d_%H%M%S')}_watch.csv")
    print(f"writing {path} every {a.interval}s; Ctrl-C to stop")
    writer = None
    with c, open(path, "w", newline="", encoding="ascii") as f:
        try:
            for i in range(a.count if a.count > 0 else 10**9):
                snap = _label(c, c.snapshot(temps, zones))
                row = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in snap.items()}
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                f.flush()
                print(f"{time.strftime('%H:%M:%S')} mode={snap.get('mode')} "
                      f"P={snap.get('pressure')} ES={snap.get('error_status', {}).get('severity') if snap.get('error_status') else None}")
                time.sleep(a.interval)
        except KeyboardInterrupt:
            pass
    return 0


def cmd_set_zone(a) -> int:
    c = make_client(a, "set_zone")
    with c:
        before = c.zone_setpoint(a.zone)
        print(f"zone {a.zone} setpoint before: {before}")
        after = c.set_zone_setpoint(a.zone, a.degrees)
        print(f"zone {a.zone} setpoint after (read back): {after}")
    return 0


def cmd_set_vacuum(a) -> int:
    c = make_client(a, "set_vacuum")
    with c:
        print(f"vacuum setpoint before: {c.vacuum_setpoint()}")
        print(f"vacuum setpoint after (read back): {c.set_vacuum_setpoint(a.value)}")
    return 0


def cmd_device(a) -> int:
    c = make_client(a, "device")
    with c:
        try:
            state = c.set_device(a.code, a.state == "open", confirm=a.confirm)
        except WriteRefused as exc:
            sys.exit(str(exc))
        print(f"{P.DEVICE_CODES[a.code]}: {'open/on' if state else 'closed/off'}")
    return 0


def cmd_discover(a) -> int:
    """Ping sweep + TCP connect test on one port across a /24."""
    ips = [f"{a.subnet}.{i}" for i in range(1, 255)]
    print(f"sweeping {a.subnet}.0/24 (ICMP + TCP {a.port}) ...")

    def check(ip: str):
        r = subprocess.run(["ping", "-n", "1", "-w", "300", ip] if os.name == "nt"
                           else ["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True)
        pingable = "TTL=" in r.stdout or "ttl=" in r.stdout
        tcp = False
        if a.port:
            s = socket.socket()
            s.settimeout(0.7)
            try:
                s.connect((ip, a.port))
                tcp = True
            except OSError:
                pass
            finally:
                s.close()
        return ip, pingable, tcp

    with cf.ThreadPoolExecutor(64) as ex:
        for ip, pingable, tcp in ex.map(check, ips):
            if pingable or tcp:
                print(f"  {ip:<16} ping={'yes' if pingable else 'no '}  tcp{a.port}={'open' if tcp else 'closed'}")
    return 0


def cmd_simulate(a) -> int:
    from .simulator import main as sim_main
    sim_main(["--host", a.host or "127.0.0.1", "--port", str(a.port or 20256),
              "--terminator", a.terminator] + (["--one-shot"] if a.one_shot else []))
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m formslab.devices.hvc3500", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", help="bench profile JSON (default: the live tvac_bench.json)")
    common.add_argument("--host")
    common.add_argument("--port", type=int)
    common.add_argument("--timeout", type=float, default=2.0)
    common.add_argument("--one-shot", action="store_true", help="new TCP connection per command")
    common.add_argument("--quiet", action="store_true", help="do not echo transactions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", parents=[common]).set_defaults(fn=cmd_probe)
    sub.add_parser("lifecycle", parents=[common]).set_defaults(fn=cmd_lifecycle)
    p = sub.add_parser("raw", parents=[common])
    p.add_argument("command")
    p.add_argument("--allow-write", action="store_true")
    p.set_defaults(fn=cmd_raw)
    sub.add_parser("snapshot", parents=[common]).set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("watch", parents=[common])
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--count", type=int, default=0, help="stop after N samples (0 = forever)")
    p.set_defaults(fn=cmd_watch)
    p = sub.add_parser("set-zone", parents=[common])
    p.add_argument("zone", type=int)
    p.add_argument("degrees", type=float)
    p.set_defaults(fn=cmd_set_zone)
    p = sub.add_parser("set-vacuum", parents=[common])
    p.add_argument("value", type=float)
    p.set_defaults(fn=cmd_set_vacuum)
    p = sub.add_parser("device", parents=[common])
    p.add_argument("code", choices=sorted(P.DEVICE_CODES))
    p.add_argument("state", choices=["open", "closed"])
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(fn=cmd_device)
    p = sub.add_parser("discover")
    p.add_argument("--subnet", required=True, help="first three octets, e.g. 172.16.21")
    p.add_argument("--port", type=int, default=0)
    p.set_defaults(fn=cmd_discover)
    p = sub.add_parser("simulate")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--terminator", choices=["cr", "crlf", "x13"], default="cr")
    p.add_argument("--one-shot", action="store_true")
    p.set_defaults(fn=cmd_simulate)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
