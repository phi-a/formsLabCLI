"""rScripts: lab routines that run inside the formsLabCLI host loop.

The runtime is formsLabCLI's, not FORMS'. It needs only the standard library,
and a run without FORMS gets a `LabForms` handle. With FORMS installed, a test
can hand the same scripts a real FORMS instance instead; they cannot tell the
difference as long as they stay within the handle contract in `handle.py`.

Writing one (see ``rScripts/rTemplate``):

    from formslab.rscripts import RScriptControl

    name = "rMine"

    def rScript(forms):
        r = RScriptControl(forms, name)
        if r.tick(seconds=5):
            return
        forms.log("five seconds passed", component=name)

The host side:

    rscripts.load(forms, ["rLACO"])
    while running:
        rscripts.tick(forms)
"""
from .control import RScriptControl
from .handle import LabForms, Scalar
from .loader import ENV, disabled, find, load, loaded, search_dirs, tick
from .tasks import rTaskRegister, rTaskRunning, rTaskStart, rTaskStop, set_logger


def C2K(celsius: float) -> float:
    return celsius + 273.15


def K2C(kelvin: float) -> float:
    return kelvin - 273.15


__all__ = [
    "RScriptControl", "LabForms", "Scalar",
    "ENV", "disabled", "find", "load", "loaded", "search_dirs", "tick",
    "rTaskRegister", "rTaskRunning", "rTaskStart", "rTaskStop", "set_logger",
    "C2K", "K2C",
]
