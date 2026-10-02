"""The LACO thermal-vacuum chamber at UIUC, as one object.

`HVC3500Client` speaks the controller's vendor protocol in zone numbers and
sensor indices. This class speaks the chamber: `platen` and `shroud`, the
sensor names on the HMI, the setpoint limits of this installation. Anything
that is a fact about the UIUC chamber rather than about the HVC-3500 product
lives here or in the bench profile (`tvac_bench.json`), never in a script.

    laco = LACO()                     # profile from tvac_bench.json
    laco.connect()
    laco.pressure()                   # -> 7.5e-03 (profile.pressure_unit)
    laco.platen.temperature()         # control sensor, degrees C
    laco.platen.set(25.0)             # clamped to the profile limits; returns the value applied
    laco.platen.on(); laco.shroud.off()
    laco.sensor("T14")                # any mapped thermocouple, degrees C
    laco.status()                     # one LacoStatus: pressure, zones, valves, faults, recipe
    laco.device("vent", True)         # a valve or pump, read first and verified
    laco.stop_pumping()               # rough valve closed, then the pump off
    laco.operation("vent2atm")        # a cycle-level vacuum operation (!VA)

`apply(request)` is the whole surface as one dict grammar: what the CAST `hvc`
block carries from the console (rLACO's cast commands) and from lab plans.

Commissioning rules (docs/HVC3500.md):
- every setpoint write is verified (the client reads back or checks the echo)
- actions (start, abort, reset, recipe run, vacuum operations, zone on/off)
  change process state and are never retried; read `status()` after one
- valves and pumps go through `device()`: read first, one toggle, verified;
  the PLC's interlocks decide, and a refusal is reported, never retried
- the vacuum operations (!VA !FA !PS !NA) act inside a running cycle; on an
  idle chamber in Manual mode use the valves (`device`) instead
- temperatures are degrees C throughout, as on the HMI; callers convert
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from formslab.devices.hvc3500 import BenchProfile, HVC3500Client, ProtocolError, load_profile
from formslab.devices.hvc3500 import protocol as P

DEVICE_LABELS = {"OR": "rough", "OV": "vent", "OF": "fill", "O4": "foreline",
                 "OG": "gate", "OP": "pump", "OT": "turbo"}
DEVICE_CODES = {label: code for code, label in DEVICE_LABELS.items()}
VALVES = ("rough", "vent", "fill", "foreline", "gate")
PUMPS = ("pump", "turbo")

# Cycle-level vacuum operations (manual appendix 9.1).
OPERATIONS = {"vent2atm": "VA", "fill2atm": "FA", "purge": "PS", "close_all": "NA"}


@dataclass
class ZoneStatus:
    name: str
    number: int
    temperature_c: float | None        # the zone's control sensor
    effective_setpoint_c: float | None  # ?Zn: tracks the sensor while the zone is idle
    target_c: float | None             # last value this process commanded, if any
    rate_c_per_min: float | None = None
    range_c: float | None = None


@dataclass
class LacoStatus:
    """A read-only snapshot of the chamber. Fields that could not be read are
    None and named in `errors`; a snapshot is never partially raised."""
    t: float
    mode: str | None
    test_status: str | None
    thermal_control: bool
    pressure: float | None
    pressure_unit: str
    pressure_rate: float | None
    vacuum_setpoint: float | None
    recipe: int | None
    recipe_step: int | None
    test_time: float | None
    fault_severity: str | None
    faults: list[str]
    fault_mask: int | None
    zones: dict[str, ZoneStatus]
    sensors: dict[str, float | None]      # by profile name, degrees C
    devices: dict[str, bool]              # rough, vent, fill, foreline, gate, pump, turbo
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and self.fault_severity == "N"

    def as_cast(self) -> dict:
        """The flat block the console's CAST `hvc` status shows."""
        d = {
            "connected": True,
            "mode": self.mode,
            "test_status": self.test_status,
            "pressure": self.pressure,
            "pressure_unit": self.pressure_unit,
            "vacuum_setpoint": self.vacuum_setpoint,
            "recipe": self.recipe,
            "recipe_step": self.recipe_step,
            "thermal_control": self.thermal_control,
            "fault_severity": self.fault_severity,
            "faults": ", ".join(self.faults) or "none",
        }
        for z in self.zones.values():
            d[f"{z.name} C"] = z.temperature_c
            d[f"{z.name} setpoint C"] = z.effective_setpoint_c
        d.update(self.devices)
        # the chamber's own thermocouples, by their HMI names
        for name, t in self.sensors.items():
            if not name.endswith("_ctrl"):
                d[f"{name} C"] = t
        return d

    def as_record(self) -> dict:
        """A flat dict for the disk log: everything, keyed as the protocol names it."""
        d = {
            "mode": self.mode, "test_status": self.test_status, "pressure": self.pressure,
            "pressure_rate": self.pressure_rate, "vacuum_setpoint": self.vacuum_setpoint,
            "recipe": self.recipe, "recipe_step": self.recipe_step, "test_time": self.test_time,
            "error_status": {"severity": self.fault_severity, "mask": self.fault_mask,
                             "names": self.faults},
        }
        for name, t in self.sensors.items():
            d[f"T_{name}"] = t
        for z in self.zones.values():
            d[f"Z{z.number}_setpoint"] = z.effective_setpoint_c
            d[f"Z{z.number}_rate"] = z.rate_c_per_min
            d[f"Z{z.number}_range"] = z.range_c
            d[f"Z{z.number}_target"] = z.target_c
        d.update(self.devices)
        if self.errors:
            d["errors"] = self.errors
        return d


