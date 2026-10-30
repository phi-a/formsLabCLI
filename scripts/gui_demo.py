"""Try formsLAB without touching the bench.

    python scripts/gui_demo.py            # then open http://localhost:8088/

A simulated HVC-3500 chamber, a throwaway config folder and a login (user
`demo`, password `demo`) are made for the session and deleted when you press
Ctrl+C (formslab.demo). The page says DEMO on its login screen and in an orange
bar, and it uses port 8088, so it is not mistaken for `labcli gui` (8080). Your
own ~/.formslab, your login, your plans and your instruments are not used.
Start a plan (`tvac` holds until you end it; `pump_demo` runs and finishes),
send `hvc vent open`, watch the chamber on Control, build a plan on the Plans
tab. The variables it prints let a second terminal watch the same bench with
`labcli`.
"""
from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Not 8080, which is where `labcli gui` listens: the two must not be confused.
    ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()

    from formslab.demo import bench

    with bench() as b:
        from formslab.gui.server import make_server
        from formslab.host.monitor import start_if_free

        server = make_server("127.0.0.1", args.port)
        monitor = start_if_free()
        print(f"GUI demo on http://localhost:{args.port}/  -  user: demo   password: demo", flush=True)
        print("A simulated chamber; nothing real is touched. Ctrl+C stops it and deletes its files.", flush=True)
        print("To watch the same bench from another terminal, set these, then run `labcli`:", flush=True)
        for key, value in b.env.items():
            print(f"  {key}={value}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            if monitor is not None:
                monitor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
