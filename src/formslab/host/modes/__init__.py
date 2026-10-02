"""Host modes: what `run <mode>` loads.

There is one mode, `tvac`: manual chamber operation. It loads the rScripts the
bench config names and runs them until ctrl `end`; the operator drives the
chamber and instruments from the console's cast tab. Which scripts is a fact
about the computer, so it lives in the live bench config
(`$FORMSLAB_CONFIG_DIR/tvac_bench.json`, seeded from the packaged default):

    "tvac": {"rscripts": ["rLACO", "rSMTC08", "rPSU"], "record_s": 30}

A lab plan (`run <plan>`, see `formslab.sequence`) names its own rScripts and
ends when its Sequence does.
"""
import json

from formslab import rscripts

MODES = ("tvac",)
DEFAULT = {"rscripts": ["rLACO", "rSMTC08", "rPSU"], "record_s": 30}

# Everything the host can launch: the mode, and `plan` for a lab plan file.
LAB_MODES = (*MODES, "plan")


def spec(mode: str = "tvac") -> dict:
    """The mode's rScripts and CSV cadence, from the bench config."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (modes: {', '.join(MODES)})")
    from formslab.devices.hvc3500.profile import profile_path

    try:
        block = json.loads(profile_path().read_text(encoding="utf-8")).get(mode) or {}
    except (OSError, ValueError):
        block = {}
    return {**DEFAULT, **block}


def initialize(mode: str = "tvac"):
    """A `LabForms` handle with the mode's rScripts loaded and recording set."""
    s = spec(mode)
    forms = rscripts.LabForms(name=mode.upper())
    rscripts.load(forms, s["rscripts"])
    forms.record(value=s["record_s"], unit="seconds")
    return forms
