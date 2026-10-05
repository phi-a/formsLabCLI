"""Kepler motion (formslab.orbit.propagate.kepler): without J2 the orbit closes,
energy and angular momentum hold; the anomalies convert both ways; the umbra and
beta agree with the circular-orbit formulas beside it; and with J2 the node turns
at the rate that keeps a sun-synchronous orbit's local time."""
import math
from datetime import datetime, timedelta, timezone
import pytest

from formslab.orbit.propagate.constants import MU, R_E
from formslab.orbit.propagate.kepler import (
    Elements, beta, live, mean_from_true, state, true_from_mean, umbra_spans,
)
from formslab.orbit.propagate.orbit import beta_uc, eclipse_half_angle
from formslab.orbit.propagate.sun import sun_dist, sun_ra_dec

EPOCH = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def circular(raan_deg, a=6928e3, i_deg=97.6):
    return Elements(a, 0.0, math.radians(i_deg), math.radians(raan_deg), 0.0, 0.0, EPOCH, j2=False)


def norm(v):
    return math.sqrt(sum(c * c for c in v))


def cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def test_the_period_is_kepler_s_third_law():
    el = circular(0)
    assert el.period == pytest.approx(2 * math.pi * math.sqrt(el.a ** 3 / MU))
    assert el.period / 60 == pytest.approx(95.65, abs=0.01)


def test_after_one_period_the_satellite_is_back_where_it_started():
    el = Elements(7000e3, 0.3, 0.5, 1.0, 2.0, 0.3, EPOCH, j2=False)
    r0, v0 = state(el, EPOCH)
    r1, v1 = state(el, EPOCH + timedelta(seconds=el.period))
    assert max(abs(a - b) for a, b in zip(r0, r1)) < 0.01                  # metres
    assert max(abs(a - b) for a, b in zip(v0, v1)) < 1e-5


def test_energy_and_angular_momentum_hold_around_an_eccentric_orbit():
    el = Elements(9000e3, 0.3, 1.1, 0.4, 2.5, 0.0, EPOCH, j2=False)
    energies, momenta = [], []
    for k in range(24):
        r, v = state(el, EPOCH + timedelta(seconds=el.period * k / 24))
        energies.append(norm(v) ** 2 / 2 - MU / norm(r))
        momenta.append(cross(r, v))
    assert all(e == pytest.approx(-MU / (2 * el.a), rel=1e-9) for e in energies)
    assert all(max(abs(a - b) for a, b in zip(h, momenta[0])) < 1e-9 * norm(momenta[0]) for h in momenta)


def test_perigee_and_apogee_are_where_the_elements_put_them():
    el = Elements(9000e3, 0.3, 1.1, 0.4, 2.5, 0.0, EPOCH)                   # at perigee at the epoch
    assert norm(state(el, EPOCH)[0]) == pytest.approx(el.a * 0.7)
    assert norm(state(el, EPOCH + timedelta(seconds=el.period / 2))[0]) == pytest.approx(el.a * 1.3)
    assert el.perigee_altitude == pytest.approx(el.a * 0.7 - R_E)


@pytest.mark.parametrize("e", [0.0, 0.1, 0.7, 0.98])
def test_true_and_mean_anomaly_convert_both_ways(e):
    for nu in (0.0, 0.3, 1.5, 3.0, 3.2, 5.9):
        assert math.remainder(true_from_mean(mean_from_true(nu, e), e) - nu, 2 * math.pi) == pytest.approx(0, abs=1e-10)


def test_the_orbit_plane_is_where_inclination_and_node_put_it():
    el = Elements(7000e3, 0.0, math.radians(30), math.radians(45), 0.0, 0.0, EPOCH)
    r, v = state(el, EPOCH)                                                  # at the ascending node
    assert r[2] == pytest.approx(0, abs=1e-6) and v[2] > 0
    assert math.degrees(math.atan2(r[1], r[0])) == pytest.approx(45)
    h = cross(r, v)
    assert math.degrees(math.acos(h[2] / norm(h))) == pytest.approx(30)


@pytest.mark.parametrize("raan", [0, 60, 191.3, 200])
def test_umbra_lasts_as_long_as_the_circular_formula_says(raan):
    el = circular(raan)
    spans = umbra_spans(el, EPOCH, EPOCH + timedelta(seconds=el.period * 2))
    whole = [(b - a).total_seconds() for a, b in spans if a > EPOCH and b < EPOCH + timedelta(seconds=el.period * 2)]
    expected = 2 * eclipse_half_angle(el.a, beta(el, EPOCH), sun_dist(EPOCH)) / el.n
    assert whole and all(w == pytest.approx(expected, abs=5) for w in whole)


