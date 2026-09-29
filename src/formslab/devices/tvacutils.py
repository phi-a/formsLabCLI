import json
import os
import time
import traceback
from datetime import datetime

from formslab.devices.DP832A import PSU
from formslab.devices.RTD16 import RTD16
from formslab.devices.psu_config import load_usbmap
from formslab.devices.psu_service import get_psu


def _init(forms, rGlobal):
    if getattr(rGlobal, "_hardware_initialized", False):
        return rGlobal
    _clear_tvac_log(rGlobal)
    _init_psu(forms, rGlobal)
    _init_sensors(forms,rGlobal)
    rGlobal._last_log_time = 0.0
    rGlobal._hardware_initialized = True
    return rGlobal


def _clear_tvac_log(rGlobal):
    try:
        rGlobal.tvaclog.unlink(missing_ok=True)
        return rGlobal
    except Exception:
        return rGlobal


def _init_psu(forms, rGlobal):
    if rGlobal.psu1 is None:
        # Intentional exception: the TVAC heater loop still owns PSU1 directly.
        forms.log(f"Initializing...", level="INFO", component="PSU1") 
        psu = get_psu("psu1")
        rGlobal.psu1 = psu
    try:
        forms.log(f"Setting channels...", level="INFO", component="PSU1") 
        rGlobal.psu1.setOVCP(1, 28.5, 2.1, True)
        rGlobal.psu1.setOVCP(2, 28.5, 2.1, True)
        rGlobal.psu1.set(1,0,0)
        rGlobal.psu1.set(2,0,0)
        rGlobal.psu1.on(1)
        rGlobal.psu1.on(2)
        rGlobal.psu1.update()
        forms.log(f"Sucessfully initialized", level="INFO", component="PSU1")
        return rGlobal 
    except Exception as e:
        tb = traceback.format_exc()
        forms.log(f"initialization failed: {e}\n{tb}", level="ERROR", component="PSU1")
    return rGlobal


def _init_sensors(forms, rGlobal):
    smtc_class = None
    smtc_import_error = None
    if rGlobal.smtcA is None or rGlobal.smtcB is None:
        try:
            from formslab.devices.SMTC08 import SMTC08 as smtc_class
        except ModuleNotFoundError as exc:
            smtc_import_error = exc
            forms.log(
                f"SMTC08 support unavailable: {exc}. Install 'pymodbus' to enable thermocouple reads.",
                level="WARNING",
                component="SMTC08",
            )

    if rGlobal.rtd1 is None:
        try:
            forms.log(f"Initializing...", level="INFO", component="RTD16") 
            rGlobal.rtd1 = RTD16("RTD1")
            rGlobal.rtd1.read_serial()
            forms.log(
                f"Sucessfully initialized on {rGlobal.rtd1.device_path}",
                level="INFO",
                component="RTD16",
            )
        except Exception as e:
            rGlobal.rtd1 = None
            forms.log(f"initialization failed: {e}", level="ERROR", component="RTD16") 
    if rGlobal.smtcA is None and smtc_class is not None:
        try:
            forms.log(f"Initializing...", level="INFO", component="SMTC08_A") 
            rGlobal.smtcA = smtc_class("SMTC08_A", slave=1)
            forms.log(f"Sucessfully initialized to {rGlobal.smtcA.port}", level="INFO", component="SMTC08_A")  
        except Exception as e:
            tb = traceback.format_exc()
            forms.log(f"Initialization failed: {e}\n{tb}", level="ERROR", component="SMTC08_A")
    elif rGlobal.smtcA is None and smtc_import_error is not None:
        rGlobal.smtcA = None
    # SMTC08_B (TC09-TC16) is optional: the bench currently has only board A,
    # so an absent map entry leaves the slot empty instead of logging an error
    if rGlobal.smtcB is None and smtc_class is not None and "SMTC08_B" not in load_usbmap():
        forms.log("Not in usbmap; TC09-TC16 disabled", level="INFO", component="SMTC08_B")
    elif rGlobal.smtcB is None and smtc_class is not None:
        try:
            forms.log(f"Initializing...", level="INFO", component="SMTC08_B") 
            rGlobal.smtcB = smtc_class("SMTC08_B", slave=1)
            forms.log(f"Sucessfully initialized to {rGlobal.smtcB.port}", level="INFO", component="SMTC08_B")  
        except Exception as e:
            tb = traceback.format_exc()
            forms.log(f"Initialization failed: {e}\n{tb}", level="ERROR", component="SMTC08_B")
    elif rGlobal.smtcB is None and smtc_import_error is not None:
        rGlobal.smtcB = None


# --- Logging ---
def _update_tvac(forms, rGlobal, data):
    if not data:
        return
    now = time.time()
    if now - rGlobal._last_log_time < rGlobal.LOG_INTERVAL:
        return
    rGlobal._last_log_time = now

    entry = {"timestamp": datetime.utcnow().strftime("%Y:%m:%d:%H:%M:%S.%f")}
    entry.update(data)

    try:
        if rGlobal.tvaclog.exists():
            with rGlobal.tvaclog.open("r+", encoding="utf-8") as f:
                try:
                    data_list = json.load(f)
                except json.JSONDecodeError:
                    data_list = []
                data_list.append(entry)
                f.seek(0)
                json.dump(data_list, f, indent=2)
                f.truncate()
        else:
            with rGlobal.tvaclog.open("w", encoding="utf-8") as f:
                json.dump([entry], f, indent=2)
    except Exception as e:
        forms.log(f"Failed to log TVAC data: {e}", level="WARNING", component="rTVAC")
