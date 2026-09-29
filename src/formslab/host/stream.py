import json
import math
import time as _time
from formslab.console.cast.castutils import AtomicJsonWrite
from pathlib import Path
from threading import Lock

_lock = Lock()

# --- Streaming rate limiter ---
_last_stream_t: float = 0.0
_stream_interval: float = 0.05  # 50 ms default → 20 Hz (matches Tauri poll rate)


def set_stream_hz(hz: float) -> None:
    """
    Set the maximum GUI streaming rate.

    Args:
        hz: Frames per second for streamfile.json updates.
            0 (or negative) disables streaming entirely — write() becomes a no-op,
            which is the fastest mode for batch/headless simulations.
    """
    global _stream_interval
    _stream_interval = (1.0 / hz) if hz > 0 else float("inf")

# The live-telemetry file lives under the run's output root (shared with the
# console and the packaged runner), resolved fresh so dev and installed runs
# agree -- never anchored to the source tree. FORMS' run dir when installed.
from formslab.host.paths import run_dir


def _json_path() -> Path:
    return run_dir() / "streamfile.json"


def _sanitize_value(val):
    """Replace NaN/Inf with None so JSON stays valid."""
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, list):
        return [_sanitize_value(v) for v in val]
    return val


def _vector_to_list(vec):
    """Best-effort 3-vector conversion to plain float list."""
    if vec is None:
        return None
    try:
        if hasattr(vec, "to_list"):
            data = vec.to_list()
        else:
            data = vec
        if isinstance(data, (list, tuple)) and len(data) == 3:
            return [float(data[0]), float(data[1]), float(data[2])]
    except Exception:
        return None
    return None


def _set_vector_snapshot(snapshot, name, values):
    """Write both array and flattened component keys for a vector."""
    if values is None:
        return
    snapshot[name] = values
    snapshot[f"{name}_x"] = values[0]
    snapshot[f"{name}_y"] = values[1]
    snapshot[f"{name}_z"] = values[2]


def _compute_lla_from_teme(forms):
    """Compute LATgd, LON, ALT via the low-fidelity TEME->ITRF path (no IAU-2006
    data needed). Repointed off the drained ``forms.bricks.models.*`` package to
    the canon frames surface."""
    from forms.bricks.frames.state import rv_itrf_from_teme
    from forms.bricks.frames.coord import Coord
    from forms.bricks.frames.frame import Frame
    from forms.bricks.framework.records import RV
    from forms.bricks.geodetic.conversions import lla_from_ecef
    from forms.bricks.math.units import deg

    rTEME = forms.get_variable('rTEME')
    vTEME = forms.get_variable('vTEME')
    if rTEME is None or vTEME is None:
        return None, None, None

    rv_teme = RV([rTEME[0], rTEME[1], rTEME[2]],
                 [vTEME[0], vTEME[1], vTEME[2]], Frame.TEME, "earth")
    rv_itrf = rv_itrf_from_teme(rv_teme, at=forms.time.instant, fidelity="low")
    geo = lla_from_ecef(Coord(rv_itrf.r[0], rv_itrf.r[1], rv_itrf.r[2], Frame.ITRF))
    return deg(geo.lat), deg(geo.lon), geo.alt


def _compute_subsolar_from_meeus(forms):
    """Compute subsolar geodetic latitude/longitude from the Meeus facade."""
    try:
        sun = getattr(forms, "sun", None)
        meeus = getattr(sun, "meeus", None) if sun is not None else None
        if meeus is None:
            return None, None

        try:
            sub_lat, sub_lon = meeus.subsolar_point
        except Exception:
            spa = getattr(meeus, "spa", None)
            if not isinstance(spa, dict):
                return None, None
            sub_lat = spa.get("subLat")
            sub_lon = spa.get("subLon")

        sub_lat = float(sub_lat)
        sub_lon = float(sub_lon)
        if (
            math.isnan(sub_lat)
            or math.isnan(sub_lon)
            or math.isinf(sub_lat)
            or math.isinf(sub_lon)
        ):
            return None, None

        return sub_lat, sub_lon
    except Exception:
        return None, None


