"""Blocks: reusable groups of plan steps, called by name, nested, expanded into
their steps when a plan is read (sequence/block.py, sequence/plan.py)."""
import json
from dataclasses import replace

import pytest

from formslab import config, rscripts
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import sequence
from formslab.sequence import PlanError, find_plan, load_plan, parse_plan
from formslab.sequence.block import BlockError, parse_block
from formslab.sequence.plan import ENV, describe_step, needed_rscripts, review


# --- in the GUI: a third kind of file beside plans and orbits -------------------------------------

def test_the_editor_lists_reads_and_saves_blocks_but_never_runs_them():
    from formslab.gui import api, plans
    listed = {p["name"]: p for p in api.list_plans()}
    assert listed["pumpdown"]["kind"] == "block" and "error" not in listed["pumpdown"]
    r = plans.read("pumpdown")
    assert r["kind"] == "block" and r["errors"] == [] and r["warnings"][0]["line"] == 16
    saved = plans.save("seal", "block seal\nload rLACO\nhvc gate close\n", None, True, "block")
    assert saved["kind"] == "block" and saved["errors"] == []
    assert plans.problems("block Seal\nload rLACO\nhvc stop\n", "block")["errors"][0]["line"] == 1
    with pytest.raises(api.ApiError):
        api.start_run("pumpdown")                                  # only plans run


def test_a_block_file_draws_and_completes_with_its_inputs():
    from formslab.gui import api
    text = find_plan("pump_soak_vent").with_name("pumpdown.block").read_text(encoding="utf-8")
    lines = api.plan_tokens(text, "block")
    assert lines[7] == [{"text": "block", "role": "verb"}, {"text": "pumpdown", "role": "name"},
                        {"text": "to", "role": "kw"}, {"text": "pressure", "role": "value"}]
    assert lines[16][3] == {"text": "{pressure}", "role": "value"}
    r = api.plan_line(["rLACO"], ["until", "chamberP", "<", "{pressure}"], "block")
    assert r["error"] is None and "within" in [o["text"] for o in r["positions"][4]]
    card = api.describe(text=text, line=8, kind="block")["cards"][0]
    assert card["help"] == "Rough the chamber down to a pressure, then seal it" and len(card["steps"]) == 8


@pytest.fixture
def blocks_dir(tmp_path, monkeypatch):
    """A folder searched first for plans and blocks; write blocks into it."""
    monkeypatch.setenv(ENV, str(tmp_path))

    def write(name, text):
        (tmp_path / f"{name}.block").write_text(text, encoding="utf-8")
    return write


def plan(*steps, load="rLACO"):
    return f"load {load}\n" + "\n".join(steps) + "\n"


# --- reading a block -----------------------------------------------------------------------

def test_a_block_reads_its_description_inputs_and_steps():
    b = parse_block(find_plan("pump_soak_vent").with_name("pumpdown.block").read_text(encoding="utf-8"))
    assert b.name == "pumpdown" and b.inputs == ("pressure",) and b.scripts == ("rLACO",)
    assert b.summary == "Rough the chamber down to a pressure, then seal it"
    assert b.details.startswith("Closes the vent, fill and gate valves") and b.details.endswith(".")
    assert b.body({"pressure": 5.0})[6][1] == ["until", "chamberP", "<", "5", "within", "20", "min"]


@pytest.mark.parametrize("text, message", [
    ("load rLACO\nhvc stop\n", "a block starts with `block <name>"),
    ("block Pump\nload rLACO\nhvc stop\n", "lowercase word"),
    ("block hold\nload rLACO\nhvc stop\n", "is a step word"),
    ("block b\nhvc stop\n", "names the rScripts it needs"),
    ("block b\nload rLACO\nrecord every 2 s\nhvc stop\n", "has no `record`"),
    ("block b\nload rLACO\nhold until end\n", "cannot `hold until end`"),
    ("block b to <x:number>\nload rLACO\nhvc stop\n", "input 'x' is not used"),
    ("block b\nload rLACO\nhvc platen {t}\n", "{t} is not an input"),
    ("block b to <x:text>\nload rLACO\nlog {x}\n", "a block's inputs are numbers"),
])
def test_a_broken_block_says_why(text, message):
    with pytest.raises(BlockError, match=message.replace("{", r"\{").replace("}", r"\}")):
        parse_block(text)


# --- calling one -----------------------------------------------------------------------------

