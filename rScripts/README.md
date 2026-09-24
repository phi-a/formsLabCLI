# rScripts — hardware routines

These run inside a FORMS mission and reach the bench: PSU channels, the
cryocooler board, TVAC shroud heaters, the sLTA imaging chain.

They are **workspace content, not package code**. FORMS loads them by name from
`<workspace>/rScripts` (`forms.core.paths.rscripts_dir()`), so they are never
imported from `formslab` — copy or symlink this directory into your FORMS
workspace:

    ln -s "$(pwd)/rScripts" /path/to/workspace/rScripts     # POSIX
    cmd /c mklink /D  C:\path\to\workspace\rScripts  %CD%\rScripts   # Windows

Each script imports two things: `formslab.devices.*` for the instruments and
`formslab.console.cast.castutils` for the CAST request/status channel the
console reads. Both need `formslab` installed; running a mission needs the
`[forms]` extra as well.

`rTemplate` is the starting point for a new routine.

## LACO chamber (HVC-3500)

`rTVAC_LACO.py` drives the LACO thermal-vacuum chamber through its HVC-3500
controller over Ethernet (`formslab.devices.hvc3500`). Launch with
`run laco` from the console; it loads only this routine, not the Rigol/RTD
heater loop in `rTVAC.py`. Bench endpoint, units and zone/sensor numbering
come from `$FORMSLAB_CONFIG_DIR/tvac_bench.json` (seeded from the packaged
default). Status is published to the CAST block `hvc`; requests are written to
the same block, e.g. `{"platen": 25.0}`, `{"platen_control": true}`,
`{"vacuum": 1e-3}`, `{"start": true}`. Raw valve/pump toggles are deliberately
not exposed - the PLC sequences those.
