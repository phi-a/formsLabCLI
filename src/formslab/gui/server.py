"""The GUI's HTTP server: `labcli gui`.

Standard library only. Static files are served as they are; everything under
/api needs a session cookie from /api/login. Safeguards, all small:

* binds 127.0.0.1 unless --listen, and then only answers a Host header it
  expects (defeats DNS rebinding of a localhost server);
* a POST must carry ``X-Requested-With: formslab`` and the cookie is
  SameSite=Strict (a page on another site cannot act as the user);
* request bodies are capped, every response is ``Cache-Control: no-store``.

Plain HTTP: the lab is locked, and from another machine the intended way in is
an SSH tunnel (``ssh -L 8080:localhost:8080 <bench>``).
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import re
import os
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from formslab import config
from formslab.gui import api, auth

STATIC = Path(__file__).parent / "static"
COOKIE = "fl_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "formslab"
MAX_BODY = 64 * 1024
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}
_NAME = re.compile(r"^[\w.-]+$")
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def audit(user: str, action: str) -> None:
    """One line in ``<config>/.run/gui.log`` for each login and (later) action."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with (config.run_dir() / "gui.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {user} {action}\n")
    except OSError:
        pass


def _clean(value):
    """JSON has no NaN or infinity: a failed reading becomes null."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "formslab-gui"

    def log_message(self, *args) -> None:        # no per-request noise on the terminal
        pass

    # -- plumbing -----------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj, headers: dict | None = None) -> None:
        self._send(code, json.dumps(_clean(obj)).encode("utf-8"), "application/json", headers)

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def _host_ok(self) -> bool:
        allowed = self.server.allowed_hosts
        return allowed is None or (self.headers.get("Host") or "").lower() in allowed

    def _token(self) -> str | None:
        morsel = SimpleCookie(self.headers.get("Cookie") or "").get(COOKIE)
        return morsel.value if morsel else None

    def _user(self) -> str | None:
        return self.server.sessions.user(self._token())

    # -- GET ----------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query, keep_blank_values=True)
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/info":                       # no login: the login page says which server this is
            return self._json(200, {"demo": self.server.demo})
        if not path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        user = self._user()
        if user is None:
            return self._error(HTTPStatus.UNAUTHORIZED, "login required")
        try:
            self._api_get(path, query, user)
        except api.ApiError as e:
            self._error(e.code, str(e))
        except Exception as e:                    # a bad request must not kill the server
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def _api_get(self, path: str, query: dict, user: str) -> None:
        def arg(name, default=None):
            return query.get(name, [default])[0]

        if path == "/api/me":
            return self._json(200, {"user": user, "demo": self.server.demo})
        if path == "/api/status":
            return self._json(200, api.status(int(arg("log", 40))))
        if path == "/api/plans":
            return self._json(200, {"plans": api.list_plans()})
        if path.startswith("/api/plans/"):
            name = path[len("/api/plans/"):]
            if not _NAME.match(name):
                return self._error(HTTPStatus.BAD_REQUEST, "bad plan name")
            return self._json(200, api.plan_read(name))
        if path == "/api/rscripts":
            return self._json(200, {"rscripts": api.rscripts_available()})
        if path == "/api/complete":
            return self._json(200, {"options": api.complete((arg("words") or "").split())})
        if path == "/api/runs":
            return self._json(200, api.list_runs())
        if path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/"):]
            if not _NAME.match(run_id):
                return self._error(HTTPStatus.BAD_REQUEST, "bad run id")
            names = [v for v in (arg("vars") or "").split(",") if v] if arg("vars") is not None else None
            result = api.run_series(run_id, names, int(arg("max", 2000)))
            if result is None:
                return self._error(HTTPStatus.NOT_FOUND, "no such run")
            return self._json(200, result)
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def _static(self, name: str) -> None:
        file = STATIC / name
        if not _NAME.match(name) or not file.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        self._send(200, file.read_bytes(), _TYPES.get(file.suffix, "application/octet-stream"))

    # -- POST ---------------------------------------------------------------------

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
        if self.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return self._error(HTTPStatus.FORBIDDEN, f"missing {CSRF_HEADER} header")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
        if length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("a JSON object is expected")
        except ValueError as e:
            return self._error(HTTPStatus.BAD_REQUEST, f"bad JSON: {e}")
        path = urlsplit(self.path).path
        if path == "/api/login":
            return self._login(body)
        user = self._user()
        if user is None:
            return self._error(HTTPStatus.UNAUTHORIZED, "login required")
        if path == "/api/logout":
            self.server.sessions.drop(self._token())
            audit(user, "logout")
            return self._json(200, {"ok": True}, {"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"})
        try:
            self._control(path, body, user)
        except api.ApiError as e:
            audit(user, f"refused {path[len('/api/'):]}: {e}")
            self._error(e.code, str(e))
        except Exception as e:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(e).__name__}: {e}")

    def _control(self, path: str, body: dict, user: str) -> None:
        """The actions that change something, noted in gui.log, and the editor's
        read-only queries (check, line options), which are POSTs only because they
        carry text."""
        if path == "/api/run":
            name = str(body.get("plan", ""))
            result = api.start_run(name)
            audit(user, f"run {name}")
        elif path == "/api/end":
            result = api.end_run()
            audit(user, "end")
        elif path in ("/api/pause", "/api/resume"):
            result = api.ctrl(path[len("/api/"):])
            audit(user, path[len("/api/"):])
        elif path == "/api/plan/check":
            return self._json(200, api.plan_check(str(body.get("text", ""))))
        elif path == "/api/describe":
            words, text, line = body.get("words", []), body.get("text"), body.get("line")
            if not (isinstance(words, list) and all(isinstance(w, str) for w in words)) \
                    or (text is not None and not isinstance(text, str)) \
                    or (line is not None and not isinstance(line, int)):
                return self._error(HTTPStatus.BAD_REQUEST, "words is a list of strings, text a string, line a number")
            return self._json(200, api.describe(words, text, line))
        elif path == "/api/plan/line":
            scripts, words = body.get("scripts", []), body.get("words", [])
            if not all(isinstance(w, str) for w in [*scripts, *words]):
                return self._error(HTTPStatus.BAD_REQUEST, "scripts and words are lists of strings")
            return self._json(200, api.plan_line(scripts, words))
        elif path == "/api/plan/needs":
            return self._json(200, {"rscripts": api.plan_needs(str(body.get("text", "")))})
        elif path == "/api/plan/tokens":
            return self._json(200, {"lines": api.plan_tokens(str(body.get("text", "")))})
        elif path == "/api/plan/delete":
            name = str(body.get("name", ""))
            result = api.plan_delete(name, body.get("base_hash"))
            audit(user, f"delete plan {name} (moved to {result['trash']})")
        elif path == "/api/plan/save":
            name = str(body.get("name", ""))
            result = api.plan_save(name, str(body.get("text", "")), body.get("base_hash"),
                                   bool(body.get("as_new")))
            audit(user, f"save plan {name}" + (" (new)" if body.get("as_new") else ""))
        elif path == "/api/cast":
            line = str(body.get("line", ""))
            result = api.send_command(line)
            audit(user, f"cast {line.strip()}" + ("" if result["ok"] else f" ({result['text']})"))
        else:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        self._json(200, result)

    def _login(self, body: dict) -> None:
        user, password = str(body.get("user", "")), str(body.get("password", ""))
        if not auth.check(user, password):
            audit(user or "?", "login refused")
            return self._error(HTTPStatus.UNAUTHORIZED, "wrong user name or password")
        token = self.server.sessions.new(user)
        audit(user, "login")
        cookie = f"{COOKIE}={token}; Max-Age={auth.SESSION_S}; Path=/; HttpOnly; SameSite=Strict"
        self._json(200, {"user": user}, {"Set-Cookie": cookie})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows lets a second server take over a port that is in use.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, sessions: auth.Sessions | None = None) -> None:
        super().__init__(address, Handler)
        self.sessions = sessions or auth.Sessions()
        # scripts/gui_demo.py sets this: a demo must never be mistaken for the bench's own GUI
        self.demo = os.environ.get("FORMSLAB_GUI_DEMO") == "1"
        host, port = self.server_address[:2]
        self.allowed_hosts = (
            {f"{h}:{port}" for h in ("localhost", "127.0.0.1", "[::1]")}
            if host in _LOOPBACK else None)        # --listen: any name may reach it


def make_server(host: str = "127.0.0.1", port: int = 8080) -> Server:
    return Server((host, port))


# --- labcli gui ---------------------------------------------------------------------

def _set_login() -> int:
    user = input("GUI user name: ").strip()
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Again: "):
        print("✗ the passwords differ; nothing changed", file=sys.stderr)
        return 1
    try:
        auth.set_login(user, password)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✔ login for {user!r} saved in {config.state_path(auth.FILE)}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="labcli gui", description="The web GUI, on this machine.")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--listen", action="store_true",
                    help="listen on every interface, not just this machine (the login is plain HTTP)")
    ap.add_argument("--set-login", action="store_true", help="set the GUI user name and password")
    args = ap.parse_args(argv)
    if args.set_login:
        return _set_login()
    if not auth.configured():
        print("✗ no GUI login is set; run `labcli gui --set-login` first", file=sys.stderr)
        return 1
    from formslab.state import ensure_runtime_files
    ensure_runtime_files()
    host = "0.0.0.0" if args.listen else "127.0.0.1"
    try:
        server = make_server(host, args.port)
    except OSError as e:
        print(f"✗ cannot listen on {host}:{args.port}: {e}", file=sys.stderr)
        return 1
    print(f"formsLabCLI GUI on http://{'localhost' if not args.listen else host}:{args.port}/   (Ctrl+C stops)")
    if args.listen:
        print("  listening on every interface; the login is sent as plain HTTP")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
