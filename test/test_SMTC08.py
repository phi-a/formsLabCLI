#!/usr/bin/env python3
import argparse
import sys
import os
# Ensure project root is on sys.path (if RTD16.py lives elsewhere)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from formslab.devices.SMTC08 import SMTC08

def typeTmv2C(mv):
    if mv < 0:
        c = [0.0, 25.173462, -1.1662878, -1.0833638,
             -0.8977354, -0.37342377, -0.086632643,
             -0.010450598, -0.00051920577]
    else:
        c = [0.0, 25.08355, 0.07860106, -0.2503131e-3,
             0.0831527e-4, -0.01228034e-6, 0.0009804036e-8,
             -0.0000441303e-10, 0.0000115924e-13, -0.0000013309e-16]
    return sum(c[i] * mv**i for i in range(len(c)))

def main():
    ap = argparse.ArgumentParser(description="Read all 8 SMTC08 thermocouple channels.")
    ap.add_argument("--label", default="SMTC08_A", help="usbmap.json entry")
    ap.add_argument("--port", help="override the usbmap port, e.g. COM3 or /dev/ttyUSB2")
    ap.add_argument("--slave", type=int, default=1, help="MODBUS slave address")
    args = ap.parse_args()

    smtc = SMTC08(args.label, slave=args.slave, port=args.port)  # uses usbmap.json
    print(f"{args.label} on {smtc.port} (slave {args.slave})")
    try:
        temps = smtc.read_all()
        mvs   = smtc.read_all_mv()
        polytemps = [typeTmv2C(mv) for mv in mvs]

        for ch in range(8):
            cjc_guess = temps[ch] - polytemps[ch]
            print(f"Ch{ch+1}: {temps[ch]:6.2f} °C | {mvs[ch]:7.4f} mV → {polytemps[ch]:6.2f} °C | CJC ≈ {cjc_guess:5.2f} °C")
    finally:
        smtc.close()

if __name__ == "__main__":
    main()

# import serial, binascii, time

# PORT = "/dev/ttyUSB3"
# BAUD = 9600

# # Modbus RTU: slave=1, function=0x04, addr=0x0000, count=0x0001
# frame = bytes([0x01,0x04,0x00,0x00,0x00,0x01,0xCA,0x31])

# ser = serial.Serial(PORT, BAUD, timeout=1)
# ser.reset_input_buffer()
# ser.reset_output_buffer()

# print("TX:", binascii.hexlify(frame))
# ser.write(frame)
# time.sleep(0.2)

# resp = ser.read(64)
# print("RX:", binascii.hexlify(resp))

# ser.close()
