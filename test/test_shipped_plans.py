"""The bench's own plans and blocks (`plans/`), the set students learn the GUI with.

Each must read with no error and no warning, and each test must run end to end
against the simulated chamber, with its holds cut short, and leave the chamber where
its header says.
"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from formslab import config, rscripts
from formslab.devices.hvc3500.simulator import Simulator
from formslab.host import sequence
from formslab.sequence import block as blockfile
from formslab.sequence import SequenceError, load_plan, plan as planfile
from formslab.sequence.plan import find_plan, review
from test_lab_sequence import bench  # noqa: F401 (fixture: rPSU and rSMTC08 on fakes)

SHIPPED = Path(__file__).resolve().parents[1] / "plans"
PATHS = {p.stem: p for p in [*SHIPPED.rglob("*.plan"), *SHIPPED.rglob("*.block")]}   # in any folder
PLANS = sorted(n for n, p in PATHS.items() if p.suffix == ".plan")
BLOCKS = sorted(n for n, p in PATHS.items() if p.suffix == ".block")


@pytest.fixture(autouse=True)
def shipped(monkeypatch):
    """The checkout's own plans, not the suite's frozen set."""
    monkeypatch.setattr(planfile, "shipped_dir", lambda: SHIPPED)
    monkeypatch.setenv(planfile.ENV, str(SHIPPED))


def test_the_set_is_what_the_docs_name():
    assert PLANS == ["Cryocooler", "ambient_to_rest", "end", "gevs_unit", "rest_to_ambient", "thermal_cycle",
                     "tvac", "warm_soak"]
    assert BLOCKS == ["begin", "cool", "detector", "highvac", "k508n", "plateau", "pumpdown", "seal", "vent",
                      "warm"]


@pytest.mark.parametrize("name", PLANS)
def test_a_plan_reads_clean_and_says_where_it_starts_and_ends(name):
    text = PATHS[name].read_text(encoding="utf-8")
    assert review(text) == ([], []), name
    if name not in ("tvac", "end"):                      # manual operation, and what End runs
        for word in ("# Start:", "# End:", "# Fails:"):
            assert word in text, f"{name} has no {word!r} in its header"


@pytest.mark.parametrize("name", BLOCKS)
def test_a_block_reads_without_error(name):
    errors, _ = blockfile.review(PATHS[name].read_text(encoding="utf-8"))
    assert errors == [], name


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


@pytest.fixture
def quick(monkeypatch):
    """Holds cut to 1.5 s."""
    def load(path):
        p = load_plan(path)
        segs = tuple(replace(s, params={**s.params, "seconds": 1.5}) if s.verb == "hold" else s
                     for s in p.sequence.segments)
        return replace(p, sequence=replace(p.sequence, segments=segs))
    monkeypatch.setattr(sequence, "load_plan", load)


@pytest.fixture
def one_pass(quick, monkeypatch):
    """Holds cut to 1.5 s, and each `repeat <n> times` to one pass."""
    shortened = sequence.load_plan

    def load(path):
        p = shortened(path)
        segs = tuple(replace(s, params={**s.params, "times": 1}) if s.verb == "repeat" and s.params["times"] else s
                     for s in p.sequence.segments)
        return replace(p, sequence=replace(p.sequence, segments=segs))
    monkeypatch.setattr(sequence, "load_plan", load)


def run(name):
    sequence.channel(plan_path=find_plan(name))


def test_ambient_to_rest_leaves_the_chamber_sealed_under_vacuum(chamber, quick):
    run("ambient_to_rest")
    s = chamber.state
    assert s.pressure < 3.0
    assert not any(s.devices.values())                  # every valve closed, both pumps off
    assert not any(s.zone_active.values())


def test_ambient_to_rest_stops_when_the_chamber_is_not_at_air(chamber, quick):
    with chamber.state.lock:
        chamber.state.pressure = 50.0
    with pytest.raises(SequenceError, match="chamberP > 700 not met"):
        run("ambient_to_rest")
    assert not chamber.state.devices["OP"]              # no pump was started


def test_rest_to_ambient_brings_a_sealed_chamber_to_air(chamber, quick):
    with chamber.state.lock:
        chamber.state.pressure = 3.0
    run("rest_to_ambient")
    s = chamber.state
    assert s.pressure >= 700 and s.devices["OV"] is True


def test_warm_soak_ends_at_air_with_the_zones_off(chamber, quick):
    run("warm_soak")
    s = chamber.state
    assert s.pressure >= 700 and s.devices["OV"] is True
    assert not s.devices["OR"] and not s.devices["OP"]
    assert not any(s.zone_active.values())


def test_thermal_cycle_ends_at_air_with_the_zones_off(chamber, quick):
    run("thermal_cycle")
    s = chamber.state
    assert s.pressure >= 700 and s.devices["OV"] is True
    assert not s.devices["OR"] and not s.devices["OP"]
    assert not any(s.zone_active.values())


def test_gevs_unit_reaches_high_vacuum_and_ends_at_air_with_everything_off(chamber, one_pass, bench):
    run("gevs_unit")                                    # its until chamberP < 1e-05 held, or it would raise
    s = chamber.state
    assert s.pressure >= 700 and s.devices["OV"] is True
    assert not any(s.devices[c] for c in ("OR", "OF", "O4", "OG", "OP", "OT"))
    assert not any(s.zone_active.values())
    assert s.zone_rate[1] == s.zone_rate[2] == 1.0      # 1 °C/min
    article = bench["psu1"].ch[2]
    assert article["vset"] == 5.0 and article["on"] is False


def test_preparing_for_darkness_reads_clean():
    text = ("load rLACO rCryoBoard rPSU rSMTC08 rSLTA rOrbit\nrecord every 10 s\n\n"
            "k508n at 17 V with 266 ohm\nuntil TC01 <= -40 C\ndetector imaging\n"
            "when TC01 > -30 C then log detector warming\nhold until end\n")
    assert review(text) == ([], [])
    labels = [s.label for s in load_plan_text(text)]
    assert {"k508n > cryo on", "until TC01 <= -40 C", "detector > slta run on"} <= set(labels)


def load_plan_text(text):
    from formslab.sequence import parse_plan
    return parse_plan(text).sequence.segments
