"""A zone held at a thermocouple: `hvc platen 40 at TC01`.

The controller holds the platen at its setpoint with its own sensor; rLACO moves
that setpoint once a minute so the thermocouple reaches the temperature. Here the
grammar, the check when a plan is read, and the hold against the simulator, whose
T5 stands in for a test article that settles short of the platen.
"""
import json
import sys
import time

import pytest

from formslab import config, rscripts
from formslab.console.cast.castutils import ReadStatus, WriteCommand
from formslab.devices.hvc3500.simulator import Simulator
from formslab.rscripts import Scalar, cast
from formslab.sequence.plan import review


# --- the words -----------------------------------------------------------------------

def test_at_names_the_thermocouple_and_the_plain_setpoint_is_unchanged():
    assert cast.request("hvc", "platen 40 at TC01".split()) == {"platen": 40.0, "platen_at": "TC01"}
    assert cast.request("hvc", "shroud -20 at HVC_T14".split()) == {"shroud": -20.0, "shroud_at": "HVC_T14"}
    assert cast.request("hvc", "platen 40".split()) == {"platen": 40.0}


def test_after_a_setpoint_at_is_offered_then_the_thermocouples():
    assert [o.text for o in cast.complete("hvc platen 40".split())] == ["at"]
    names = [o.text for o in cast.complete("hvc platen 40 at".split())]
    assert names[:2] == ["TC01", "TC02"] and "HVC_T5" in names
    assert "platenT" not in names and "chamberP" not in names       # temperatures of sensors only


def test_an_unknown_thermocouple_is_refused_with_what_fits():
    with pytest.raises(cast.GrammarError, match="did you mean"):
        cast.request("hvc", "platen 40 at TC1".split())


# --- read with the plan --------------------------------------------------------------

def test_a_plan_must_load_the_routine_that_reads_the_thermocouple():
    errors, _ = review("load rLACO\nhvc platen 40 at TC01\n")
    assert errors == [(2, "TC01 is published by rSMTC08; add it to `load`")]
    assert review("load rLACO rSMTC08\nhvc platen 40 at TC01\n")[0] == []
    assert review("load rLACO\nhvc platen 40 at HVC_T5\n")[0] == []   # the controller's own


# --- one step of the hold ------------------------------------------------------------

class _Run:
    def __init__(self, **values):
        self.values, self.logged = values, []

    def variable(self, name):
        return self.values.get(name)

    def log(self, message, level="INFO", component=None):
        self.logged.append((level, message))


class _Laco:
    class profile:
        zones = {"platen": 1, "shroud": 2}
        limits = {"hold_at_gain_per_min": 0.5, "hold_at_band_c": 15.0}

    def __init__(self):
        self.sent = []

    def apply(self, request):
        self.sent.append(request)
        return [("INFO", f"setpoint -> {request}")]


@pytest.fixture
def rlaco():
    path = rscripts.find("rLACO")
    import importlib.util
    spec = importlib.util.spec_from_file_location("rLACO_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.rg.cast = {"thermal_control": True}
    return module


def _held(rlaco, laco, run, target=40.0):
    rlaco._take_holds(run, laco, {"platen": target, "platen_at": "TC01"})
    rlaco.rg.holds["platen"]["next"] = 0.0


def _tc(celsius, age_s=0.0):
    s = Scalar("TC01", celsius + 273.15, "K")
    s.updated = time.monotonic() - age_s
    return s


def test_each_step_moves_the_setpoint_by_gain_times_the_difference(rlaco):
    laco, run = _Laco(), _Run(TC01=_tc(30.0))
    _held(rlaco, laco, run)
    rlaco._hold(run, laco)
    assert laco.sent == [{"platen": 45.0}]                 # 40 + 0.5 x (40 - 30)


def test_the_setpoint_never_leaves_the_band(rlaco):
    laco, run = _Laco(), _Run(TC01=_tc(-100.0))            # a thermocouple off the article
    _held(rlaco, laco, run)
    for _ in range(5):
        rlaco.rg.holds["platen"]["next"] = 0.0
        rlaco._hold(run, laco)
    assert max(r["platen"] for r in laco.sent) == 55.0


def test_an_old_or_missing_reading_or_control_off_stops_the_moves(rlaco):
    for run, on in ((_Run(TC01=_tc(30.0, age_s=300)), True), (_Run(), True), (_Run(TC01=_tc(30.0)), False)):
        laco = _Laco()
        rlaco.rg.cast = {"thermal_control": on}
        _held(rlaco, laco, run)
        rlaco._hold(run, laco)
        assert laco.sent == [] and rlaco.rg.holds["platen"]["waiting"]
        assert "waiting" in rlaco._hold_status(laco)["platen held at"]


@pytest.mark.parametrize("request_", [{"platen": 30.0}, {"platen_control": False}])
def test_a_plain_setpoint_or_control_off_ends_the_hold(rlaco, request_):
    laco, run = _Laco(), _Run()
    _held(rlaco, laco, run)
    assert rlaco._take_holds(run, laco, request_) == request_
    assert "platen" not in rlaco.rg.holds


# --- against the simulator -----------------------------------------------------------

@pytest.fixture
def chamber(monkeypatch):
    monkeypatch.delenv(rscripts.ENV, raising=False)
    rscripts.disabled.clear()
    with Simulator() as sim:
        profile = json.loads(config.default_path("tvac_bench.json").read_text(encoding="utf-8"))
        profile["connection"].update(host=sim.host, port=sim.port, timeout_s=2.0, poll_interval_s=0.0)
        # a step every 0.2 s instead of a minute: the gain per minute scaled to match
        profile["limits"].update(hold_at_gain_per_min=30.0)
        (config.config_dir() / "tvac_bench.json").write_text(json.dumps(profile), encoding="utf-8")
        yield sim


def test_the_article_settles_at_the_temperature_and_the_platen_above_it(chamber, tmp_path):
    run = rscripts.Run(name="T", record_dir=tmp_path)
    rscripts.load(run, ["rLACO"])
    sys.modules["rScripts.rLACO"].HOLD_EVERY = 0.2
    WriteCommand({"platen": 40.0, "platen_at": "HVC_T5", "platen_control": True}, "hvc")
    article = lambda: chamber.state.temps[5]
    settled_since, deadline = None, time.monotonic() + 60
    while time.monotonic() < deadline:
        rscripts.tick(run)
        if abs(article() - 40.0) < 0.5:
            settled_since = settled_since or time.monotonic()
            if time.monotonic() - settled_since > 3:
                break
        else:
            settled_since = None
        time.sleep(0.02)
    assert abs(article() - 40.0) < 0.5, article()
    assert 43.0 < chamber.state.zone_setpoint[1] <= 55.0     # 0.8 x platen + 4.4 = 40 at 44.5
    assert ReadStatus("hvc")["platen held at"] == "HVC_T5 40 °C"
    rscripts.shutdown(run)


def test_a_restored_load_line_includes_the_routine_that_reads_the_thermocouple():
    from formslab.sequence.plan import needed_rscripts
    assert needed_rscripts("hvc platen 40 at TC01\n") == ["rLACO", "rSMTC08"]


def test_the_setpoint_box_keeps_its_help_and_at_carries_the_holds():
    box = next(o for o in cast.complete("hvc platen".split()) if o.kind == "number")
    assert box.help == "Set the platen temperature"
    assert [o.help for o in cast.complete("hvc platen 40".split())] == ["Hold a thermocouple at a temperature"]
