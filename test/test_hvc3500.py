"""HVC-3500 driver tests: protocol parsing and the client against the in-process simulator.

The HVC-3500 driver against its simulator. No hardware needed.
"""
import pytest

from formslab.devices.hvc3500 import protocol as P


def test_encode_appends_cr():
    assert P.encode("?MC") == b"?MC\r"
    assert P.encode("!Z1:855") == b"!Z1:855\r"
    assert P.encode("!VS:1.5e-05") == b"!VS:1.5e-05\r"


@pytest.mark.parametrize("bad", ["MC", "?", "?mc", "?MC\r", "!Z1:85.5;rm", "?TOOLONG"])
def test_encode_rejects_bad_commands(bad):
    with pytest.raises(ValueError):
        P.encode(bad)


def test_decode_strips_any_terminator():
    assert P.decode_reply(b"AUTO\r") == "AUTO"
    assert P.decode_reply(b"AUTO\r\n") == "AUTO"
    assert P.decode_reply(b"AUTO\x13") == "AUTO"


def test_parse_mode():
    assert P.parse_reply("?MC", "AUTO").raw == "AUTO"
    with pytest.raises(P.ProtocolError):
        P.parse_reply("?MC", "RUNNING")


def test_parse_keyed_reply_and_mismatch():
    r = P.parse_reply("?VP", "VP:7.5000000E-03")
    assert (r.key, r.value) == ("VP", "7.5000000E-03")
    assert P.parse_number(r.value) == 7.5e-3
    with pytest.raises(P.ProtocolError):
        P.parse_reply("?VP", "VS:1.0")


def test_parse_bare_ack():
    assert P.parse_reply("!CS", "CS").raw == "CS"
    with pytest.raises(P.ProtocolError):
        P.parse_reply("!CS", "CA")


def test_er_raises():
    with pytest.raises(P.ProtocolError):
        P.parse_reply("?QQ", "ER")


def test_temperature_tenths():
    assert P.parse_temperature("458") == 45.8
    assert P.parse_temperature("-105") == -10.5
    assert P.format_temperature(85.5) == "855"
    assert P.format_temperature(-10.55) == "-105" or P.format_temperature(-10.55) == "-106"
    with pytest.raises(P.ProtocolError):
        P.parse_temperature("45.8")


def test_error_status_examples_from_manual():
    es = P.parse_error_status("W: 0000065536")
    assert es.severity == "W" and es.active == (16,) and es.names == ["Control air low"]
    # manual example 2 says faults 20 and 22 but prints 4194304, which is bit 22 alone
    es = P.parse_error_status("F: 00004194304")
    assert es.active == (22,)
    es = P.parse_error_status("F: 5242880")
    assert es.active == (20, 22)
    assert P.parse_error_status("N: 0000000000").ok
    unknown = P.parse_error_status("W: 1073741824")
    assert unknown.active == (30,) and unknown.names == ["unknown bit 30"]


def test_error_status_rejects_garbage():
    for bad in ("X: 1", "W:abc", "W", "W: 4294967296"):
        with pytest.raises(P.ProtocolError):
            P.parse_error_status(bad)


def test_device_state():
    assert P.parse_device_state("O") is True
    assert P.parse_device_state(" C") is False
    with pytest.raises(P.ProtocolError):
        P.parse_device_state("ON")


def test_write_classes_are_disjoint():
    assert not set(P.SETPOINT_WRITES) & set(P.ACTION_WRITES)
    assert not set(P.TOGGLE_WRITES) & set(P.ACTION_WRITES)


# --- client against the simulator ---
import socket
import time


from formslab.devices.hvc3500 import HVC3500Client, ProtocolError, WriteRefused
from formslab.devices.hvc3500.simulator import Simulator


@pytest.fixture(params=[b"\r", b"\r\n", b"\x13"], ids=["CR", "CRLF", "x13"])
def sim(request):
    with Simulator(terminator=request.param) as s:
        yield s


def client(sim, **kw):
    return HVC3500Client(sim.host, sim.port, timeout=2.0, inter_command_delay=0.0,
                         toggle_settle_s=1.3, **kw)


def test_discriminating_probe(sim):
    with client(sim) as c:
        assert c.mode() == "AUTO"
        assert c.test_status() == "IDLE,Stand By,Ready"
        assert c.thermal_control_active() is False
        assert c.pressure() == pytest.approx(760.0)
        assert c.error_status().ok
        with pytest.raises(ProtocolError):
            c.transact("?QQ")          # ER


