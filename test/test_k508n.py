"""The K508N cryocooler through rCryoBoard, against a stand-in board.

The board's I2C bus is the recording fake of test_cryoboard; its supply, psu1 CH1,
is the CAST status rPSU would publish. Each `cryo` request is answered (done, or
refused with why) and the board's state is published for plans to wait on.
"""
import importlib.util
from pathlib import Path

import pytest

from formslab import config, rscripts
from formslab.console.cast.castutils import CommandState, UpdateStatus, WriteCommand
from formslab.devices.cryocooler import board as boardmod, owner
from formslab.sequence.plan import review

from test_cryoboard import FakeI2C

ROOT = Path(__file__).resolve().parents[1]


def supply(on=True, volts=24.0, amps=1.0):
    """psu1 CH1 as rPSU reports it."""
    UpdateStatus("psu1", {"1": {"on": on, "vset": volts, "cset": amps, "vmeas": volts if on else 0.0,
                                "cmeas": 0.3 if on else 0.0}})


@pytest.fixture
def cryo(monkeypatch):
    """rCryoBoard, fresh, with the stand-in board behind it; (module, run, bus)."""
    bus = FakeI2C()
    real = boardmod.CryoBoard
    monkeypatch.setattr(boardmod, "CryoBoard",
                        lambda label: real(label, config_path=config.usbmap_path(), transport=bus))
    monkeypatch.setattr(owner.time, "sleep", lambda s: None)
    spec = importlib.util.spec_from_file_location("rCryoBoard_under_test", ROOT / "rScripts" / "rCryoBoard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.rg.TICK_INTERVAL = 0.0
    run = rscripts.Run(name="T")
    return m, run, bus


def send(m, run, request, ticks=3):
    rid = WriteCommand(request, "cryo")
    for _ in range(ticks):
        m.rScript(run)
    return CommandState("cryo", rid)


def test_startup_then_on_is_answered_and_published(cryo):
    m, run, bus = cryo
    supply()
    assert send(m, run, {"startup": True})["ok"] is True
    assert run.get("CRYO_LINK") == 1 and run.get("CRYO_ON") == 0
    out = send(m, run, {"voltage": 17.0, "resistance": 266.0, "enabled": True})
    assert out["ok"] is True and "voltage=17.00 V" in out["messages"][0]
    assert run.get("CRYO_ON") == 1 and run.get("CRYO_CCV") == pytest.approx(17.0)
    assert run.get("CRYO_RES") == pytest.approx(266.0, abs=9)    # the nearest of the resistor's 64 steps
    assert run.get("CRYO_SUPPLY_V") == pytest.approx(24.0)
    assert run.get("CRYO_OK") == 1                        # the converter's own enable bit on, no fault


def test_the_k508n_range_reaches_down_to_8_5_volts(cryo):
    m, run, _ = cryo
    supply()
    send(m, run, {"startup": True})
    assert send(m, run, {"voltage": 8.5})["ok"] is True


def test_a_start_with_no_supply_is_refused_with_why(cryo, monkeypatch):
    m, run, _ = cryo
    monkeypatch.setattr(m, "READY_WAIT_S", 0.0)
    out = send(m, run, {"startup": True})
    assert out["ok"] is False and "psu1 CH1, is not at 24 V 1 A" in out["messages"][0]


def test_after_shutdown_a_setting_is_refused_until_startup(cryo):
    m, run, _ = cryo
    supply()
    send(m, run, {"startup": True})
    assert send(m, run, {"shutdown": True})["ok"] is True
    out = send(m, run, {"enabled": True})
    assert out["ok"] is False and "send cryo startup" in out["messages"][0]


def test_the_supply_is_24_v_1_a_protected_at_1_25_a():
    from formslab.devices.cryocooler import config as cc
    assert (cc.CRYO_SUPPLY_VOLTAGE_V, cc.CRYO_SUPPLY_CURRENT_A, cc.CRYO_SUPPLY_OCP_A) == (24.0, 1.0, 1.25)


# --- the block -------------------------------------------------------------------------

PLAN = "load rCryoBoard rPSU rSMTC08\nrecord every 10 s\n\nk508n at 17 V with 266 ohm\nuntil TC01 <= -40 C\n"


@pytest.fixture
def shipped(monkeypatch):
    from formslab.sequence import plan as planfile
    monkeypatch.setattr(planfile, "shipped_dir", lambda: ROOT / "plans")


def test_a_plan_runs_the_k508n_then_waits_for_the_cold_as_long_as_it_takes(shipped):
    assert review(PLAN) == ([], [])


@pytest.mark.parametrize("call, error", [
    ("k508n at 8 V with 266 ohm", "outside 8.5..20 V"),
    ("k508n at 17 V with 266", "expected ohm"),
    ("k508n at 17 with 266 ohm", "expected V"),
])
def test_a_k508n_call_says_its_units_and_stays_in_range(shipped, call, error):
    errors, _ = review(PLAN.replace("k508n at 17 V with 266 ohm", call))
    assert errors and error in errors[0][1], errors


# --- the board's supply, set from a plan -----------------------------------------------

@pytest.mark.parametrize("words, error", [
    ("supply psu1 ch1 at 24 V 1 A protect 23 V 1.25 A", "over-voltage protection, 23 V, is below the supply's 24 V"),
    ("supply psu1 ch1 at 24 V 1 A protect 24.5 V 0.5 A", "over-current protection, 0.5 A, is below the supply's 1 A"),
    ("supply psu1 ch1 at 26 V 1 A protect 26 V 1 A", "outside 20..24.5 V"),            # the board's most
    ("supply psu1 ch1 at 24 V 3 A protect 24.5 V 3 A", "outside 0.1..2 A"),
    ("supply psu1 ch1 at 18 V 1 A protect 24 V 1 A", "outside 20..24.5 V"),            # too little to drive the cooler
    ("supply psu2 ch3 at 24 V 1 A protect 24.5 V 1.25 A",                             # not the channel it is wired to
     "the hardware map says the cryocooler board is wired to psu1 ch1"),
])
def test_a_supply_setting_is_refused_with_why(words, error):
    from formslab.rscripts import cast
    with pytest.raises(cast.GrammarError, match=error):
        cast.request("cryo", words.split())


def test_cryo_supply_sends_its_settings_with_protection_to_the_mapped_channel(cryo):
    from formslab.console.cast.castutils import ReadCommand
    m, run, _ = cryo
    supply(volts=22.0, amps=0.8)                      # the channel as rPSU will report it once applied
    out = send(m, run, {"supply": {"psu": "psu1", "channel": 1, "volts": 22.0, "amps": 0.8, "ovp": 23.0, "ocp": 1.0}})
    assert out["ok"] is True and "supply 22 V 0.8 A, protected at 23 V 1 A" in out["messages"]
    sent = ReadCommand("psu1")["1"]
    assert (sent["voltage"], sent["current"], sent["ovp"], sent["ocp"], sent["on"]) == (22.0, 0.8, 23.0, 1.0, True)


def test_a_supply_request_for_another_channel_is_refused_when_it_runs(cryo):
    m, run, _ = cryo
    supply()
    out = send(m, run, {"supply": {"psu": "psu2", "channel": 3, "volts": 24.0, "amps": 1.0, "ovp": 24.5, "ocp": 1.25}})
    assert out["ok"] is False and "wired to psu1 ch1, not psu2 ch3" in out["messages"][0]


def test_a_plan_that_runs_the_board_may_not_command_its_channel(tmp_path, monkeypatch):
    """The hardware map gives psu1 CH1 to rCryoBoard: loaded through a block, as k508n
    loads it, no step or rule of the plan may command that channel; the others are free."""
    from formslab.sequence.plan import ENV
    monkeypatch.setenv(ENV, str(tmp_path))
    (tmp_path / "cooler.block").write_text("# Run the cooler\nblock cooler on\nload rCryoBoard rPSU\n\ncryo on\n",
                                           encoding="utf-8")
    base = "load rCryoBoard rPSU\ncooler on\n"
    for step in ("psu1 ch1 set 12 1", "psu1 ch1 off", "when CRYO_ON = true then psu1 ch1 off"):
        errors, _ = review(base + step + "\n")
        assert errors == [(3, "psu1 ch1 feeds the cryocooler board, and rCryoBoard, loaded here, drives it.")], step
    assert review(base + "psu1 ch2 on\n")[0] == []


# --- a board that does not come up ---------------------------------------------------------

class StuckBus(FakeI2C):
    """The Pico's answer when the board side is unpowered: SCL held low."""

    def write_register(self, address, register, data):
        raise RuntimeError('Traceback (most recent call last):\n  File "main.py", line 114, in write\n'
                           "OSError: [Errno 110] ETIMEDOUT")


@pytest.fixture
def stuck(monkeypatch):
    """rCryoBoard with a board that times out on I2C; its supply on but drawing 2 mA."""
    bus = StuckBus()
    real = boardmod.CryoBoard
    monkeypatch.setattr(boardmod, "CryoBoard",
                        lambda label: real(label, config_path=config.usbmap_path(), transport=bus))
    monkeypatch.setattr(owner.time, "sleep", lambda s: None)
    monkeypatch.setattr(owner, "INIT_RETRY_S", 0.0)
    spec = importlib.util.spec_from_file_location("rCryoBoard_stuck", ROOT / "rScripts" / "rCryoBoard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.rg.TICK_INTERVAL = 0.0
    UpdateStatus("psu1", {"1": {"on": True, "vset": 24.0, "cset": 1.0, "vmeas": 24.0, "cmeas": 0.0023}})
    return m, rscripts.Run(name="T"), bus


def test_a_board_that_does_not_answer_is_refused_at_once_with_why(stuck):
    m, run, bus = stuck
    out = send(m, run, {"startup": True}, ticks=4)
    assert out["ok"] is False
    why = out["messages"][0]
    assert "ETIMEDOUT" in why and "24.0 V, 2 mA" in why and "check the cable" in why
    assert bus.closed                                     # the Pico is released for the next try
    from formslab.console.cast.castutils import ReadStatus
    assert "ETIMEDOUT" in ReadStatus("cryo")["ERR"]       # and the status card says so


@pytest.mark.parametrize("error, words", [
    ("OSError: [Errno 110] ETIMEDOUT", "clock line stayed low"),
    ("OSError: [Errno 19] ENODEV", "nothing answered"),
    ("could not open port 'COM9'", "serial port did not open"),
])
def test_the_reason_names_what_the_i2c_error_means(error, words):
    why = owner.diagnose(RuntimeError(error), {"vmeas": 24.0, "cmeas": 0.25})
    assert words in why and "24.0 V, 250 mA" in why and "check the cable" not in why


def test_end_may_cut_the_boards_channel_and_nothing_switches_it_back_on(cryo):
    from formslab.rscripts.rules import assess
    m, run, _ = cryo
    supply()
    send(m, run, {"startup": True})
    off = {"1": {"on": False}}
    assert assess("psu1", off)                            # a plan step may not
    assert assess("psu1", off, ending=True) == []         # the end script may
    assert assess("psu1", {"1": {"on": True}}, ending=True)   # but not switch it on
    run.ending = True
    supply(on=False)                                       # the end script has cut it
    for _ in range(3):
        m.rScript(run)
    from formslab.console.cast.castutils import ReadCommand
    assert not (ReadCommand("psu1") or {}).get("1", {}).get("on")   # rCryoBoard did not ask for it back


def test_cryo_on_turns_on_an_output_the_converter_dropped_and_says_why(cryo):
    from formslab.devices.cryocooler import registers as regs
    m, run, bus = cryo
    logged = []
    run.log = lambda message, **kw: logged.append(message)
    supply()
    send(m, run, {"startup": True})
    send(m, run, {"voltage": 12.0, "enabled": True})
    assert run.get("CRYO_OK") == 1
    bus.status = 0b01000001                               # OCP latched...
    bus.registers[(regs.VCONV_ADDR, regs.MODE)] = 0b00100000   # ...and the output off
    send(m, run, {"update": True})
    bus.status = 0b00000001                               # read once, the flag is gone
    send(m, run, {"update": True})
    from formslab.console.cast.castutils import ReadStatus
    assert run.get("CRYO_OK") == 0 and ReadStatus("cryo")["LASTFAULT"].startswith("OCP at")
    assert any("went off without cryo off" in line and "OCP" in line for line in logged)
    assert send(m, run, {"enabled": True})["ok"] is True
    assert run.get("CRYO_OK") == 1                        # on again, from the converter's own bit
