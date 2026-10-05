"""Two-body Kepler motion (formslab.kepler.kepler): the orbit closes, energy and
angular momentum hold, the anomalies convert both ways, and the umbra and beta
agree with the orbit models' own formulas."""
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import formslab
from formslab.kepler.constants import MU, R_E
from formslab.kepler.kepler import (
    Elements, beta, live, mean_from_true, state, true_from_mean, umbra_spans,
)

EPOCH = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
PACKAGE = Path(formslab.__file__).parent


def circular(raan_deg, a=6928e3, i_deg=97.6):
    return Elements(a, 0.0, math.radians(i_deg), math.radians(raan_deg), 0.0, 0.0, EPOCH)


def norm(v):
    return math.sqrt(sum(c * c for c in v))


def cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


@pytest.mark.parametrize("name", ["constants.py", "sun.py"])
def test_the_copies_of_the_orbit_models_constants_and_sun_are_identical(name):
    """formslab.orbit imports nothing outside itself and kepler must not need
    numpy, so each has a copy; they must not drift apart."""
    assert (PACKAGE / "kepler" / name).read_bytes() == (PACKAGE / "orbit" / "propagate" / name).read_bytes()


def test_the_period_is_kepler_s_third_law():
    el = circular(0)
    assert el.period == pytest.approx(2 * math.pi * math.sqrt(el.a ** 3 / MU))
    assert el.period / 60 == pytest.approx(95.65, abs=0.01)


def test_after_one_period_the_satellite_is_back_where_it_started():
    el = Elements(7000e3, 0.3, 0.5, 1.0, 2.0, 0.3, EPOCH)
    r0, v0 = state(el, EPOCH)
    r1, v1 = state(el, EPOCH + timedelta(seconds=el.period))
    assert max(abs(a - b) for a, b in zip(r0, r1)) < 0.01                  # metres
    assert max(abs(a - b) for a, b in zip(v0, v1)) < 1e-5


def test_energy_and_angular_momentum_hold_around_an_eccentric_orbit():
    el = Elements(9000e3, 0.3, 1.1, 0.4, 2.5, 0.0, EPOCH)
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


# The conical-umbra formulas of formslab.orbit.propagate.orbit, which needs numpy
# to import; copied here so the comparison runs on a base install.
def eclipse_half_angle(a, b, d):
    from formslab.kepler.constants import R_SUN
    k, eps = R_E / a, (R_SUN - R_E) / d
    arg = (k * eps + math.sqrt((1 - k ** 2) * (1 - eps ** 2))) / abs(math.cos(b))
    return 0.0 if arg >= 1.0 else math.acos(arg)


@pytest.mark.parametrize("raan", [0, 60, 191.3, 200])
def test_umbra_lasts_as_long_as_the_orbit_models_say(raan):
    from formslab.kepler.sun import sun_dist

    el = circular(raan)
    spans = umbra_spans(el, EPOCH, EPOCH + timedelta(seconds=el.period * 2))
    whole = [(b - a).total_seconds() for a, b in spans if a > EPOCH and b < EPOCH + timedelta(seconds=el.period * 2)]
    expected = 2 * eclipse_half_angle(el.a, beta(el, EPOCH), sun_dist(EPOCH)) / el.n
    assert whole and all(w == pytest.approx(expected, abs=5) for w in whole)


def test_with_the_sun_near_the_orbit_normal_there_is_no_umbra():
    el = circular(281.3)                                                     # dawn-dusk at the epoch
    assert abs(math.degrees(beta(el, EPOCH))) > 80
    assert umbra_spans(el, EPOCH, EPOCH + timedelta(seconds=el.period * 2)) == []


def test_beta_matches_the_orbit_models_formula():
    from formslab.kepler.sun import sun_ra_dec

    el = circular(120)
    ra, dec = sun_ra_dec(EPOCH)
    sin_beta = math.sin(el.i) * math.cos(dec) * math.sin(el.raan - ra) + math.cos(el.i) * math.sin(dec)
    assert beta(el, EPOCH) == pytest.approx(math.asin(sin_beta))


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
