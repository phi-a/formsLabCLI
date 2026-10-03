#!/usr/bin/env python3
"""
Hardware bring-up script for the cryocooler control board.

NOT a pytest test -- ``conftest.py`` excludes it from collection. It needs
PSU2, the USB-I2C bridge and the board itself. The hardware-independent
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
        "output: enabled=%s pgood=%s intvref=%s faults=%s healthy=%s"
        % (
            state.get("enabled"),
            state.get("pgood"),
            state.get("intvref"),
            state.get("faults"),
            state.get("output_healthy"),
        )
    )
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
    board = CryoBoard("cryo_board")
    try:
        psu.setOVCP(CRYO_PSU_CHANNEL, CRYO_SUPPLY_OVP_V, CRYO_SUPPLY_OCP_A, True)
        psu.set(CRYO_PSU_CHANNEL, CRYO_SUPPLY_VOLTAGE_V, CRYO_SUPPLY_CURRENT_A)
        psu.on(CRYO_PSU_CHANNEL)
        time.sleep(1.5)

        print("--- scan ---")
        print(board.scan())

        print("--- initialize (output off) ---")
        board.initialize(voltage=args.volts, resistance=args.ohms, enabled=False)
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
        finally:
            psu.off(CRYO_PSU_CHANNEL)
            psu.close()
    print("done; output disabled and supply off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
