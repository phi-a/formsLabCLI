"""Lab plans: `.plan` files run by formsLabCLI's own sequence runner.

Covers the plan grammar (and what it refuses as FORMS' business), the runner's
verbs on a fake clock, the rShutdown hook, rPSU and rSMTC08 against fake
instruments, and the host running the shipped first-test plan end to end.
Nothing here imports FORMS or opens a port.
"""
import csv
import json
import math
import sys

import pytest

from formslab import rscripts
from formslab.console.cast.castutils import (
    CommandPending, ReadCommand, ReadStatus, WriteCommand,
)
from formslab.rscripts import control
from formslab.sequence import (
    LabSequenceRunner, ListSink, PlanError, SequenceError, find_plan, parse_plan,
)
from formslab.sequence.spec import Segment, Sequence
from formslab.sequence.__main__ import main as check_main

PLAN = "load rPSU rSMTC08\nhold 1 s\n"


def plan(*lines):
    """A plan on the checkout's rPSU and rSMTC08, which declare psu1/psu2 and TC01..TC16."""
    return "load rPSU rSMTC08\n" + "\n".join(lines) + "\n"


# --- the plan grammar -----------------------------------------------------------

def test_a_plan_parses_into_segments():
    p = parse_plan("# a comment\nload rPSU rSMTC08\nrecord every 5 s\n\n"
                   "log go\npsu1 CH1 on\nhold 2 min\nuntil TC01 above 30 C timeout 1 min\n")

    segs = p.sequence.segments
    assert [s.verb for s in segs] == ["log", "command", "hold", "until"]
    assert segs[0].params == {"message": "go"}
    assert segs[1].params == {"label": "psu1", "request": {"1": {"on": True}}, "timeout_s": 10.0}
    assert segs[2].params == {"seconds": 120.0}
    assert segs[3].params == {"variable": "TC01", "side": "above", "value": 30.0, "unit": "C",
                              "timeout_s": 60.0}
    assert [s.label for s in segs] == ["log go", "psu1 CH1 on", "hold 2 min",
                                       "until TC01 above 30 C timeout 1 min"]
    assert p.rscripts == ("rPSU", "rSMTC08") and (p.record_interval, p.record_unit) == (5.0, "seconds")
    assert p.sequence.to_manifest()["clock"] == "wall"


def test_record_and_durations_take_s_min_h():
    p = parse_plan("load rSMTC08\nrecord every 2 min\nhold 1.5 h\nuntil TC01 below 300 timeout 30 s\n")
    assert (p.record_interval, p.record_unit) == (2.0, "minutes")
    assert p.sequence.segments[0].params == {"seconds": 5400.0}
    assert p.sequence.segments[1].params["timeout_s"] == 30.0
    assert p.sequence.segments[1].params["unit"] is None       # the variable's own unit


@pytest.mark.parametrize("source, needle", [
    (PLAN + "orbit.a = 7000\n", ":3: `orbit.a` is FORMS mission configuration"),
    (PLAN + "@variables\ndef declare():\n    pass\n", "FORMS mission code"),
    (plan("propagate 60 s"), "`propagate` is a FORMS mission operation"),
    (plan("until TC01 above 30 C"), "a wait on hardware always has a limit"),
    (plan("psu9 ch1 on"), "got 'psu9'"),
    (plan("psu1 ch4 on"), "expected ch1, ch2, ch3 or update after 'psu1', got 'ch4'; did you mean 'ch3'?"),
    (plan("hold -1 s"), "-1 must be >= 0"),
    (plan("hold 0 s"), "hold needs a positive duration"),
    (plan("hold 30s"), "write `30 s`, with a space"),
    (plan("hold 30 sec"), "did you mean 's'?"),
    (plan("hold 30 min # soak"), "comments go on their own line"),
    (plan("hvc pump on"), "hvc is declared by rLACO; add it to `load`"),
    (plan("tc read"), "tc takes no commands"),
    (plan("until chamberP below 5 timeout 1 min"), "chamberP is published by rLACO; add it to `load`"),
    (plan("until TC99 below 5 timeout 1 min"), "after 'until', got 'TC99'"),
    ("load rLACO\nuntil chamberP below 5 C timeout 1 min\n", "chamberP is in Torr"),
    (plan("hold 1 s", "load rLACO"), ":3: `load` goes before the first step"),
    ("load rPSU\nload rSMTC08\nhold 1 s\n", "`load` appears twice"),
    ("hold 1 s\n", "a plan starts with `load"),
    ("load rPSU\n", "the plan has no steps"),
    ("load rPSU\nrecord every 0 s\nhold 1 s\n", "record needs a positive duration"),
    ('rscripts.load = ["rA"]\nsequence.operations = [{"hold": 1}]\n', "the old plan format"),
])
def test_what_a_plan_refuses(source, needle):
    with pytest.raises(PlanError) as err:
        parse_plan(source)
    assert needle in str(err.value)


