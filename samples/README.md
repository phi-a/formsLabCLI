Recorded bench data, for trying the analysis scripts without a TVAC run.

`TVAC-test0.json` is a shroud-heater log captured during a thermal-vacuum test
(the format `formslab.devices.shroud` writes). It is sample data, not package
data — 2 MB has no business inside the wheel — so it lives here rather than
beside the driver:

    python scripts/plot_temp.py samples/TVAC-test0.json
    python scripts/ramp_time.py samples/TVAC-test0.json
