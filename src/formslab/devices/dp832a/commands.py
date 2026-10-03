"""Helpers for routing PSU channel commands through the CAST PSU owners."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from formslab.console.cast.castutils import ReadStatus, WriteCommand


def _channel_key(channel: int | str) -> str:
    return str(int(channel))


def build_psu_channel_request(
    channel: int | str,
    *,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    on: Optional[bool] = None,
    ovp: Optional[float] = None,
    ocp: Optional[float] = None,
    protect: Optional[bool] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build a normalized PSU CAST request payload for a single channel."""
    payload: Dict[str, Any] = {}

    if ovp is not None:
        payload["ovp"] = float(ovp)
    if ocp is not None:
        payload["ocp"] = float(ocp)
    if protect is not None:
        payload["protect"] = bool(protect)

    if voltage is not None or current is not None:
        if voltage is None or current is None:
            raise ValueError("PSU channel request requires both voltage and current")
        payload["voltage"] = float(voltage)
        payload["current"] = float(current)

    if on is not None:
        payload["on"] = bool(on)

    return {_channel_key(channel): payload}


def merge_psu_requests(*requests: Dict[str, Dict[str, Any]], update: Optional[bool] = None) -> Dict[str, Any]:
    """Merge multiple per-channel PSU request payloads into one CAST write."""
    merged: Dict[str, Any] = {}

    for request in requests:
        if not request:
            continue
        for key, value in request.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value

    if update is not None:
        merged["update"] = bool(update)

    return merged


def queue_psu_request(label: str, *requests: Dict[str, Dict[str, Any]], update: Optional[bool] = None) -> Dict[str, Any]:
    """Write a merged PSU request to the CAST channel for ``label``."""
    payload = merge_psu_requests(*requests, update=update)
    if payload:
        WriteCommand(payload, label)
    return payload


def read_psu_channel_status(label: str, channel: int | str) -> Dict[str, Any]:
    """Read the latest CAST status for a specific PSU channel."""
    status = ReadStatus(label) or {}
    key = _channel_key(channel)
    channel_status = status.get(key)
    if isinstance(channel_status, dict):
        return channel_status
    return {}


def channel_matches(
    channel_status: Dict[str, Any],
    *,
    on: Optional[bool] = None,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    voltage_tol: float = 0.25,
    current_tol: float = 0.25,
) -> bool:
    """Check whether a PSU channel status dict matches the desired state."""
    if on is not None and bool(channel_status.get("on")) != bool(on):
        return False

    if voltage is not None:
        vset = channel_status.get("vset")
        if vset is None or abs(float(vset) - float(voltage)) > float(voltage_tol):
            return False

    if current is not None:
        cset = channel_status.get("cset")
        if cset is None or abs(float(cset) - float(current)) > float(current_tol):
            return False

    return True


def classify_channel_status(
    channel_status: Dict[str, Any],
    *,
    on: Optional[bool] = None,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    voltage_tol: float = 0.25,
    current_tol: float = 0.25,
) -> str:
    """
    Classify a PSU channel status snapshot.

    Returns one of:
      - ``"match"`` when required fields are present and match the target
      - ``"mismatch"`` when required fields are present but do not match
      - ``"unknown"`` when the channel exists but required telemetry is missing
      - ``"missing"`` when no channel status is available at all
    """
    if not isinstance(channel_status, dict) or not channel_status:
        return "missing"

    required_fields = []
    if on is not None:
        required_fields.append("on")
    if voltage is not None:
        required_fields.append("vset")
    if current is not None:
        required_fields.append("cset")

    if any(channel_status.get(field) is None for field in required_fields):
        return "unknown"

    if channel_matches(
        channel_status,
        on=on,
        voltage=voltage,
        current=current,
        voltage_tol=voltage_tol,
        current_tol=current_tol,
    ):
        return "match"

    return "mismatch"


def psu_channel_matches(
    label: str,
    channel: int | str,
    *,
    on: Optional[bool] = None,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    voltage_tol: float = 0.25,
    current_tol: float = 0.25,
) -> bool:
    """Convenience wrapper around ``read_psu_channel_status`` + ``channel_matches``."""
    return channel_matches(
        read_psu_channel_status(label, channel),
        on=on,
        voltage=voltage,
        current=current,
        voltage_tol=voltage_tol,
        current_tol=current_tol,
    )


def psu_channel_state(
    label: str,
    channel: int | str,
    *,
    on: Optional[bool] = None,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    voltage_tol: float = 0.25,
    current_tol: float = 0.25,
) -> str:
    """Return ``match``, ``mismatch``, ``unknown``, or ``missing`` for a PSU channel."""
    return classify_channel_status(
        read_psu_channel_status(label, channel),
        on=on,
        voltage=voltage,
        current=current,
        voltage_tol=voltage_tol,
        current_tol=current_tol,
    )


def wait_for_psu_channel(
    label: str,
    channel: int | str,
    *,
    on: Optional[bool] = None,
    voltage: Optional[float] = None,
    current: Optional[float] = None,
    timeout: float = 5.0,
    poll_interval: float = 0.2,
    voltage_tol: float = 0.25,
    current_tol: float = 0.25,
) -> bool:
    """Poll CAST status until a PSU channel matches the desired state."""
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        if psu_channel_matches(
            label,
            channel,
            on=on,
            voltage=voltage,
            current=current,
            voltage_tol=voltage_tol,
            current_tol=current_tol,
        ):
            return True
        time.sleep(float(poll_interval))
    return False
