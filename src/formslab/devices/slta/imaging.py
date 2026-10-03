#!/usr/bin/env python3
"""Utilities for capturing images using the SLTA hardware."""
import time
import signal
import threading
from typing import Callable, Optional

from formslab.devices.dp832a.driver import PSU
from formslab.devices.slta.camera import SLTA
from formslab.devices.slta.camera_v2 import SLTAv2

# Global reference for cleanup
slta = None
psu_created = False

def handle_signal(signum):
    if slta:
        print(f"\n⚠️ Caught signal {signum}, attempting cleanup...")
        try:
            slta.pkill()
            print("🛑 SLTA process terminated cleanly.")
            slta = None
        except Exception as e:
            print(f"❌ Error during pkill: {e}")
    exit(1)

def capture(
    psu: Optional[PSU] = None,
    cmd: dict = None,
    *,
    power_on: Optional[Callable[[], None]] = None,
    power_off: Optional[Callable[[], None]] = None,
) -> None:
    """Capture a single image using the SLTA."""
    global slta, psu_created
    # Register handlers for clean shutdown
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    version = cmd.get('version', 'v1') if cmd else 'v1'

    if psu is None and power_on is None and power_off is None:
        psu = PSU.configure('psu2')
        psu.setOVCP(1, 12.5, 1.7, True)
        psu.set(1, 12, 1.5)
        psu_created = True

    if version == 'v2':
        slta = SLTAv2()
    else:
        slta = SLTA()

    try:
        if power_on is not None:
            power_on()
        elif psu is not None:
            psu.on(1)
        print(f"\U0001F7E2 SLTA power ON: Idle for {cmd['idle']} seconds")
        time.sleep(cmd['idle'])

        if version == 'v1':
            # v1: configure.exe → SilentWatch → inline bash read → pkill
            process = slta.configure()
            slta.SilentWatch(process, text="Accepting commands")
            print("\U0001F535 SLTA accepting commands")
            slta.read(cmd)
            slta.pkill()
        else:
            # v2: configure.exe → SilentWatch → slta_run_v1.sh (with timeout) → pkill
            process = slta.configure()
            slta.SilentWatch(process, text="Accepting commands")
            print("\U0001F535 SLTA accepting commands")
            slta.read(cmd)  # SLTAv2.read() has timeout + error handling
            slta.pkill()

    finally:
        try:
            if power_off is not None:
                power_off()
            elif psu is not None:
                psu.off(1)
        except Exception:
            pass
        if psu_created:
            try:
                psu.shutdown()
                psu.disconnect()
            except Exception:
                pass
        print("\U0001F534 SLTA shutdown complete")

def main() -> None:
    """Entry point for command line execution."""
    # Register handlers for clean shutdown
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    capture()

if __name__ == "__main__":
    main()
