"""The GUI's read API (freshness, status, runs) and its server (login, headers,
limits), then the rule that matters most: it never opens an instrument."""
import ast
import json
import socket
import subprocess
import time
from pathlib import Path
from unittest import mock

import pytest

from formslab import config
from formslab.console.cast import castutils
from formslab.gui import api
from formslab.gui.server import CSRF_HEADER, MAX_BODY

from gui_helpers import Client, running_server, write_run

HOST = {"pid": 4321, "plan": "tvac", "started": "2026-10-04T12:00:00+00:00", "output": "/x"}
NOW = 1_000_000.0


# --- freshness: when is a value live? ----------------------------------------------------

def block(age, status=None):
    return {"timestamp": NOW - age, "status": status or {}}


def test_nothing_is_live_without_a_run():
    f = api.freshness("tc", block(0.5), None, NOW)
    assert f["live"] is False and "no run" in f["reason"]


def test_a_fresh_block_of_a_running_host_is_live():
    assert api.freshness("tc", block(1.0), HOST, NOW)["live"] is True


def test_a_block_older_than_three_cadences_is_not_live():
    f = api.freshness("tc", block(30), HOST, NOW)                  # tc publishes every 2 s
    assert f["live"] is False and "not updated for 30 s" in f["reason"]
    assert api.freshness("hvc", block(30), HOST, NOW)["live"] is False      # ~6 s cadence, limit 20 s
    assert api.freshness("hvc", block(15, {"connected": True}), HOST, NOW)["live"] is True


def test_the_chamber_must_say_it_is_connected():
    f = api.freshness("hvc", block(1, {"connected": False, "error": "timed out"}), HOST, NOW)
    assert f == {"live": False, "age_s": 1.0, "reason": "timed out"}
    assert api.freshness("hvc", block(1, {}), HOST, NOW)["reason"] == "chamber not connected"


def test_a_block_written_to_by_a_command_alone_gets_no_credit_without_its_owner():
    """A command bumps the block's timestamp; with no owner publishing, it ages out."""
    assert api.freshness("psu1", block(10), HOST, NOW)["live"] is False


# --- status ---------------------------------------------------------------------------------

def test_status_with_no_host_and_a_cast_file():
    castutils.GenerateCleanCast()
    castutils.UpdateStatus("tc", {"TC01 C": 20.5})
    s = api.status()
    assert s["host"] is None and s["blocks"]["tc"]["status"] == {"TC01 C": 20.5}
    assert s["blocks"]["tc"]["live"] is False and s["cast_unreadable"] is False
    assert {"hvc", "tc", "psu1", "psu2", "cryo", "slta"} <= set(s["blocks"])


def test_status_with_a_running_host(monkeypatch):
    castutils.GenerateCleanCast()
    castutils.UpdateStatus("tc", {"TC01 C": 20.5})
    castutils.WriteCommand({"stop_pumping": True}, "hvc")
    monkeypatch.setattr(api, "host", lambda: HOST)
    s = api.status()
    assert s["host"] == HOST and s["last_run"] is None
    assert s["blocks"]["tc"]["live"] is True
    assert s["blocks"]["hvc"]["pending"] is True and s["blocks"]["tc"]["pending"] is False


def test_status_survives_an_unreadable_cast_file():
    castutils.GenerateCleanCast()
    from formslab.state import cast_state_path
    cast_state_path().write_text("{ torn", encoding="utf-8")
    s = api.status()
    assert s["cast_unreadable"] is True and s["blocks"] == {}
    assert cast_state_path().read_text(encoding="utf-8") == "{ torn"          # a read, never a repair


def test_status_reads_never_write_the_cast_file():
    castutils.GenerateCleanCast()
    from formslab.state import cast_state_path
    before = cast_state_path().read_bytes()
    api.status()
    api.status()
    assert cast_state_path().read_bytes() == before


def test_the_log_tail_is_the_end_of_the_host_log():
    from formslab.console.log.logcli import log_path
    assert api.log_tail() == []
    log_path().write_text("\n".join(f"line {i}" for i in range(300)) + "\n", encoding="utf-8")
    assert api.log_tail(3) == ["line 297", "line 298", "line 299"]
    assert len(api.log_tail(1000)) == 300
    log_path().write_bytes(b"x" * 200_000 + b"\nlast\n")
    assert api.log_tail(1)[-1] == "last"


