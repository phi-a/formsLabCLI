"""
Parameterized shroud temperature ramp sequence.

Cools the cryocooler, then ramps the shrouds in fixed steps, holding at each
setpoint. Optionally hands off to another routine (typically rTVAC_EQCTRL) on
completion.

Phases:
    0. Initial setup         — shrouds to T_START, cryo prepared (disabled)
    1. Rest / settle (tREST) — shrouds stabilize at T_START, optional SLTA on
    2. CC cooldown (tCD)     — cryocooler on, system cools at T_START
    3. Ramp sweep            — every tHOLD hours, step shrouds by T_STEP toward T_END
    4. Final hold (tEQ)      — hold at T_END with CC on
    5. Handoff               — CC shutdown, transition to next_routine

Config parameters:
    T_START: float = 220       Starting shroud temperature (K)
    T_END: float = 280         Final shroud temperature (K)
    T_STEP: float = 10         Shroud temperature increment per hold (K)
    CCV: float = 17            Cryocooler voltage (V)
    CCVRES: float = None       Optional cryocooler resistance (ohm)
    tREST: float = 0.5         Rest / settle time before CC turns on (h)
    tCD: float = 1.5           Cryocooler cooldown time before ramp starts (h)
    tHOLD: float = 1           Hold time per ramp step (h)
    tEQ: float = 1             Equilibrium hold time at T_END (h)
    SLTA: bool = False         Enable SLTA heat source
    tR: int = 30               Default record cadence (s)
    tRESTr: int = tR           Rest / settle record cadence (s)
    tCDr: int = 1              Cooldown record cadence (s)
    tHOLDr: int = tR           Ramp-step record cadence (s)
    tEQr: int = tR             Final equilibrium record cadence (s)
    next_routine: str = ""     Routine to start after completion
    next_TpS: float = 220      +Shroud setpoint for handoff (K, defaults to T_START if omitted)
    next_TmS: float = 220      -Shroud setpoint for handoff (K, defaults to T_START if omitted)
    next_tR: int = 60          Record cadence for rTVAC_EQCTRL handoff

Historical .zen usage (the .forms path refuses routines.load):
    routines.load = [
        {
            "name": "rShroudRamp",
            "config": {
                "T_START": 220,
                "T_END": 280,
                "T_STEP": 10,
                "CCV": 17,
                "CCVRES": 266,
                "tREST": 0.5,
                "tCD": 1.5,
                "tHOLD": 1,
                "tEQ": 1,
                "SLTA": False,
                "tR": 30,
                "next_routine": "rTVAC_EQCTRL",
                "next_TpS": 220,
                "next_TmS": 220,
                "next_tR": 60
            }
        }
    ]
"""
from time import time
from dataclasses import dataclass, field
from typing import Callable, Dict

from forms.runtime.routine import routine
from formslab.console.cast.castutils import WriteCommand
from formslab.devices.cryoboard_utils import queue_cryo_request
from formslab.devices.psu_command_utils import build_psu_channel_request, queue_psu_request


@dataclass
class Event:
    """Scheduled event in the timeline."""
    at: float
    action: Callable[[], None]
    label: str
    done: bool = False


@dataclass
class State:
    """Per-instance state for shroud ramp routine."""
    initialized: bool = False
    completed: bool = False
    t0: float = 0
    timeline: list = field(default_factory=list)


_states: Dict[int, State] = {}


def _get_state(config: dict) -> State:
    """Get or create state for this config."""
    key = hash(frozenset(config.items()))
    if key not in _states:
        _states[key] = State()
    return _states[key]


