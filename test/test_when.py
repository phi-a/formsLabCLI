"""`when <condition> then <command>`: a rule beside the plan's steps.

From its line to the end of the run, also while the plan is paused, it sends its
command each time its condition comes to hold, and at once if it holds already. A
refused command stops the run. Here: the words, the checks when a plan is read,
what the rules check makes of a rule, the runner on a fake clock, and a guard
against the simulated chamber.
"""
import json
import sys

import pytest

from formslab import config, rscripts
from formslab.console.cast.castutils import CommandPending
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import sequence as host
from formslab.sequence import parse_plan
from formslab.sequence.plan import _grammar, needed_rscripts, review
from formslab.sequence.runner import SequenceError
from formslab.sequence.spec import Segment

from test_lab_sequence import fake_time, hold, run, script_dir, start, write  # noqa: F401 (fixtures)
from test_loops import paired, loop


# --- the words -----------------------------------------------------------------------

@pytest.mark.parametrize("line, cond, do", [
    ("when platenT > 90 C then hvc platen off",
     {"variable": "platenT", "op": ">", "value": 90.0, "unit": "C"},
     {"verb": "command", "label": "hvc", "request": {"platen_control": False}, "timeout_s": 10.0}),
    ("when chamberP > 1 then log vacuum lost",
     {"variable": "chamberP", "op": ">", "value": 1.0, "unit": None},
     {"verb": "log", "message": "vacuum lost"}),
    ("when TC01 >= 40 C then hvc platen 40 at TC01",
     {"variable": "TC01", "op": ">=", "value": 40.0, "unit": "C"},
     {"verb": "command", "label": "hvc", "request": {"platen": 40.0, "platen_at": "TC01"}, "timeout_s": 10.0}),
])
def test_a_rule_is_a_condition_and_an_action(line, cond, do):
    seg = _grammar(("rLACO", "rSMTC08"))[0].parse(line.split())
    assert seg == Segment("when", {"cond": cond, "do": do})


def test_after_the_condition_then_and_after_then_the_actions():
    g = _grammar(("rLACO",))[0]
    assert [o.text for o in g.complete("when platenT > 90".split())][:3] == ["then", "C", "K"]
    assert "Torr" in [o.text for o in g.complete("when chamberP > 1".split())]
    assert [o.text for o in g.complete("when platenT > 90 C then".split())] == ["log", "hvc"]


# --- read with the plan --------------------------------------------------------------

@pytest.mark.parametrize("text, error", [
    ("load rLACO\nwhen TC01 > 90 C then hvc platen off\n", "TC01 is published by rSMTC08; add it to `load`"),
    ("load rSMTC08\nwhen TC01 > 90 C then hvc platen off\n", "hvc is declared by rLACO; add it to `load`"),
    ("load rLACO\nwhen platenT > 90 C then hvc platen 40 at TC01\n", "TC01 is published by rSMTC08"),
    ("load rLACO\nwhen platenT > 90 C within 5 min then hvc platen off\n", "expected then"),
    ("load rLACO\nwhen chamberP above 1 then hvc platen off\n", "`above` is no longer a word"),
])
def test_what_a_rule_is_refused_for(text, error):
    errors, _ = review(text)
    assert any(error in m for _, m in errors), errors


def test_a_log_rule_may_hold_any_words():
    assert review("load rLACO\nwhen chamberP > 1 then log pressure above 1 Torr # 2\n") == ([], [])


def test_a_block_cannot_hold_a_rule_yet():
    from formslab.sequence.block import BlockError, parse_block as parse
    with pytest.raises(BlockError, match="cannot hold a `when`"):
        parse("block guard\nload rLACO\nwhen platenT > 90 C then hvc platen off\n")


def test_a_restored_load_line_includes_what_a_rule_reads_and_commands():
    assert needed_rscripts("when TC01 > 90 C then hvc platen off\n") == ["rLACO", "rSMTC08"]


SEALED = ("hvc rough close\nhvc gate close\nuntil platenT > 10 C within 1 min\nuntil platenT < 60 C within 1 min\n"
          "until shroudT > 10 C within 1 min\nuntil shroudT < 60 C within 1 min\nhvc vent open\n")


def test_what_a_rule_may_change_is_not_known_after_it():
    assert review("load rLACO\n" + SEALED) == ([], [])
    _, warnings = review("load rLACO\nwhen chamberP < 1 then hvc rough open\n" + SEALED)
    assert warnings == [(9, "Checked when the step runs: Vacuum valve closed. "
                            "To settle it here, add hvc rough close before this step.")]


def test_a_rule_that_only_sets_what_the_plan_sets_changes_nothing():
    assert review("load rLACO\nwhen chamberP < 1 then hvc rough close\n" + SEALED) == ([], [])


def test_a_rule_may_not_command_a_channel_another_routine_drives():
    errors, _ = review("load rLACO rCryoBoard rPSU\nwhen chamberP < 1 then psu1 ch1 on\nhold 1 s\n")
    assert errors and "rCryoBoard, loaded here, drives it" in errors[0][1]


# --- the runner ----------------------------------------------------------------------

