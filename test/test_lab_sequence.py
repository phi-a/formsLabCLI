"""Lab plans: `.forms` documents run by formsLabCLI's own sequence runner.

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
    LabSequenceRunner, ListSink, PlanError, SequenceError, find_plan, is_lab_plan, parse_plan,
)
from formslab.sequence.__main__ import main as check_main

PLAN = '''
mission.name = "t"
rscripts.load = ["rA"]
sequence.operations = [{"hold": 1, "units": "seconds"}]
'''


def plan_src(ops, extra=""):
    return f'rscripts.load = ["rA"]\n{extra}sequence.operations = {ops!r}\n'


# --- the plan grammar -----------------------------------------------------------

def test_a_plan_parses_into_segments():
    plan = parse_plan(plan_src([
        {"log": "go"},
        {"command": "psu1", "request": {"1": {"on": True}}},
        {"hold": 2, "units": "minutes"},
        {"until": "TC01", "above": 30.0, "unit": "C", "timeout_s": 60},
    ], 'recording.interval = 5\n'))

    assert [s.verb for s in plan.sequence.segments] == ["log", "command", "hold", "until"]
    assert plan.sequence.segments[2].params == {"seconds": 120.0}
    assert plan.sequence.segments[1].params["timeout_s"] == 10.0
    assert plan.rscripts == ("rA",) and plan.record_interval == 5.0
    assert plan.sequence.to_manifest()["clock"] == "wall"


@pytest.mark.parametrize("source, needle", [
    (PLAN + 'orbit.a = 7000\n', "FORMS mission configuration"),
    (PLAN + '@variables\ndef declare():\n    pass\n', "FORMS mission code"),
    (plan_src([{"propagate": 60, "units": "seconds"}]), "FORMS mission operation"),
    (plan_src([{"until": "TC01", "above": 30}]), "a wait on hardware always has a limit"),
    (plan_src([{"command": "psu9", "request": {"x": 1}}]), "not a CAST label"),
    (plan_src([{"hold": 5, "command": "psu1"}]), "exactly one of"),
    (plan_src([{"hold": 5, "for": 3}]), "does not take ['for']"),
    (plan_src([{"hold": -1}]), "positive duration"),
    (PLAN + 'mission.name = "again"\n', "assigned twice"),
    (PLAN + 'x = 1\n', "block.field = value"),
    (PLAN + 'mission.colour = "red"\n', "not a lab plan field"),
    ('sequence.operations = [{"hold": 1}]\n', "rscripts.load must be"),
    (plan_src([]), "non-empty list"),
])
def test_what_a_plan_refuses(source, needle):
    with pytest.raises(PlanError, match=None) as err:
        parse_plan(source)
    assert needle in str(err.value)


def test_rscripts_load_is_what_makes_a_forms_file_a_lab_plan(tmp_path):
    lab = tmp_path / "lab.forms"
    lab.write_text(PLAN, encoding="utf-8")
    mission = tmp_path / "mission.forms"
    mission.write_text('orbit.a = 7000\nsequence.operations = [{"propagate": 60}]\n',
                       encoding="utf-8")
    assert is_lab_plan(lab) and not is_lab_plan(mission)


def test_plans_are_found_by_name_or_path(tmp_path, monkeypatch):
    (tmp_path / "mine.forms").write_text(PLAN, encoding="utf-8")
    monkeypatch.setenv("FORMSLAB_PLANS_DIR", str(tmp_path))
    assert find_plan("mine") == tmp_path / "mine.forms"
    assert find_plan(str(tmp_path / "mine.forms")) == tmp_path / "mine.forms"
    assert find_plan("psu1_smtc08_first").name == "psu1_smtc08_first.forms"   # the checkout's
    assert find_plan("nowhere") is None


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
def forms(tmp_path):
    return rscripts.LabForms(name="T", record_dir=tmp_path)


@pytest.fixture
def script_dir(tmp_path, monkeypatch):
    d = tmp_path / "rScripts"
    d.mkdir()
    monkeypatch.setenv(rscripts.ENV, str(d))
    rscripts.disabled.clear()
    return d


def write(d, name, body):
    (d / f"{name}.py").write_text(body, encoding="utf-8")


def run(forms, ops, fake_time, **kw):
    plan = parse_plan(plan_src(ops))
    sink = ListSink()
    runner = LabSequenceRunner(forms, plan.sequence, sink=sink, hz=4,     # 0.25 s: exact
                               clock=fake_time.clock, sleep=fake_time.sleep, **kw)
    return runner, sink, runner.run


# A stand-in owner of psu1: takes each request and publishes what it applied.
PSU_OWNER = '''
from formslab.console.cast.castutils import ReadCommand, UpdateStatus
applied = []
def rScript(forms):
    req = ReadCommand("psu1")
    if req:
        applied.append(req)
        UpdateStatus("psu1", {"applied": len(applied)})
'''


def test_hold_runs_the_scripts_for_its_duration(forms, fake_time, script_dir):
    write(script_dir, "rA", "calls = []\ndef rScript(forms):\n    calls.append(1)\n")
    rscripts.load(forms, ["rA"])
    runner, sink, go = run(forms, [{"hold": 2, "units": "seconds"}], fake_time)

    result = go()

    assert result.segment_steps == [8]           # 2 s at 4 Hz
    assert len(sys.modules["rScripts.rA"].calls) == 8
    assert sink.kinds()[0] == "sequence_started" and sink.kinds()[-1] == "sequence_finished"
    assert sink.events[-1].error is None


def test_a_command_waits_until_its_owner_takes_it(forms, fake_time, script_dir):
    write(script_dir, "rA", PSU_OWNER)
    rscripts.load(forms, ["rA"])
    _, _, go = run(forms, [{"command": "psu1", "request": {"1": {"on": True}}},
                           {"command": "psu1", "request": {"1": {"on": False}}}], fake_time)

    result = go()

    assert sys.modules["rScripts.rA"].applied == [{"1": {"on": True}}, {"1": {"on": False}}]
    assert result.segment_steps == [1, 1]
    assert not CommandPending("psu1")


def test_a_command_nobody_takes_fails_the_run(forms, fake_time, script_dir):
    write(script_dir, "rA", "def rScript(forms): pass\n")
    rscripts.load(forms, ["rA"])
    _, sink, go = run(forms, [{"command": "psu1", "request": {"1": {"on": True}},
                               "timeout_s": 1}], fake_time)

    with pytest.raises(SequenceError, match="not taken within 1 s"):
        go()
    assert "not taken" in sink.events[-1].error


def test_until_converts_kelvin_to_the_limits_unit(forms, fake_time, script_dir):
    write(script_dir, "rA", '''
def rScript(forms):
    v = forms.get_variable("TC01") or forms.types.scalar("TC01", 293.15, unit="K")
    v.set(v.value + 1.0, unit="K")
''')
    rscripts.load(forms, ["rA"])
    _, _, go = run(forms, [{"until": "TC01", "above": 25.0, "unit": "C", "timeout_s": 60}],
                   fake_time)

    go()

    assert forms.get_variable("TC01").value - 273.15 > 25.0


def test_until_times_out_with_the_last_value(forms, fake_time, script_dir):
    write(script_dir, "rA", 'def rScript(forms):\n'
                            '    forms.types.scalar("TC01", 293.15, unit="K")\n')
    rscripts.load(forms, ["rA"])
    _, _, go = run(forms, [{"until": "TC01", "above": 25.0, "unit": "C", "timeout_s": 3}],
                   fake_time)

    with pytest.raises(SequenceError, match=r"last 20 C"):
        go()


def test_paused_time_does_not_use_up_a_hold(forms, fake_time, script_dir):
    write(script_dir, "rA", "def rScript(forms): pass\n")
    rscripts.load(forms, ["rA"])
    polls = []

    def poll():                      # the first poll sits paused for 100 s
        polls.append(1)
        if len(polls) == 1:
            fake_time.sleep(100.0)

    _, _, go = run(forms, [{"hold": 1}], fake_time, poll=poll)
    result = go()

    assert result.segment_steps == [4]


def test_rshutdown_runs_last_loaded_first_even_after_a_failure(forms, script_dir, capsys):
    for n in ("A", "C"):
        write(script_dir, f"r{n}", "def rScript(forms): pass\n"
                                  f"def rShutdown(forms):\n    forms.order.append('{n}')\n")
    write(script_dir, "rB", "def rScript(forms): pass\n"
                            "def rShutdown(forms):\n    raise RuntimeError('stuck')\n")
    forms.order = []

    rscripts.load(forms, ["rA", "rB", "rC"])
    rscripts.shutdown(forms)
    rscripts.shutdown(forms)                  # once only

    assert forms.order == ["C", "A"]
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
    from formslab.devices import SMTC08 as smtc_module, psu_config, psu_service

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


def test_rpsu_applies_requests_publishes_scalars_and_turns_off_at_shutdown(forms, bench):
    assert rscripts.load(forms, ["rPSU"]) == ["rPSU"]
    WriteCommand({"1": {"voltage": 1.0, "current": 0.1, "on": True}}, "psu1")
    rscripts.tick(forms)

    psu = bench["psu1"]
    assert psu.ch[1]["on"] and psu.ch[1]["vset"] == 1.0
    assert "psu2" not in bench                           # disabled: never opened
    assert forms.get_variable("PSU1_CH1_V").value == 1.0
    assert forms.get_variable("PSU1_CH1_ON").value == 1.0
    assert ReadStatus("psu1")["1"]["on"] is True

    rscripts.shutdown(forms)
    assert psu.ch[1]["on"] is False and psu.disconnected


def test_rpsu_leaves_channels_it_did_not_switch_on(forms, bench):
    rscripts.load(forms, ["rPSU"])
    rscripts.tick(forms)
    bench["psu1"].on(2)                                  # an operator, from the front panel
    rscripts.shutdown(forms)
    assert bench["psu1"].ch[2]["on"] is True


def test_rsmtc08_publishes_kelvin_and_skips_an_absent_board(forms, bench, capsys):
    assert rscripts.load(forms, ["rSMTC08"]) == ["rSMTC08"]
    rscripts.tick(forms)

    assert forms.get_variable("TC01").value == pytest.approx(293.15)
    assert forms.get_variable("TC08").value == pytest.approx(300.15)
    assert forms.get_variable("TC01").unit == "K"
    assert forms.get_variable("TC09") is None
    assert "SMTC08_B not configured" in capsys.readouterr().out


def test_rsmtc08_reads_nan_while_a_board_fails(forms, bench, monkeypatch):
    rscripts.load(forms, ["rSMTC08"])
    module = sys.modules["rScripts.rSMTC08"]

    class Broken:
        port = "COM7"

        def read_all(self):
            raise IOError("MODBUS timeout")

        def close(self):
            pass

    module.rg.boards["SMTC08_A"] = Broken()
    rscripts.tick(forms)
    assert math.isnan(forms.get_variable("TC01").value)
    assert "SMTC08_A" not in module.rg.boards            # closed, retried later


# --- the host, end to end -------------------------------------------------------------

def test_host_runs_the_first_plan_and_leaves_psu1_off(bench, monkeypatch):
    """`run psu1_smtc08_first` minus the subprocess and the waiting: holds are
    shortened, everything else is the shipped plan."""
    from dataclasses import replace

    from formslab.host import sequence as host
    from formslab.host.modes import plan as planmode
    from formslab.sequence import load_plan

    def quick(path):
        p = load_plan(path)
        segs = tuple(replace(s, params={"seconds": 0.3}) if s.verb == "hold" else s
                     for s in p.sequence.segments)
        return replace(p, record_interval=0.1, sequence=replace(p.sequence, segments=segs))

    monkeypatch.setattr(planmode, "load_plan", quick)

    host.channel(plan_path=find_plan("psu1_smtc08_first"))

    psu = bench["psu1"]
    assert psu.ch[1]["on"] is False and psu.ch[1]["vset"] == 1.0
    events = [json.loads(line) for line in
              host.events_path().read_text(encoding="utf-8").splitlines()]
    assert events[0]["kind"] == "sequence_started" and events[0]["manifest"]["segment_count"] == 10
    assert events[-1] == {"kind": "sequence_finished", "name": "psu1_smtc08_first",
                          "steps": events[-1]["steps"], "error": None}
    from formslab.config import output_dir
    headers = [next(csv.reader(p.open(encoding="utf-8")))
               for p in sorted(output_dir().glob("psu1_smtc08_first_*.csv"))]
    assert "TC01 [K]" in headers[-1] and "PSU1_CH1_V [V]" in headers[-1]


def test_host_refuses_a_plan_whose_scripts_do_not_load(tmp_path, monkeypatch):
    from formslab.host import sequence as host

    monkeypatch.setenv(rscripts.ENV, str(tmp_path))
    plan = tmp_path / "p.forms"
    plan.write_text('rscripts.load = ["rMissing"]\nsequence.operations = [{"hold": 1}]\n',
                    encoding="utf-8")
    with pytest.raises(PlanError, match="rMissing"):
        host.channel(plan_path=plan)
    assert not host.lock_path().exists()
