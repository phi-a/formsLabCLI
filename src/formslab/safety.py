"""The off switches below End: the supplies' outputs, and the PowerSwitch.

A run's own routines turn the supplies off when it ends. These are for when
no run is there to do it: none is going, or its host was lost. Two layers,
in this order (memory/rules/safety.md):

1. `supplies_off`: every enabled supply's outputs off, over its own VISA
   session, then the front panel handed back.
2. `outlet`: an outlet of the Digital Loggers PowerSwitch on, off or cycled,
   for a supply that does not answer: cycle the outlet it is on, then turn its
   outputs off again.

Both are refused while a live run is going, because its routines own the
supplies and the host's end does this properly. This is the one module
outside devices/ that opens a supply or the switch; `formslab.lab` never does.
"""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError

from formslab import lab
from formslab.config import usbmap_path
from formslab.devices.powerswitch import driver as switch

CONNECT_RETRY_S = 1.0        # a supply just freed by a killed host can be held for a moment
REACH_TIMEOUT_S = 2.0
SWITCH_TIMEOUT_S = 5.0
CYCLE_DELAY_S = 5.0
ACTIONS = ("on", "off", "cycle")


class Refused(Exception):
    """A run is going: its routines own the supplies."""


def _no_run() -> None:
    running = lab.host()
    if running is not None:
        raise Refused(f"a run is going ({running['plan']}): `labcli end` turns the supplies off; "
                      "`labcli kill` stops a host that will not")


# --- the supplies ---------------------------------------------------------------------

def supplies_off(labels: tuple[str, ...] | list[str] = ()) -> list[dict]:
    """Every output of each enabled supply (or of `labels`) off. One entry per
    supply: {label, ok, text}. A supply that is not reached is said, and the
    others still go off."""
    _no_run()
    from formslab.devices.dp832a.config import enabled_psu_labels
    from formslab.devices.dp832a.driver import CHANNELS, PSU

    out = []
    for label in list(labels) or list(enabled_psu_labels()):
        psu = PSU(label)
        try:
            try:
                psu.connect()
            except Exception:
                time.sleep(CONNECT_RETRY_S)
                psu.connect()
        except Exception as e:
            out.append({"label": label, "ok": False, "text": f"{label}: not reached: {e}"})
            continue
        try:
            psu.alloff()
            out.append({"label": label, "ok": True,
                        "text": f"{label}: {', '.join(f'CH{c}' for c in CHANNELS)} off"})
        except Exception as e:
            out.append({"label": label, "ok": False, "text": f"{label}: outputs not turned off: {e}"})
        finally:
            try:
                psu.disconnect()
            except Exception:
                pass
    return out


# --- the PowerSwitch ---------------------------------------------------------------------

def _feeds() -> dict:
    """What each outlet feeds, as the hardware map's PS entry says (`outlets`:
    {"3": "psu1"}); empty until it is written down at the bench."""
    try:
        raw = json.loads(usbmap_path().read_text(encoding="utf-8")).get("PS", {})
    except (OSError, ValueError, AttributeError):
        return {}
    feeds = raw.get("outlets") if isinstance(raw, dict) else None
    return {str(k): str(v) for k, v in feeds.items()} if isinstance(feeds, dict) else {}


def _reach(config) -> tuple[bool, str]:
    """Whether the switch answers with these credentials, and why not."""
    try:
        status = switch._http_get(f"http://{config.host}/", config.user, config.password, REACH_TIMEOUT_S)
    except HTTPError as e:
        why = f"{config.host} answered HTTP {e.code}"
        if e.code in (401, 403):
            why += f"; check the user name and {switch.PASSWORD_ENV}"
        return False, why
    except (URLError, TimeoutError, OSError) as e:
        return False, (f"{config.host} not reached: {e}. This computer should have {config.local_ip} on "
                       f"{config.adapter}; `labcli power setup` adds it")
    return 200 <= status < 400, f"reached, HTTP {status}"


def outlets() -> dict:
    """The switch as configured, whether it answers, and each outlet with what it feeds."""
    config = switch.load_config()
    ok, note = _reach(config)
    feeds = _feeds()
    return {"host": config.host, "adapter": config.adapter, "local_ip": config.local_ip,
            "reachable": ok, "note": note,
            "outlets": [{"number": n, "feeds": feeds.get(str(n), "")} for n in range(1, config.outlet_count + 1)]}


def outlet(number: int, action: str, delay_s: float = CYCLE_DELAY_S) -> dict:
    """Outlet `number` on, off, or cycled (off, `delay_s`, on). {ok, lines}:
    one line per switch made; nothing is sent when the switch does not answer."""
    _no_run()
    config = switch.load_config()
    if action not in ACTIONS:
        raise ValueError(f"an outlet is switched on, off or cycle, not {action!r}")
    if not 1 <= int(number) <= config.outlet_count:
        raise ValueError(f"an outlet is 1 to {config.outlet_count}, not {number}")
    ok, note = _reach(config)
    if not ok:
        return {"ok": False, "lines": [note]}
    feeds = _feeds().get(str(number), "")
    what = f"outlet {number}" + (f" ({feeds})" if feeds else "")
    lines = []
    for state in (("off", "on") if action == "cycle" else (action,)):
        try:
            status = switch._http_get(f"http://{config.host}/outlet?{number}={state.upper()}",
                                      config.user, config.password, SWITCH_TIMEOUT_S)
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            return {"ok": False, "lines": lines + [f"{what} {state}: failed: {e}"]}
        if not 200 <= status < 400:
            return {"ok": False, "lines": lines + [f"{what} {state}: failed: HTTP {status}"]}
        lines.append(f"{what} {state}")
        if action == "cycle" and state == "off":
            time.sleep(delay_s)
    return {"ok": True, "lines": lines}
