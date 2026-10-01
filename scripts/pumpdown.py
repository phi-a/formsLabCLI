"""Rough pumpdown of the LACO chamber (HVC-3500) in Manual mode.

    python scripts/pumpdown.py                  # against the built-in simulator
    python scripts/pumpdown.py --live           # real chamber, READ-ONLY pre-check
    python scripts/pumpdown.py --live --pump    # real chamber, pumps after you type PUMP
    python scripts/pumpdown.py --live --stop    # real chamber: rough valve closed, pump off

Sequence (the one proven on the chamber 2026-09-24; every toggle is read
first, sent once and verified ~3 s later):
  1. Pre-check: Manual mode, no fault, every field read; vent, fill, foreline
     and gate valves closed, turbo off. (The PLC will not open the rough valve
     otherwise.) A closed vent valve: scripts/vent_test.py --live --close.
  2. !OP  vacuum (roughing) pump ON, then --settle s (PLC: pump on >= 10 s).
  3. !OR  rough (evac) valve OPEN.
  4. Read every --poll s until pressure < --target, a fault, or --max-min.
  5. !OR  rough valve CLOSED, then !OP pump OFF -- also on Ctrl-C or any
     error: the PLC will not stop the pump while the rough valve is open.
     The chamber is left sealed under rough vacuum.

--stop does step 5 on its own -- for a pump started from the HMI or a run that
died: rough valve closed first, then the pump off, each only if needed.

Turbo, gate, foreline, vent and fill are never commanded. Readings go to
outputs/pumpdown_<UTC>.csv and raw command/reply frames to .jsonl beside it.
Exit code 0 = target reached, 1 = stopped short, 2 = pre-check refused.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vent_test import _now, show, simulated_chamber  # noqa: E402

from formslab import config  # noqa: E402
from formslab.devices.hvc3500 import ProtocolError, load_profile  # noqa: E402
from formslab.devices.laco import LACO  # noqa: E402

MUST_BE_CLOSED = ("vent", "fill", "foreline", "gate", "turbo")


def prechecks(st) -> list[str]:
    problems = [f"could not read {k}: {v}" for k, v in st.errors.items()]
    if (st.mode or "").upper() != "MANUAL":
        problems.append(f"mode is {st.mode}, not MANUAL (a cycle owns the valves)")
    if st.fault_severity not in ("N", None):
        problems.append(f"controller fault {st.fault_severity}: {', '.join(st.faults)}")
    for name in MUST_BE_CLOSED:
        if st.devices.get(name):
            hint = " -- close it: python scripts/vent_test.py --live --close" if name == "vent" else ""
            problems.append(f"{name} is open/on{hint}")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true", help="the real chamber (default: simulator)")
    ap.add_argument("--pump", action="store_true", help="with --live: actually pump down")
    ap.add_argument("--stop", action="store_true",
                    help="close the rough valve, then turn the pump off, and exit")
    ap.add_argument("--target", type=float, default=2.0, help="stop below this pressure")
    ap.add_argument("--max-min", type=float, default=None,
                    help="minutes before giving up (default 15 live, 0.5 simulated)")
    ap.add_argument("--settle", type=float, default=None,
                    help="seconds the pump runs before the rough valve opens (default 15)")
    ap.add_argument("--poll", type=float, default=None, help="seconds between readings")
    args = ap.parse_args(argv)

    sim = None
    if args.live:
        profile = load_profile()
        target = f"LIVE chamber {profile.host}:{profile.port}"
    else:
        sim, profile = simulated_chamber()
        with sim.state.lock:
            sim.state.pressure, sim.state.mode = 760.0, "MANUAL"
        target = "SIMULATOR"
    max_s = 60 * (args.max_min or (15.0 if args.live else 0.5))
    settle = args.settle if args.settle is not None else (15.0 if args.live else 0.5)
    poll = args.poll or (5.0 if args.live else 0.5)
    unit = profile.pressure_unit

    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    csv_path = config.output_dir() / f"pumpdown_{stamp}.csv"
    raw_log = (config.output_dir() / f"pumpdown_{stamp}.jsonl").open("w", encoding="utf-8")

    print(f"Pumpdown -- {target} -- to {args.target:g} {unit}")
    laco = LACO(profile, toggle_settle_s=3.0,
                on_transaction=lambda t: (raw_log.write(t.as_json() + "\n"), raw_log.flush()))
    rough_open = pump_on = False
    try:
        laco.connect()
        st = laco.status()
        print("Before:")
        show(st, unit)
        if args.stop:
            rough_open, pump_on = bool(st.devices.get("rough")), bool(st.devices.get("pump"))
            if not (rough_open or pump_on):
                print("Rough valve closed and pump off already; nothing sent.")
            return 0              # the finally block closes / stops what is on
        problems = prechecks(st)
        if problems:
            print("REFUSED:\n  " + "\n  ".join(problems))
            return 2
        print("Pre-checks passed.")
        if args.live:
            if not args.pump:
                print("Read-only run: nothing was commanded. Add --pump to pump down.")
                return 0
            if input("Start the roughing pump and pump down now? Type PUMP to proceed: ").strip() != "PUMP":
                print("Not started.")
                return 2

        t0 = time.monotonic()
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["utc", "elapsed_s", f"pressure_{unit}", "pump", "rough_valve", "fault"])

            def reading(tag):
                st = laco.status()
                el = time.monotonic() - t0
                w.writerow([_now(), round(el, 1), st.pressure, st.devices.get("pump"),
                            st.devices.get("rough"), st.fault_severity])
                f.flush()
                print(f"  {el:7.1f} s  {tag:<14} {st.pressure} {unit}  faults {st.fault_severity}")
                return st

            pump_on = laco.client.set_device("OP", True, confirm=True)
            print(f"Vacuum pump verified ON at {_now()}; settling {settle:g} s.")
            time.sleep(settle)
            rough_open = laco.client.set_device("OR", True, confirm=True)
            print(f"Rough valve verified OPEN at {_now()}.")

            while True:
                st = reading("pumping")
                if st.fault_severity not in ("N", None):
                    print(f"STOPPED: fault {', '.join(st.faults)}")
                    return 1
                if st.pressure is not None and st.pressure < args.target:
                    print(f"PASSED: {st.pressure} {unit} after {time.monotonic() - t0:.0f} s")
                    return 0
                if time.monotonic() - t0 >= max_s:
                    print(f"STOPPED: still {st.pressure} {unit} after {max_s / 60:g} min")
                    return 1
                time.sleep(poll)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 1
    except (OSError, ProtocolError) as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        # Rough valve first: the PLC will not stop the pump while it is open.
        for code, flag, label in (("OR", rough_open, "rough valve CLOSED"),
                                  ("OP", pump_on, "vacuum pump OFF")):
            if not flag:
                continue
            try:
                laco.client.set_device(code, False, confirm=True)
                print(f"{label}: verified.")
            except (OSError, ProtocolError) as exc:
                print(f"!! {label} NOT verified: {exc}  <- check the HMI")
        try:
            if rough_open or pump_on:
                st = laco.status()
                print(f"Final: {st.pressure} {unit}, sealed; "
                      + " ".join(f"{k}={'OPEN' if v else 'closed'}" for k, v in st.devices.items()))
        finally:
            laco.close()
            raw_log.close()
            if sim is not None:
                sim.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(main())
