"""rScripts: lab routines that run inside the formsLabCLI host loop.

The runtime needs only the standard library. Every run hands its scripts a
`LabForms` handle (see `handle.py` for what it offers). An rScript owns its
instruments for the run: it applies CAST requests for them, publishes their
readings as variables, and leaves them safe in ``rShutdown``.

Writing one (rScripts/README.md has more; rLACO and rSMTC08 are short examples):

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
    rscripts.shutdown(forms)      # each script's rShutdown, if it has one
"""
from .control import RScriptControl
from .handle import LabForms, Scalar
from .loader import ENV, disabled, find, load, loaded, search_dirs, shutdown, tick
from .tasks import rTaskRegister, rTaskRunning, rTaskStart, rTaskStop, set_logger


def C2K(celsius: float) -> float:
    return celsius + 273.15


def K2C(kelvin: float) -> float:
    return kelvin - 273.15


__all__ = [
    "RScriptControl", "LabForms", "Scalar",
    "ENV", "disabled", "find", "load", "loaded", "search_dirs", "shutdown", "tick",
    "rTaskRegister", "rTaskRunning", "rTaskStart", "rTaskStop", "set_logger",
    "C2K", "K2C",
]
