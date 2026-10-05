# --- rOrbit: an orbit file, followed during a run ---
#
# Publishes where the satellite of an orbit file (docs/ORBIT.md) is, once a
# second: whether it is in the Earth's umbra, how long that umbra lasts and how
# much of it is left, how long until the next one, the beta angle and the
# altitude. rSLTA's umbra captures read InUmbra, UmbraDuration and
# UmbraTimeRemaining. It owns no hardware.
#
# `orbit follow <name>` puts the satellite where the wall clock does, as the GUI's
# live panel shows it; `orbit replay <name>` starts it at the file's epoch at that
# step, so a test sees the same orbit every time. Until either, it publishes nothing.
import math
import os
import time
from datetime import datetime, timedelta, timezone

from formslab.console.cast.castutils import ReportResult, TakeCommand, UpdateStatus
from formslab.orbit import file as orbitfile
from formslab.orbit.propagate import kepler
from formslab.orbit.propagate.constants import R_E

name = os.path.splitext(os.path.basename(__file__))[0]
LABEL = "orbit"
PUBLISH_INTERVAL = 1.0

CAST_LABELS = (LABEL,)
RESULT_LABELS = (LABEL,)   # a file that does not read is refused, so the plan stops there


def COMMANDS():
    """The orbit files on the plan path are the choices."""
    names = [p.stem for p in orbitfile.discover()]
    which = f"<orbit:{'|'.join(names)}>" if names else "<orbit:text>"
    return [
        (f"follow {which}", """Follow an orbit on the wall clock
         The satellite is where the orbit file puts it now, as the live panel on the
         Plans tab shows it. From this step, the run publishes InUmbra, UmbraDuration,
         UmbraTimeRemaining, NextUmbra, OrbitBeta and OrbitAltitude once a second.""",
         lambda orbit: {"follow": orbit}),
        (f"replay {which}", """Start an orbit at its epoch, now
         The satellite starts where the orbit file puts it at its epoch, at this step,
         and moves on from there, so a test sees the same orbit every time. It
         publishes the same values as follow.""",
         lambda orbit: {"replay": orbit}),
    ]


VARIABLES = [("InUmbra", None), ("UmbraDuration", "s"), ("UmbraTimeRemaining", "s"),
             ("NextUmbra", "s"), ("OrbitBeta", "deg"), ("OrbitAltitude", "km")]


def READINGS(label, status):
    return [("Orbit", None, [("orbit", "Orbit"), ("mode", "Mode"), ("in_umbra", "In umbra"),
                             ("umbra_left_s", "Umbra left (s)"), ("next_umbra_s", "Next umbra (s)"),
                             ("beta_deg", "Beta (deg)"), ("altitude_km", "Altitude (km)")])]


class rGlobal:
    elements = None      # kepler.Elements being followed
    orbit = None         # its file's name
    mode = None          # "follow" or "replay"
    offset = None        # orbit time minus wall-clock time
    spans = None         # umbra spans [(start, end)] around the orbit time
    renew_at = None      # orbit time after which the spans are found again
    next_publish = 0.0
    told = False         # "no orbit chosen" is logged once


rg = rGlobal


def _choose(request):
    """Follow or replay the orbit file the request names; (ok, message)."""
    mode = "follow" if "follow" in request else "replay"
    wanted = str(request[mode])
    path = next((p for p in orbitfile.discover() if p.stem.lower() == wanted.lower()), None)
    if path is None:
        return False, f"no orbit file named {wanted!r}"
    try:
        elements = orbitfile.parse(path.read_text(encoding="utf-8"))
    except (OSError, orbitfile.OrbitError) as e:
        return False, f"{path.stem} does not read: {e}"
    now = datetime.now(timezone.utc)
    rg.elements, rg.orbit, rg.mode = elements, path.stem, mode
    rg.offset = elements.epoch - now if mode == "replay" else timedelta(0)
    rg.spans = rg.renew_at = None
    rg.next_publish = 0.0
    return True, f"{mode} {path.stem}" + (f" from its epoch, {elements.epoch:%Y-%m-%d %H:%M:%S} UTC"
                                          if mode == "replay" else "")


def where(el, t, spans):
    """What rOrbit publishes for orbit time `t`, given the umbra `spans` around it."""
    current = next(((a, b) for a, b in spans if a <= t < b), None)
    coming = next(((a, b) for a, b in spans if a > t), None)
    r, _ = kepler.state(el, t)
    return {
        "InUmbra": 1 if current else 0,
        # In umbra, this umbra's whole length; in sunlight, the next one's (rSLTA sets
        # its exposure from it ahead of time); 0 when there is none.
        "UmbraDuration": ((current or coming)[1] - (current or coming)[0]).total_seconds()
                         if current or coming else 0.0,
        "UmbraTimeRemaining": (current[1] - t).total_seconds() if current else 0.0,
        "NextUmbra": (coming[0] - t).total_seconds() if coming else float("nan"),
        "OrbitBeta": math.degrees(kepler.beta(el, t)),
        "OrbitAltitude": (sum(c * c for c in r) ** 0.5 - R_E) / 1000,
    }


def spans_around(el, t):
    """Umbra spans from one period before `t` to three after, and the time after
    which they no longer reach far enough: so an umbra under way, and the next one,
    are always whole."""
    period = timedelta(seconds=el.period)
    return kepler.umbra_spans(el, t - period, t + 3 * period), t + period


def rScript(run):
    request, ids = TakeCommand(label=LABEL)
    if request:
        ok, message = _choose(request)
        run.log(message, level="INFO" if ok else "ERROR", component=name)
        ReportResult(LABEL, ids, ok, [message])
    if time.monotonic() < rg.next_publish:
        return
    rg.next_publish = time.monotonic() + PUBLISH_INTERVAL
    if rg.elements is None:
        if not rg.told:
            run.log("no orbit chosen yet: `orbit follow <name>` or `orbit replay <name>` chooses one",
                    component=name)
            rg.told = True
        UpdateStatus(label=LABEL, status={"orbit": None, "mode": "none chosen"})
        return
    t = datetime.now(timezone.utc) + rg.offset
    if rg.spans is None or t > rg.renew_at:
        rg.spans, rg.renew_at = spans_around(rg.elements, t)
    values = where(rg.elements, t, rg.spans)
    for key, unit in VARIABLES:
        run.publish(key, values[key], unit)
    UpdateStatus(label=LABEL, status={
        "orbit": rg.orbit, "mode": rg.mode, "in_umbra": bool(values["InUmbra"]),
        "umbra_left_s": round(values["UmbraTimeRemaining"]),
        "next_umbra_s": None if math.isnan(values["NextUmbra"]) else round(values["NextUmbra"]),
        "beta_deg": round(values["OrbitBeta"], 1), "altitude_km": round(values["OrbitAltitude"], 1)})
