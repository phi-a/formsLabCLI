"""Host modes: the rScripts a `run <mode>` loads, and its CSV cadence.

A mode runs until ctrl `end`. A lab plan (`run <plan>`, see `formslab.sequence`)
names its own rScripts in the plan and ends when its Sequence does.

    laco   the LACO chamber through its HVC-3500 controller (rLACO)
    tvac   the Rigol/RTD bench: PSU service plus the shroud heater loop

`rCryoBoard` is not in `tvac`: its supply is PSU1 CH1, which rTVAC's heater
loop also drives (see docs/CRYOCOOLER.md). Run it from a plan that does not
load rTVAC.
"""
from formslab import rscripts

MODES = {
    "laco": {"rscripts": ["rLACO"], "record_s": 30},
    "tvac": {"rscripts": ["rPSU", "rTVAC"], "record_s": 30},
}

# Everything the host can launch: the modes, and `plan` for a lab plan file.
LAB_MODES = (*MODES, "plan")


def initialize(mode: str):
    """A `LabForms` handle with the mode's rScripts loaded and recording set."""
    spec = MODES[mode]
    forms = rscripts.LabForms(name=mode.upper())
    rscripts.load(forms, spec["rscripts"])
    forms.record(value=spec["record_s"], unit="seconds")
    return forms