@routine(hold=10)
def shroudramp(forms, config):
    """Execute parameterized shroud ramp sequence."""
    state = _get_state(config)

    T_START = config.get("T_START", 220)
    T_END = config.get("T_END", 280)
    T_STEP = config.get("T_STEP", 10)
    CCV = config.get("CCV", 17)
    CCVRES = config.get("CCVRES", None)

    tREST = config.get("tREST", 0.5)
    tCD = config.get("tCD", 1.5)
    tHOLD = config.get("tHOLD", 1)
    tEQ = config.get("tEQ", 1)
    SLTA = config.get("SLTA", False)
    tR = config.get("tR", 30)
    tRESTr = config.get("tRESTr", tR)
    tCDr = config.get("tCDr", 1)
    tHOLDr = config.get("tHOLDr", tR)
    tEQr = config.get("tEQr", tR)

    next_routine = str(config.get("next_routine", "") or "").strip()
    next_TpS = config.get("next_TpS", T_START)
    next_TmS = config.get("next_TmS", T_START)
    next_tR = config.get("next_tR", 60)

    name = f"ramp_{T_START}K_{T_END}K_{CCV}V"
    H = 3600  # seconds per hour

    if not state.initialized:
        state.t0 = time()
        state.timeline = []

        def after(dt, action, label):
            state.timeline.append(Event(at=state.t0 + dt, action=action, label=label))

        def cmd_shroud(key, val):
            def _f():
                WriteCommand({key: val}, "tvac")
                forms.log(f"Shroud {key} -> {val} K", component=name)
            return _f

        def cmd_psu2(ch, v, c):
            def _f():
                queue_psu_request(
                    "psu2",
                    build_psu_channel_request(ch, voltage=v, current=c),
                    update=True,
                )
                forms.log(f"PSU2 CH{ch} -> {v:.2f}V, {c:.3f}A", component=name)
            return _f

        def cmd_cryo(*, voltage=None, resistance=None, enabled=None, startup=None, shutdown=None):
            def _f():
                queue_cryo_request(
                    forms,
                    component=name,
                    voltage=voltage,
                    resistance=resistance,
                    enabled=enabled,
                    startup=startup,
                    shutdown=shutdown,
                )
            return _f

        def cmd_psu2_on(ch):
            def _f():
                queue_psu_request(
                    "psu2",
                    build_psu_channel_request(ch, on=True),
                    update=True,
                )
                forms.log(f"PSU2 CH{ch} ON", component=name)
            return _f

        def cmd_psu2_off(ch):
            def _f():
                queue_psu_request(
                    "psu2",
                    build_psu_channel_request(ch, on=False),
                    update=True,
                )
                forms.log(f"PSU2 CH{ch} OFF", component=name)
            return _f

        def cmd_record(rate):
            def _f():
                forms.record(value=rate, unit="seconds")
                forms.log(f"Record cadence -> {rate}s", component=name)
            return _f

        def cmd_cryo_on():
            def _f():
                queue_cryo_request(forms, component=name, enabled=True)
                cmd_record(tCDr)()
            return _f

        def cmd_cryo_off():
            def _f():
                queue_cryo_request(forms, component=name, shutdown=True)
            return _f

        t = 0

        # Phase 0: Initial setup at T_START
        after(t, lambda: forms.record(value=tRESTr, unit="seconds"), f"Record = {tRESTr}s")
        after(t, cmd_shroud("PY", T_START), f"PY = {T_START} K")
        after(t + 5, cmd_shroud("MY", T_START), f"MY = {T_START} K")
        after(
            t + 10,
            cmd_cryo(startup=True, voltage=CCV, resistance=CCVRES, enabled=False),
            f"Cryo startup at {CCV}V" + (f" / {float(CCVRES):.1f}ohm" if CCVRES is not None else ""),
        )

        if SLTA:
            after(t + 10, cmd_psu2(1, 12.0, 1.25), "SLTA 12V 1.25A")
            after(t + 30, cmd_psu2_on(1), f"SLTA ON - equilibrium at {T_START}K")

        # Phase 1 end / Phase 2 start: cryocooler on, begin cooldown
        t = max(tREST * H, 20)
        after(t, cmd_cryo_on(), f"CC ON - cooldown at {T_START}K")

        # Phase 2 end / Phase 3 start: cooldown complete, begin ramp
        t += tCD * H
        after(t, cmd_record(tHOLDr), f"Ramp start - Record = {tHOLDr}s")

        # Phase 3: Ramp sweep
        temp = T_START
        while temp < T_END:
            temp += T_STEP
            if temp > T_END:
                temp = T_END
            after(t, cmd_shroud("PY", temp), f"Ramp PY = {temp} K")
            after(t + 5, cmd_shroud("MY", temp), f"Ramp MY = {temp} K")
            t += tHOLD * H

        # Phase 4: Final equilibrium hold at T_END
        after(t, cmd_record(tEQr), f"Final hold - Record = {tEQr}s")
        t += tEQ * H

        # Phase 5: Handoff prep — shut down cryocooler
        after(t, cmd_cryo_off(), f"CC OFF (shutdown) at {T_END}K")
        if SLTA:
            after(t, cmd_psu2_off(1), "SLTA OFF - complete")

        state.initialized = True
        forms.log(
            f"{len(state.timeline)} events scheduled "
            f"(tREST={tREST}h, tCD={tCD}h, steps={T_START}->{T_END}K @ {T_STEP}K/{tHOLD}h, tEQ={tEQ}h)",
            component=name,
        )

    now = time()
    for ev in state.timeline:
        if not ev.done and now >= ev.at:
            try:
                ev.action()
                ev.done = True
                forms.log(f"Executed: {ev.label}", component=name)
            except Exception as e:
                ev.done = True
                forms.log(f"Event '{ev.label}' failed: {e}", level="ERROR", component=name)

    if state.initialized and not state.completed and all(ev.done for ev in state.timeline):
        state.completed = True
        forms.log("All events complete", component=name)
        try:
            if next_routine:
                next_config = None
                if next_routine == "rTVAC_EQCTRL":
                    next_config = {
                        "TpS": next_TpS,
                        "TmS": next_TmS,
                        "tR": next_tR,
                    }
                forms.log(
                    f"Requesting routine handoff -> {next_routine}",
                    component=name,
                )
                forms.routines.transition_to(next_routine, config=next_config)
            else:
                forms.log("Routine complete - stopping self", component=name)
                forms.routines.stop()
        except Exception as e:
            forms.log(
                f"Routine handoff failed: {e}",
                level="ERROR",
                component=name,
            )


def routine_reset(forms, config=None):
    """Clear per-config shroud ramp state so the routine can be restarted."""
    key = hash(frozenset((config or {}).items()))
    _states.pop(key, None)
