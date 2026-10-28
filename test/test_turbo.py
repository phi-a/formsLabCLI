"""The turbo pump coasts after it is turned off, and the controller does not say
for how long. formsLAB counts it as turning for turbo_spindown_s after it was last
seen on (devices/hvc3500/turbo.py), and the rules on the foreline and the pumps
wait for that, in a plan and live."""
import json
import time

import pytest

from formslab.config import run_dir
from formslab.devices.hvc3500 import turbo
from formslab.rscripts import rules
from formslab.sequence import PlanError, parse_plan
from formslab.sequence.plan import review
from test_rules import blocks, plan


def test_seen_on_then_off_it_turns_for_the_spindown_time():
    t = 1000.0
    assert turbo.spinning(True, 60, now=t) is True
    assert turbo.spinning(False, 60, now=t + 59) is True
    assert turbo.spinning(False, 60, now=t + 60) is False


def test_never_seen_on_it_is_stopped_and_unread_it_is_unknown():
    assert turbo.spinning(False, 60) is False
    assert turbo.spinning(None, 60) is None


def test_the_sighting_outlives_the_process():
    t = time.time()
    turbo.spinning(True, 60, now=t)
    assert json.loads((run_dir() / "turbo_on.json").read_text(encoding="utf-8")) == {"last_on": t}
    turbo.forget()                                      # a host started after this one
    assert turbo.spinning(False, 60, now=t + 30) is True
    assert turbo.spinning(None, 60, now=t + 30) is True  # unread, but seen on half a minute ago


def test_the_status_block_gets_turbo_spinning():
    assert "turbo_spinning" not in turbo.annotate({}, {})          # nothing read, never seen
    assert turbo.annotate({"turbo": True}, {"turbo_spindown_s": 5})["turbo_spinning"] is True
    later = time.time() + 10
    assert turbo.annotate({"turbo": False}, {"turbo_spindown_s": 5}, now=later) == {
        "turbo": False, "turbo_spinning": False}


def test_a_plan_waits_for_the_turbo_to_stop_before_closing_the_foreline():
    errors, warnings = review(plan("hvc turbo off", "hvc foreline close"))
    assert errors == [] and len(warnings) == 1
    assert "Turbo pump stopped" in warnings[0][1] and "until TurboSpinning = false" in warnings[0][1]
    assert review(plan("hvc turbo off", "until TurboSpinning = false", "hvc foreline close")) == ([], [])
    # Settled, it stays settled through other commands, as a valve's state does.
    assert review(plan("hvc turbo off", "until TurboSpinning = false", "hvc gate close",
                       "hvc foreline close")) == ([], [])
    # A wait that may go on without it proves nothing.
    _, warnings = review(plan("hvc turbo off", "until TurboSpinning = false within 10 min or go on",
                              "hvc foreline close"))
    assert len(warnings) == 1
    with pytest.raises(PlanError, match=r"Needs Turbo pump stopped \(line 3 changed it\)"):
        parse_plan(plan("hvc foreline open", "hvc turbo on", "hvc foreline close"))


def test_a_coasting_turbo_refuses_the_foreline_and_the_pump_live():
    coasting = blocks(turbo=False, turbo_spinning=True)
    assert "Needs Turbo pump stopped." in rules.refusal("hvc", {"foreline": "close"}, blocks=coasting)
    assert "Turbo pump stopped" in rules.refusal("hvc", {"pump": "off"}, blocks=coasting)
    assert rules.refusal("hvc", {"foreline": "close"}, blocks=blocks()) is None
