"""formslab - the lab console for thermal-vacuum testing.

Control bench instruments and chamber temperatures from the terminal, and run
hardware test sequences against them: Rigol programmable supplies, thermocouple
and RTD readers, TVAC shroud heaters, the LACO chamber's HVC-3500 controller, a
cryocooler control board behind a Pi Pico I2C bridge, a Digital Loggers power
switch, and the sLTA imaging chain.

* `formslab.console`  - the tab-switching REPL and its command tables.
* `formslab.devices`  - the drivers, one module per instrument.
* `formslab.rscripts` - the runtime for rScripts, the routines that own the
  instruments while a run is going.
* `formslab.sequence` - lab plans: hardware test sequences.
* `formslab.host`     - the process that runs rScripts and plans.

Run the console with `labcli` (see `formslab.app`).
"""

__version__ = "0.1.0"