class Zone:
    """One thermal zone by its chamber name. Degrees C."""

    def __init__(self, laco: "LACO", name: str, number: int) -> None:
        self._laco = laco
        self.name = name
        self.number = number
        self.target_c: float | None = None     # last commanded by this process
        ctrl = laco.profile.sensors.get(f"{name}_ctrl")
        self.control_sensor: int | None = int(ctrl) if ctrl is not None else None

    @property
    def bounds(self) -> tuple[float, float]:
        return self._laco.profile.setpoint_bounds(self.name)

    def temperature(self) -> float:
        """The zone's control sensor."""
        if self.control_sensor is None:
            raise KeyError(f"profile has no {self.name}_ctrl sensor")
        return self._laco.client.temperature(self.control_sensor)

    def setpoint(self) -> float:
        """The effective setpoint (?Zn). Tracks the sensor while the zone is idle."""
        return self._laco.client.zone_setpoint(self.number)

    def set(self, degrees_c: float, *, clamp: bool = True) -> float:
        """Command a setpoint. Out-of-range values are clamped to the profile
        limits (or refused with `clamp=False`). Returns the value the
        controller took; compare it with what you asked for."""
        lo, hi = self.bounds
        wanted = float(degrees_c)
        applied = min(max(wanted, lo), hi)
        if applied != wanted and not clamp:
            raise ValueError(f"{self.name} setpoint {wanted} C outside {lo}..{hi} C")
        got = self._laco.client.set_zone_setpoint(self.number, applied)
        self.target_c = got
        return got

    def set_rate(self, c_per_min: float) -> float:
        """Rate setpoint, degrees C per minute. Verified by read-back."""
        return self._laco.client.set_zone_rate(self.number, float(c_per_min))

    def set_range(self, degrees_c: float) -> float:
        """Control range (deadband), degrees C. Verified by read-back."""
        return self._laco.client.set_zone_range(self.number, float(degrees_c))

    def on(self) -> None:
        """Turn the zone's thermal control on (!ZSn)."""
        self._laco.client.activate_zone(self.number, confirm=True)

    def off(self) -> None:
        """Turn the zone's thermal control off (!ZOn)."""
        self._laco.client.deactivate_zone(self.number, confirm=True)

    def __repr__(self) -> str:
        return f"Zone({self.name}, zone {self.number})"


