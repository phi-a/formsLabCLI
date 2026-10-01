"""
Parameterized cryocooler chilldown sequence.

Runs a thermal cycle: rest -> chilldown -> equilibrium hold -> warmup

Config parameters:
    TpS: float = 240            +Shroud wall temperature (K)
    TmS: float = 240            -Shroud wall temperature (K)
    CCV: float = 12             Cryocooler voltage (V)
    CCVRES: float = None        Optional cryocooler resistance (ohm)
    tREST: float = 1            Rest / settle time before chilldown (h)
    tCD: float = 2              Chilldown time (h)
    tEQ: float = 1              Equilibrium hold time after chilldown (h)
    tWU: float = 3              Warmup time (h)
    SLTA: bool = False
    tR: int = 30                Default record cadence (s)
    tRESTr: int = tR            Rest / settle record cadence (s)
    tCDr: int = 1               Chilldown record cadence (s)
    tEQr: int = tR              Equilibrium hold record cadence (s)
    tWUr: int = 1               Warmup record cadence (s)
    next_routine: str = ""      Optional routine to start after completion
    next_tR: int = 60           Record cadence for rTVAC_EQCTRL handoff

Historical .zen usage (the .forms path refuses routines.load):
    routines.load = [
        {
            "name": "rChilldown",
            "config": {
                "TpS": 240,
                "TmS": 240,
                "CCV": 17,
                "CCVRES": 270,
                "tREST": 1,
                "tCD": 2,
                "tEQ": 1,
                "tWU": 3,
                "tR": 30,
                "tRESTr": 30,
                "tCDr": 1,
                "tEQr": 30,
                "tWUr": 1,
                "SLTA": False,
                "next_routine": "rTVAC_EQCTRL",
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
    """Per-instance state for chilldown routine."""
    initialized: bool = False
    completed: bool = False
    t0: float = 0
    timeline: list = field(default_factory=list)


# Module-level state keyed by config hash
_states: Dict[int, State] = {}


def _get_state(config: dict) -> State:
    """Get or create state for this config."""
    key = hash(frozenset(config.items()))
    if key not in _states:
        _states[key] = State()
    return _states[key]


@routine(hold=10)
def chilldown(forms, config):
    """Execute parameterized chilldown sequence."""
    state = _get_state(config)

    TpS = config.get("TpS", 240)
    TmS = config.get("TmS", 240)
    CCV = config.get("CCV", 17)
    CCVRES = config.get("CCVRES", 240)

    tREST = config.get("tREST", 1)
    tCD = config.get("tCD", 2)
    tEQ = config.get("tEQ", 1)
    tWU = config.get("tWU", 3)
    SLTA = config.get("SLTA", False)
    tR = config.get("tR", 30)
    tRESTr = config.get("tRESTr", tR)
    tCDr = config.get("tCDr", 1)
    tEQr = config.get("tEQr", tR)
    tWUr = config.get("tWUr", 1)
    next_routine = str(config.get("next_routine", "") or "").strip()
    next_tR = config.get("next_tR", 60)

    name = f"chilldown_{TpS}K_{CCV}V"
    H = 3600  # seconds per hour

    # Build timeline once
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

        def cmd_cryo(*, voltage=None, resistance=None, enabled=None, startup=None):
            def _f():
                queue_cryo_request(
                    forms,
                    component=name,
                    voltage=voltage,
                    resistance=resistance,
                    enabled=enabled,
                    startup=startup,
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
                cmd_record(tWUr)()
            return _f

        def cmd_restore_record():
            return cmd_record(tR)

        def cmd_eq_hold_start():
            def _f():
                forms.log("Chilldown complete — entering equilibrium hold", component=name)
                cmd_record(tEQr)()
            return _f

        def cmd_warmup_complete():
            def _f():
                forms.log("Warmup complete", component=name)
                cmd_restore_record()()
            return _f

        # Build timeline
        t = 0

        # Initial setup
        after(t, lambda: forms.record(value=tRESTr, unit="seconds"), f"Record = {tRESTr}s")
        after(t, cmd_shroud("PY", TpS), f"PY = {TpS} K")
        after(t + 5, cmd_shroud("MY", TmS), f"MY = {TmS} K")
        after(
            t + 10,
            cmd_cryo(startup=True, voltage=CCV, resistance=CCVRES, enabled=False),
            f"Cryo startup at {CCV}V" + (f" / {float(CCVRES):.1f}ohm" if CCVRES is not None else ""),
        )

        # SLTA setup (optional)
        if SLTA:
            after(t + 10, cmd_psu2(1, 12.0, 1.25), "SLTA 12V 1.25A")
            after(t + 30, cmd_psu2_on(1), "SLTA ON - equilibrium")

        # Rest / settle phase
        t = max(tREST * H, 20)
        after(t, cmd_cryo_on(), "CCOUT ON - chilldown")

        # Chilldown phase
        t += tCD * H
        after(t, cmd_eq_hold_start(), "EQ hold start")

        # Equilibrium hold phase
        t += tEQ * H
        after(t, cmd_cryo_off(), "CCOUT OFF - warmup")

        # Warmup phase
        t += tWU * H
        after(
            t,
            cmd_warmup_complete(),
            "Warmup complete",
        )
        if SLTA:
            after(t, cmd_psu2_off(1), "SLTA OFF - complete")

        state.initialized = True
        forms.log(
            f"{len(state.timeline)} events scheduled "
            f"(tREST={tREST}h, tCD={tCD}h, tEQ={tEQ}h, tWU={tWU}h)",
            component=name,
        )

    # Execute due events
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
                        "TpS": TpS,
                        "TmS": TmS,
                        "tR": next_tR,
                    }
                forms.log(
                    f"Requesting routine handoff -> {next_routine}",
                    component=name,
                )
                forms.routines.transition_to(next_routine, config=next_config)
            else:
                forms.log("Routine complete — stopping self", component=name)
                forms.routines.stop()
        except Exception as e:
            forms.log(
                f"Routine handoff failed: {e}",
                level="ERROR",
                component=name,
            )


def routine_reset(forms, config=None):
    """Clear per-config chilldown state so the routine can be restarted."""
    key = hash(frozenset((config or {}).items()))
    _states.pop(key, None)
