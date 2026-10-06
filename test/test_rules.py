"""Declared prerequisites (formslab.rscripts.rules): the chamber's rules, read
from a plan (errors and warnings) and checked live against a status block."""
import time

import pytest

from formslab.devices.hvc3500.rules import _safe, laco_effects
from formslab.rscripts import rules
from formslab.sequence import PlanError, find_plan, parse_plan
from formslab.sequence.plan import review

GUARDS = "".join(f"until {z}T {s} {v} C within 1 min\n"
                 for z in ("platen", "shroud") for s, v in ((">=", 10), ("<=", 60)))


def plan(*steps, load="rLACO"):
    return f"load {load}\n" + "\n".join(steps) + "\n"


# --- read from a plan ----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["laco_vent", "laco_pumpdown", "psu1_smtc08_first", "tvac"])
def test_the_shipped_plans_have_no_rule_errors(name):
    errors, _ = review(find_plan(name).read_text(encoding="utf-8"))
    assert errors == []


def test_the_vent_plans_guards_establish_the_vent_window():
    _, warnings = review(find_plan("laco_vent").read_text(encoding="utf-8"))
    [(_, message)] = warnings
    assert message == ("Checked when the step runs: Vacuum valve closed and Gate valve closed. To settle it "
                       "here, add hvc rough close and hvc gate close before this step.")


def test_a_vent_with_nothing_established_is_a_warning_and_the_plan_reads():
    p = parse_plan(plan("hvc vent open"))
    [(line, message)] = p.warnings
    assert line == 2 and "Platen at least 10 \u00b0C" in message and "until platenT >= 10 C" in message


def test_a_plan_that_breaks_a_rule_cannot_run():
    with pytest.raises(PlanError) as e:
        parse_plan(plan("hvc rough open", "hvc vent open"))
    assert e.value.errors == [(3, e.value.errors[0][1])]
    assert e.value.errors[0][1].startswith("Needs Vacuum valve closed (line 2 changed it). Air may only come in")


def test_what_the_plan_establishes_is_not_warned_about():
    assert parse_plan(plan("hvc rough close", "hvc gate close", GUARDS + "hvc vent open")).warnings == ()


def test_a_guard_lasts_until_the_next_hold_or_command():
    p = parse_plan(plan("hvc rough close", "hvc gate close", GUARDS + "hold 1 s", "hvc vent open"))
    assert "Platen at least 10 \u00b0C" in p.warnings[0][1]


def test_a_looser_guard_does_not_establish_a_tighter_limit():
    loose = GUARDS.replace("<= 60", "<= 70")
    p = parse_plan(plan("hvc rough close", "hvc gate close", loose + "hvc vent open"))
    assert "Platen at most 60 \u00b0C" in p.warnings[0][1] and "Platen at least" not in p.warnings[0][1]


def test_a_guard_in_kelvin_counts():
    kelvin = GUARDS.replace("10 C", "283.15 K").replace("60 C", "333.15 K")
    assert parse_plan(plan("hvc rough close", "hvc gate close", kelvin + "hvc vent open")).warnings == ()


def test_stop_and_cycle_operations():
    stopped = parse_plan(plan("hvc stop", "hvc turbo off", "hvc foreline close", "hvc pump off"))
    assert stopped.warnings == ()
    with pytest.raises(PlanError, match="Needs Vacuum valve closed"):
        parse_plan(plan("hvc rough open", "hvc foreline open"))
    after_cycle = parse_plan(plan("hvc rough open", "hvc vent2atm", "hvc foreline open"))
    assert "Vacuum valve closed" in after_cycle.warnings[-1][1]  # unknown again, not broken


def test_the_gate_needs_the_crossover_pressure():
    p = parse_plan(plan("hvc rough close", "hvc foreline open", "hvc turbo on",
                        "until chamberP < 0.01 within 1 h", "hvc gate open"))
    assert p.warnings == ()
    p = parse_plan(plan("hvc rough close", "hvc foreline open", "hvc turbo on", "hvc gate open"))
    assert "Chamber pressure at most 0.01 Torr" in p.warnings[0][1] and "until chamberP <= 0.01" in p.warnings[0][1]


def test_a_wait_that_may_go_on_proves_nothing():
    p = parse_plan(plan("hvc rough close", "hvc foreline open", "hvc turbo on",
                        "until chamberP < 0.01 within 1 h or go on", "hvc gate open"))
    assert "Chamber pressure at most 0.01 Torr" in p.warnings[0][1]


def test_an_owned_supply_channel_is_an_error_while_its_owner_is_loaded():
    with pytest.raises(PlanError, match="psu1 ch1 feeds the cryocooler board, and rCryoBoard, loaded here"):
        parse_plan(plan("psu1 ch1 off", load="rPSU rCryoBoard"))
    parse_plan(plan("psu1 ch1 off", load="rPSU"))                 # free while rCryoBoard is not
    parse_plan(plan("psu1 ch2 off", load="rPSU rCryoBoard"))      # another channel


