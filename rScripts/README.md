# rScripts — lab routines

An rScript is a file `<name>.py` with a module-level `def rScript(forms):`. The
formsLabCLI host (`formslab.host.sequence`) loads a list of them by name and
calls each one every loop. They reach the bench through `formslab.devices.*`
and talk to the console through the CAST channel
(`formslab.console.cast.castutils`).

The runtime is formsLabCLI's own, `formslab.rscripts`, and needs no FORMS:

    from formslab.rscripts import RScriptControl

    name = "rMine"

    def rScript(forms):
        if RScriptControl(forms, name).tick(seconds=5):   # True = not yet
            return
        forms.log("five seconds passed", component=name)

Gates count **wall-clock** seconds: `tick(seconds=N)` runs now and then every
N s, `hold(seconds=N)` waits N s, runs once, and re-arms. Hardware is never paced
by simulated time.

## The `forms` handle

On a lab run (`run laco`) `forms` is a `LabForms`: `log`, `types.scalar` /
`get_variable`, `record` (CSV of every variable to `outputs/<run>_<UTC>.csv`),
`time.clock()`, `transition.request()`. On a FORMS mission it is the real FORMS
instance, which offers all of that and more. Stay within that list and a
script runs under both.

A script that needs FORMS itself (satellite state, frames) sets
`requires = ("forms",)`; lab runs skip it with a log line. `enable = False` at
module level keeps a script from loading at all.

## Where scripts are found

First match wins: `$FORMSLAB_RSCRIPTS_DIR` (`os.pathsep`-separated), then
`<cwd>/rScripts`, then this directory in a formsLabCLI checkout. No symlink
into a FORMS workspace is needed any more.

## LACO chamber (HVC-3500)

`rLACO.py` is the default control-and-monitor routine for the UIUC LACO
thermal-vacuum chamber, through its HVC-3500 controller over Ethernet
(`formslab.devices.hvc3500`). Launch with `run laco` from the console; it needs
no FORMS. Bench endpoint, units and zone/sensor numbering come from
`$FORMSLAB_CONFIG_DIR/tvac_bench.json` (seeded from the packaged default).
Status is published to the CAST block `hvc`; requests are written to the same
block, e.g. `{"platen": 25.0}`, `{"platen_control": true}`, `{"vacuum": 1e-3}`,
`{"start": true}`. Raw valve/pump toggles are deliberately not exposed - the PLC
sequences those.

## Legacy scripts

The Rigol/RTD bench scripts (`rTVAC`, `rTVAC_EQCTRL`, `rPSU`, `rFSS`, ...) and
`rTemplate` still import FORMS' old runtime (`forms.utils.rScripts`) and run only
in the FORMS modes (`run tvac`, missions). They are due to be replaced by LACO
routines (`rLACO_EQCTRL`, ...) on the runtime above.
