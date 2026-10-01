"""Shroud heater PI loop for the Rigol/RTD bench (used by rTVAC).

Commands PSU1 directly; see rTVAC for which channels.
"""
from time import time, sleep
import json
from pathlib import Path

from formslab.config import output_dir

class HeaterController:
    """Direct PSU1 heater loop used by TVAC.

    This remains the current exception to the shared PSU-owner model:
    TVAC's heater controller still commands PSU1 directly.
    """
    def __init__(self, psu, forms, channel_map, target_vars,
                 kp=2.0, ki=0.1, vlim=28.0, clim=2.5,
                 integrator_clip=100.0, decay_factor=0.95,
                 log_file="TVAC.json", log_interval=30.0):

        self.psu = psu
        self.forms = forms
        self.channel_map = channel_map
        self.target_vars = target_vars
        self.kp = kp
        self.ki = ki
        self.vlim = vlim
        self.clim = clim
        self.integrator_clip = integrator_clip
        self.decay_factor = decay_factor
        self._integral = {name: 0.0 for name in channel_map}
        self._last_time = time()

        # Run product, not code: into the output directory, never beside the
        # driver. site-packages is read-only on a shared lab machine.
        self.log_file = (Path(log_file) if Path(log_file).is_absolute()
                         else output_dir() / log_file)
        self.log_interval = log_interval
        self._last_log_time = time()

    def update(self):
        now = time()
        dt = now - self._last_time if self._last_time else 1.0
        self._last_time = now

        # --- CONTROL LOOP & SINGLE MEASUREMENT ---
        measured = {}  # cache {ch: (voltage_meas, current_meas)}
        for var_name, ch in self.channel_map.items():
            T_target   = self.forms.get_variable(self.target_vars[var_name]).value
            T_measured = self.forms.get_variable(var_name).value
            error      = T_target - T_measured

            # PID compute
            P = self.kp * error
            I = self.ki * self._integral[var_name]
            raw_v = P + I
            v_set = min(max(raw_v, 0.0), self.vlim)

            # apply and measure once
            self.psu.set(ch, v_set, self.clim)
            sleep(0.5)
            v_meas, i_meas = self.psu.measure(ch)
            sleep(0.1)

            # sanitize
            if v_meas is None or i_meas is None:
                self.forms.log(f"PSU read returned None on CH{ch}",
                               level='ERROR', component="HeaterController")
                v_meas, i_meas = 0.0, 0.0

            measured[ch] = (v_meas, i_meas)

            # integrator update
            in_cc   = abs(i_meas - self.clim) < 0.05
            sat     = (v_set >= self.vlim) or (self.psu.safe_lt(v_meas, v_set - 1.0) and in_cc)
            if sat:
                self._integral[var_name] *= self.decay_factor
            else:
                self._integral[var_name] += error * dt
                self._integral[var_name] = max(min(self._integral[var_name],
                                                   self.integrator_clip),
                                               -self.integrator_clip)

        # --- LOGGING & TENSOR REGISTRATION ---
        if now - self._last_log_time >= self.log_interval:
            self._last_log_time = now

            # build log entry
            log_entry = {"timestamp": self.forms.time.timestamp}
            for var_name, ch in self.channel_map.items():
                Tm = self.forms.get_variable(var_name).value
                v_meas, i_meas = measured[ch]
                log_entry[var_name] = Tm
                log_entry[f"CH{ch}"] = {
                    "T_measured": Tm,
                    "voltage":    v_meas,
                    "current":    i_meas
                }

            # PSU1 readback per heater channel, as the scalars rPSU also publishes
            for ch, (v, i) in measured.items():
                for suffix, value, unit in (("V", v, "V"), ("I", i, "A")):
                    name = f"PSU1_CH{ch}_{suffix}"
                    var = self.forms.get_variable(name) or self.forms.types.scalar(
                        name, unit=unit, overwrite=False)
                    var.set(value=value, unit=unit)
            # write JSON log
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                if self.log_file.exists():
                    with open(self.log_file, "r+", encoding="utf-8") as f:
                        try:
                            arr = json.load(f)
                        except json.JSONDecodeError:
                            arr = []
                        arr.append(log_entry)
                        f.seek(0)
                        json.dump(arr, f, indent=2)
                        f.truncate()
                else:
                    with open(self.log_file, "w", encoding="utf-8") as f:
                        json.dump([log_entry], f, indent=2)

                # self.forms.log(f"Logged TVAC data to {self.log_file.name}",
                #                level="INFO", component="shroud")

            except Exception as e:
                self.forms.log(f"Failed to log TVAC data: {e}",
                               level="ERROR", component="shroud")
