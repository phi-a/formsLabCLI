"""Try the web GUI without touching the bench.

    python scripts/gui_demo.py            # then open http://localhost:8088/

A simulated HVC-3500 chamber, a throwaway config folder and a login (user
`demo`, password `demo`) are made for the session and deleted when you press
Ctrl+C. The page says DEMO on its login screen and in an orange bar, and it uses
port 8088, so it is not mistaken for `labcli gui` (8080). Your own ~/.formslab, your login, your plans and your instruments are
not used. Start a plan (`tvac` holds until you end it; `pump_demo` runs and
finishes), send `hvc vent open`, watch the Chamber tab, build a plan on the
Plans tab.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Not 8080, which is where `labcli gui` listens: the two must not be confused.
    ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="formslab-gui-demo-"))
    for sub in ("config", "out", "plans"):
        (root / sub).mkdir()
    # Set before formslab reads them: everything below uses the throwaway folder.
    os.environ.update(FORMSLAB_CONFIG_DIR=str(root / "config"), FORMSLAB_OUTPUT_DIR=str(root / "out"),
                      FORMSLAB_PLANS_DIR=str(root / "plans"), FORMSLAB_GUI_DEMO="1")
    os.chdir(root)

    from formslab import config
    from formslab.devices.hvc3500.simulator import Simulator
    from formslab.gui import auth
    from formslab.gui.server import make_server
    from formslab.state import ensure_runtime_files

    (root / "plans" / "tvac.plan").write_text(
        "# Manual operation of the simulated chamber: use the command box, then End run.\n"
        "load rLACO\nrecord every 2 s\nhold until end\n", encoding="utf-8")
    (root / "plans" / "pump_demo.plan").write_text(
        "# Pump on, wait, rough valve, stop: runs by itself.\n"
        "load rLACO\nrecord every 2 s\nhvc pump on\nhold 15 s\nhvc rough open\nhold 10 s\nhvc stop\nlog done\n",
        encoding="utf-8")
    auth.set_login("demo", "demo")
    ensure_runtime_files()

    try:
        with Simulator() as sim:
            profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
            profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0)
            (root / "config" / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
            server = make_server("127.0.0.1", args.port)
            print(f"GUI demo on http://localhost:{args.port}/  -  user: demo   password: demo", flush=True)
            print("A simulated chamber; nothing real is touched. Ctrl+C stops it and deletes its files.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    finally:
        os.chdir(Path(__file__).resolve().parent)
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