# Publishes X from a list, one value a loop, then the last one; takes psu1 requests.
OWNER = '''
from formslab.console.cast.castutils import TakeCommand, ReportResult
CAST_LABELS = ("psu1",)
VALUES = {values}
applied, calls = [], []
def rScript(run):
    calls.append(1)
    run.publish("X", VALUES[min(len(calls), len(VALUES)) - 1])
    request, ids = TakeCommand("psu1")
    if request:
        applied.append(request)
'''

REFUSER = '''
from formslab.console.cast.castutils import TakeCommand, ReportResult
CAST_LABELS = ("psu1",)
RESULT_LABELS = ("psu1",)
def rScript(run):
    run.publish("X", 1)
    request, ids = TakeCommand("psu1")
    if request:
        ReportResult("psu1", ids, False, ["channel locked"])
'''


def rule(text, request=None, op=">", value=0.0, message=None):
    do = ({"verb": "log", "message": message} if message else
          {"verb": "command", "label": "psu1", "request": request, "timeout_s": 10.0})
    return Segment("when", {"cond": {"variable": "X", "op": op, "value": value, "unit": None}, "do": do},
                   label=text)


def owner(script_dir, run, values):
    write(script_dir, "rA", OWNER.format(values=values))
    rscripts.load(run, ["rA"])
    return sys.modules["rScripts.rA"]


ON = {"2": {"on": True}}


def test_a_rule_that_holds_already_acts_at_once_and_only_once(run, fake_time, script_dir):
    a = owner(script_dir, run, [1])
    rscripts.tick(run)                                    # X = 1 before the rule's line
    _, _, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON), hold(3)], fake_time)
    go()
    assert a.applied == [ON]


def test_a_rule_acts_each_time_its_condition_comes_to_hold(run, fake_time, script_dir):
    a = owner(script_dir, run, [0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0])
    _, _, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON), hold(5)], fake_time)
    go()
    assert a.applied == [ON, ON]                          # two rises, not a request per loop


def test_a_loop_does_not_arm_a_rule_twice(run, fake_time, script_dir):
    a = owner(script_dir, run, [1])
    _, _, go = start(run, paired(loop(times=3), rule("when X > 0 then psu1 ch2 on", ON), hold(1),
                                 Segment("end", {})), fake_time)
    go()
    assert a.applied == [ON]


def test_a_refused_command_stops_the_run_and_names_the_rule(run, fake_time, script_dir):
    write(script_dir, "rA", REFUSER)
    rscripts.load(run, ["rA"])
    _, sink, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON), hold(30)], fake_time)
    with pytest.raises(SequenceError, match=r"^when X > 0 then psu1 ch2 on: psu1: .* refused: channel locked"):
        go()
    assert sink.events[-1].error.startswith("when X > 0")


def test_the_run_ends_only_once_a_rules_command_is_answered(run, fake_time, script_dir):
    write(script_dir, "rA", REFUSER)
    rscripts.load(run, ["rA"])
    rscripts.tick(run)
    _, _, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON)], fake_time)   # the last step
    with pytest.raises(SequenceError, match="refused: channel locked"):
        go()


def test_a_step_to_the_same_owner_waits_for_the_rules_command(run, fake_time, script_dir):
    a = owner(script_dir, run, [1])
    rscripts.tick(run)
    off = {"2": {"on": False}}
    step = Segment("command", {"label": "psu1", "request": off, "timeout_s": 10.0})
    _, _, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON), step], fake_time)
    go()
    assert a.applied == [ON, off]                         # two requests, not one merged


def test_a_rule_keeps_watching_while_the_plan_is_paused(run, fake_time, script_dir):
    a = owner(script_dir, run, [0])
    seen = []

    def poll():                                           # paused once, while X comes to hold
        if not seen:
            run.publish("X", 1)
            runner.watch()
            seen.append(CommandPending("psu1"))

    runner, _, go = start(run, [rule("when X > 0 then psu1 ch2 on", ON), hold(1)], fake_time, poll=poll)
    go()
    assert seen == [True] and a.applied[:1] == [ON]       # sent while paused, carried out after


def test_a_log_rule_writes_its_line(run, fake_time, script_dir, capsys):
    owner(script_dir, run, [0, 1])
    logged = []
    run.log = lambda message, **_: logged.append(message)
    _, _, go = start(run, [rule("when X > 0 then log X rose", message="X rose"), hold(1)], fake_time)
    go()
    assert "X rose" in logged


# --- against the simulated chamber ----------------------------------------------------

@pytest.fixture
def chamber(monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    with Simulator() as sim:
        profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0, poll_interval_s=0.5)
        (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
        with sim.state.lock:
            sim.state.mode = "MANUAL"
        yield sim


def test_a_guard_turns_the_platen_off_while_the_plan_holds(chamber, tmp_path):
    plan = tmp_path / "guard.plan"
    plan.write_text("load rLACO\nrecord every 1 s\n"
                    "when platenT > 30 C then hvc platen off\n"
                    "hvc platen rate 0.1\nhvc platen 50\nhvc platen on\nhold 10 s\n", encoding="utf-8")
    assert parse_plan(plan.read_text(encoding="utf-8")).warnings == ()
    host.channel(plan_path=plan)
    s = chamber.state
    assert s.zone_active[1] is False                      # the guard turned it off
    assert 30.0 <= s.temps[2] < 50.0                      # it stopped warming short of 50
