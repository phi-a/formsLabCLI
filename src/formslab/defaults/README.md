Shipped defaults. Read-only: these are replaced on upgrade.

`usbmap.json` is copied into the config directory (`$FORMSLAB_CONFIG_DIR`, else
`~/.formslab`) the first time a driver asks for it. Edit the copy there for your
bench -- edits here are overwritten by the next install. `labcli hardware`
shows each entry found or missing; `formslab/hardware.py` says how an entry
names its device.