def test_effects():
    assert laco_effects({"stop_pumping": True}) == {"rough": False, "pump": False}
    assert laco_effects({"rough": "open", "pump": "on"}) == {"rough": True, "pump": True}
    assert laco_effects({"purge": True}) is None and laco_effects({"recipe_run": "start"}) is None
    assert laco_effects({"platen": 20.0}) == {}


# --- checked live ----------------------------------------------------------------------------

SEALED = {"connected": True, "fault_severity": "N", "pressure": 4.4, "platen C": 20.0, "shroud C": 21.0,
          "rough": False, "vent": False, "fill": False, "foreline": False, "gate": False,
          "pump": True, "turbo": False}


def blocks(**status):
    return {"hvc": {"timestamp": time.time(), "status": {**SEALED, **status}}}


def test_a_vent_inside_the_window_is_allowed_live():
    assert rules.refusal("hvc", {"vent": "open"}, blocks=blocks()) is None


def test_a_vent_outside_the_window_is_refused_with_the_reason():
    why = rules.refusal("hvc", {"vent": "open"}, blocks=blocks(**{"platen C": 85.0}))
    assert why.startswith("Needs Platen at most 60 \u00b0C. Air may only come in")


def test_an_old_report_proves_nothing():
    old = {"hvc": {"timestamp": time.time() - 60, "status": SEALED}}
    problems = rules.assess("hvc", {"vent": "open"}, blocks=old)     # the vent rule, and the fault rule
    why = problems[0][0]
    assert "Vacuum valve closed (unknown)" in why and "Nothing heard from hvc for 60 s." in why
    assert not any(definite for _, definite in problems)                # a plan step would wait


def test_a_fault_allows_only_what_makes_the_chamber_safer():
    faulted = blocks(fault_severity="F")
    assert "severity F" in rules.refusal("hvc", {"platen": 20.0}, blocks=faulted)
    assert "severity F" in rules.refusal("hvc", {"pump": "on"}, blocks=faulted)
    for safe in ({"vent": "close"}, {"stop_pumping": True}, {"platen_control": False},
                 {"reset": True}, {"abort": True}, {"close_all": True}):
        assert _safe(safe) and rules.refusal("hvc", safe, blocks=faulted) is None, safe
    assert rules.refusal("hvc", {"platen": 20.0}, blocks=blocks(fault_severity="W")) is None


def test_the_cryo_output_needs_its_supply():
    low = {"cryo": {"timestamp": time.time(), "status": {"CCVINM": 12.0}}}
    assert "Needs Board supply at least 20 V." in rules.refusal("cryo", {"enabled": True}, blocks=low)
    ok = {"cryo": {"timestamp": time.time(), "status": {"CCVINM": 24.0}}}
    assert rules.refusal("cryo", {"enabled": True}, blocks=ok) is None
    assert rules.refusal("cryo", {"enabled": False}, blocks=low) is None


def test_an_owned_channel_is_refused_while_its_owner_reports():
    running = {"cryo": {"timestamp": time.time(), "status": {}}}
    assert "rCryoBoard drives it" in rules.refusal("psu1", {"1": {"on": False}}, blocks=running)
    assert rules.refusal("psu1", {"2": {"on": False}}, blocks=running) is None
    gone = {"cryo": {"timestamp": time.time() - 600, "status": {}}}
    assert rules.refusal("psu1", {"1": {"on": False}}, blocks=gone) is None


def test_the_turbo_keeps_its_backing():
    with pytest.raises(PlanError, match="Needs Turbo pump off"):
        parse_plan(plan("hvc foreline open", "hvc turbo on", "hvc foreline close"))
    with pytest.raises(PlanError, match="Needs Turbo pump off"):
        parse_plan(plan("hvc turbo on", "hvc rough open"))
    spinning = blocks(turbo=True, foreline=True)
    assert "Needs Turbo pump off." in rules.refusal("hvc", {"foreline": "close"}, blocks=spinning)
    assert "Turbo pump off" in rules.refusal("hvc", {"rough": "open"}, blocks=spinning)
    assert rules.refusal("hvc", {"foreline": "close"}, blocks=blocks()) is None


def test_reasons_are_whole_sentences():
    from formslab.devices.hvc3500 import load_profile
    from formslab.devices.hvc3500.rules import laco_rules
    for rule in laco_rules(load_profile()):
        assert rule.why[0].isupper() and rule.why.endswith(".") and "`" not in rule.why, rule.why


def test_roughing_is_refused_once_the_chamber_is_below_the_crossover():
    """Opening the roughing line to a chamber already at high vacuum can let
    roughing-pump oil flow back into it."""
    sealed = dict(vent=False, fill=False, foreline=False, gate=False, turbo=False)
    why = rules.refusal("hvc", {"rough": "open"}, blocks=blocks(pressure=0.001, **sealed))
    assert why.startswith("Needs Chamber pressure at least 0.01 Torr. Opening the roughing line")
    assert rules.refusal("hvc", {"rough": "open"}, blocks=blocks(pressure=743.0, **sealed)) is None
    p = parse_plan(plan("hvc turbo off", "hvc vent close", "hvc fill close", "hvc foreline close",
                        "hvc gate close", "until chamberP > 0.01 within 1 min", "hvc rough open"))
    assert p.warnings == ()
