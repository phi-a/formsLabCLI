"""
TVAC equilibrium controller.

Holds the shrouds at fixed setpoints indefinitely.

Config parameters:
    TpS: float = 240            +Shroud wall temperature (K)
    TmS: float = 240            -Shroud wall temperature (K)
    tR: int = 60                Record cadence (s)

Historical .zen usage (the .forms path refuses routines.load):
    routines.load = [
        {
            "name": "rTVAC_EQCTRL",
            "config": {
                "TpS": 240,
                "TmS": 240,
                "tR": 60
            }
        }
    ]
"""

import json
import os
from dataclasses import dataclass, field
from time import time
from typing import Callable, Dict, List, Optional

from formslab.console.cast.castutils import WriteCommand
from forms.utils.rScripts import RScriptControl
from forms.runtime.routine import routine


name = os.path.splitext(os.path.basename(__file__))[0]


@dataclass
class Event:
    at: float
    action: Callable[[], None]
    label: str
    done: bool = False


@dataclass
class State:
    initialized: bool = False
    t0: float = 0.0
    timeline: List[Event] = field(default_factory=list)


_states: Dict[str, State] = {}


def _get_state(config: Optional[dict]) -> State:
    key = json.dumps(config or {}, sort_keys=True, default=str)
    if key not in _states:
        _states[key] = State()
    return _states[key]


def _run(forms, state: State, TpS: float, TmS: float, tR: int) -> None:
    if not state.initialized:
        state.t0 = time()
        state.timeline = []

        def after(dt_s: float, action: Callable[[], None], label: str) -> None:
            state.timeline.append(Event(at=state.t0 + dt_s, action=action, label=label))

        def cmd_shroud(axis: str, temp_k: float):
            def _f():
                WriteCommand({axis: temp_k}, "tvac")
                forms.log(f"Shroud {axis} set -> {temp_k} K", component=name)

            return _f

        after(0, lambda: forms.record(value=tR, unit="seconds"), f"Record = {tR}s")
        after(0, cmd_shroud("PY", TpS), f"PY = {TpS} K")
        after(5, cmd_shroud("MY", TmS), f"MY = {TmS} K")

        state.initialized = True
        forms.log(
            f"EQCTRL initialized at t0={state.t0:.0f}. Holding {TpS}/{TmS} K indefinitely.",
            component=name,
        )

    now = time()
    for ev in state.timeline:
        if not ev.done and now >= ev.at:
            try:
                ev.action()
                ev.done = True
                forms.log(f"Executed event: {ev.label}", component=name)
            except Exception as exc:
                ev.done = True
                forms.log(f"Event '{ev.label}' failed: {exc}", level="ERROR", component=name)


@routine(tick=3)
def eqctrl(forms, config):
    state = _get_state(config)

    TpS = config.get("TpS", 240)
    TmS = config.get("TmS", 240)
    tR = config.get("tR", 60)

    _run(forms, state, TpS, TmS, tR)


def rScript(forms):
    try:
        ctrl = RScriptControl(forms, name)
        ctrl.tick(seconds=3)
        if ctrl:
            return
    except Exception as exc:
        forms.log(f"RScriptControl exception: {exc}", level="ERROR", component=name)
        return

    _run(forms, _get_state({}), 240, 240, 60)


def routine_reset(forms, config=None):
    """Clear per-config EQCTRL state so the routine can be restarted cleanly."""
    key = json.dumps(config or {}, sort_keys=True, default=str)
    _states.pop(key, None)
