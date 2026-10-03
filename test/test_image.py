import os
import sys
import time

# Ensure the repository's python/ directory is on the module search path so that
# ``import lab`` works when running this file directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from formslab.devices.dp832a.driver import PSU
from formslab.devices.slta.camera import SLTA


PSU_DEVICE = "psu2"

# Channel settings
CH1_VOLTAGE = 12.0  # SLTA supply voltage
CH1_CURRENT = 1.5
CH2_VOLTAGE = 0.0   # Cryocooler supply voltage (adjust as needed)
CH2_CURRENT = 0.0
EXPOSURE = 10
IDLE = 10
cmd = {'mode': 'E', 'exposure': 10, 'idle': 10, 'TK': 185,'IMAGEDIR': '/blanks'}
def main(cmd: dict) -> None:
    print(cmd['exposure'])
    psu = PSU(PSU_DEVICE)
    psu.setOVCP(1, CH1_VOLTAGE + 0.25, CH1_CURRENT + 0.25, True)
    psu.setOVCP(2, CH2_VOLTAGE + 0.25, CH2_CURRENT + 0.25, True)
    psu.set(1, CH1_VOLTAGE, CH1_CURRENT)
    psu.set(2, CH2_VOLTAGE, CH2_CURRENT)

    slta = SLTA()

    try:
        psu.on(1)
        psu.on(2)
        print("\u2705 PSU2 channels 1 and 2 ON")

        time.sleep(IDLE)
        process = slta.configure()
        slta.SilentWatch(process, text="Accepting commands")
        slta.read(cmd)
    finally:
        slta.pkill()
        psu.alloff()
        psu.shutdown()
        psu.close()
        print("\u274c Test complete")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            EXPOSURE = int(float(sys.argv[1]))
        except ValueError:
            print("Invalid exposure value provided; using default")
    main(cmd)