"""The rScripts runtime: loader, realtime gates, the FORMS-free handle, and
rLACO driving the HVC-3500 simulator through a lab-mode host loop.

Nothing here imports FORMS. That is the point: chamber control has to run on a
machine with only formsLabCLI installed.
"""
import csv
import json
import sys

import pytest

from formslab import config, rscripts
from formslab.console.cast.castutils import ReadStatus, WriteCommand
from formslab.devices.hvc3500.simulator import Simulator
from formslab.rscripts import control, loader


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


# --- loader ------------------------------------------------------------------

COUNTER = "calls = []\ndef rScript(run):\n    calls.append(1)\n"


def test_load_then_tick_runs_each_script_once_per_tick(run, script_dir):
    write(script_dir, "rA", COUNTER)
    write(script_dir, "rB", COUNTER)

    assert rscripts.load(run, ["rA", "rB.py"]) == ["rA", "rB"]
    rscripts.tick(run)
    rscripts.tick(run)

    assert sys.modules["rScripts.rA"].calls == [1, 1]
    assert sys.modules["rScripts.rB"].calls == [1, 1]


def test_env_dir_wins_over_the_checkout(run, script_dir):
    write(script_dir, "rLACO", "def rScript(run): pass\n")
    assert rscripts.find("rLACO") == script_dir / "rLACO.py"


def test_static_disable_is_not_even_imported(run, script_dir, capsys):
    write(script_dir, "rOff", "enable = False\nraise RuntimeError('imported')\n")
    assert rscripts.load(run, ["rOff"]) == []
    assert "enable = False" in capsys.readouterr().out


def test_missing_and_entryless_scripts_are_reported(run, script_dir, capsys):
    write(script_dir, "rNoEntry", "x = 1\n")
    assert rscripts.load(run, ["rNoEntry", "rNowhere"]) == []
    out = capsys.readouterr().out
    assert "no rScript(run)" in out and "rNowhere: not found" in out


def test_runtime_disable_stops_a_loaded_script(run, script_dir):
    write(script_dir, "rA", COUNTER)
    rscripts.load(run, ["rA"])
    rscripts.disabled.add("rA")
    rscripts.tick(run)
    rscripts.disabled.clear()
    assert sys.modules["rScripts.rA"].calls == []


def test_a_failing_script_is_logged_once_per_error_then_recovery(run, script_dir, capsys):
    write(script_dir, "rFlaky", "fail = True\ndef rScript(run):\n"
                                "    if fail: raise OSError('link down')\n")
    rscripts.load(run, ["rFlaky"])
    for _ in range(5):
        rscripts.tick(run)
    sys.modules["rScripts.rFlaky"].fail = False
    rscripts.tick(run)

    out = capsys.readouterr().out
    assert out.count("[ERROR] [rScript] rFlaky: OSError: link down") == 1
    assert "rFlaky: recovered" in out


# --- realtime gates ----------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(control, "clock", lambda: now[0])
    return now


def test_initialize_is_true_once(run):
    assert rscripts.RScriptControl(run, "s").initialize() is True
    assert rscripts.RScriptControl(run, "s").initialize() is False


def test_tick_runs_now_then_every_interval(run, clock):
    ran = []
    for t in (0, 1, 4.9, 5, 6, 10):
        clock[0] = 100.0 + t
        if not rscripts.RScriptControl(run, "s").tick(seconds=5):
            ran.append(t)
    assert ran == [0, 5, 10]


def test_tick_by_iterations(run):
    ran = [i for i in range(7) if not rscripts.RScriptControl(run, "s").tick(iterations=3)]
    assert ran == [0, 3, 6]


def test_hold_waits_then_runs_once_and_rearms(run, clock):
    ran = []
    for t in (0, 2, 5, 6, 10, 11):
        clock[0] = 100.0 + t
        if not rscripts.RScriptControl(run, "s").hold(seconds=5):
            ran.append(t)
    assert ran == [5, 11]


def test_gates_are_per_script_and_per_handle(run, clock, tmp_path):
    other = rscripts.Run(record_dir=tmp_path)
    assert not rscripts.RScriptControl(run, "a").tick(seconds=5)
    assert not rscripts.RScriptControl(run, "b").tick(seconds=5)
    assert not rscripts.RScriptControl(other, "a").tick(seconds=5)


# --- Run ----------------------------------------------------------------

def test_publish_creates_then_updates(run):
    a = run.publish("chamberP", 760.0, "Torr")
    assert run.publish("chamberP", 1e-3, "Torr") is a
    assert run.get("chamberP") == 1e-3 and run.variable("chamberP").unit == "Torr"
    assert run.get("missing") is None and run.get("missing", 0) == 0
    assert run.variable("missing") is None


def test_publish_refuses_a_silent_unit_change(run):
    run.publish("chamberP", 1.0, "Torr")
    with pytest.raises(ValueError, match="chamberP is in Torr"):
        run.publish("chamberP", 133.0, "Pa")
    run.publish("chamberP", 2.0)                # no unit given: the value only


def test_names_keep_creation_order(run):
    for n in ("b", "a", "c"):
        run.publish(n, 1)
    assert run.names() == ["b", "a", "c"]


