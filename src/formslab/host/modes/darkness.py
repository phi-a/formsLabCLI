
from forms.core.forms import FORMS
from forms.utils.rScripts import rScripts, eScript

def initialize():
    forms = FORMS()
    planet = forms.planet

    # --- TLE Setup & Time Initialization ---
    tle = """           
        1 25544U 98067A   26041.53604394  .00010157  00000-0  19516-3 0  9996
        2 25544  51.6312 206.5863 0011092  84.3744 275.8509 15.48525084552126
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
    # Note: Experiment routines moved to .forms mission files.
    # Legacy rScripts are loaded from the top-level rScripts/ folder.
    rScripts(forms,[
        "rVARIABLES.py",
        "rFSS.py",
        "rSTATES.py",
        "rPSU.py",
        "rCryoBoard.py",
        "rTVAC.py",
        "rSLTA.py",
        ])
    eScript(forms)
    # --- Record ---
    forms.record(value=30,unit='seconds')
    return forms
