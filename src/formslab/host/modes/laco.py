"""LACO chamber mode (HVC-3500 over Ethernet). Runs without FORMS.

The chamber's PLC owns vacuum sequencing and thermal control, so this mode
needs no orbit and no heater loop: a `LabForms` handle and `rLACO`, which polls
and commands the controller. Each loop is paced in real time by the host.
"""
from formslab import rscripts

SCRIPTS = ["rLACO"]
RECORD_SECONDS = 30


def initialize():
    forms = rscripts.LabForms(name="LACO")
    rscripts.load(forms, SCRIPTS)
    forms.record(value=RECORD_SECONDS, unit="seconds")
    return forms