def test_a_call_expands_into_the_blocks_steps_with_its_inputs():
    p = load_plan(find_plan("pump_soak_vent"))
    labels = [s.label for s in p.sequence.segments]
    assert labels[:2] == ["pumpdown > hvc vent close", "pumpdown > hvc fill close"]
    until = next(s for s in p.sequence.segments if s.verb == "until")
    assert until.params["value"] == 5 and until.origin == (("pumpdown", 17),)
    assert "hold 30 min" in labels and labels[-1] == "log pumped down, soaked and vented"
    assert [s.label for s in p.sequence.segments if s.label.startswith("vent >")][-1] == \
        "vent > until chamberP > 700 within 10 min"


def test_an_input_out_of_range_is_refused_at_the_call():
    with pytest.raises(PlanError, match="900 is outside 0.01..760 Torr"):
        parse_plan(plan("pumpdown to 900 Torr"))


def test_a_step_the_input_breaks_is_reported_at_the_call_with_the_block_and_line(blocks_dir):
    blocks_dir("hot", "# Heat the platen\nblock hot to <t:number 0..1000 C>\nload rLACO\n\nhvc platen {t}\n")
    with pytest.raises(PlanError) as e:
        parse_plan(plan("hvc stop", "hot to 900 C"))
    assert e.value.errors == [(3, "In hot (line 5): hvc platen: 900 is outside -180..200 C")]


def test_blocks_nest(blocks_dir):
    blocks_dir("seal", "block seal\nload rLACO\nhvc gate close\nhvc rough close\n")
    blocks_dir("safe", "block safe\nload rLACO\nseal\nhvc vent close\n")
    p = parse_plan(plan("safe"))
    assert [s.label for s in p.sequence.segments] == ["safe > seal > hvc gate close", "safe > seal > hvc rough close",
                                                      "safe > hvc vent close"]
    assert p.sequence.segments[0].origin == (("safe", 3), ("seal", 3))
    assert p.sequence.to_manifest()["segments"][0]["origin"] == "safe line 3 > seal line 3"


def test_a_block_that_calls_itself_is_an_error(blocks_dir):
    blocks_dir("ping", "block ping\nload rLACO\npong\n")
    blocks_dir("pong", "block pong\nload rLACO\nping\n")
    with pytest.raises(PlanError, match="calls itself: ping > pong > ping"):
        parse_plan(plan("ping"))


def test_blocks_nest_eight_deep_and_no_deeper(blocks_dir):
    blocks_dir("b0", "block b0\nload rLACO\nhvc stop\n")
    for k in range(1, 10):
        blocks_dir(f"b{k}", f"block b{k}\nload rLACO\nb{k - 1}\n")
    parse_plan(plan("b7"))                                         # 8 blocks deep
    with pytest.raises(PlanError, match="nest more than 8 deep"):
        parse_plan(plan("b8"))


def test_the_plan_must_load_what_the_block_needs():
    with pytest.raises(PlanError, match="pumpdown needs rLACO; add it to `load`"):
        parse_plan(plan("pumpdown to 5 Torr", load="rSMTC08"))


def test_a_broken_or_clashing_block_is_refused_at_its_call(blocks_dir):
    blocks_dir("stuck", "block stuck\nload rLACO\nhold until end\n")
    with pytest.raises(PlanError, match="block stuck cannot be used: a block cannot `hold until end`"):
        parse_plan(plan("stuck"))
    blocks_dir("hvc", "block hvc\nload rLACO\nhvc stop\n")
    parse_plan(plan("hvc stop"))                                   # the instrument still works
    from formslab.rscripts import cast
    from formslab.sequence.block import available
    assert "block hvc has an instrument's name" in available(cast.owners()[0])[1]["hvc"]


# --- rules, help and needs see through a call -------------------------------------------------

def test_what_a_block_establishes_holds_after_it():
    p = parse_plan(plan("pumpdown to 5 Torr", "hvc vent open"))
    vent = [m for n, m in p.warnings if n == 3]
    assert vent and "Vacuum valve closed" not in vent[0] and "Gate valve closed" not in vent[0]
    assert "Platen at least 10" in vent[0]


def test_a_finding_inside_a_block_names_where():
    _, warnings = review(plan("pumpdown to 5 Torr"))
    assert warnings and all(m.startswith("In pumpdown line 16: ") for _, m in warnings)


def test_the_vent_block_establishes_its_own_prerequisites():
    assert review(plan("vent within 30 min"))[1] == []