def write(forms):
    """
    Build a flat dict of timestamp and all registered variables,
    then atomically overwrite streamfile.json.

    Rate-limited to _stream_interval seconds of wall-clock time between writes.
    Set stream_hz=0 via set_stream_hz() to disable streaming entirely.
    """
    global _last_stream_t
    now = _time.monotonic()
    if now - _last_stream_t < _stream_interval:
        return  # Too soon — GUI polls at 50 ms and won't notice skipped writes
    _last_stream_t = now

    with _lock:
        try:
            snapshot = {}
            sat = getattr(forms, "satellite", None)

            # timestamp
            ts = getattr(forms.time, "timestamp", None)
            if ts is not None:
                snapshot["timestamp"] = ts

            # ready flag from propagator config (for GUI to know when position is valid)
            ready = False
            if sat is not None:
                prop_config = getattr(sat, '_propagator_config', None)
                if prop_config is not None:
                    ready = bool(prop_config.ready)
                else:
                    ready = getattr(sat, "state", None) is not None
            snapshot["ready"] = ready

            # all registered vars
            for name in forms.list_variables():
                var = forms.get_variable(name)
                if getattr(var, "registered", False):
                    try:
                        val = var.as_record_value()
                    except Exception:
                        val = getattr(var, "value", None)
                    snapshot[name] = val

            # Canonical 3D telemetry contract:
            # always publish inertial state in GCRS regardless of user scripts.
            if sat is not None:
                try:
                    r_eci = _vector_to_list(sat.get_position())
                    _set_vector_snapshot(snapshot, "rECI", r_eci)
                except Exception:
                    pass

                try:
                    v_eci = _vector_to_list(sat.get_velocity())
                    _set_vector_snapshot(snapshot, "vECI", v_eci)
                except Exception:
                    pass

                if "LATgd" not in snapshot or "LON" not in snapshot or "ALT" not in snapshot:
                    try:
                        lla = sat.lla
                        lat, lon, alt = float(lla.lat), float(lla.lon), float(lla.alt)
                        if math.isnan(lat) or math.isnan(lon):
                            raise ValueError("NaN from IAU-2006 path")
                        snapshot.setdefault("LATgd", lat)
                        snapshot.setdefault("LON", lon)
                        snapshot.setdefault("ALT", alt)
                    except Exception:
                        # Fallback: use TEME->ECEF->GEO (no IAU06 data needed)
                        try:
                            lat, lon, alt = _compute_lla_from_teme(forms)
                            if lat is not None:
                                snapshot.setdefault("LATgd", lat)
                                snapshot.setdefault("LON", lon)
                                snapshot.setdefault("ALT", alt)
                        except Exception:
                            pass

                if "InUmbra" not in snapshot:
                    try:
                        snapshot["InUmbra"] = bool(sat.eclipse.in_umbra)
                    except Exception:
                        pass

            # Canonical Earth rotation angle for 3D Earth spin.
            try:
                gmst = forms.planet.gmst(forms.time.JD)
                if isinstance(gmst, (tuple, list)):
                    gmst = gmst[0]
                snapshot["gmst"] = float(gmst)
            except Exception:
                pass

            # Canonical sun geodetic subsolar point for 2D map marker.
            if "subLAT" not in snapshot or "subLON" not in snapshot:
                try:
                    sub_lat, sub_lon = _compute_subsolar_from_meeus(forms)
                    if sub_lat is not None and sub_lon is not None:
                        snapshot.setdefault("subLAT", sub_lat)
                        snapshot.setdefault("subLON", sub_lon)
                except Exception:
                    pass

            # Sun direction unit vector (J2000 equatorial) for 3D celestial sphere
            sun_dir = getattr(forms, '_sun_dir_j2000', None)
            if sun_dir is not None and len(sun_dir) == 3:
                snapshot["sunDir"] = [float(sun_dir[0]), float(sun_dir[1]), float(sun_dir[2])]

            # Sanitize: replace NaN/Inf with None so JSON stays valid
            snapshot = {k: _sanitize_value(v) for k, v in snapshot.items()}

            AtomicJsonWrite(snapshot, _json_path())
        except Exception as e:
            print(f"[stream] Failed to write JSON snapshot: {e}")

def read():
    """
    Load and return the flat snapshot dict from JSON, or None.
    """
    json_path = _json_path()
    if not json_path.exists():
        return None
    with _lock:
        try:
            with json_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[stream] Failed to read JSON snapshot: {e}")
            return None

def flush(forms):
    """
    Force one final stream write, bypassing the rate limiter.

    Call this at end-of-simulation so the GUI receives the terminal state
    even if the last loop tick was throttled.
    """
    global _last_stream_t
    _last_stream_t = 0.0  # reset limiter
    write(forms)           # guaranteed to execute


def cleanup_shared_memory():
    """Delete the JSON snapshot file."""
    try:
        _json_path().unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[stream] Failed to delete JSON snapshot: {e}")