def test_names_and_keywords_ignore_case_but_keep_their_spelling():
    p = parse_plan(plan("HOLD 1 S", "until tc01 ABOVE 30 c TIMEOUT 1 MIN"))
    assert p.sequence.segments[1].params["variable"] == "TC01"
    assert p.sequence.segments[1].params["unit"] == "C"


def test_log_text_is_kept_as_written():
    p = parse_plan(plan("log step #2: PSU1 CH1  on"))
    assert p.sequence.segments[0].params == {"message": "step #2: PSU1 CH1 on"}


def test_plans_are_found_by_name_or_path(tmp_path, monkeypatch):
    (tmp_path / "mine.plan").write_text(PLAN, encoding="utf-8")
    monkeypatch.setenv("FORMSLAB_PLANS_DIR", str(tmp_path))
    assert find_plan("mine") == tmp_path / "mine.plan"
    assert find_plan(str(tmp_path / "mine.plan")) == tmp_path / "mine.plan"
    assert find_plan("psu1_smtc08_first").name == "psu1_smtc08_first.plan"   # the checkout's
    assert find_plan("nowhere") is None


def test_every_shipped_plan_checks_clean(capsys):
    for name in ("tvac", "psu1_smtc08_first", "laco_pumpdown", "laco_vent"):
        assert check_main([name]) == 0, name


def test_the_shipped_first_plan_checks_clean(capsys):
    assert check_main(["psu1_smtc08_first"]) == 0
    out = capsys.readouterr().out
    assert "rPSU" in out and "rSMTC08" in out and "held      100 s" in out


# --- the runner ---------------------------------------------------------------------

class FakeTime:
    """A clock that only moves when the runner sleeps, shared with the gates."""

    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


@pytest.fixture
def fake_time(monkeypatch):
    t = FakeTime()
    monkeypatch.setattr(control, "clock", t.clock)
    return t


@pytest.fixture
def run(tmp_path):
    return rscripts.Run(name="T", record_dir=tmp_path)


@pytest.fixture
def script_dir(tmp_path, monkeypatch):
    d = tmp_path / "rScripts"
    d.mkdir()
    monkeypatch.setenv(rscripts.ENV, str(d))
    rscripts.disabled.clear()
    return d


def write(d, name, body):
    (d / f"{name}.py").write_text(body, encoding="utf-8")


def start(run, segments, fake_time, **kw):
    """Run segments directly: the runner's tests do not go through the parser."""
    sink = ListSink()
    runner = LabSequenceRunner(run, Sequence(name="t", segments=tuple(segments)), sink=sink,
                               hz=4, clock=fake_time.clock, sleep=fake_time.sleep, **kw)
    return runner, sink, runner.execute


def hold(seconds):
    return Segment("hold", {"seconds": float(seconds)})


def command(label, request, timeout_s=10.0):
    return Segment("command", {"label": label, "request": request, "timeout_s": timeout_s})


def until(variable, side, value, unit, timeout_s):
    return Segment("until", {"variable": variable, "side": side, "value": value, "unit": unit,
                             "timeout_s": timeout_s})


# A stand-in owner of psu1: takes each request and publishes what it applied.
PSU_OWNER = '''
from formslab.console.cast.castutils import ReadCommand, UpdateStatus
applied = []
def rScript(run):
    req = ReadCommand("psu1")
    if req:
        applied.append(req)
        UpdateStatus("psu1", {"applied": len(applied)})
'''


def test_hold_runs_the_scripts_for_its_duration(run, fake_time, script_dir):
    write(script_dir, "rA", "calls = []\ndef rScript(run):\n    calls.append(1)\n")
    rscripts.load(run, ["rA"])
    runner, sink, go = start(run, [hold(2)], fake_time)

    result = go()

    assert result.segment_steps == [8]           # 2 s at 4 Hz
    assert len(sys.modules["rScripts.rA"].calls) == 8
    assert sink.kinds()[0] == "sequence_started" and sink.kinds()[-1] == "sequence_finished"
    assert sink.events[-1].error is None


def test_a_command_waits_until_its_owner_takes_it(run, fake_time, script_dir):
    write(script_dir, "rA", PSU_OWNER)
    rscripts.load(run, ["rA"])
    _, _, go = start(run, [command("psu1", {"1": {"on": True}}),
                           command("psu1", {"1": {"on": False}})], fake_time)

    result = go()

    assert sys.modules["rScripts.rA"].applied == [{"1": {"on": True}}, {"1": {"on": False}}]
    assert result.segment_steps == [1, 1]
    assert not CommandPending("psu1")