def test_with_the_sun_near_the_orbit_normal_there_is_no_umbra():
    el = circular(281.3)                                                     # dawn-dusk at the epoch
    assert abs(math.degrees(beta(el, EPOCH))) > 80
    assert umbra_spans(el, EPOCH, EPOCH + timedelta(seconds=el.period * 2)) == []


def test_beta_matches_the_circular_formula():
    el = circular(120)
    assert beta(el, EPOCH) == pytest.approx(beta_uc(el.i, el.raan, *sun_ra_dec(EPOCH))[0])


def test_live_says_where_the_satellite_is_and_when_the_umbra_comes():
    el = circular(191.3)                                                     # noon-midnight: umbra every orbit
    now = EPOCH + timedelta(hours=3)
    r = live(el, now)
    assert r["altitude_km"] == pytest.approx((6928e3 - R_E) / 1000)
    assert r["speed_km_s"] == pytest.approx(math.sqrt(MU / 6928e3) / 1000)
    assert r["since_epoch_s"] == 3 * 3600 and r["period_s"] == pytest.approx(el.period)
    assert len(r["spans"]) in (1, 2) and all(0 <= a < b <= el.period for a, b in r["spans"])
    if r["umbra"]:
        assert r["spans"][0][0] == 0 and r["umbra_ends_s"] > 0
    else:
        assert r["next_umbra_s"] == pytest.approx(r["spans"][0][0])
    total = sum(b - a for a, b in r["spans"])
    assert 30 * 60 < total < 40 * 60


# --- the Orbit the models sweep, built from elements ------------------------------------------

from formslab.orbit.propagate.orbit import Orbit  # noqa: E402

# The sweep is one orbit of two-body motion (see Orbit); compared without J2.
ECCENTRIC = Elements(8000e3, 0.1, math.radians(40), math.radians(70), math.radians(30), math.radians(50), EPOCH,
                     j2=False)


def test_a_circular_orbit_sweeps_from_the_node_at_the_epoch():
    orbit = Orbit.from_epoch(6771e3, math.radians(51.6), math.radians(30), EPOCH)
    assert orbit.u0 == 0 and orbit.true_latitude(1.234) == 1.234
    assert orbit.radius(2.0) == 6771e3 and orbit.utc_at(0.0) == EPOCH


def test_the_sweep_runs_from_perigee_to_apogee():
    orbit = Orbit(ECCENTRIC)
    assert orbit.radius(ECCENTRIC.argp) == pytest.approx(8000e3 * 0.9)
    assert orbit.radius(ECCENTRIC.argp + math.pi) == pytest.approx(8000e3 * 1.1)
    assert orbit.rho(ECCENTRIC.argp) > orbit.rho(ECCENTRIC.argp + math.pi)


def test_the_sweep_is_where_kepler_puts_the_satellite_at_that_time():
    orbit = Orbit(ECCENTRIC)
    assert orbit.utc_at(orbit.u0) == EPOCH
    for u in (0.0, 1.0, 2.5, 4.0, 6.0):
        r, _ = state(ECCENTRIC, orbit.utc_at(u))
        # metres; a datetime holds microseconds, 7 mm of flight
        assert max(abs(a - b) for a, b in zip(orbit.position_eci(u), r)) < 0.01


def test_lvlh_stays_a_right_handed_frame_on_an_ellipse():
    import numpy as np
    orbit = Orbit(ECCENTRIC)
    for u in (0.3, 2.0, 5.1):
        m = orbit.eci_from_lvlh(u)
        assert np.allclose(m.T @ m, np.eye(3)) and np.linalg.det(m) == pytest.approx(1.0)


@pytest.mark.parametrize("elements", [ECCENTRIC, Elements(6928e3, 0.0, math.radians(97.6), math.radians(191.3), 0, 0, EPOCH)])
def test_the_sweeps_umbra_is_where_the_live_propagation_finds_it(elements):
    """The sweep freezes the Sun at the epoch; over one orbit it moves about 0.07
    degrees, so the two agree to a few seconds."""
    orbit = Orbit(elements)
    arcs = orbit.eclipse_arcs
    t0 = orbit.utc_at(0.0)
    spans = umbra_spans(elements, t0, t0 + timedelta(seconds=orbit.period))
    assert len(arcs) == 1
    entry, exit_ = arcs[0]
    starts = [(a - t0).total_seconds() for a, _ in spans if a > t0]
    ends = [(b - t0).total_seconds() for _, b in spans if b < t0 + timedelta(seconds=orbit.period)]
    assert min(abs(entry / orbit.n - s) for s in starts) < 5
    assert min(abs((exit_ % (2 * math.pi)) / orbit.n - e) for e in ends) < 5
    assert orbit.in_eclipse((entry + exit_) / 2) and not orbit.in_eclipse(exit_ + 0.1)


