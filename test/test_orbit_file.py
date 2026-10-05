"""Orbit files (formslab.orbit.file): seven elements, each once; every problem
reported with its line; the editor's options and tokens; and the GUI routes that
open, save and propagate them."""
import math
from pathlib import Path

import pytest

from formslab.gui import api, plans
from formslab.orbit.file import (
    GROUPS, ORDER, OrbitError, describe, discover, line_options, parse, review, tokens,
)
from formslab.sequence import discover as discover_plans
from formslab.sequence.plan import user_plans_dir

from gui_helpers import Client, running_server

SHIPPED = ("leo_dawn_dusk", "leo_noon")
GOOD = """# an orbit
epoch 2026-10-05T12:00:00Z
a 6928 km
e 0.001
i 97.6 deg
raan 120 deg
argp 90 deg
nu 10 deg
"""


def shipped(name):
    return next(p for p in discover() if p.stem == name)


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_orbit_reads(name):
    assert review(shipped(name).read_text(encoding="utf-8")) == ([], [])


def test_the_elements_are_read_in_metres_and_radians():
    el = parse(GOOD)
    assert el.a == 6928e3 and el.e == 0.001
    assert el.i == pytest.approx(math.radians(97.6)) and el.argp == pytest.approx(math.pi / 2)
    assert el.nu == pytest.approx(math.radians(10)) and el.epoch.isoformat() == "2026-10-05T12:00:00+00:00"


def test_order_case_and_comments_do_not_matter():
    lines = GOOD.splitlines()
    shuffled = "\n".join([lines[0], *reversed(lines[1:])]).replace("raan", "RAAN") + "\n\n# the end\n"
    assert parse(shuffled) == parse(GOOD)


def test_every_problem_is_reported_with_its_line():
    errors = dict(review("""# broken
epoch 2026-10-05T12:00
a 6900km
i 200 deg
raan 1 deg
raan 2 deg
argp 0 deg # a note
foo 3
""")[0])
    assert errors[0] == "the orbit has no `e` or `nu`; every element is needed: epoch, a, e, i, raan, argp, nu"
    assert errors[2] == "'2026-10-05T12:00': give the time in UTC, ending in Z"
    assert "write `6900 km`, with a space" in errors[3]
    assert errors[4] == "i: 200 is outside 0..180"
    assert errors[6] == "`raan` is already on line 5; an orbit names each element once"
    assert errors[7] == "comments go on their own line"
    assert errors[8].startswith("expected epoch, a, e, i, raan, argp or nu, got 'foo'")
    assert 1 not in errors and 5 not in errors


def test_a_time_that_is_not_one_is_refused():
    errors = dict(review(GOOD.replace("2026-10-05T12:00:00Z", "tomorrow"))[0])
    assert errors[2] == "'tomorrow' is not a time; write it as 2026-10-05T12:00:00Z"
    assert review(GOOD.replace("12:00:00Z", "12:00:00+00:00")) == ([], [])


def test_the_perigee_must_clear_the_earth():
    errors = review(GOOD.replace("a 6928 km", "a 7000 km").replace("e 0.001", "e 0.1"))[0]
    assert errors == [(4, "the perigee, a(1 - e), is -78 km above the surface; it must be at least 100 km")]
    assert dict(review(GOOD.replace("a 6928 km", "a 6400 km"))[0])[3] == "a: 6400 must be >= 6480"


def test_parse_raises_with_every_error():
    with pytest.raises(OrbitError) as e:
        parse("a 6928 km\n")
    assert str(e.value).startswith("the orbit has no `epoch` or `e`")
    assert e.value.errors[0][0] == 0


# --- the editor -------------------------------------------------------------------------------

def test_the_first_word_offers_every_element_with_what_it_describes():
    first = line_options([])["positions"][0]
    assert [o["text"] for o in first] == list(ORDER)
    assert {o["text"]: o["part"] for o in first} == GROUPS


