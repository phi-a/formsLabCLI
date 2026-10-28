"""What is plugged in, beside what the hardware map expects.

    labcli hardware        the table, in the console
    GET /api/hardware      the same, for the GUI's Hardware page

Read-only. It lists the serial ports (pyserial) and the VISA USB instruments
(pyvisa) this computer sees, without opening any of them, so it is safe while a
run owns the instruments. Each entry of the live map (`usbmap.json`) is then
matched to one of them by what the map says about it, the sturdiest first:

1. its USB serial number (`serial_number`, or the one in a VISA `resource`)
2. its port name (`resource`; `resource_windows` on Windows): COM3, /dev/ttyUSB2
3. its place on the USB tree (`interface_id`, `port`): 1-4.3.1, which Windows
   and Linux both report, numbered by each computer's own hubs
4. its USB vendor and product id, for an entry without a serial number, and
   only when no other entry or device has the same: two Picos are told apart
   by serial number or place, never guessed

How an entry was found says how sturdy it is: a serial number follows the device
to any port; a port name changes with the order things are plugged in. An entry
with a network address (the PowerSwitch, the sLTA's Ethernet adapter) is not
looked for here.
"""
from __future__ import annotations

import json
import re
import sys

from formslab.config import usbmap_path

_VISA_USB = re.compile(r"^USB\d*::0x([0-9a-f]{4})::0x([0-9a-f]{4})::([^:]+)::", re.I)
_NETWORK = ("host", "host_ip", "target_ip")
BY = ("serial number", "port name", "USB path", "USB id")


def plugged() -> tuple[list[dict], list[str]]:
    """Every serial port and VISA USB instrument this computer sees,
    {address, usb, serial, description, path}, and what could not be listed."""
    found, errors = [], []
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            found.append({"address": p.device, "usb": f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "",
                          "serial": p.serial_number or "", "description": p.description or "",
                          "path": (p.location or "").split(":")[0]})
    except Exception as e:                        # no pyserial, or the OS refused: say so, list the rest
        errors.append(f"serial ports: {e}")
    try:
        import pyvisa
        rm = pyvisa.ResourceManager()
        try:
            for r in rm.list_resources("USB?*::INSTR"):
                m = _VISA_USB.match(r)
                found.append({"address": r, "usb": f"{m[1]}:{m[2]}".lower() if m else "",
                              "serial": m[3] if m else "", "description": "VISA instrument", "path": ""})
        finally:
            rm.close()
    except Exception as e:
        errors.append(f"VISA instruments: {e}")
    return found, errors


def _identity(entry: dict) -> list[str]:
    """What the map says to find `entry` by, one per rule in `BY` ('' where it says nothing)."""
    resource = (sys.platform == "win32" and entry.get("resource_windows")) or entry.get("resource") or ""
    visa = _VISA_USB.match(resource)
    vid, pid = entry.get("vendor_id") or (visa and visa[1]), entry.get("product_id") or (visa and visa[2])
    return [str(entry.get("serial_number") or (visa[3] if visa else "")),
            "" if visa else resource,
            str(entry.get("interface_id") or entry.get("port") or "").split(":")[0],
            f"{vid}:{pid}".lower() if vid and pid else ""]


def _match(want: str, rule: int, device: dict) -> bool:
    have = (device["serial"], device["address"], device["path"], device["usb"])[rule]
    return bool(want) and want.lower() == have.lower()


def survey() -> dict:
    """{map, entries, plugged, errors}. Each entry: {label, description, enabled,
    status ('found', 'missing', 'network' or 'none'), by, address, wants}; each
    plugged device names the entry it was matched to (`label`, or None)."""
    devices, errors = plugged()
    path = usbmap_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raw = {}
        errors.append(f"the hardware map: {e}")
    entries = []
    for label, entry in raw.items():
        if not isinstance(entry, dict) or label.startswith("_"):
            continue
        ids = _identity(entry)
        network = next((str(entry[k]) for k in _NETWORK if entry.get(k)), "")
        if network or not any(ids[:3]):           # on the network, or a vendor and product id alone: a hub
            ids = ["", "", "", ""]
        entries.append({"label": label, "description": str(entry.get("description", "")),
                        "enabled": entry.get("enabled", True) is not False, "ids": ids,
                        "status": "missing" if any(ids) else "network" if network else "none",
                        "by": None, "address": None,
                        "wants": next((f"{BY[i]} {v}" for i, v in enumerate(ids) if v),
                                      f"network {network}" if network else "")})
    for d in devices:
        d["label"] = None
    for rule in range(len(BY)):
        for e in entries:
            if e["status"] != "missing" or not (want := e["ids"][rule]):
                continue
            free = [d for d in devices if d["label"] is None and _match(want, rule, d)]
            if rule == 3 and (e["ids"][0] or len(free) != 1        # a serial number must match; never guess
                              or sum(x["ids"][3] == want for x in entries if x["status"] == "missing") != 1):
                continue
            if free:
                free[0]["label"] = e["label"]
                e.update(status="found", by=BY[rule], address=free[0]["address"])
    for e in entries:
        del e["ids"]
    return {"map": str(path), "entries": entries, "plugged": devices, "errors": errors}
