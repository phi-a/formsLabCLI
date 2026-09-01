Bench analysis scripts. Not part of the `formslab` package: each runs top to
bottom and opens a plot window, so importing one would do work rather than
define anything.

    python scripts/plot_temp.py samples/TVAC-test0.json
    python scripts/ramp_time.py samples/TVAC-test0.json

Both need the plotting extra: `pip install -e ".[analysis]"`.