def test_a_sweep_at_a_later_date_starts_where_j2_has_carried_the_orbit():
    el = Elements(8000e3, 0.1, math.radians(40), math.radians(70), math.radians(30), math.radians(50), EPOCH)
    later = EPOCH + timedelta(days=40)
    orbit = Orbit(el.at(later))
    r, _ = state(el, later)
    assert max(abs(a - b) for a, b in zip(orbit.position_eci(orbit.u0), r)) < 1e-3


# --- J2 -------------------------------------------------------------------------------------------

def test_j2_turns_a_sun_synchronous_plane_with_the_sun():
    raan_dot, _, _ = Elements(6928e3, 0.0, math.radians(97.6), 0.0, 0.0, 0.0, EPOCH).rates
    assert math.degrees(raan_dot) * 86400 == pytest.approx(360 / 365.2422, rel=0.01)       # 0.9856 deg/day


def test_j2_rates_have_their_textbook_signs_and_zeros():
    def rates(i_deg):
        return Elements(7000e3, 0.01, math.radians(i_deg), 0.0, 0.0, 0.0, EPOCH).rates
    assert rates(30)[0] < 0 < rates(150)[0]                         # prograde: the node moves west
    assert rates(90)[0] == pytest.approx(0, abs=1e-15)              # a polar plane does not turn
    assert rates(math.degrees(math.asin(math.sqrt(0.8))))[1] == pytest.approx(0, abs=1e-15)   # critical: perigee fixed
    assert rates(30)[2] > Elements(7000e3, 0.01, 0.5, 0, 0, 0, EPOCH).n * 0.999


def test_without_j2_nothing_drifts():
    el = circular(120)
    later = el.at(EPOCH + timedelta(days=100))
    assert (later.raan, later.argp) == (el.raan, el.argp)


def test_moving_the_epoch_there_and_back_gives_the_same_orbit():
    el = Elements(7500e3, 0.05, 1.0, 2.0, 3.0, 4.0, EPOCH)
    back = el.at(EPOCH + timedelta(days=12, seconds=345)).at(EPOCH)
    for a, b in ((back.raan, el.raan), (back.argp, el.argp), (back.nu, el.nu)):
        assert math.remainder(a - b, 2 * math.pi) == pytest.approx(0, abs=1e-9)


@pytest.mark.parametrize("name, ltan", [("leo_dawn_dusk", 18.0), ("leo_noon", 12.0)])
def test_the_shipped_sun_synchronous_orbits_keep_their_local_time_for_a_year(name, ltan):
    """Without J2 the plane stays put while the Sun moves a degree a day: within a
    month the local time had moved two hours and the hot case had an eclipse.
    What is left is the equation of time, measured from the epoch's: +11 min on
    5 October, -14 min in February, so up to 25 minutes."""
    from formslab.orbit.file import discover, parse
    from formslab.orbit.propagate.orbit import ltan_for_raan

    el = parse(next(p for p in discover() if p.stem == name).read_text(encoding="utf-8"))
    for days in range(0, 366, 30):
        t = el.epoch + timedelta(days=days)
        assert abs(ltan_for_raan(t, el.at(t).raan) - ltan) < 0.5


def test_the_noon_orbit_is_the_cold_case_all_year():
    from formslab.orbit.file import discover, parse

    el = parse(next(p for p in discover() if p.stem == "leo_noon").read_text(encoding="utf-8"))
    for days in range(0, 366, 45):
        t = el.epoch + timedelta(days=days)
        spans = umbra_spans(el, t, t + timedelta(seconds=el.period * 2))
        assert max((b - a).total_seconds() for a, b in spans) > 34 * 60


def test_the_panel_and_the_run_read_the_umbra_the_same_way():
    """The live panel (live) and rOrbit (situation) share one reading."""
    from formslab.orbit.propagate.kepler import situation, umbra_window

    el = Elements(6928e3, 0.001, math.radians(97.6), math.radians(191.3), 0.0, 0.0, EPOCH)
    for minutes in (0, 20, 50, 80):
        t = EPOCH + timedelta(minutes=minutes)
        panel, run = live(el, t), situation(el, t, umbra_window(el, t)[0])
        assert panel["umbra"] == run["in_umbra"] and panel["next_umbra_s"] == run["next_umbra_s"]
        assert panel["umbra_duration_s"] == run["umbra_duration_s"] > 30 * 60
