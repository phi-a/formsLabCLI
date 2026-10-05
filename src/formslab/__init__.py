"""formslab - the lab console for thermal-vacuum testing.

Control bench instruments and chamber temperatures from the terminal or a web
page, and run hardware test sequences against them: the LACO chamber's HVC-3500
controller (its temperature zones, valves and pumps), Rigol programmable
supplies, SMTC08 thermocouple readers, a cryocooler control board behind a Pi
Pico I2C bridge, a Digital Loggers power switch, and the sLTA imaging chain.

* `formslab.console`  - the tab-switching REPL and its command tables.
* `formslab.devices`  - the drivers, one module per instrument.
* `formslab.rscripts` - the runtime for rScripts, the routines that own the
  instruments while a run is going.
* `formslab.sequence` - lab plans: hardware test sequences.
* `formslab.host`     - the process that runs rScripts and plans.
* `formslab.gui`      - the web GUI (`labcli gui`), over the same files.

Run the console with `labcli` (see `formslab.app`).
"""

__version__ = "0.1.0"
