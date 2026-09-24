"""TVAC mode for the LACO chamber (HVC-3500 over Ethernet).

Same host setup as `tvac.py`, but the chamber is an integrated PLC that owns
vacuum sequencing and thermal control, so the Rigol PSU / RTD / SMTC08 heater
loop is not loaded. Only `rTVAC_LACO.py` runs, polling and commanding the
controller over ASCII/TCP.
"""
from forms.core.forms import FORMS
from forms.utils.rScripts import rScripts, eScript

def initialize():
    forms = FORMS()
    planet = forms.planet

    # --- TLE Setup & Time Initialization ---
    tle = """
        1 25544U 98067A   25209.13279725  .00012211  00000-0  22036-3 0  9997
        2 25544  51.6347 104.1294 0001992 125.2997 234.8178 15.50161265521514
    """
    forms.load_tle(tle)
    
    # --- Time Span & Propagation Mode ---
    forms.time.set_span(mode="pulse", duration=float('inf'), units="minutes")

    # --- Propagator ---
    propagator = forms.satellite.set_propagator('sgp4')
    propagator.bind(forms)
    propagator.initialize()
    propagator.pulse(fixed_dt=2)
    propagator.history = True
    # --- Logging ---
    print("Start Time (JD):", forms.time.epoch0)
    print("End Time   (JD):", forms.time.epoch1)

    # --- Routine Scripts ---
    rScripts(forms, ["rTVAC_LACO.py"])
    eScript(forms)
    # --- Record ---
    forms.record(value=30,unit='seconds')
    return forms
