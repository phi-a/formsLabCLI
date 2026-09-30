"""
Experimental shroud ramp with bounded cryocooler trim.

Default behavior:
- Set both shrouds to 200 K
- Turn cryocooler on at the minimum configured drive
- Hold TC11 near 170 K by nudging CCVOUT / CCVRES within bounds
- Raise shrouds by 10 K every 4 hours until 280 K

Historical .zen usage (the .forms path refuses routines.load):
    routines.load = [
        {"name": "rEX_CryoRamp"}
    ]
"""

from __future__ import annotations

import json
import os
from math import isfinite
from time import time
from typing import Callable

from formslab.console.cast.castutils import ReadStatus, WriteCommand
from forms.runtime.routine import routine
from formslab.devices.cryoboard_utils import queue_cryo_request
from formslab.devices.cryo_config import ccvres_code_from_ohms, ccvres_ohms_from_code
from formslab.devices.psu_command_utils import build_psu_channel_request, queue_psu_request


name = os.path.splitext(os.path.basename(__file__))[0]
HOUR_S = 3600.0


class Event:
    def __init__(self, at: float, action: Callable[[], None], label: str):
        self.at = at
        self.action = action
        self.label = label
        self.done = False


class State:
    def __init__(self):
        self.initialized = False
        self.t0 = 0.0
        self.timeline = []
        self.ccvout = 12.0
        self.ccvres_code = 0
        self.last_trim = 0.0
        self.last_log = 0.0


_states: dict[str, State] = {}


def _get_state(config: dict) -> State:
    key = json.dumps(config or {}, sort_keys=True, default=str)
    if key not in _states:
        _states[key] = State()
    return _states[key]


