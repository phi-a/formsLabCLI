"""What each supply channel is wired to, from the hardware map.

A supply's entry in `usbmap.json` may say what its channels feed and which
rScript drives them:

    "psu1": { ...,
      "channels": {"1": {"feeds": "cryocooler board", "owner": "rCryoBoard"}} }

A channel with an owner is that script's while it runs; the rest are free for
plans and the console. A bench copy of the map written before `channels`
existed takes them from the packaged default, so the wiring is never unknown.
"""
from __future__ import annotations

import json

from formslab.config import USBMAP_NAME, default_path, usbmap_path


def _read(path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def wiring() -> dict[str, dict[str, dict]]:
    """{supply label: {channel: {feeds, owner}}} for every supply on the bench."""
    live, packaged = _read(usbmap_path()), _read(default_path(USBMAP_NAME))
    out = {}
    for label in dict.fromkeys([*live, *packaged]):
        entry = live.get(label, packaged.get(label))
        if not (isinstance(label, str) and label.lower().startswith("psu") and isinstance(entry, dict)):
            continue
        channels = entry.get("channels")
        if not isinstance(channels, dict):
            channels = (packaged.get(label) or {}).get("channels") or {}
        out[label.lower()] = {str(ch): dict(v) for ch, v in channels.items()
                              if isinstance(v, dict) and str(ch).isdigit()}
    return out


def channel(label: str, ch) -> dict:
    """{feeds, owner} for `label` channel `ch`; {} for a free channel."""
    return wiring().get(label.lower(), {}).get(str(ch), {})


def channel_of(owner: str) -> tuple[str, int] | None:
    """(supply label, channel) the rScript `owner` drives, or None."""
    for label, channels in wiring().items():
        for ch, info in channels.items():
            if str(info.get("owner", "")).lower() == owner.lower():
                return label, int(ch)
    return None


def supply_for(owner: str, default: tuple[str, int]) -> tuple[str, int]:
    """The channel `owner` drives, or `default` when the map does not say."""
    return channel_of(owner) or default
