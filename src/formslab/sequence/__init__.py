"""Lab sequences: `.plan` files that drive the bench through rScripts.

A plan is one step per line: ``load`` names the rScripts that own the
instruments, then commands (the cast tab's words), hold, until and log. The
host runs it on a `Run` in real time; see `plan` for the syntax and `runner`
for how a step runs.

    python -m formslab.sequence rest_from_ambient  # check, no hardware
    python -m formslab.host.sequence rest_from_ambient

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