def test_after_an_element_comes_its_value_then_its_unit():
    r = line_options(["i", "97.6"])
    value, unit = r["positions"][1][0], r["positions"][2]
    assert value["kind"] == "number" and (value["lo"], value["hi"]) == (0.0, 180.0) and value["part"] == "plane"
    assert [o["text"] for o in unit] == ["deg"] and r["complete"] is False
    assert line_options(["i", "97.6", "deg"])["complete"] is True
    assert line_options(["x"])["error"].startswith("expected epoch")


def test_every_word_gets_its_role_and_the_element_its_group():
    assert tokens("# c\n\na 6928 km\ni 200 deg\n") == [
        [{"text": "# c", "role": "comment"}], [],
        [{"text": "a", "role": "verb", "part": "shape"}, {"text": "6928", "role": "value"}, {"text": "km", "role": "kw"}],
        [{"text": "i", "role": "verb", "part": "plane"}, {"text": "200", "role": "bad"}, {"text": "deg", "role": "bad"}],
    ]
    assert tokens("epoch 2026-10-05T12:00:00Z")[0][1] == {"text": "2026-10-05T12:00:00Z", "role": "value"}


@pytest.mark.parametrize("name", SHIPPED)
def test_a_shipped_orbit_has_no_bad_words(name):
    assert all(t["role"] != "bad" for line in tokens(shipped(name).read_text(encoding="utf-8")) for t in line)


def test_the_help_card_says_what_the_element_is():
    card = describe(GOOD, 5)["cards"][0]
    assert card["usage"] == "i <inclination> deg" and card["part"] == "plane" and card["complete"] is True
    assert card["help"] == "Tilt the orbit's plane" and "equator" in card["details"]
    assert describe(GOOD, 1) == {"cards": [], "rules": []}


# --- beside the plans -------------------------------------------------------------------------

def test_an_orbit_is_listed_for_the_editor_but_never_as_a_plan_to_run():
    listing = {p["name"]: p for p in api.list_plans()}
    assert all(listing[n]["kind"] == "orbit" and listing[n]["editable"] is False for n in SHIPPED)
    assert listing["tvac"]["kind"] == "plan"
    assert not any(p.stem in SHIPPED for p in discover_plans())
    with pytest.raises(api.ApiError) as e:
        api.start_run("leo_noon")
    assert e.value.code == 404


def test_an_orbit_is_read_and_saved_as_an_orbit():
    r = plans.read("leo_noon")
    assert r["kind"] == "orbit" and r["editable"] is False and r["errors"] == []
    saved = plans.save("my_orbit", GOOD, None, as_new=True, kind="orbit")
    assert saved["kind"] == "orbit" and saved["errors"] == []
    assert (user_plans_dir() / "my_orbit.orbit").read_text(encoding="utf-8") == GOOD
    again = plans.save("my_orbit", GOOD.replace("nu 10", "nu 20"), saved["hash"], as_new=False)
    assert again["kind"] == "orbit"
    assert {p["name"]: p["kind"] for p in api.list_plans()}["my_orbit"] == "orbit"


def test_a_name_is_taken_by_a_plan_or_an_orbit_alike():
    with pytest.raises(plans.PlanFileError, match="already exists"):
        plans.save("leo_noon", "load rPSU\nhold 1 s\n", None, as_new=True)
    with pytest.raises(plans.PlanFileError, match="already exists"):
        plans.save("tvac", GOOD, None, as_new=True, kind="orbit")
    with pytest.raises(plans.PlanFileError) as e:
        plans.save("other", GOOD, None, as_new=True, kind="sat")
    assert e.value.code == 400


def test_deleting_your_orbit_keeps_it_in_the_trash_as_an_orbit():
    saved = plans.save("gone", GOOD, None, as_new=True, kind="orbit")
    r = plans.delete("gone", saved["hash"])
    assert Path(r["trash"]).suffix == ".orbit" and Path(r["trash"]).read_text(encoding="utf-8") == GOOD


def test_an_orbit_with_mistakes_is_listed_as_not_whole():
    plans.save("half", "a 6928 km\n", None, as_new=True, kind="orbit")
    assert {p["name"]: p.get("error") for p in api.list_plans()}["half"].startswith("line 0: the orbit has no")


