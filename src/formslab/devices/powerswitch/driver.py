"""Configure and control the lab Digital Loggers PowerSwitch.

The PowerSwitch is a network appliance, not a serial PSU. Its durable lab
identity lives in the live ``usbmap.json`` under ``PS``. This module provides both
the HTTP outlet operations and the platform-specific network setup used by the
labcli PSU tab.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from formslab.config import usbmap_path


PASSWORD_ENV = "FORMS_POWERSWITCH_PASSWORD"


@dataclass(frozen=True)
class PowerSwitchConfig:
    label: str = "PS"
    description: str = "Digital Loggers PowerSwitch"
    host: str = "192.168.0.100"
    user: str = "darkness"
    password: str = "1024"
    adapter: str = "Ethernet 2"
    local_ip: str = "192.168.0.50"
    prefix_length: int = 24
    outlet_count: int = 8


def load_config(path: Path | None = None) -> PowerSwitchConfig:
    """Load the permanent ``PS`` lab record with backwards-safe defaults.

    Resolved at call time, not at import: the config directory is chosen by
    the environment, which a caller may set after this module is loaded.
    """
    path = path if path is not None else usbmap_path()
    raw: dict = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("PS", {})
    except (OSError, ValueError, TypeError):
        pass

    password_env = str(raw.get("password_env", PASSWORD_ENV))
    password = os.environ.get(password_env, str(raw.get("password", "1024")))
    return PowerSwitchConfig(
        label="PS",
        description=str(raw.get("description", "Digital Loggers PowerSwitch")),
        host=str(raw.get("host", "192.168.0.100")),
        user=str(raw.get("user", "darkness")),
        password=password,
        adapter=str(raw.get("adapter", "Ethernet 2")),
        local_ip=str(raw.get("local_ip", "192.168.0.50")),
        prefix_length=int(raw.get("prefix_length", 24)),
        outlet_count=int(raw.get("outlet_count", 8)),
    )


def _netmask(prefix_length: int) -> str:
    network = ipaddress.IPv4Network(f"0.0.0.0/{prefix_length}")
    return str(network.netmask)


def windows_setup_command(config: PowerSwitchConfig) -> list[str]:
    """Return the exact persistent Windows IPv4 command for ``config``."""
    return [
        "netsh",
        "interface",
        "ipv4",
        "set",
        "address",
        f"name={config.adapter}",
        "source=static",
        f"address={config.local_ip}",
        f"mask={_netmask(config.prefix_length)}",
        "gateway=none",
        "store=persistent",
    ]


def _run(
    command: Sequence[str],
    *,
    timeout: float = 15.0,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    return runner(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _print_process_failure(proc: subprocess.CompletedProcess) -> None:
    detail = (proc.stderr or proc.stdout or "command failed without output").strip()
    print(f"[error] {detail}")


def setup_windows(
    config: PowerSwitchConfig,
    *,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Persist the dedicated PowerSwitch IPv4 address on Windows."""
    inspect = _run(
        ["netsh", "interface", "show", "interface", f"name={config.adapter}"],
        runner=runner,
    )
    if inspect.returncode != 0:
        print(f"[error] Network adapter {config.adapter!r} was not found.")
        print("        Update lab/usbmap.json -> PS.adapter with the dedicated adapter name.")
        return False

    command = windows_setup_command(config)
    print(
        f"[setup] {config.label}: {config.adapter} -> "
        f"{config.local_ip}/{config.prefix_length} (persistent, no gateway)"
    )
    if dry_run:
        print("[dry-run] " + subprocess.list2cmdline(command))
        return True

    applied = _run(command, runner=runner)
    if applied.returncode != 0:
        _print_process_failure(applied)
        print("[hint] Re-open labcli with 'Run as administrator' and retry --setup.")
        return False

    print(f"[ok] Configured {config.adapter} for PowerSwitch {config.host}.")
    return True