def _finite(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _sync_cryo_state(state: State) -> None:
    status = ReadStatus("cryo")

    ccvout = _finite(status.get("CCV"))
    if ccvout is not None:
        state.ccvout = ccvout

    code = status.get("CCVRES#")
    if isinstance(code, (int, float)):
        state.ccvres_code = int(code)


def _write_shroud(forms, axis: str, temp_k: float) -> None:
    WriteCommand({axis: float(temp_k)}, "tvac")
    forms.log(f"{axis} shroud -> {temp_k:.1f} K", component=name)


def _write_psu2(forms, channel: int, voltage: float, current: float) -> None:
    queue_psu_request(
        "psu2",
        build_psu_channel_request(
            channel,
            voltage=float(voltage),
            current=float(current),
        ),
        update=True,
    )
    forms.log(
        f"PSU2 CH{channel} -> {voltage:.2f}V @ {current:.3f}A",
        component=name,
    )


def _write_psu2_on(forms, channel: int, enabled: bool) -> None:
    queue_psu_request(
        "psu2",
        build_psu_channel_request(channel, on=bool(enabled)),
        update=True,
    )
    forms.log(f"PSU2 CH{channel} -> {'ON' if enabled else 'OFF'}", component=name)


def _nudge_resistance(code: int, code_min: int, code_max: int, increase_cooling: bool, up_cools_more: bool) -> int:
    step = 1 if up_cools_more else -1
    if not increase_cooling:
        step *= -1
    return max(code_min, min(code_max, code + step))


@routine
def ex_cryo_ramp(forms, config):
    state = _get_state(config)
    
    shroud_start_k = float(config.get("shroud_start_k", 200.0))
    shroud_end_k = float(config.get("shroud_end_k", 280.0))
    shroud_step_k = float(config.get("shroud_step_k", 10.0))
    start_dwell_h = float(config.get("start_dwell_h", 4.0))
    step_dwell_h = float(config.get("step_dwell_h", 4.0))

    cryo_var = str(config.get("cryo_var", "TC11"))
    cryo_target_k = float(config.get("cryo_target_k", 170.0))
    cryo_band_k = float(config.get("cryo_band_k", 5.0))
    trim_s = float(config.get("trim_s", 300.0))
    log_s = float(config.get("log_s", 1800.0))

    ccvout_min_v = float(config.get("ccvout_min_v", 12.0))
    ccvout_max_v = float(config.get("ccvout_max_v", 20.0))
    ccvout_step_v = float(config.get("ccvout_step_v", 0.5))
    ccvres_min_ohm = float(config.get("ccvres_min_ohm", 200.0))
    ccvres_max_ohm = float(config.get("ccvres_max_ohm", 1000.0))
    ccvres_up_cools_more = bool(config.get("ccvres_up_cools_more", True))

    record_s = float(config.get("record_s", 10.0))
    slta_enable = bool(config.get("slta_enable", False))
    slta_v = float(config.get("slta_v", 12.0))
    slta_a = float(config.get("slta_a", 1.25))

    code_min = ccvres_code_from_ohms(ccvres_min_ohm)
    code_max = ccvres_code_from_ohms(ccvres_max_ohm)

    if not state.initialized:
        state.t0 = time()
        state.timeline = []
        state.ccvout = ccvout_min_v
        state.ccvres_code = code_min
        state.last_trim = state.t0
        state.last_log = state.t0

        def after(dt_s: float, action: Callable[[], None], label: str) -> None:
            state.timeline.append(Event(at=state.t0 + dt_s, action=action, label=label))

        after(0, lambda: forms.record(value=record_s, unit="seconds"), f"Record -> {record_s:.0f}s")
        after(0, lambda: _write_shroud(forms, "PY", shroud_start_k), f"PY -> {shroud_start_k:.1f}K")
        after(5, lambda: _write_shroud(forms, "MY", shroud_start_k), f"MY -> {shroud_start_k:.1f}K")
        after(
            10,
            lambda: queue_cryo_request(
                forms,
                component=name,
                voltage=state.ccvout,
                code=state.ccvres_code,
                enabled=True,
            ),
            "Cryo -> minimum drive ON",
        )

        if slta_enable:
            after(15, lambda: _write_psu2(forms, 1, slta_v, slta_a), "SLTA supply set")
            after(20, lambda: _write_psu2_on(forms, 1, True), "SLTA supply ON")

        t_s = start_dwell_h * HOUR_S
        temp_k = shroud_start_k + shroud_step_k
        while temp_k <= shroud_end_k + 1e-9:
            after(t_s, lambda temp_k=temp_k: _write_shroud(forms, "PY", temp_k), f"PY -> {temp_k:.1f}K")
            after(t_s + 5, lambda temp_k=temp_k: _write_shroud(forms, "MY", temp_k), f"MY -> {temp_k:.1f}K")
            t_s += step_dwell_h * HOUR_S
            temp_k += shroud_step_k

        after(
            t_s,
            lambda: forms.log("Shroud ramp complete; cryo trim loop still active", component=name),
            "Ramp complete",
        )

        state.initialized = True
        forms.log(
            f"Scheduled {len(state.timeline)} events for {shroud_start_k:.0f}->{shroud_end_k:.0f} K shroud ramp",
            component=name,
        )

    now = time()
    for event in state.timeline:
        if event.done or now < event.at:
            continue
        try:
            event.action()
            event.done = True
            forms.log(f"Executed: {event.label}", component=name)
        except Exception as exc:
            event.done = True
            forms.log(f"Event failed '{event.label}': {exc}", level="ERROR", component=name)

    _sync_cryo_state(state)

    if now - state.last_trim < trim_s:
        return

    state.last_trim = now

    try:
        cryo_temp_k = _finite(forms.get_variable(cryo_var).value)
    except Exception as exc:
        forms.log(f"Failed reading {cryo_var}: {exc}", level="WARNING", component=name)
        return

    if cryo_temp_k is None:
        return

    upper_k = cryo_target_k + cryo_band_k
    lower_k = cryo_target_k - cryo_band_k
    request = {}

    if cryo_temp_k > upper_k:
        next_code = _nudge_resistance(
            state.ccvres_code,
            code_min,
            code_max,
            increase_cooling=True,
            up_cools_more=ccvres_up_cools_more,
        )
        if next_code != state.ccvres_code:
            state.ccvres_code = next_code
            request["code"] = state.ccvres_code
        elif state.ccvout < ccvout_max_v:
            state.ccvout = min(ccvout_max_v, state.ccvout + ccvout_step_v)
            request["voltage"] = state.ccvout

    elif cryo_temp_k < lower_k:
        if state.ccvout > ccvout_min_v:
            state.ccvout = max(ccvout_min_v, state.ccvout - ccvout_step_v)
            request["voltage"] = state.ccvout
        else:
            next_code = _nudge_resistance(
                state.ccvres_code,
                code_min,
                code_max,
                increase_cooling=False,
                up_cools_more=ccvres_up_cools_more,
            )
            if next_code != state.ccvres_code:
                state.ccvres_code = next_code
                request["code"] = state.ccvres_code

    if request:
        queue_cryo_request(
            forms,
            voltage=request.get("voltage"),
            code=request.get("code"),
            enabled=True,
            component=name,
        )

    if now - state.last_log >= log_s:
        state.last_log = now
        forms.log(
            (
                f"{cryo_var}={cryo_temp_k:.2f}K "
                f"target={cryo_target_k:.2f}K "
                f"CCVOUT={state.ccvout:.2f}V "
                f"CCVRES={ccvres_ohms_from_code(state.ccvres_code):.1f}ohm "
                f"(#{state.ccvres_code})"
            ),
            component=name,
        )


def routine_reset(forms, config=None):
    """Clear per-config ramp state so the routine can be restarted cleanly."""
    key = json.dumps(config or {}, sort_keys=True, default=str)
    _states.pop(key, None)
