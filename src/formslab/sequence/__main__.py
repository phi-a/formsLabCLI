"""Check a lab plan without touching hardware.

    python -m formslab.sequence <plan>

Reads the plan, reports where each rScript resolves, and lists the operations
with their total held time. Exit 0 when the plan would load, 1 when it would
not, 2 when no such plan exists.
"""
from __future__ import annotations

import sys
from argparse import ArgumentParser

from formslab import rscripts
from formslab.sequence.plan import PlanError, find_plan, load_plan, search_dirs


def main(argv=None, out=None) -> int:
    """`out`: one stream for everything said (labcli's watch screen captures it);
    by default stdout, with the refusals on stderr."""
    ap = ArgumentParser(prog="python -m formslab.sequence", description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="a .plan file: a path, or a name found in plans/")
    args = ap.parse_args(argv)

    def say(text: str, error: bool = False) -> None:
        print(text, file=out or (sys.stderr if error else sys.stdout))

    path = find_plan(args.plan)
    if path is None:
        where = ", ".join(str(d) for d in search_dirs()) or "no plans/ directory"
        say(f"no plan {args.plan!r} (searched {where})", error=True)
        return 2
    try:
        plan = load_plan(path)
    except PlanError as e:
        say(f"invalid: {e}", error=True)
        return 1

    say(f"plan      {plan.name}  ({path})")
    say(f"record    every {plan.record_interval:g} {plan.record_unit}")
    missing = []
    for name in plan.rscripts:
        found = rscripts.find(name)
        missing += [] if found else [name]
        say(f"rScript   {name:<12} {found or 'NOT FOUND'}")
    held, depth = 0.0, 0
    say("sequence")
    for i, seg in enumerate(plan.sequence.segments, 1):
        held += seg.params.get("seconds") or 0.0
        depth -= seg.verb == "end"
        say(f"  {i:>2}. {'  ' * depth}{seg.label}")
        depth += seg.verb == "repeat"
    loops = any(s.verb == "repeat" for s in plan.sequence.segments)
    say(f"held      {held:g} s" + (" (a loop's holds counted once)" if loops else "")
        + ", plus command and until waits"
        + ("; runs until ctrl `end`" if plan.sequence.open_ended else ""))
    for n, m in plan.warnings:
        say(f"  {path.name}:{n}: warning: {m} (checked when the step runs)")
    if missing:
        say(f"missing rScripts: {', '.join(missing)}", error=True)
        return 1
    say(f"\nrun:  labcli run {path.stem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
