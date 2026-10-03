"""rScripts: lab routines that run inside the formsLabCLI host loop.

The runtime needs only the standard library. Every run hands its scripts a
`Run` (see `run.py` for what it offers). An rScript owns its
instruments for the run: it applies CAST requests for them, publishes their
readings as variables, and leaves them safe in ``rShutdown``.

Writing one (rScripts/README.md has more; rLACO and rSMTC08 are short examples):

    from formslab.rscripts import RScriptControl

    name = "rMine"

    def rScript(run):
        r = RScriptControl(run, name)
        if r.tick(seconds=5):
            return
        run.log("five seconds passed", component=name)

The host side:

    rscripts.load(run, ["rLACO"])
    while running:
        rscripts.tick(run)
    rscripts.shutdown(run)      # each script's rShutdown, if it has one
"""
from .control import RScriptControl
from .run import Run, Scalar
from .loader import ENV, disabled, find, load, loaded, search_dirs, shutdown, tick
from .tasks import rTaskRegister, rTaskRunning, rTaskStart, rTaskStop, set_logger


def C2K(celsius: float) -> float:
    return celsius + 273.15


def K2C(kelvin: float) -> float:
    return kelvin - 273.15


__all__ = [
    "RScriptControl", "Run", "Scalar",
    "ENV", "disabled", "find", "load", "loaded", "search_dirs", "shutdown", "tick",
    "rTaskRegister", "rTaskRunning", "rTaskStart", "rTaskStop", "set_logger",
    "C2K", "K2C",
]
