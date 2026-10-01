"""Vent test for the LACO chamber (HVC-3500): pre-checks, vent, watch it rise.

    python scripts/vent_test.py                 # against the built-in simulator
    python scripts/vent_test.py --live          # real chamber, READ-ONLY pre-check
    python scripts/vent_test.py --live --vent   # real chamber, vents after you type VENT

Steps:
  1. Read the chamber: pressure, vent valve, zone temperatures, faults, test state.
  2. Pre-checks. Every field read; no fault; every zone temperature inside the
     profile's vent window (min/max_vent_temp_c, 10-60 C on the shipped
     profile -- the PLC checks this too, this script refuses first).
  3. !VA (vent to atmosphere). The PLC sequences the valves itself.
  4. Poll every --poll seconds until pressure >= --atm (in the profile's
     pressure unit), a fault appears, or --timeout passes. Each reading goes to
     outputs/vent_test_<UTC>.csv.

Exit code 0 = vented, 1 = failed or timed out, 2 = pre-check refused.
Ctrl-C while watching only stops watching: the vent has already been commanded.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone

from formslab import config
from formslab.devices.hvc3500 import BenchProfile, load_profile
from formslab.devices.hvc3500.simulator import Simulator
from formslab.devices.laco import LACO


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def show(st, unit: str) -> None:
    zones = ", ".join(f"{z.name} {z.temperature_c} C" for z in st.zones.values())
    print(f"  pressure {st.pressure} {unit}   vent valve {'OPEN' if st.devices.get('vent') else 'closed'}")
    print(f"  {zones}")
    print(f"  test {st.test_status}   thermal control {'on' if st.thermal_control else 'off'}   "
          f"faults {st.fault_severity} {', '.join(st.faults) or 'none'}")


def prechecks(st, profile: BenchProfile) -> list[str]:
    """Reasons not to vent; empty when it is safe to try."""
    lo = profile.limits.get("min_vent_temp_c", 10.0)
    hi = profile.limits.get("max_vent_temp_c", 60.0)
    problems = [f"could not read {k}: {v}" for k, v in st.errors.items()]
    if st.fault_severity not in ("N", None):
        problems.append(f"controller fault {st.fault_severity}: {', '.join(st.faults)}")
    for z in st.zones.values():
        t = z.temperature_c
        if t is None or not lo <= t <= hi:
            problems.append(f"{z.name} at {t} C is outside the vent window {lo:g}-{hi:g} C")
    return problems


def simulated_chamber():
    """A simulator pumped down to a few Torr, and a profile pointing at it."""
    sim = Simulator().__enter__()
    with sim.state.lock:
        sim.state.pressure = 2.0
    d = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
    d["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
    return sim, BenchProfile.from_dict(d)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--live", action="store_true", help="the real chamber (default: simulator)")
    ap.add_argument("--vent", action="store_true", help="with --live: actually vent")
    ap.add_argument("--atm", type=float, default=700.0, help="pressure that counts as vented")
    ap.add_argument("--timeout", type=float, default=None,
                    help="seconds to wait for --atm (default 1200 live, 30 simulated)")
    ap.add_argument("--poll", type=float, default=None, help="seconds between readings")
    args = ap.parse_args(argv)

    sim = None
    if args.live:
        profile = load_profile()
        target = f"LIVE chamber {profile.host}:{profile.port}"
    else:
        sim, profile = simulated_chamber()
        target = "SIMULATOR"
    timeout = args.timeout or (1200.0 if args.live else 30.0)
    poll = args.poll or (5.0 if args.live else 0.5)
    unit = profile.pressure_unit

    print(f"Vent test -- {target}")
    laco = LACO(profile)
    try:
        laco.connect()
        st = laco.status()
        print("Before:")
        show(st, unit)
        problems = prechecks(st, profile)
        if problems:
            print("REFUSED:\n  " + "\n  ".join(problems))
            return 2
        print("Pre-checks passed.")
        if st.devices.get("vent") and st.pressure is not None and st.pressure >= args.atm:
            print(f"Already at {st.pressure} {unit} with the vent valve open; nothing to do.")
            return 0

        if args.live:
            if not args.vent:
                print("Read-only run: nothing was commanded. Add --vent to vent.")
                return 0
            answer = input(f"Vent the chamber to atmosphere now? Type VENT to proceed: ")
            if answer.strip() != "VENT":
                print("Not vented.")
                return 2

        path = config.output_dir() / f"vent_test_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
        laco.vent()
        t0 = time.monotonic()
        print(f"!VA sent at {_now()}. Watching every {poll:g} s (limit {timeout:g} s); log {path}")
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["utc", "elapsed_s", f"pressure_{unit}", "vent_valve", "fault",
                        *[f"{z}_C" for z in laco.zones]])
            try:
                while True:
                    time.sleep(poll)
                    st = laco.status()
                    el = time.monotonic() - t0
                    w.writerow([_now(), round(el, 1), st.pressure, st.devices.get("vent"),
                                st.fault_severity, *[z.temperature_c for z in st.zones.values()]])
                    f.flush()
                    print(f"  {el:7.1f} s  {st.pressure} {unit}  vent "
                          f"{'open' if st.devices.get('vent') else 'closed'}  faults {st.fault_severity}")
                    if st.fault_severity not in ("N", None):
                        print(f"FAILED: fault {', '.join(st.faults)} after {el:.0f} s")
                        return 1
                    if st.pressure is not None and st.pressure >= args.atm:
                        print(f"PASSED: {st.pressure} {unit} after {el:.0f} s")
                        return 0
                    if el >= timeout:
                        print(f"FAILED: still {st.pressure} {unit} after {timeout:g} s")
                        return 1
            except KeyboardInterrupt:
                print("Stopped watching. The vent was commanded and continues on the PLC.")
                return 1
    finally:
        laco.close()
        if sim is not None:
            sim.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(main())
