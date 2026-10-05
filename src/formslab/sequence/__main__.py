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


def main(argv=None) -> int:
    ap = ArgumentParser(prog="python -m formslab.sequence", description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="a .plan file: a path, or a name found in plans/")
    args = ap.parse_args(argv)

    path = find_plan(args.plan)
    if path is None:
        where = ", ".join(str(d) for d in search_dirs()) or "no plans/ directory"
        print(f"no plan {args.plan!r} (searched {where})", file=sys.stderr)
        return 2
    try:
        plan = load_plan(path)
    except PlanError as e:
        print(f"invalid: {e}", file=sys.stderr)
        return 1

    print(f"plan      {plan.name}  ({path})")
    print(f"record    every {plan.record_interval:g} {plan.record_unit}")
    missing = []
    for name in plan.rscripts:
        found = rscripts.find(name)
        missing += [] if found else [name]
        print(f"rScript   {name:<12} {found or 'NOT FOUND'}")
    held, depth = 0.0, 0
    print("sequence")
    for i, seg in enumerate(plan.sequence.segments, 1):
        held += seg.params.get("seconds") or 0.0
        depth -= seg.verb == "end"
        print(f"  {i:>2}. {'  ' * depth}{seg.label}")
        depth += seg.verb == "repeat"
    loops = any(s.verb == "repeat" for s in plan.sequence.segments)
    print(f"held      {held:g} s" + (" (a loop's holds counted once)" if loops else "")
          + ", plus command and until waits"
          + ("; runs until ctrl `end`" if plan.sequence.open_ended else ""))
    if missing:
        print(f"missing rScripts: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"\nrun:  python -m formslab.host.sequence \"{path}\"")
    print(f"      or in the console ctrl tab: run {path.stem}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