def test_record_writes_every_variable_at_its_cadence(run, clock, monkeypatch):
    run.publish("platenT", 295.0, "K")
    run.record(value=30, unit="seconds")
    run.record()                        # due immediately
    run.record()                        # not due yet
    run.record(force=True)

    rows = list(csv.reader(run.record.path.open(encoding="utf-8")))
    assert rows[0] == ["index", "timestamp", "platenT [K]"]
    assert [r[0] for r in rows[1:]] == ["0", "1"]
    assert run.record.path.name.startswith("T_")


def test_a_new_variable_starts_a_new_file(run):
    run.publish("a", 1)
    run.record(force=True)
    first = run.record.path
    run.publish("b", 2)
    run.record(force=True)
    assert run.record.path != first and run.record.path.stem.endswith("_1")


def test_recording_off_writes_nothing(run):
    run.publish("a", 1)
    run.recording = False
    run.record(force=True)
    assert run.record.path is None


# --- rLACO against the simulator ---------------------------------------------

@pytest.fixture
def chamber(monkeypatch):
    """A simulated HVC-3500 and a bench profile pointing at it."""
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    with Simulator() as sim:
        profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0,
                                     poll_interval_s=0.0)
        (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
        yield sim


def test_rlaco_reads_the_chamber_without_run(run, chamber):
    assert rscripts.load(run, ["rLACO"]) == ["rLACO"]
    rscripts.tick(run)

    status = ReadStatus("hvc")
    assert status["connected"] is True
    assert status["pressure"] == pytest.approx(760.0)
    assert status["faults"] == "none"
    assert run.get("chamberP") == pytest.approx(760.0)
    assert run.variable("chamberP").unit == "Torr"
    assert run.get("HVC_platen_ctrl") == pytest.approx(22.2 + 273.15)


def test_rlaco_applies_a_cast_setpoint_request(run, chamber):
    rscripts.load(run, ["rLACO"])
    WriteCommand({"platen": 25.0}, "hvc")
    rscripts.tick(run)

    assert chamber.state.zone_setpoint[1] == pytest.approx(25.0)
    assert run.get("target_platen") == pytest.approx(25.0 + 273.15)


def _tvac_plan(tmp_path, monkeypatch, scripts):
    """A bench's own tvac.plan (searched before the checkout's), so a test
    never loads rPSU against the real supply."""
    d = tmp_path / "plans"
    d.mkdir(exist_ok=True)
    (d / "tvac.plan").write_text(
        'mission.name = "tvac"\n'
        f"rscripts.load = {scripts!r}\n"
        "recording.interval = 30\n"
        'sequence.operations = [{"hold": "until end"}]\n', encoding="utf-8")
    monkeypatch.setenv("FORMSLAB_PLANS_DIR", str(d))
    from formslab.sequence import find_plan
    return find_plan("tvac")


def _operator(after_s, *actions):
    """Run `actions` and then a ctrl `end` from another thread, as an operator
    at the console would while the host runs."""
    import threading

    from formslab.console.ctrl.ctrlutils import WriteCommand

    def act():
        for a in actions:
            a()
        WriteCommand("end")

    timer = threading.Timer(after_s, act)
    timer.start()
    return timer


def test_tvac_plan_runs_until_end(chamber, tmp_path, monkeypatch):
    """`run tvac` end to end, minus the subprocess: the host loads the plan's
    scripts, ticks them in real time, records, stops on ctrl `end`, releases
    its lock."""
    from formslab.host import sequence

    plan = _tvac_plan(tmp_path, monkeypatch, ["rLACO"])
    timer = _operator(4.0)            # the first full chamber read takes ~2 s
    sequence.channel(plan)
    timer.join()

    assert ReadStatus("hvc")["connected"] is True
    assert list(config.output_dir().glob("tvac_*.csv"))
    assert not sequence.lock_path().exists()
    events = [json.loads(line) for line in
              sequence.events_path().read_text(encoding="utf-8").splitlines()]
    assert events[-1]["kind"] == "sequence_finished"
    assert events[-1]["ended"] == "operator" and events[-1]["error"] is None


def test_a_cast_command_reaches_the_chamber_through_the_host(chamber, tmp_path, monkeypatch):
    """The whole path: cast tab words -> rLACO's cast_request -> CAST -> rLACO
    in the host -> LACO.apply -> the controller."""
    from formslab.console.cast import castcli
    from formslab.host import sequence

    plan = _tvac_plan(tmp_path, monkeypatch, ["rLACO"])
    typed = []      # typed while the host runs: it clears stale requests at start
    timer = _operator(0.5, lambda: typed.append(castcli.execute_command(["hvc", "platen", "35"])),
                      lambda: __import__("time").sleep(1.5))
    sequence.channel(plan)
    timer.join()
    assert "platen" in typed[0].content.plain
    assert chamber.state.zone_setpoint[1] == pytest.approx(35.0)


def test_ctrl_end_stops_the_run_and_runs_rshutdown(script_dir, tmp_path, monkeypatch):
    """`end` from the console reaches the host through ctrl, and the run's
    rShutdown still happens."""
    from formslab.host import sequence

    write(script_dir, "rStop", "def rScript(run): pass\n"
                               "def rShutdown(run):\n    open(__file__ + '.done', 'w').close()\n")
    plan = _tvac_plan(tmp_path, monkeypatch, ["rStop"])
    timer = _operator(0.5)
    sequence.channel(plan)                         # would run forever without `end`
    timer.join()
    assert (script_dir / "rStop.py.done").exists()