def test_a_command_nobody_takes_fails_the_run(run, fake_time, script_dir):
    write(script_dir, "rA", "def rScript(run): pass\n")
    rscripts.load(run, ["rA"])
    _, sink, go = start(run, [command("psu1", {"1": {"on": True}}, timeout_s=1)], fake_time)

    with pytest.raises(SequenceError, match="not taken within 1 s"):
        go()
    assert "not taken" in sink.events[-1].error


def test_until_converts_kelvin_to_the_limits_unit(run, fake_time, script_dir):
    write(script_dir, "rA", '''
def rScript(run):
    run.publish("TC01", (run.get("TC01") or 293.15) + 1.0, "K")
''')
    rscripts.load(run, ["rA"])
    _, _, go = start(run, [until("TC01", "above", 25.0, "C", 60)], fake_time)

    go()

    assert run.get("TC01") - 273.15 > 25.0


def test_until_times_out_with_the_last_value(run, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(run):\n'
                            '    run.publish("TC01", 293.15, "K")\n')
    rscripts.load(run, ["rA"])
    _, _, go = start(run, [until("TC01", "above", 25.0, "C", 3)], fake_time)

    with pytest.raises(SequenceError, match=r"last 20 C"):
        go()


def test_paused_time_does_not_use_up_a_hold(run, fake_time, script_dir):
    write(script_dir, "rA", "def rScript(run): pass\n")
    rscripts.load(run, ["rA"])
    polls = []

    def poll():                      # the first poll sits paused for 100 s
        polls.append(1)
        if len(polls) == 1:
            fake_time.sleep(100.0)

    _, _, go = start(run, [hold(1)], fake_time, poll=poll)
    result = go()

    assert result.segment_steps == [4]


def test_rshutdown_runs_last_loaded_first_even_after_a_failure(run, script_dir, capsys):
    for n in ("A", "C"):
        write(script_dir, f"r{n}", "def rScript(run): pass\n"
                                  f"def rShutdown(run):\n    run.order.append('{n}')\n")
    write(script_dir, "rB", "def rScript(run): pass\n"
                            "def rShutdown(run):\n    raise RuntimeError('stuck')\n")
    run.order = []

    rscripts.load(run, ["rA", "rB", "rC"])
    rscripts.shutdown(run)
    rscripts.shutdown(run)                  # once only

    assert run.order == ["C", "A"]
    assert "rB: rShutdown failed: RuntimeError: stuck" in capsys.readouterr().out


# --- rPSU and rSMTC08 against fake instruments -----------------------------------------

class FakePSU:
    def __init__(self, label):
        self.label = label
        self.ch = {c: {"on": False, "vset": 0.0, "cset": 0.0, "vmeas": 0.0, "cmeas": 0.0}
                   for c in (1, 2, 3)}
        self.state = {}
        self.disconnected = False

    def set(self, ch, v, c):
        self.ch[ch].update(vset=v, cset=c)

    def on(self, ch):
        self.ch[ch].update(on=True, vmeas=self.ch[ch]["vset"], cmeas=0.05)

    def off(self, ch):
        self.ch[ch].update(on=False, vmeas=0.0, cmeas=0.0)

    def status(self):
        self.state = {c: dict(s) for c, s in self.ch.items()}
        return dict(self.state)

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def bench(monkeypatch):
    """rPSU and rSMTC08 from the checkout, wired to fakes: psu1 enabled, psu2
    disabled, SMTC08_A reading 20..27 C, SMTC08_B not in the usbmap."""
    from formslab.devices.smtc08 import driver as smtc_module
    from formslab.devices.dp832a import config as psu_config, service as psu_service

    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    psus = {}

    def get_psu(label):
        return psus.setdefault(label, FakePSU(label))

    class FakeSMTC08:
        def __init__(self, label="SMTC08"):
            if label != "SMTC08_A":
                raise ValueError(f"Device '{label}' not found in USB map.")
            self.port = "COM7"

        def read_all(self):
            return [20.0 + i for i in range(8)]

        def close(self):
            pass

    monkeypatch.setattr(psu_service, "get_psu", get_psu)
    monkeypatch.setattr(psu_config, "enabled_psu_labels", lambda: ("psu1",))
    monkeypatch.setattr(smtc_module, "SMTC08", FakeSMTC08)
    return psus


