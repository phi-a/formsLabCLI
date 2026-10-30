"""A bench to try formsLAB and labcli on, without touching anything real.

    with bench() as b:        # a simulated chamber and a throwaway folder

`bench()` makes a temporary config, output and plans folder and points the
environment at them (FORMSLAB_CONFIG_DIR, FORMSLAB_OUTPUT_DIR,
FORMSLAB_PLANS_DIR, and FORMSLAB_GUI_DEMO so the GUI says DEMO); writes the
`tvac` and `pump_demo` plans, the login demo/demo and a bench profile aimed at
a simulated HVC-3500; and deletes it all on exit. scripts/gui_demo.py serves
the GUI on one; `labcli --demo` opens the watch screen on one. `b.env` is what
a second terminal sets to join the same bench.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

PLANS = {
    "tvac.plan": ("# Manual operation of the simulated chamber: use the command line, then End.\n"
                  "load rLACO\nrecord every 2 s\nhold until end\n"),
    "pump_demo.plan": ("# Pump on, wait, rough valve, stop: runs by itself.\n"
                       "load rLACO\nrecord every 2 s\nhvc pump on\nhold 15 s\nhvc rough open\n"
                       "hold 10 s\nhvc stop\nlog done\n"),
}
ENV = ("FORMSLAB_CONFIG_DIR", "FORMSLAB_OUTPUT_DIR", "FORMSLAB_PLANS_DIR", "FORMSLAB_GUI_DEMO")


@dataclass
class Bench:
    root: Path
    simulator: object
    env: dict


@contextlib.contextmanager
def bench(login: tuple[str, str] = ("demo", "demo")):
    root = Path(tempfile.mkdtemp(prefix="formslab-demo-"))
    for sub in ("config", "out", "plans"):
        (root / sub).mkdir()
    saved = {k: os.environ.get(k) for k in ENV}
    env = {"FORMSLAB_CONFIG_DIR": str(root / "config"), "FORMSLAB_OUTPUT_DIR": str(root / "out"),
           "FORMSLAB_PLANS_DIR": str(root / "plans")}
    os.environ.update(env, FORMSLAB_GUI_DEMO="1")
    cwd = os.getcwd()
    os.chdir(root)
    try:
        from formslab import config
        from formslab.devices.hvc3500.simulator import Simulator
        from formslab.gui import auth
        from formslab.state import ensure_runtime_files

        for name, text in PLANS.items():
            (root / "plans" / name).write_text(text, encoding="utf-8")
        auth.set_login(*login)
        ensure_runtime_files()
        with Simulator() as sim:
            profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
            profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
            (root / "config" / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
            yield Bench(root=root, simulator=sim, env=env)
    finally:
        os.chdir(cwd)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)