# --- over HTTP --------------------------------------------------------------------------------

@pytest.fixture
def server(monkeypatch):
    with running_server(monkeypatch) as srv:
        yield srv


@pytest.fixture
def client(server):
    return Client(server).login()


def test_the_editor_routes_take_the_kind(client):
    assert client.json("GET", "/api/plans/leo_dawn_dusk")[1]["kind"] == "orbit"
    code, body = client.json("POST", "/api/plan/check", {"text": "a 6928 km\n", "kind": "orbit"})
    assert code == 200 and body["errors"][0]["line"] == 0
    code, body = client.json("POST", "/api/plan/line", {"scripts": [], "words": ["e"], "kind": "orbit"})
    assert code == 200 and body["positions"][1][0]["text"] == "eccentricity"
    code, body = client.json("POST", "/api/plan/tokens", {"text": "nu 0 deg", "kind": "orbit"})
    assert body["lines"][0][0] == {"text": "nu", "role": "verb", "part": "place"}
    code, body = client.json("POST", "/api/describe", {"text": GOOD, "line": 3, "kind": "orbit"})
    assert body["cards"][0]["part"] == "shape"
    code, body = client.json("POST", "/api/plan/save", {"name": "mine", "text": GOOD, "as_new": True, "kind": "orbit"})
    assert code == 200 and body["kind"] == "orbit" and (user_plans_dir() / "mine.orbit").is_file()
    assert client.json("POST", "/api/plan/check", {"text": "", "kind": "sat"})[0] == 400


def test_the_live_panel_is_the_orbit_now_or_why_not(server, client):
    code, body = client.json("POST", "/api/orbit/live", {"text": GOOD})
    assert code == 200 and 540 < body["altitude_km"] < 560 and isinstance(body["umbra"], bool)
    code, body = client.json("POST", "/api/orbit/live", {"text": "a 6928 km\n"})
    assert code == 200 and body["error"].startswith("the orbit has no")
    assert Client(server).json("POST", "/api/orbit/live", {"text": GOOD})[0] == 401


# --- an orbit file drives the environment models ------------------------------------------------

def test_an_orbit_file_runs_through_view_flux_and_environment():
    from formslab.orbit.file import load
    from formslab.orbit.geometry import LVLHFixed
    from formslab.orbit.thermal.pipeline import CubeSat, catalog, env, flux, view

    orbit = load(shipped("leo_noon"))
    sat = CubeSat(catalog("6u_double_deployable"))
    vl = view(sat.geometry, orbit, LVLHFixed(), facets=["bus_-Z"], n=24, n_mu=6, n_az=12, hemi_n_az=7, hemi_n_el=5)
    tenv = env(flux(vl, ["bus_-Z"], solar_panel_temperature_K=300.0, body_temperature=290.0)).data["bus_-Z"]
    assert tenv.shape[0] == 24 and (tenv > 0).all()
    assert 0.3 < vl.eclipse.mean() < 0.45                       # about 35 minutes of 96


def test_an_eccentric_orbit_sees_more_earth_at_perigee():
    import numpy as np
    from formslab.orbit.geometry import LVLHFixed
    from formslab.orbit.propagate.orbit import Orbit
    from formslab.orbit.thermal.pipeline import CubeSat, catalog, view

    text = GOOD.replace("a 6928 km", "a 8000 km").replace("e 0.001", "e 0.1")
    orbit = Orbit(parse(text))
    vl = view(CubeSat(catalog("6u_double_deployable")).geometry, orbit, LVLHFixed(), facets=["bus_-Z"],
              n=24, n_mu=6, n_az=12, hemi_n_az=7, hemi_n_el=5)
    earth = vl.earth["bus_-Z"].mean(axis=(1, 2))
    perigee = int(np.argmin([orbit.radius(u) for u in vl.u]))
    apogee = int(np.argmax([orbit.radius(u) for u in vl.u]))
    assert earth[perigee] > earth[apogee] * 1.2
