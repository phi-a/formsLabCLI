import os
import sys
import time

# Ensure the repository's python/ directory is on the module search path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from formslab.devices.dp832a.driver import PSU


def main():
    print("Initializing PSU...")
    psu = PSU.configure("psu1")
    # Set protection limits separately
    psu.setOVCP(1, ovp=5.5, ocp=0.6)
    psu.setOVCP(2, ovp=3.6, ocp=0.5)
    psu.setOVCP(3, ovp=12.5, ocp=1.0)
    # Configure channels: set voltage/current
    psu.set(1, v=5.0, c=0.5)
    psu.set(2, v=3.3, c=0.4)
    psu.set(3, v=12.0, c=0.8)


    print("→ All channels configured. Now testing ON/OFF + measure...")
    for ch in [1, 2, 3]:
        try:
            psu.on(ch)
            v, c = psu.measure(ch)
            print(f"CH{ch} ON → V={v:.3f} V, I={c:.3f} A")
            psu.off(ch)
        except Exception as e:
            print(f"✗ CH{ch} test failed: {e}")

    psu.update()
    print("\nFinal PSU state:")
    for ch, info in psu.state.items():
        print(f"CH{ch}: {info}")

    psu.shutdown()
    # psu.update()
    # print("\nFinal PSU state:")
    # for ch, info in psu.state.items():
    #     print(f"CH{ch}: {info}")

    psu.close()
    print("✔ PSU shutdown and closed.")


if __name__ == "__main__":
    main()