def test_rpsu_applies_requests_publishes_scalars_and_turns_off_at_shutdown(run, bench):
    assert rscripts.load(run, ["rPSU"]) == ["rPSU"]
    WriteCommand({"1": {"voltage": 1.0, "current": 0.1, "on": True}}, "psu1")
    rscripts.tick(run)

    psu = bench["psu1"]
    assert psu.ch[1]["on"] and psu.ch[1]["vset"] == 1.0
    assert "psu2" not in bench                           # disabled: never opened
    assert run.get("PSU1_CH1_V") == 1.0
    assert run.get("PSU1_CH1_ON") == 1.0
    assert ReadStatus("psu1")["1"]["on"] is True

    rscripts.shutdown(run)
    assert psu.ch[1]["on"] is False and psu.disconnected


def test_rpsu_leaves_channels_it_did_not_switch_on(run, bench):
    rscripts.load(run, ["rPSU"])
    rscripts.tick(run)
    bench["psu1"].on(2)                                  # an operator, from the front panel
    rscripts.shutdown(run)
    assert bench["psu1"].ch[2]["on"] is True


def test_rsmtc08_publishes_kelvin_and_skips_an_absent_board(run, bench, capsys):
    assert rscripts.load(run, ["rSMTC08"]) == ["rSMTC08"]
    rscripts.tick(run)

    assert run.get("TC01") == pytest.approx(293.15)
    assert run.get("TC08") == pytest.approx(300.15)
    assert run.variable("TC01").unit == "K"
    assert run.variable("TC09") is None
    assert "SMTC08_B not configured" in capsys.readouterr().out


def test_rsmtc08_reads_nan_while_a_board_fails(run, bench, monkeypatch):
    rscripts.load(run, ["rSMTC08"])
    module = sys.modules["rScripts.rSMTC08"]

    class Broken:
        port = "COM7"

        def read_all(self):
            raise IOError("MODBUS timeout")

        def close(self):
            pass

    module.rg.boards["SMTC08_A"] = Broken()
    rscripts.tick(run)
    assert math.isnan(run.get("TC01"))
    assert "SMTC08_A" not in module.rg.boards            # closed, retried later


# --- the host, end to end -------------------------------------------------------------

def test_host_runs_the_first_plan_and_leaves_psu1_off(bench, monkeypatch):
    """`run psu1_smtc08_first` minus the subprocess and the waiting: holds are
    shortened, everything else is the shipped plan."""
    from dataclasses import replace

    from formslab.host import sequence as host
    from formslab.sequence import load_plan

    def quick(path):
        p = load_plan(path)
        segs = tuple(replace(s, params={"seconds": 0.3}) if s.verb == "hold" else s
                     for s in p.sequence.segments)
        return replace(p, record_interval=0.1, sequence=replace(p.sequence, segments=segs))

    monkeypatch.setattr(host, "load_plan", quick)

    host.channel(plan_path=find_plan("psu1_smtc08_first"))

    psu = bench["psu1"]
    assert psu.ch[1]["on"] is False and psu.ch[1]["vset"] == 1.0
    events = [json.loads(line) for line in
              host.events_path().read_text(encoding="utf-8").splitlines()]
    assert events[0]["kind"] == "sequence_started" and events[0]["manifest"]["segment_count"] == 10
    assert events[-1] == {"kind": "sequence_finished", "name": "psu1_smtc08_first",
                          "steps": events[-1]["steps"], "error": None,
                          "ended": None}
    from formslab.config import output_dir
    headers = [next(csv.reader(p.open(encoding="utf-8")))
               for p in sorted(output_dir().glob("psu1_smtc08_first_*.csv"))]
    assert "TC01 [K]" in headers[-1] and "PSU1_CH1_V [V]" in headers[-1]


def test_host_refuses_a_plan_whose_scripts_do_not_load(tmp_path, monkeypatch):
    from formslab.host import sequence as host

    monkeypatch.setenv(rscripts.ENV, str(tmp_path))
    plan = tmp_path / "p.plan"
    plan.write_text("load rMissing\nhold 1 s\n", encoding="utf-8")
    with pytest.raises(PlanError, match="rMissing"):
        host.channel(plan_path=plan)
    assert not host.lock_path().exists()


def test_hold_until_end_is_open_ended():
    p = parse_plan(plan("hold until end"))
    assert p.sequence.segments[0].params == {"seconds": None}
    with pytest.raises(PlanError, match="unexpected '5' after 'hold until end'"):
        parse_plan(plan("hold until end 5 min"))
    with pytest.raises(PlanError, match="until"):
        parse_plan(plan("hold forever"))


def test_the_shipped_tvac_plan_checks_clean(capsys):
    assert check_main(["tvac"]) == 0
    out = capsys.readouterr().out
    assert "rLACO" in out and "rSMTC08" in out and "rPSU" in out
    assert "runs until ctrl `end`" in out