class LACO:
    """The chamber. Owns the bench profile and one `HVC3500Client`."""

    def __init__(self, profile: BenchProfile | None = None,
                 client: HVC3500Client | None = None, **client_kwargs) -> None:
        self.profile = profile or load_profile()
        self.client = client or HVC3500Client(
            self.profile.host, self.profile.port,
            timeout=self.profile.timeout_s, **client_kwargs)
        self.connected = False
        self.zones: dict[str, Zone] = {
            name: Zone(self, name, int(n)) for name, n in self.profile.zones.items()}
        for name, zone in self.zones.items():
            # laco.platen, laco.shroud -- unless the profile names a zone after a method
            if not hasattr(self, name):
                setattr(self, name, zone)

    # ------------------------------------------------------------ connection
    def connect(self) -> None:
        self.client.connect()
        self.connected = True

    def close(self) -> None:
        self.client.close()
        self.connected = False

    def __enter__(self) -> "LACO":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def endpoint(self) -> str:
        return f"{self.profile.host}:{self.profile.port}"

    # ------------------------------------------------------------------ reads
    def zone(self, name: str) -> Zone:
        try:
            return self.zones[name]
        except KeyError:
            raise KeyError(f"no zone {name!r}; have {sorted(self.zones)}") from None

    def pressure(self) -> float:
        """Chamber pressure in `profile.pressure_unit`."""
        return self.client.pressure()

    def sensor(self, name: str) -> float:
        """A thermocouple by its profile name (e.g. 'T14', 'platen_ctrl'), degrees C."""
        try:
            n = self.profile.sensors[name]
        except KeyError:
            raise KeyError(f"no sensor {name!r}; have {sorted(self.profile.sensors)}") from None
        return self.client.temperature(int(n))

    def faults(self) -> P.ErrorStatus:
        return self.client.error_status()

    def status(self) -> LacoStatus:
        """Everything the chamber reports, in one read. Per-item failures are
        recorded in `errors`; a transport failure on the first read raises."""
        sensor_nums = sorted(set(int(n) for n in self.profile.sensors.values()))
        zone_nums = sorted(set(z.number for z in self.zones.values()))
        snap = self.client.snapshot(temperatures=sensor_nums, zones=zone_nums)
        if all(snap.get(k) is None for k in ("mode", "test_status", "pressure")):
            self.connected = False
            first = next(iter(snap.get("errors", {}).values()), "no reply")
            raise OSError(f"HVC-3500 not responding: {first}")
        self.connected = True

        es = snap.get("error_status") or {}
        by_num = {n: snap.get(f"T{n}") for n in sensor_nums}
        zones = {}
        for z in self.zones.values():
            zones[z.name] = ZoneStatus(
                name=z.name, number=z.number,
                temperature_c=by_num.get(z.control_sensor) if z.control_sensor is not None else None,
                effective_setpoint_c=snap.get(f"Z{z.number}_setpoint"),
                target_c=z.target_c,
                rate_c_per_min=snap.get(f"Z{z.number}_rate"),
                range_c=snap.get(f"Z{z.number}_range"))
        return LacoStatus(
            t=snap.get("t", time.time()),
            mode=snap.get("mode"),
            test_status=snap.get("test_status"),
            thermal_control="holding temperature" in str(snap.get("test_status") or "").lower(),
            pressure=snap.get("pressure"),
            pressure_unit=self.profile.pressure_unit,
            pressure_rate=snap.get("pressure_rate"),
            vacuum_setpoint=snap.get("vacuum_setpoint"),
            recipe=snap.get("recipe"),
            recipe_step=snap.get("recipe_step"),
            test_time=snap.get("test_time"),
            fault_severity=es.get("severity"),
            faults=list(es.get("names", [])),
            fault_mask=es.get("mask"),
            zones=zones,
            sensors={name: by_num.get(int(n)) for name, n in self.profile.sensors.items()},
            devices={label: bool(snap[code]) for code, label in DEVICE_LABELS.items()
                     if snap.get(code) is not None},
            errors=dict(snap.get("errors", {})),
        )

    def quick_status(self) -> dict:
        """Pressure, faults and every valve and pump -- what a command changes --
        in 9 reads (about 1.5 s) instead of the ~38 of `status()`. Keys as in
        `LacoStatus.as_cast()`, so it can be merged into the CAST block."""
        c = self.client
        es = c.error_status()
        d = {"connected": True, "pressure": c.pressure(),
             "fault_severity": es.severity, "faults": ", ".join(es.names) or "none"}
        for code, label in DEVICE_LABELS.items():
            d[label] = c.device_state(code)
        self.connected = True
        return d

    # ----------------------------------------------------------------- writes
    def vacuum(self, setpoint: float) -> float:
        """Vacuum setpoint in `profile.pressure_unit`. Verified by read-back."""
        return self.client.set_vacuum_setpoint(float(setpoint))

    def vacuum_range(self, value: float) -> float:
        return self.client.set_vacuum_range(float(value))

    def vacuum_rate(self, value: float) -> float:
        return self.client.set_vacuum_rate(float(value))

    def hold_time(self, seconds: float) -> float:
        return self.client.set_hold_time(float(seconds))

    def recipe(self, n: int) -> int:
        """Select recipe n (!TR). Verified by read-back."""
        return self.client.select_recipe(int(n))

    def recipe_run(self, run: bool) -> None:
        """!RS starts the selected recipe, !RO stops it. Not retried."""
        self.client.action("RS" if run else "RO", confirm=True)

    def start(self) -> None:
        """!CS: start, or continue a held recipe step. Not retried."""
        self.client.action("CS", confirm=True)

    def abort(self) -> None:
        """!CA: abort the running cycle. Not retried."""
        self.client.action("CA", confirm=True)

    def reset(self) -> None:
        """!CR: reset the controller (starts its recovery/home sequence). Not retried."""
        self.client.action("CR", confirm=True)

    def operation(self, name: str) -> None:
        """A cycle-level vacuum operation: vent2atm (!VA), fill2atm (!FA),
        purge (!PS), close_all (!NA). They act inside a running cycle; an idle
        chamber acknowledges them and does nothing."""
        try:
            code = OPERATIONS[name]
        except KeyError:
            raise KeyError(f"no operation {name!r}; have {sorted(OPERATIONS)}") from None
        self.client.action(code, confirm=True)

    def device(self, name: str, on: bool) -> bool:
        """Open/close a valve or start/stop a pump by name (rough, vent, fill,
        foreline, gate, pump, turbo): read first, at most one toggle, verified
        after the actuation delay. Raises ProtocolError if the PLC kept it
        (an interlock); never retries."""
        try:
            code = DEVICE_CODES[name]
        except KeyError:
            raise KeyError(f"no valve or pump {name!r}; have {sorted(DEVICE_CODES)}") from None
        return self.client.set_device(code, bool(on), confirm=True)

    def stop_pumping(self) -> list[str]:
        """Rough valve closed, then the roughing pump off -- the PLC will not
        stop the pump while the rough valve is open. Refuses to stop the pump
        while the turbo runs or the foreline is open (the turbo backs onto it).
        Returns what it did."""
        done = []
        if self.client.device_state("OR"):
            self.device("rough", False)
            done.append("rough valve closed")
        if self.client.device_state("OP"):
            if self.client.device_state("OT") or self.client.device_state("O4"):
                raise ProtocolError("turbo on or foreline open: stop the turbo and close the "
                                    "foreline before stopping the roughing pump")
            self.device("pump", False)
            done.append("pump off")
        return done

    # -------------------------------------------------------- CAST requests
    def apply(self, request: dict) -> list[tuple[str, str]]:
        """Apply a request block (the CAST `hvc` grammar) and report what
        happened as (level, message) pairs. A failing item is reported, not
        raised, so one bad key cannot stop the rest. Keys, applied in this order:

            <zone>: C   <zone>_rate: C/min   <zone>_range: C      setpoints
            vacuum: P   vacuum_range: P   vacuum_rate   hold_s: s
            recipe: n                                            select recipe
            <zone>_control: bool | "on"/"off"                    thermal control
            rough|vent|fill|foreline|gate: "open"|"close"|bool   valves
            pump|turbo: "on"|"off"|bool                          pumps
            stop_pumping: true                                   rough closed, pump off
            recipe_run: "start"|"stop"|bool                      !RS / !RO
            start | abort | reset: true                          !CS !CA !CR
            vent2atm | fill2atm | purge | close_all: true        !VA !FA !PS !NA
        """
        out: list[tuple[str, str]] = []
        unit = self.profile.pressure_unit
        known = set()

        def attempt(what, fn, done):
            try:
                out.append(("INFO", done(fn())))
            except (OSError, ProtocolError, ValueError, KeyError) as e:
                out.append(("ERROR", f"{what}: {e}"))

        def number(key):
            known.add(key)
            v = request.get(key)
            if v is None:
                return None
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                out.append(("WARNING", f"{key}: expected a number, got {v!r}; ignored"))
                return None
            return float(v)

        for zone in self.zones.values():
            if (v := number(zone.name)) is not None:
                lo, hi = zone.bounds
                if not lo <= v <= hi:
                    out.append(("WARNING",
                                f"{zone.name} setpoint {v} clamped to {min(max(v, lo), hi)} C"))
                attempt(f"{zone.name} setpoint", lambda z=zone, v=v: z.set(v),
                        lambda got, z=zone: f"{z.name} setpoint -> {got} C")
            if (v := number(f"{zone.name}_rate")) is not None:
                attempt(f"{zone.name} rate", lambda z=zone, v=v: z.set_rate(v),
                        lambda got, z=zone: f"{z.name} rate -> {got} C/min")
            if (v := number(f"{zone.name}_range")) is not None:
                attempt(f"{zone.name} range", lambda z=zone, v=v: z.set_range(v),
                        lambda got, z=zone: f"{z.name} range -> {got} C")
        for key, fn, shown in (("vacuum", self.vacuum, "vacuum setpoint -> {} " + unit),
                               ("vacuum_range", self.vacuum_range, "vacuum range -> {} " + unit),
                               ("vacuum_rate", self.vacuum_rate, "vacuum rate -> {}"),
                               ("hold_s", self.hold_time, "hold time -> {} s"),
                               ("recipe", self.recipe, "recipe -> {}")):
            if (v := number(key)) is not None:
                attempt(key, lambda fn=fn, v=v: fn(v), lambda got, shown=shown: shown.format(got))

        for zone in self.zones.values():
            key = f"{zone.name}_control"
            known.add(key)
            if (v := _flag(request.get(key), ("on", "off"))) is not None:
                attempt(key, lambda z=zone, v=v: z.on() if v else z.off(),
                        lambda _, z=zone, v=v: f"{z.name} thermal control {'ON' if v else 'OFF'}")

        for name in (*VALVES, *PUMPS):
            known.add(name)
            if name not in request:
                continue
            words = ("open", "close") if name in VALVES else ("on", "off")
            v = _flag(request[name], words)
            if v is None:
                out.append(("WARNING", f"{name}: expected {'/'.join(words)}, got {request[name]!r}"))
                continue
            attempt(name, lambda n=name, v=v: self.device(n, v),
                    lambda got, n=name, w=words: f"{n} verified {w[0] if got else w[1]}")

        known.add("stop_pumping")
        if request.get("stop_pumping") is True:
            attempt("stop_pumping", self.stop_pumping,
                    lambda done: "stop_pumping: " + (", ".join(done) or "already stopped"))

        known.add("recipe_run")
        if (v := _flag(request.get("recipe_run"), ("start", "stop"))) is not None:
            attempt("recipe_run", lambda v=v: self.recipe_run(v),
                    lambda _, v=v: f"recipe {'started' if v else 'stopped'}")
        actions = [("start", self.start), ("abort", self.abort), ("reset", self.reset)]
        actions += [(op, lambda op=op: self.operation(op)) for op in OPERATIONS]
        for key, fn in actions:
            known.add(key)
            if request.get(key) is True:
                attempt(key, fn, lambda _, key=key: f"{key} sent")

        for key in request:
            if key not in known:
                out.append(("WARNING", f"unknown request key {key!r}; ignored"))
        return out


def _flag(value, words: tuple[str, str]):
    """True/False from a bool or one of two words (e.g. "open"/"close");
    None when absent or not understood."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in words:
        return value.lower() == words[0]
    return None
