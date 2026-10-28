"""USB devices, found one way: what is plugged in, matched to the hardware map.

    hardware.address(label)   the port or VISA resource to open for a map entry
    labcli hardware           every entry, found or not, in the console
    GET /api/hardware         the same, for the GUI's Hardware page

Each entry of the live map (`usbmap.json`) says how to tell its device apart,
the sturdiest first:

    "address": "COM3"            used as it is: a pinned port, RS-232, a TCPIP Rigol
    "serial":  "DP8B279M00280"   the device with that USB serial number, on any port
    "path":    "1-2.3.2"         the device at that place on the USB tree, for an
                                 adapter with no serial number (the SMTC08's CH340)
    "usb":     "1a86:7523"       its vendor and product id: checked beside a serial
                                 or a path, and alone only for the one of its kind

A place on the USB tree is numbered by each computer's own hubs, so each
machine's map holds its own; Windows and Linux both report it. Listing opens
nothing (pyserial, pyvisa), so all of this is safe while a run owns the
instruments. Two Picos are told apart by serial number or place, never guessed.

A map in the old form (`resource`, `resource_windows`, `interface_id`,
`serial_number`, ...) is converted the first time it is read; the old one is
kept beside it as `usbmap.json.old`.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from formslab.config import USBMAP_NAME, default_path, usbmap_path

_VISA_USB = re.compile(r"^USB\d*::0x([0-9a-f]{4})::0x([0-9a-f]{4})::([^:]+)::", re.I)
KEYS = ("address", "serial", "path", "usb")
BY = ("address", "serial number", "USB path", "USB id")
# The old form's keys: how each driver used to find its port, and what no code read.
OLD = ("resource", "resource_windows", "port", "port_windows", "interface_id", "serial_number",
       "vendor_id", "product_id", "hub_location", "hub_port", "windows_hub_port",
       "windows_location_path", "recovery_method", "auto_deploy", "firmware_version",
       "mac_address", "host_ip", "target_ip")
UNUSED = ("hub1", "slta")              # no code reads them; the camera has its addresses built in


def _serial_ports() -> tuple[list[dict], list[str]]:
    try:
        from serial.tools import list_ports
        return [{"address": p.device, "usb": f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "",
                 "serial": p.serial_number or "", "description": p.description or "",
                 "path": (p.location or "").split(":")[0]} for p in list_ports.comports()], []
    except Exception as e:                        # no pyserial, or the OS refused: say so, list the rest
        return [], [f"serial ports: {e}"]


def _visa() -> tuple[list[dict], list[str]]:
    try:
        import pyvisa
        rm = pyvisa.ResourceManager()
        try:
            found = []
            for r in rm.list_resources("USB?*::INSTR"):
                m = _VISA_USB.match(r)
                found.append({"address": r, "usb": f"{m[1]}:{m[2]}".lower() if m else "",
                              "serial": m[3] if m else "", "description": "VISA instrument", "path": ""})
            return found, []
        finally:
            rm.close()
    except Exception as e:
        return [], [f"VISA instruments: {e}"]


def plugged() -> tuple[list[dict], list[str]]:
    """Every serial port and VISA USB instrument this computer sees,
    {address, usb, serial, description, path}, and what could not be listed."""
    ports, e1 = _serial_ports()
    visa, e2 = _visa()
    return ports + visa, e1 + e2


# --- the map ------------------------------------------------------------------------

def load(path=None) -> dict:
    """The hardware map: the live one, or the one at `path`. The live map in the
    old form is converted once, first, and the old one kept as usbmap.json.old."""
    live = path is None
    path = usbmap_path() if live else Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if live and _old(raw) and path != default_path(USBMAP_NAME):
        from formslab.console.safefile import atomic_write_text

        raw = convert(raw, plugged()[0])
        try:
            backup = path.with_name(path.name + ".old")
            if not backup.exists():
                shutil.copyfile(path, backup)
            atomic_write_text(path, json.dumps(raw, indent=2) + "\n")
        except OSError:                           # a read-only home: use it converted, write it next time
            pass
    return raw


def _entries(raw: dict) -> dict:
    return {k: v for k, v in raw.items() if isinstance(v, dict) and not k.startswith("_")}


def entry(label: str, path=None) -> dict:
    """`label`'s entry. ValueError when the map has none, or it is off."""
    e = _entries(load(path)).get(label)
    if e is None:
        raise ValueError(f"{label} is not in the hardware map, {path or usbmap_path()}")
    if e.get("enabled", True) is False:
        raise ValueError(f'{label} is off in the hardware map ("enabled": false)')
    return e


def _old(raw: dict) -> bool:
    return any(label in UNUSED or any(k in e for k in OLD) for label, e in _entries(raw).items())


