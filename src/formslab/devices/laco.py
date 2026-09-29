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

Commissioning rules carried over from the tvac repo:
- every setpoint write is verified (the client reads back or checks the echo)
- start/abort/vent change process state and are never retried; after one, read
  `status()` before deciding anything
- raw valve and pump toggles are not exposed; the PLC sequences those
- temperatures are degrees C throughout, as on the HMI; callers convert
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from formslab.devices.hvc3500 import BenchProfile, HVC3500Client, ProtocolError, load_profile
from formslab.devices.hvc3500 import protocol as P

DEVICE_LABELS = {"OR": "rough", "OV": "vent", "OF": "fill", "O4": "foreline",
                 "OG": "gate", "OP": "pump", "OT": "turbo"}


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

    # ----------------------------------------------------------------- writes
    def vacuum(self, setpoint: float) -> float:
        """Vacuum setpoint in `profile.pressure_unit`. Verified by read-back."""
        return self.client.set_vacuum_setpoint(float(setpoint))

    def start(self) -> None:
        """!CS: start, or continue a held recipe step. Not retried."""
        self.client.action("CS", confirm=True)

    def abort(self) -> None:
        """!CA: abort the running cycle. Not retried."""
        self.client.action("CA", confirm=True)

    def vent(self) -> None:
        """!VA: vent to atmosphere. The PLC checks vent temperatures first."""
        self.client.action("VA", confirm=True)

    # -------------------------------------------------------- CAST requests
    def apply(self, request: dict) -> list[tuple[str, str]]:
        """Apply a console request block and report what happened.

        The request grammar is what the CAST `hvc` block accepts (see
        rScripts/rLACO.py). Returns (level, message) pairs for the caller's
        log; a failing item is reported, not raised, so one bad key cannot
        stop the rest.
        """
        out: list[tuple[str, str]] = []
        for zone in self.zones.values():
            val = request.get(zone.name)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                try:
                    got = zone.set(val)
                    if got != float(val):
                        out.append(("WARNING", f"{zone.name} setpoint {val} clamped to {got} C"))
                    out.append(("INFO", f"{zone.name} setpoint -> {got} C"))
                except (OSError, ProtocolError) as e:
                    out.append(("ERROR", f"{zone.name} setpoint write: {e}"))
            ctl = request.get(f"{zone.name}_control")
            if isinstance(ctl, bool):
                try:
                    zone.on() if ctl else zone.off()
                    out.append(("INFO", f"{zone.name} thermal control {'ON' if ctl else 'OFF'}"))
                except (OSError, ProtocolError) as e:
                    out.append(("ERROR", f"{zone.name} control {ctl}: {e}"))
        vac = request.get("vacuum")
        if isinstance(vac, (int, float)) and not isinstance(vac, bool):
            try:
                out.append(("INFO", f"vacuum setpoint -> {self.vacuum(vac)} {self.profile.pressure_unit}"))
            except (OSError, ProtocolError) as e:
                out.append(("ERROR", f"vacuum setpoint write: {e}"))
        for key, fn in (("start", self.start), ("abort", self.abort), ("vent", self.vent)):
            if request.get(key) is True:
                try:
                    fn()
                    out.append(("INFO", f"{key} sent"))
                except (OSError, ProtocolError) as e:
                    out.append(("ERROR", f"{key}: {e}"))
        return out