def test_runs_are_found_in_the_output_folder_and_in_the_hosts_folder(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_run(config.output_dir(), name="here")
    write_run(elsewhere, name="there")
    monkeypatch.setattr(api, "read_lock", lambda: {**HOST, "output": str(elsewhere)})
    monkeypatch.setattr(api, "host", lambda: None)
    assert {r["name"] for r in api.list_runs()["runs"]} == {"here", "there"}
    assert api.run_series("there_20261004T120000Z", ["TC01"])["series"]["TC01"][0] == 293.15
    assert api.run_series("nope_20261004T120000Z", None) is None


# --- the server ------------------------------------------------------------------------------

def test_the_page_and_its_assets_are_served_without_a_login(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server)
        for path, kind in (("/", "text/html"), ("/static/app.js", "text/javascript"),
                           ("/static/chart.js", "text/javascript"), ("/static/style.css", "text/css")):
            resp, body = c.call("GET", path)
            assert resp.status == 200 and resp.getheader("Content-Type").startswith(kind), path
            assert resp.getheader("Cache-Control") == "no-store" and body
        assert c.call("GET", "/static/nope.js")[0].status == 404
        assert c.call("GET", "/static/..%2Fserver.py")[0].status == 404
        assert c.call("GET", "/elsewhere")[0].status == 404


def test_the_api_needs_a_login(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server)
        for path in ("/api/status", "/api/runs", "/api/me", "/api/runs/tvac_20261004T120000Z"):
            assert c.json("GET", path)[0] == 401, path
        assert c.json("POST", "/api/logout", {})[0] == 401
        c.cookie = "fl_session=not-a-session"
        assert c.json("GET", "/api/status")[0] == 401


def test_logging_in_out_and_a_wrong_password(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server)
        code, body = c.json("POST", "/api/login", {"user": "tester", "password": "wrong"})
        assert code == 401 and "wrong user name" in body["error"]
        assert c.call("POST", "/api/login", {"user": "tester", "password": "wrong"})[0].getheader("Set-Cookie") is None

        c.login()
        resp, _ = c.call("POST", "/api/login", {"user": "tester", "password": "pw-for-tests"})
        cookie = resp.getheader("Set-Cookie")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert c.json("GET", "/api/me") == (200, {"user": "tester", "demo": False})
        assert c.json("GET", "/api/status")[0] == 200
        assert c.json("POST", "/api/logout", {})[0] == 200
        assert c.json("GET", "/api/status")[0] == 401                    # the session is gone

        log = (config.run_dir() / "gui.log").read_text(encoding="utf-8")
        assert "tester login refused" in log and "tester login" in log and "tester logout" in log


def test_a_post_needs_the_request_header_and_a_sane_body(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server).login()
        resp, _ = c.call("POST", "/api/logout", {}, headers={CSRF_HEADER: "something else"})
        assert resp.status == 403
        resp, _ = c.call("POST", "/api/login", raw=b"{ not json")
        assert resp.status == 400
        resp, _ = c.call("POST", "/api/login", raw=b"[1, 2]")
        assert resp.status == 400
        resp, _ = c.call("POST", "/api/login", raw=b"x" * (MAX_BODY + 1))
        assert resp.status == 413
        assert c.json("GET", "/api/me")[0] == 200                         # none of that logged us out


def test_another_site_cannot_ride_the_session(monkeypatch):
    """No header, no action -- a form on another site cannot set it."""
    with running_server(monkeypatch) as server:
        c = Client(server).login()
        resp, _ = c.call("POST", "/api/logout", {}, headers={"Content-Type": "text/plain", CSRF_HEADER: ""})
        assert resp.status == 403 and c.json("GET", "/api/me")[0] == 200


def test_an_unexpected_host_header_is_refused(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server)
        assert c.call("GET", "/", headers={"Host": "evil.example"})[0].status == 403
        assert c.call("GET", "/", headers={"Host": f"localhost:{c.port}"})[0].status == 200
        assert c.call("GET", "/", headers={"Host": f"127.0.0.1:{c.port}"})[0].status == 200


def test_runs_and_series_over_http_with_gaps_as_null(monkeypatch):
    with running_server(monkeypatch) as server:
        write_run(config.output_dir())
        c = Client(server).login()
        code, listing = c.json("GET", "/api/runs")
        assert code == 200 and listing["runs"][0]["id"] == "tvac_20261004T120000Z"
        code, body = c.json("GET", "/api/runs/tvac_20261004T120000Z?vars=TC01,chamberP&max=500")
        assert code == 200 and body["series"]["TC01"] == [293.15, 294.15, None]    # nan is null, valid JSON
        assert body["units"]["chamberP"] == "Torr"
        assert c.json("GET", "/api/runs/tvac_20261004T120000Z?vars=")[1]["series"] == {}
        assert c.json("GET", "/api/runs/nope_20261004T120000Z")[0] == 404
        assert c.json("GET", "/api/runs/..%2f..%2fcastfile")[0] == 400


def test_status_over_http(monkeypatch):
    with running_server(monkeypatch) as server:
        castutils.GenerateCleanCast()
        castutils.UpdateStatus("tc", {"TC01 C": float("nan")})
        c = Client(server).login()
        code, body = c.json("GET", "/api/status?log=5")
        assert code == 200 and body["blocks"]["tc"]["status"] == {"TC01 C": None}
        assert body["host"] is None and body["log"] == []


# --- it never opens an instrument -----------------------------------------------------------------

GUI = Path(api.__file__).parent
FORBIDDEN = ("serial", "pyvisa", "pymodbus", "usb", "formslab.devices", "mpremote")


def test_no_gui_module_imports_a_driver():
    offenders = []
    for path in GUI.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            offenders += [(path.name, n) for n in names if n.split(".")[0] in FORBIDDEN or n.startswith("formslab.devices")]
    assert offenders == []


def test_the_page_never_puts_server_text_in_innerhtml():
    for name in ("app.js", "chart.js", "editor.js", "tvac.js"):
        assert "innerHTML" not in (GUI / "static" / name).read_text(encoding="utf-8")


def test_every_endpoint_works_with_every_instrument_door_shut(monkeypatch):
    """Serial ports, VISA, outbound sockets (except to this server) and new
    processes all raise: the GUI must not need any of them to answer."""
    def shut(*a, **k):
        raise AssertionError("the GUI tried to open an instrument or a process")

    real_connect = socket.socket.connect

    def only_loopback(self, address):
        if str(address[0]) not in ("127.0.0.1", "::1", "localhost"):
            shut()
        return real_connect(self, address)

    with running_server(monkeypatch) as server:
        write_run(config.output_dir())
        castutils.GenerateCleanCast()
        c = Client(server).login()
        with monkeypatch.context() as m:
            try:
                import serial
                m.setattr(serial.Serial, "__init__", shut)
            except ImportError:
                pass
            try:
                import pyvisa
                m.setattr(pyvisa, "ResourceManager", shut)
            except ImportError:
                pass
            m.setattr(socket.socket, "connect", only_loopback)
            m.setattr(subprocess, "Popen", shut)
            for path in ("/", "/api/me", "/api/status", "/api/status?log=500", "/api/runs",
                         "/api/runs/tvac_20261004T120000Z?vars=TC01", "/static/app.js"):
                assert c.call("GET", path)[0].status == 200, path


# --- labcli gui ---------------------------------------------------------------------------------

def test_labcli_gui_needs_a_login_first(capsys):
    from formslab import cli
    assert cli.main(["gui"]) == 1
    assert "--set-login" in capsys.readouterr().err


def test_a_busy_port_is_reported_not_a_traceback(monkeypatch, capsys):
    from formslab.gui import auth, server
    monkeypatch.setattr(auth, "ITERATIONS", 1000)
    auth.set_login("a", "b")
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        assert server.main(["--port", str(taken.getsockname()[1])]) == 1
    assert "cannot listen" in capsys.readouterr().err


def test_the_help_lists_the_gui(capsys):
    from formslab import cli
    cli.main(["help"])
    assert "labcli gui" in capsys.readouterr().out


# --- the demo server must never pass for the bench's own ------------------------------------------

def test_the_login_page_can_tell_a_demo_from_the_real_server(monkeypatch):
    with running_server(monkeypatch) as server:
        c = Client(server)
        assert c.json("GET", "/api/info") == (200, {"demo": False})             # asked before any login
        assert c.json("GET", "/api/status")[0] == 401                           # and it opens nothing else
        monkeypatch.setenv("FORMSLAB_GUI_DEMO", "1")
    with running_server(monkeypatch) as demo:
        c = Client(demo)
        assert c.json("GET", "/api/info") == (200, {"demo": True})
        assert Client(demo).login().json("GET", "/api/me")[1] == {"user": "tester", "demo": True}
