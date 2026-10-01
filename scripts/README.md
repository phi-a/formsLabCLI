Tools that are not part of the `formslab` package.

**Launchers.** `labcli.cmd` (Windows) and `labcli.sh` (Linux, macOS) start the
console from the checkout's venv, for a desktop shortcut or a double-click.

**Analysis.** Each runs top to bottom and opens a plot window, so importing
one would do work rather than define anything. They need the plotting extra:
`pip install -e ".[analysis]"`.

    python scripts/plot_temp.py samples/TVAC-test0.json
    python scripts/ramp_time.py samples/TVAC-test0.json

`fz2fits.py` converts sLTA `.fz` frames to `.fits`; it needs `astropy`.

**sLTA sequencer tables.** `slta_sequencer_*_v1.xml` are the CCD clock-state
tables deployed on the sLTA host. Nothing here loads them; they are kept as the
reference copy.