def convert(raw: dict, devices: list[dict]) -> dict:
    """The map in the new form. Each entry's old keys become `usb`, `serial`, `path`
    or `address`; a device plugged in now then gives its own: the serial number it
    reports, or its place when it has none. Keys no code reads go."""
    out, ids = {}, {}
    for label, e in raw.items():
        if label in UNUSED:
            continue
        if label not in _entries(raw) or "host" in e:
            out[label] = e
            continue
        here = (sys.platform == "win32" and (e.get("port_windows") or e.get("resource_windows"))) \
            or e.get("resource") or ""
        visa = _VISA_USB.match(str(e.get("resource_windows") or e.get("resource") or ""))
        vid, pid = e.get("vendor_id") or (visa and visa[1]), e.get("product_id") or (visa and visa[2])
        ids[label] = {"address": str(e.get("address") or ("" if visa else here)),
                      "serial": str(e.get("serial") or e.get("serial_number") or (visa[3] if visa else "")),
                      "path": str(e.get("path") or e.get("interface_id") or e.get("port") or "").split(":")[0],
                      "usb": str(e.get("usb") or (f"{vid}:{pid}" if vid and pid else "")).lower()}
        out[label] = {k: v for k, v in e.items() if k not in OLD}
    for label, (_, d) in _match(ids, devices).items():
        ids[label] = {"usb": d["usb"], "serial": d["serial"], "path": "" if d["serial"] else d["path"],
                      "address": "" if d["serial"] or d["path"] else d["address"]}
    for label, i in ids.items():
        if i["serial"] or i["path"]:
            i["address"] = ""                     # this computer's port name: the sturdier keys do without
        rest = out[label]
        out[label] = {**({"description": rest["description"]} if "description" in rest else {}),
                      **{k: i[k] for k in KEYS if i[k]}, **rest}
    return out


# --- matching -----------------------------------------------------------------------

def _ids(e: dict) -> list[str]:
    return [str(e.get(k) or "").lower() for k in KEYS]


def _network(e: dict) -> str:
    address = str(e.get("address") or "")
    return str(e.get("host") or (address if address.upper().startswith("TCPIP") else ""))


def _fits(ids: list[str], rule: int, d: dict) -> bool:
    have = (d["address"], d["serial"], d["path"], d["usb"])[rule].lower()
    usb_ok = rule in (0, 3) or not ids[3] or ids[3] == d["usb"].lower()   # a serial or a place, of that kind
    return bool(ids[rule]) and ids[rule] == have and usb_ok


def _match(entries: dict, devices: list[dict]) -> dict:
    """{label: (rule, device)} for each entry found, the sturdiest rule first, each
    device claimed once. A USB id alone finds a device only when it is the one of
    its kind plugged in and the one entry wanting it, and never past a serial."""
    found, free = {}, list(devices)
    wanted = {label: _ids(e) for label, e in entries.items() if not _network(e)}
    for rule in range(len(KEYS)):
        for label, ids in wanted.items():
            if label in found or not ids[rule]:
                continue
            fits = [d for d in free if _fits(ids, rule, d)]
            if rule == 3 and (ids[1] or len(fits) != 1
                              or sum(i[3] == ids[3] for k, i in wanted.items() if k not in found) != 1):
                continue
            if fits:
                found[label] = (rule, fits[0])
                free.remove(fits[0])
    return found


def _wants(e: dict) -> str:
    if e.get("address"):
        return f"address {e['address']}"
    return " ".join(filter(None, [str(e.get("usb") or ""),
                                  e.get("serial") and f"serial number {e['serial']}",
                                  e.get("path") and f"at USB path {e['path']}"]))


def _as_entry(d: dict) -> str:
    """What to put in the map for device `d`."""
    ids = ({"usb": d["usb"], "serial": d["serial"]} if d["serial"]
           else {"usb": d["usb"], "path": d["path"]} if d["path"] else {"address": d["address"]})
    return json.dumps({k: v for k, v in ids.items() if v})[1:-1]


def address(label: str, e: dict | None = None) -> str:
    """The port (COM5, /dev/ttyACM0) or VISA resource to open for `label`, or its
    entry `e`. ValueError when the map has no such entry or it is off;
    RuntimeError when its device is not plugged in."""
    e = entry(label) if e is None else e
    if e.get("address"):
        return str(e["address"])
    entries = {**_entries(load()), label: e}
    devices, errors = _serial_ports()             # cheap; a Rigol on USB is only in the VISA listing
    if label not in (found := _match(entries, devices)):
        visa, more = _visa()
        devices, errors = devices + visa, errors + more
        found = _match(entries, devices)
    if label in found:
        return found[label][1]["address"]
    there = ", ".join(f"{d['address']} ({d['usb']}{' serial ' + d['serial'] if d['serial'] else ''})"
                      for d in devices) or "nothing"
    raise RuntimeError(f"{label} wants {_wants(e) or 'nothing to find it by'}; plugged in: {there}"
                       + "".join(f"; could not list {x}" for x in errors) + ". See labcli hardware.")


def survey() -> dict:
    """{map, entries, plugged, errors}. Each entry: {label, description, enabled,
    status ('found', 'missing', 'network' or 'none'), by, address, wants}; each
    plugged device names the entry it was matched to (`label`, or None) and what
    to put in the map for it (`entry`)."""
    devices, errors = plugged()
    try:
        entries = _entries(load())
    except (OSError, ValueError) as e:
        entries = {}
        errors.append(f"the hardware map: {e}")
    found = _match(entries, devices)
    for d in devices:
        d.update(label=None, entry=_as_entry(d))
    rows = []
    for label, e in entries.items():
        rule, d = found.get(label, (None, None))
        net = _network(e)
        if d:
            d["label"] = label
        rows.append({"label": label, "description": str(e.get("description", "")),
                     "enabled": e.get("enabled", True) is not False,
                     "status": "found" if d else "network" if net else "missing" if any(_ids(e)) else "none",
                     "by": BY[rule] if d else None, "address": d["address"] if d else None,
                     "wants": f"network {net}" if net else _wants(e)})
    return {"map": str(usbmap_path()), "entries": rows, "plugged": devices, "errors": errors}
