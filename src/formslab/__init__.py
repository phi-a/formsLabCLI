"""formslab - the FORMS lab console.

A hardware CLI for benchtop instruments: Rigol programmable supplies, a
cryocooler control board reached over a Pi Pico I2C bridge, RTD and
thermocouple readers, TVAC shroud heaters, a Digital Loggers power switch, and
the sLTA imaging chain.

Three things live here:

* `formslab.console` - the tab-switching REPL and its command tables.
* `formslab.devices` - the drivers, one module per instrument.
* `formslab.bridge`  - the single seam onto FORMS. Everything else in this
  package is standalone; importing `bridge` is what asks for the optional
  `[forms]` extra.

Run it with `labcli` (see `formslab.app`).
"""

__version__ = "0.1.0"
