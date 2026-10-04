"""Shared helpers for the GUI tests: recorder CSVs, a running server, a client."""
import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from formslab.gui import auth
from formslab.gui.server import make_server

USER, PASSWORD = "tester", "pw-for-tests"


def write_run(directory: Path, name="tvac", stamp="20261004T120000Z", part=None,
              header=("TC01 [K]", "chamberP [Torr]", "PSU1_CH1_ON"), rows=None) -> Path:
    """A recorder CSV: index,timestamp,<columns>. `rows` are lists of cell strings."""
    suffix = f"_{part}" if part is not None else ""
    path = Path(directory) / f"{name}_{stamp}{suffix}.csv"
    rows = rows if rows is not None else [
        ["2026-10-04T12:00:00.000Z", "293.15", "760", "0"],
        ["2026-10-04T12:00:10.000Z", "294.15", "700", "1"],
        ["2026-10-04T12:00:20.000Z", "nan", "650", "1"],
    ]
    lines = ["index,timestamp," + ",".join(header)]
    lines += [f"{i}," + ",".join(r) for i, r in enumerate(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@contextmanager
def running_server(monkeypatch):
    """A real server on a free port, with a known login (fast to hash)."""
    monkeypatch.setattr(auth, "ITERATIONS", 1000)
    monkeypatch.setattr(auth, "FAIL_DELAY_S", 0.01)
    auth.set_login(USER, PASSWORD)
    server = make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


class Client:
    def __init__(self, server):
        self.port = server.server_address[1]
        self.cookie = None

    def call(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = dict(headers or {})
        if method == "POST":
            h.setdefault("X-Requested-With", "formslab")
            h.setdefault("Content-Type", "application/json")
        if self.cookie:
            h["Cookie"] = self.cookie
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp, payload

    def json(self, method, path, body=None, **kw):
        resp, payload = self.call(method, path, body, **kw)
        return resp.status, (json.loads(payload) if payload and resp.getheader("Content-Type", "").startswith("application/json") else payload)

    def login(self):
        resp, _ = self.call("POST", "/api/login", {"user": USER, "password": PASSWORD})
        assert resp.status == 200
        self.cookie = resp.getheader("Set-Cookie").split(";")[0]
        return self
