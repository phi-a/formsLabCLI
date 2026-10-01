"""Vent test for the LACO chamber (HVC-3500): pre-checks, vent, watch it rise.

    python scripts/vent_test.py                 # against the built-in simulator
    python scripts/vent_test.py --live          # real chamber, READ-ONLY pre-check
    python scripts/vent_test.py --live --vent   # real chamber, vents after you type VENT
    python scripts/vent_test.py --live --close  # real chamber, closes the vent valve

Two ways to vent (--method):

  valve  (default) Open the vent valve with its toggle, !OV: read first, one
         toggle, verify ~3 s later. This is what venting means while the
         chamber is idle or in Manual mode -- the HMI's manual vent icon. The
         PLC's interlock needs the rough (evac) and gate valves closed; the
         pre-check refuses otherwise. The valve is left open at atmosphere, as
         the HMI leaves it.
  cycle  Send !VA, "Vent to Atmosphere". That is a vacuum *operation*: the
         manual uses it inside a running cycle (ASCII recipe started with !CS).
         Sent to an idle chamber it is acknowledged and nothing happens, so the
         script gives up if the vent valve has not opened within 15 s.

Steps:
  1. Read the chamber: pressure, valves, zone temperatures, faults, test state.
  2. Pre-checks: every field read; no fault; every zone inside the profile's
     vent window (min/max_vent_temp_c, 10-60 C shipped); for `valve`, rough and
     gate valves closed.
  3. Vent, then poll every --poll s until pressure >= --atm (profile unit), a
     fault appears, or --timeout passes. Readings go to
     outputs/vent_test_<UTC>.csv and every raw command/reply to .jsonl beside it.

--close closes the vent valve instead (the same !OV toggle, read first and
verified), sealing the chamber at whatever pressure it is at. The PLC will not
start a pumpdown with the vent valve open.

Exit code 0 = vented (or closed), 1 = failed or timed out, 2 = pre-check refused.
Ctrl-C while watching only stops watching; the valve stays as commanded.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone

from formslab import config
from formslab.devices.hvc3500 import BenchProfile, ProtocolError, load_profile
from formslab.devices.hvc3500.simulator import Simulator
from formslab.devices.laco import LACO

VALVE_OPEN_WITHIN_S = 15.0       # for `cycle`: how long !VA gets to open the valve


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def show(st, unit: str) -> None:
    zones = ", ".join(f"{z.name} {z.temperature_c} C" for z in st.zones.values())
    valves = " ".join(f"{k}={'OPEN' if v else 'closed'}" for k, v in st.devices.items())
    print(f"  pressure {st.pressure} {unit}   mode {st.mode}   test {st.test_status}")
    print(f"  {zones}   thermal control {'on' if st.thermal_control else 'off'}")
    print(f"  {valves}")
    print(f"  faults {st.fault_severity} {', '.join(st.faults) or 'none'}")


def prechecks(st, profile: BenchProfile, method: str) -> list[str]:
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
    if method == "valve":
        for valve in ("rough", "gate"):
            if st.devices.get(valve):
                problems.append(f"{valve} valve is open; the PLC will not open the vent valve "
                                "until it is closed")
    return problems


def close_vent(laco) -> int:
    """Close the vent valve: read first, one !OV toggle if it is open, verify."""
    if not laco.client.device_state("OV"):
        print("Vent valve already closed; nothing sent.")
        return 0
    try:
        laco.client.set_device("OV", False, confirm=True)
    except ProtocolError as exc:
        print(f"FAILED: {exc}")
        return 1
    st = laco.status()
    print(f"Vent valve verified CLOSED at {_now()}; chamber sealed at {st.pressure} "
          f"{laco.profile.pressure_unit}.")
    return 0


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
    ap.add_argument("--close", action="store_true", help="close the vent valve and exit")
    ap.add_argument("--method", choices=("valve", "cycle"), default="valve",
                    help="valve = !OV toggle (idle/Manual); cycle = !VA (inside a running cycle)")
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

    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    csv_path = config.output_dir() / f"vent_test_{stamp}.csv"
    raw_log = (config.output_dir() / f"vent_test_{stamp}.jsonl").open("w", encoding="utf-8")

    print(f"Vent test -- {target} -- method {args.method}")
    laco = LACO(profile, toggle_settle_s=3.0,
                on_transaction=lambda t: (raw_log.write(t.as_json() + "\n"), raw_log.flush()))
    try:
        laco.connect()
        st = laco.status()
        print("Before:")
        show(st, unit)
        if args.close:
            return close_vent(laco)
        problems = prechecks(st, profile, args.method)
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
            answer = input("Vent the chamber to atmosphere now? Type VENT to proceed: ")
            if answer.strip() != "VENT":
                print("Not vented.")
                return 2

        t0 = time.monotonic()
        if args.method == "valve":
            try:
                laco.client.set_device("OV", True, confirm=True)
            except ProtocolError as exc:
                print(f"FAILED: {exc}")
                print("The PLC refused the vent valve. Check the HMI for the interlock message.")
                return 1
            print(f"Vent valve verified OPEN at {_now()}.")
        else:
            reply = laco.client.action("VA", confirm=True)
            print(f"!VA sent at {_now()}; controller replied {reply.raw!r}.")
        print(f"Watching every {poll:g} s (limit {timeout:g} s); log {csv_path}")

        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["utc", "elapsed_s", f"pressure_{unit}", "vent_valve", "fault",
                        *[f"{z}_C" for z in laco.zones]])
            try:
                while True:
                    time.sleep(poll)
                    st = laco.status()
                    el = time.monotonic() - t0
                    vent_open = st.devices.get("vent")
                    w.writerow([_now(), round(el, 1), st.pressure, vent_open, st.fault_severity,
                                *[z.temperature_c for z in st.zones.values()]])
                    f.flush()
                    print(f"  {el:7.1f} s  {st.pressure} {unit}  vent "
                          f"{'open' if vent_open else 'closed'}  faults {st.fault_severity}")
                    if st.fault_severity not in ("N", None):
                        print(f"FAILED: fault {', '.join(st.faults)} after {el:.0f} s")
                        return 1
                    if st.pressure is not None and st.pressure >= args.atm:
                        print(f"PASSED: {st.pressure} {unit} after {el:.0f} s")
                        return 0
                    if not vent_open and el >= VALVE_OPEN_WITHIN_S:
                        print(f"FAILED: the vent valve is still closed {el:.0f} s after the "
                              "command." + (" !VA only runs inside a cycle; use --method valve "
                                            "on an idle chamber." if args.method == "cycle" else ""))
                        return 1
                    if el >= timeout:
                        print(f"FAILED: still {st.pressure} {unit} after {timeout:g} s")
                        return 1
            except KeyboardInterrupt:
                print("Stopped watching. The vent valve stays as commanded.")
                return 1
    finally:
        laco.close()
        raw_log.close()
        if sim is not None:
            sim.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(main())