def test_snapshot_reads_everything(sim):
    with client(sim) as c:
        snap = c.snapshot(temperatures=[0, 3], zones=[1, 2])
    assert "errors" not in snap
    assert snap["T0"] == pytest.approx(22.0) and snap["T3"] == pytest.approx(22.3)
    # zone idle -> effective setpoint tracks the zone's control sensor (T2), not the commanded 20.0
    assert snap["Z1_setpoint"] == pytest.approx(22.2) and snap["OR"] is False
    assert snap["error_status"]["severity"] == "N"


def test_one_shot_lifecycle():
    with Simulator(one_shot=True) as s:
        c = client(s, persistent=False)
        for _ in range(3):
            assert c.mode() == "AUTO"
        assert all(t.reconnected for t in c.transactions)
        # persistent mode against a one-shot server fails on the 2nd command, without retry
        p = client(s, persistent=True)
        p.mode()
        with pytest.raises(OSError):
            p.mode()
        assert len(p.transactions) == 2


def test_timeout_is_not_retried():
    with Simulator(reply_delay_s=0.5) as s:
        c = client(s)
        c.timeout = 0.1
        with pytest.raises(socket.timeout):
            c.mode()
        assert len(c.transactions) == 1 and not c.transactions[0].ok


def test_setpoint_writes_read_back(sim):
    with client(sim) as c:
        assert c.set_zone_setpoint(1, 85.5) == 85.5
        assert c.set_zone_rate(2, 0.4) == 0.4
        assert c.set_zone_range(1, 1.5) == 1.5
        assert c.set_vacuum_setpoint(2.5e-4) == pytest.approx(2.5e-4)
        assert c.set_hold_time(120) == 120
        assert c.select_recipe(3) == 3
        assert c.set_zone_setpoint(7, -150.0) == -150.0      # firmware exposes 7 zones
        with pytest.raises(ValueError):
            c.set_zone_setpoint(8, 10)
        with pytest.raises(ValueError):
            c.select_recipe(21)


def test_zone_setpoint_commanded_vs_effective(sim):
    """As observed on the chamber: !Zn persists (HMI shows it) while ?Zn keeps
    tracking the sensor until the zone is activated."""
    with client(sim) as c:
        assert c.set_zone_setpoint(1, 40.0) == 40.0          # verified by echo
        assert c.zone_setpoint(1) == pytest.approx(22.2)     # still tracking T2
        c.activate_zone(1, confirm=True)
        assert c.zone_setpoint(1) == 40.0                    # now the commanded value
        assert c.thermal_control_active() is True            # ?TC third field
        c.deactivate_zone(1, confirm=True)
        assert c.thermal_control_active() is False


def test_actions_require_confirm(sim):
    with client(sim) as c:
        with pytest.raises(WriteRefused):
            c.action("CS")
        with pytest.raises(WriteRefused):
            c.activate_zone(1)
        with pytest.raises(ValueError):
            c.action("OR", confirm=True)    # toggles are not actions
        assert c.action("CS", confirm=True).raw == "CS"
        assert c.test_status().startswith("RUNNING")
        assert c.activate_zone(1, confirm=True).raw == "ZS1"
        assert c.action("CA", confirm=True).raw == "CA"
        es = c.error_status()
        assert es.severity == "F" and es.active == (0,) and es.names == ["Abort"]
        c.action("CR", confirm=True)
        assert c.error_status().ok


def test_device_toggle_is_read_first_single_send_verify(sim):
    with client(sim) as c:
        with pytest.raises(WriteRefused):
            c.set_device("OP", True)
        n = len(c.transactions)
        assert c.set_device("OP", True, confirm=True) is True
        sent = [t.tx for t in c.transactions[n:]]
        assert sent == ["?OP<CR>", "!OP<CR>", "?OP<CR>"]
        # already on: no toggle sent
        n = len(c.transactions)
        assert c.set_device("OP", True, confirm=True) is True
        assert [t.tx for t in c.transactions[n:]] == ["?OP<CR>"]


def test_interlocked_toggle_reports_no_change(sim):
    with client(sim) as c:
        # gate valve interlock: turbo off -> the toggle is accepted but nothing moves
        with pytest.raises(ProtocolError, match="interlock"):
            c.set_device("OG", True, confirm=True)
        assert [t.tx for t in c.transactions].count("!OG<CR>") == 1   # exactly one toggle


def test_pumpdown_model(sim):
    with client(sim) as c:
        c.set_vacuum_setpoint(1e-3)
        c.set_device("OP", True, confirm=True)
        c.set_device("OR", True, confirm=True)
        time.sleep(1.5)
        assert c.pressure() < 760.0
        assert c.pressure_rate() < 0
