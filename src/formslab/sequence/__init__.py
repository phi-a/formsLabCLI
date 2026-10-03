"""Lab sequences: `.plan` files that drive the bench through rScripts.

A plan names the rScripts that own the instruments (``rscripts.load``) and an
ordered list of operations (``sequence.operations``): hold, command, until,
log. The host runs it on a `LabForms` handle in real time; see `plan` for the
grammar and `runner` for how a segment runs.

    python -m formslab.sequence psu1_smtc08_first          # check, no hardware
    python -m formslab.host.sequence psu1_smtc08_first

Orbit work is FORMS' job: it computes a profile offline, and a plan replays it.
"""
from .events import JsonlEventSink, ListSink, NullSink
from .plan import (
    Plan, PlanError, discover, find_plan, load_plan, parse_plan, search_dirs,
)
from .runner import LabSequenceRunner, RunResult, SequenceError
from .spec import Segment, Sequence

__all__ = [
    "JsonlEventSink", "ListSink", "NullSink",
    "Plan", "PlanError", "discover", "find_plan", "load_plan", "parse_plan",
    "search_dirs",
    "LabSequenceRunner", "RunResult", "SequenceError",
    "Segment", "Sequence",
]