def setup_linux(
    config: PowerSwitchConfig,
    *,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    """Configure the current Linux session without assuming a distro manager."""
    inspect = _run(["ip", "link", "show", "dev", config.adapter], runner=runner)
    if inspect.returncode != 0:
        print(f"[error] Network interface {config.adapter!r} was not found.")
        print("        Update lab/usbmap.json -> PS.adapter with the dedicated interface name.")
        return False

    commands = [
        ["ip", "addr", "replace", f"{config.local_ip}/{config.prefix_length}", "dev", config.adapter],
        ["ip", "link", "set", config.adapter, "up"],
    ]
    print(
        f"[setup] {config.label}: {config.adapter} -> "
        f"{config.local_ip}/{config.prefix_length}"
    )
    if dry_run:
        for command in commands:
            print("[dry-run] " + subprocess.list2cmdline(command))
        return True

    for command in commands:
        applied = _run(command, runner=runner)
        if applied.returncode != 0:
            _print_process_failure(applied)
            print("[hint] Run labcli with sufficient network-administration privileges.")
            return False
    print(f"[ok] Configured {config.adapter} for PowerSwitch {config.host}.")
    return True


def setup_network(
    config: PowerSwitchConfig,
    *,
    dry_run: bool = False,
    system: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    system = system or platform.system()
    if system == "Windows":
        return setup_windows(config, dry_run=dry_run, runner=runner)
    if system == "Linux":
        return setup_linux(config, dry_run=dry_run, runner=runner)
    print(f"[error] PowerSwitch network setup is not implemented for {system}.")
    return False


def _authorized_request(url: str, user: str, password: str) -> Request:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    request = Request(url)
    request.add_header("Authorization", f"Basic {token}")
    return request


def _http_get(url: str, user: str, password: str, timeout: float) -> int:
    with urlopen(_authorized_request(url, user, password), timeout=timeout) as response:
        return int(response.status)


def check_reachability(config: PowerSwitchConfig, timeout: float = 2.0) -> bool:
    """Check both network reachability and PowerSwitch authentication."""
    url = f"http://{config.host}/"
    try:
        status = _http_get(url, config.user, config.password, timeout)
        print(f"[status] {config.label} GET / -> HTTP {status}")
        return 200 <= status < 400
    except HTTPError as exc:
        print(f"[error] {config.label} responded with HTTP {exc.code}.")
        if exc.code in (401, 403):
            print(f"        Check {PASSWORD_ENV} and the configured username.")
        return False
    except (URLError, TimeoutError, OSError) as exc:
        print(f"[error] Cannot reach {config.label} at {config.host}: {exc}")
        print(
            f"        Expected {config.adapter} at "
            f"{config.local_ip}/{config.prefix_length}; run --setup and check cable/power."
        )
        return False


def switch(
    state: str,
    outlet: int,
    config: PowerSwitchConfig,
    timeout: float = 5.0,
) -> bool:
    if outlet < 1 or outlet > config.outlet_count:
        print(f"[error] Outlet {outlet} is outside 1-{config.outlet_count}.")
        return False
    url = f"http://{config.host}/outlet?{outlet}={state.upper()}"
    try:
        status = _http_get(url, config.user, config.password, timeout)
    except HTTPError as exc:
        print(f"[error] Outlet {outlet} {state.upper()} failed: HTTP {exc.code}")
        return False
    except (URLError, TimeoutError, OSError) as exc:
        print(f"[error] Request to outlet {outlet} failed: {exc}")
        return False
    if 200 <= status < 400:
        print(f"Outlet {outlet} {state.upper()} OK (HTTP {status})")
        return True
    print(f"[error] Outlet {outlet} {state.upper()} failed: HTTP {status}")
    return False


def cycle(outlet: int, delay: float, config: PowerSwitchConfig) -> bool:
    if not switch("OFF", outlet, config):
        return False
    time.sleep(delay)
    return switch("ON", outlet, config)


def build_parser(config: PowerSwitchConfig | None = None) -> argparse.ArgumentParser:
    config = config or load_config()
    parser = argparse.ArgumentParser(description="Configure and control the lab PowerSwitch")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--user", default=config.user)
    parser.add_argument("--password", default=config.password)
    parser.add_argument("--adapter", default=config.adapter)
    parser.add_argument("--local-ip", default=config.local_ip)
    parser.add_argument("--prefix-length", type=int, default=config.prefix_length)
    parser.add_argument("--outlet", type=int, default=1)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", help="show setup without changing the adapter")
    parser.add_argument("action", choices=["setup", "status", "on", "off", "cycle"])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    base = load_config()
    args = build_parser(base).parse_args(argv)
    config = PowerSwitchConfig(
        label=base.label,
        description=base.description,
        host=args.host,
        user=args.user,
        password=args.password,
        adapter=args.adapter,
        local_ip=args.local_ip,
        prefix_length=args.prefix_length,
        outlet_count=base.outlet_count,
    )

    if args.action == "setup":
        return 0 if setup_network(config, dry_run=args.dry_run) else 1
    if args.action == "status":
        return 0 if check_reachability(config) else 1
    if not check_reachability(config):
        return 1
    if args.action in ("on", "off"):
        return 0 if switch(args.action.upper(), args.outlet, config) else 1
    return 0 if cycle(args.outlet, args.delay, config) else 1


if __name__ == "__main__":
    raise SystemExit(main())