def test_a_change_inside_a_block_is_named_where_it_breaks_a_rule(blocks_dir):
    blocks_dir("rough", "block rough\nload rLACO\nhvc rough open\n")
    with pytest.raises(PlanError) as e:
        parse_plan(plan("rough", "hvc vent open"))
    assert "Needs Vacuum valve closed (rough line 3 at line 2 changed it)" in e.value.errors[0][1]


def test_needed_rscripts_follow_calls():
    assert needed_rscripts("pumpdown to 5 Torr\nvent within 30 min\n") == ["rLACO"]


def test_the_help_card_for_a_call_lists_its_steps_and_every_prerequisite():
    info = describe_step(plan("pumpdown to 5 Torr"), 2)
    [card] = info["cards"]
    assert card["help"] == "Rough the chamber down to a pressure, then seal it"
    assert card["steps"][0] == "hvc vent close" and "until chamberP < {pressure} within 20 min" in card["steps"]
    texts = {c["text"] for r in info["rules"] for c in r["conditions"]}
    assert {"Gate valve closed", "Turbo pump off", "Chamber pressure at least 0.01 Torr"} <= texts


# --- end to end ------------------------------------------------------------------------------

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
            sim.state.pressure = 743.0
        yield sim


def test_a_plan_of_blocks_pumps_down_soaks_and_vents(chamber, monkeypatch):
    def quick(path):                                   # holds cut to 1.5 s
        p = load_plan(path)
        segs = tuple(replace(s, params={**s.params, "seconds": 1.5}) if s.verb == "hold" else s
                     for s in p.sequence.segments)
        return replace(p, sequence=replace(p.sequence, segments=segs))
    monkeypatch.setattr(sequence, "load_plan", quick)
    sequence.channel(plan_path=find_plan("pump_soak_vent"))
    s = chamber.state
    assert s.pressure >= 700 and s.devices["OV"] is True             # vented, valve left open
    assert not s.devices["OR"] and not s.devices["OP"]                # pumpdown sealed it


@pytest.mark.parametrize("name", ["eclipse", "sunrise"])
def test_a_block_that_waits_on_an_orbit_leaves_choosing_it_to_its_caller(name):
    """Inside a block, a wait on InUmbra before any `orbit follow` is the calling plan's
    to settle: a warning on the block, not an error that lists it as unusable."""
    from pathlib import Path
    from formslab.gui import api
    from formslab.sequence.block import review as review_block
    text = (Path(__file__).resolve().parent / "fixtures" / "plans" / f"{name}.block").read_text(encoding="utf-8")
    errors, warnings = review_block(text)
    assert errors == [] and "The plan that calls this block must do it first" in warnings[0][1]
    assert {p["name"]: p for p in api.list_plans()}[name].get("error") is None


# --- units and thermocouples in a call -------------------------------------------------------

CHILL = ("# Cool to a temperature\nblock chill to <temperature:number -200..30 C> at <sensor:temperature>\n"
         "load rPSU\n\nuntil {sensor} <= {temperature} C\n")


def test_a_call_writes_each_number_with_its_unit():
    with pytest.raises(PlanError, match="expected Torr after 'pumpdown to 5'"):
        parse_plan(plan("pumpdown to 5"))
    assert parse_plan(plan("pumpdown to 5 Torr")).sequence.segments


def test_a_block_takes_a_thermocouple_and_the_plan_loads_its_routine(blocks_dir):
    blocks_dir("chill", CHILL)
    seg = parse_plan(plan("chill to -40 C at TC01", load="rPSU rSMTC08")).sequence.segments[0]
    assert seg.label == "chill > until TC01 <= -40 C" and seg.params["timeout_s"] is None
    errors, _ = review(plan("chill to -40 C at TC01", load="rPSU"))
    assert errors == [(2, "In chill (line 5): TC01 is published by rSMTC08; add it to `load`")]
    with pytest.raises(PlanError, match="did you mean 'TC09'"):
        parse_plan(plan("chill to -40 C at TC99", load="rPSU rSMTC08"))


def test_a_block_with_a_thermocouple_reads_on_its_own():
    from formslab.sequence.block import review as review_block
    assert review_block(CHILL) == ([], [])          # the caller chooses, and loads, the thermocouple


def test_a_misnamed_draft_does_not_hide_the_block_rightly_named(blocks_dir):
    blocks_dir("chill", CHILL)
    blocks_dir("draft1", CHILL.replace("block chill", "block chill"))     # holds `block chill`, named draft1
    assert parse_plan(plan("chill to -40 C at TC01", load="rPSU rSMTC08")).sequence.segments
