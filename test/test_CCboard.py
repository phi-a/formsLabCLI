#!/usr/bin/env python3
"""
Hardware bring-up script for the cryocooler control board.

NOT a pytest test -- ``conftest.py`` excludes it from collection. It needs
the board's supply (psu1 CH1), the Pico USB-I2C bridge and the board itself, and
no run going: it drives the supply and the Pico directly. The hardware-independent
coverage lives in ``test_cryo_registers.py`` and ``test_cryoboard.py``.

Importing this module does nothing. Every hardware action is behind
``main()``, and the converter output stays off unless ``--enable`` is passed.

    python test/test_CCboard.py                 # power up, scan, report status
    python test/test_CCboard.py --enable        # also drive the output briefly
    python test/test_CCboard.py --volts 15 --ohms 400 --enable

Expected on a healthy board with 24 V in::

    I2C devices: ['0x18', '0x74']
    converter present: True   digipot present: True

Remember the two thresholds are different: above roughly 15 V in, the devices
answer the scan; the converter cannot be commanded to produce an output until
roughly 20 V. A good scan and a dead output is a power symptom, not a bus one.

It prints what the supply delivers and the scan's verdict first, and stops there
when the bus is silent; a failure prints what it means (docs/CRYOCOOLER.md, When
the board does not start).
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from formslab.devices.cryocooler.board import CryoBoard
from formslab.devices.cryocooler.config import (
    CRYO_DEFAULT_OUTPUT_VOLTAGE_V,
    CRYO_DEFAULT_RESISTANCE_OHMS,
    CRYO_PSU_CHANNEL,
    CRYO_PSU_LABEL,
    CRYO_SUPPLY_CURRENT_A,
    CRYO_SUPPLY_OCP_A,
    CRYO_SUPPLY_OVP_V,
    CRYO_SUPPLY_VOLTAGE_V,
)
from formslab.devices.cryocooler.owner import diagnose
from formslab.devices.dp832a.driver import PSU


def report(board):
    state = board.status()
    print("I2C devices:", state.get("i2c_devices"))
    print(
        "converter present:",
        state.get("converter_present"),
        "  digipot present:",
        state.get("digipot_present"),
    )
    print(
        "output: commanded=%s converter_on=%s mode=%s faults=%s ok=%s"
        % (
            "on" if state.get("enabled") else "off",
            state.get("output_on"),
            state.get("conversion"),
            state.get("faults"),
            state.get("output_healthy"),
        )
    )
    print("        (the converter reports no power-good: measure Vout with a meter)")
    print(
        "programmed: %s V / %s ohm (code %s)"
        % (
            state.get("output_voltage_v"),
            state.get("resistance_ohms"),
            state.get("resistance_code"),
        )
    )
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volts", type=float, default=CRYO_DEFAULT_OUTPUT_VOLTAGE_V)
    parser.add_argument("--ohms", type=float, default=CRYO_DEFAULT_RESISTANCE_OHMS)
    parser.add_argument(
        "--enable",
        action="store_true",
        help="energise the converter output for --hold seconds",
    )
    parser.add_argument("--hold", type=float, default=5.0)
    args = parser.parse_args(argv)

    psu = PSU(CRYO_PSU_LABEL)
    try:
        psu.connect()
    except Exception as exc:
        print(f"STOP: {CRYO_PSU_LABEL} could not be opened ({type(exc).__name__}). Is a run going? "
              "A run's rPSU holds the supply: end it first. Otherwise check the supply is on and on USB.")
        return 1
    board = CryoBoard("cryo_board")
    try:
        psu.setOVCP(CRYO_PSU_CHANNEL, CRYO_SUPPLY_OVP_V, CRYO_SUPPLY_OCP_A, True)
        psu.set(CRYO_PSU_CHANNEL, CRYO_SUPPLY_VOLTAGE_V, CRYO_SUPPLY_CURRENT_A)
        psu.on(CRYO_PSU_CHANNEL)
        time.sleep(1.5)

        volts, amps = psu.measure(CRYO_PSU_CHANNEL)
        supply = {"vmeas": volts, "cmeas": amps}
        print("--- supply ---")
        print(f"{CRYO_PSU_LABEL} CH{CRYO_PSU_CHANNEL}: {volts} V, {None if amps is None else round(amps * 1000, 1)} mA")

        print("--- scan ---")
        found = board.present()
        print(found)
        if not (found.get("converter") and found.get("digipot")):
            print("STOP: the board's chips do not both answer, so nothing is written to them.")
            print("      A powered board answers ['0x18', '0x74']; check its power and the I2C wires.")
            return 1

        print("--- initialize (output off) ---")
        try:
            board.initialize(voltage=args.volts, resistance=args.ohms, enabled=False)
        except Exception as exc:
            print("FAILED:", diagnose(exc, supply))
            return 1
        report(board)

        print("--- registers ---")
        for device, values in board.read_registers().items():
            print(device, {k: "0x%02X" % v for k, v in values.items()})

        if args.enable:
            print("--- output ON for %.1f s ---" % args.hold)
            board.enable_output()
            report(board)
            time.sleep(args.hold)
    finally:
        try:
            board.shutdown()
        except Exception as exc:                 # the cause was printed above; one line here
            print("board shutdown did not reach the board:", type(exc).__name__)
        finally:
            try:
                psu.off(CRYO_PSU_CHANNEL)
                psu.close()
            except Exception as exc:
                print(f"{CRYO_PSU_LABEL} CH{CRYO_PSU_CHANNEL} OFF did not reach the supply "
                      f"({type(exc).__name__}): turn it off at the front panel")
    print("done; output disabled and supply off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
