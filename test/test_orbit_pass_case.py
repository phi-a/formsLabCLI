"""Contract tests for the resolved single-pass model.

These pin the numbers that the poster and paper quote. If a constant or a
geometry routine changes underneath, this is where it should surface -- not in
a figure nobody re-reads at 100 % zoom.
"""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone

import pytest

# formslab.orbit needs the `orbit` extra (numpy, scipy, matplotlib); a base
# install skips these tests.
for _dist in ("numpy", "scipy", "matplotlib"):
    pytest.importorskip(_dist)

from formslab.orbit.imaging.optimize import Instrument, optimize
from formslab.orbit.imaging.pass_case import (
    DEFAULT_MIN_EXPOSURE_S,
    build_pass_case,
)
from formslab.orbit.visibility.target import (
    OBSERVABLE,
    OCCULTED,
    PARTIAL,
    visibility_state,
)


class PassCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.case = build_pass_case()

    # -- timing model ------------------------------------------------------

    def test_schedule_exactly_fills_open_window(self) -> None:
        """startup + B + N*C == T_open, with no slack and no overrun."""
        case = self.case
        sol = case.solution
        total = (
            case.inst.startup_s
            + sol.exposure_s
            + sol.n_samples * case.inst.readout_per_sample
        )
        self.assertAlmostEqual(total, case.open_sky_s, places=6)

    def test_operational_spans_are_contiguous_and_end_at_sunrise(self) -> None:
        case = self.case
        spans = case.operational_spans_s
        self.assertEqual(spans[0][0], "startup")
        self.assertEqual(spans[-1][0], "readout")

        # exactly one exposure block: Skipper reads non-destructively, so the
        # pass is [startup][one exposure][N reads], not N exposure/read pairs
        self.assertEqual(sum(1 for name, _, _ in spans if name == "exposure"), 1)
        self.assertEqual(
            sum(1 for name, _, _ in spans if name == "readout"),
            case.solution.n_samples,
        )

        for (_, _, prev_end), (_, next_start, _) in zip(spans, spans[1:]):
            self.assertAlmostEqual(prev_end, next_start, places=9)

        open_start = case.time_of_u(case.open_window.start_rad)
        self.assertAlmostEqual(spans[0][1], open_start, places=9)
        self.assertAlmostEqual(spans[-1][2], open_start + case.open_sky_s, places=6)

    # -- geometry ----------------------------------------------------------

    def test_state_spans_partition_the_eclipse(self) -> None:
        case = self.case
        total = sum(t1 - t0 for _, t0, t1 in case.state_spans_s)
        self.assertAlmostEqual(total, case.eclipse_s, places=6)

        for (_, _, prev_end), (_, next_start, _) in zip(
            case.state_spans_s, case.state_spans_s[1:]
        ):
            self.assertAlmostEqual(prev_end, next_start, places=9)

    def test_state_spans_agree_with_visibility_state(self) -> None:
        """Cross-check the span decomposition against the point classifier."""
        case = self.case
        geom = case.geom
        for state, t0, t1 in case.state_spans_s:
            u_mid = case.u_of_time(0.5 * (t0 + t1))
            self.assertEqual(
                visibility_state(
                    u_mid,
                    geom.umbra_center_rad,
                    geom.umbra_half_rad,
                    geom.uc_target_rad,
                    geom.target_visible_half_rad,
                    geom.target_clear_half_rad,
                ),
                state,
                f"span {state} [{t0:.1f}, {t1:.1f}] s disagrees at its midpoint",
            )

    def test_state_durations_match_dense_sampling(self) -> None:
        """Independent check: classify the orbit point by point and total up.

        The retired landscape generator got this wrong -- it charged the limb
        margin on both sides of the pass even when the pass ended at sunrise
        before the trailing margin could occur, inflating `partial` to roughly
        twice its true value and shrinking `occulted` to match.
        """
        case = self.case
        geom = self.case.geom
        samples = 200_000
        period = case.orbit_period_s
        dt = period / samples

        totals: dict[str, float] = {}
        for k in range(samples):
            u = case.u_of_time(k * dt)
            state = visibility_state(
                u, geom.umbra_center_rad, geom.umbra_half_rad,
                geom.uc_target_rad, geom.target_visible_half_rad,
                geom.target_clear_half_rad,
            )
            totals[state] = totals.get(state, 0.0) + dt

        spans: dict[str, float] = {}
        for state, t0, t1 in case.state_spans_s:
            spans[state] = spans.get(state, 0.0) + (t1 - t0)

        for state, span_total in spans.items():
            self.assertAlmostEqual(
                totals.get(state, 0.0), span_total, delta=2.0 * dt,
                msg=f"{state}: spans say {span_total:.2f} s, sampling says "
                    f"{totals.get(state, 0.0):.2f} s",
            )

    def test_partial_is_clipped_to_the_umbra(self) -> None:
        """Only limb margins that fall inside the eclipse are counted."""
        case = self.case
        geom = case.geom
        both_margins = (
            2.0 * (geom.target_visible_half_rad - geom.target_clear_half_rad)
            / case.mean_motion_rad_s
        )
        partial = sum(t1 - t0 for s, t0, t1 in case.state_spans_s if s == PARTIAL)
        self.assertLess(partial, both_margins)

    def test_timeline_matches_arc_geometry(self) -> None:
        case = self.case
        self.assertAlmostEqual(case.time_of_u(case.u_exit_rad), case.eclipse_s, places=6)
        self.assertAlmostEqual(case.time_of_u(case.u_entry_rad), 0.0, places=9)

    def test_observable_span_equals_open_sky_budget(self) -> None:
        case = self.case
        observable = sum(
            t1 - t0 for state, t0, t1 in case.state_spans_s if state == OBSERVABLE
        )
        self.assertAlmostEqual(observable, case.open_sky_s, places=6)

    # -- optimization ------------------------------------------------------

    def test_optimum_matches_brute_force(self) -> None:
        case = self.case
        best_n, best_obj = None, -1.0
        readout = case.inst.readout_per_sample
        usable = case.open_sky_s - case.inst.startup_s
        for n in range(1, case.n_max + 1):
            b = usable - readout * n
            if b <= 0.0:
                break
            if b < case.min_exposure_s:
                continue
            if b * n > best_obj:
                best_n, best_obj = n, b * n
        self.assertEqual(case.solution.n_samples, best_n)
        self.assertAlmostEqual(case.solution.objective, best_obj, places=6)

    def test_min_exposure_threshold_respected(self) -> None:
        case = self.case
        self.assertGreaterEqual(case.solution.exposure_s, case.min_exposure_s)
        self.assertEqual(case.min_exposure_s, DEFAULT_MIN_EXPOSURE_S)

    def test_objective_curve_is_consistent_with_solution(self) -> None:
        case = self.case
        rows = {n: (b, obj) for n, b, obj in case.objective_curve}
        b_star, obj_star = rows[case.solution.n_samples]
        self.assertAlmostEqual(b_star, case.solution.exposure_s, places=6)
        self.assertAlmostEqual(obj_star, case.solution.objective, places=6)

    # -- the figure would be misleading without these -----------------------

    def test_default_pass_is_a_binding_case(self) -> None:
        """The obstruction constraint must actually bite on the poster date.

        Near mid-season the umbra arc lies entirely inside the target-clear arc
        and T_open == T_eclipse, which would render an "obstruction analysis"
        figure that shows a constraint doing nothing.
        """
        case = self.case
        self.assertLess(case.open_sky_s, case.eclipse_s)
        states = {state for state, _, _ in case.state_spans_s}
        self.assertIn(OCCULTED, states)
        self.assertIn(PARTIAL, states)
        self.assertIn(OBSERVABLE, states)

    def test_min_exposure_floor_cuts_within_the_plotted_range(self) -> None:
        """The SV-7 exposure floor should be visible on the objective curve."""
        case = self.case
        cut = case.min_exposure_cut_n
        self.assertIsNotNone(cut)
        self.assertGreater(cut, case.solution.n_samples)

    def test_default_pass_reference_values(self) -> None:
        """Pins the numbers quoted in docs/POSTER_SPEC.md."""
        m = self.case.measures
        self.assertAlmostEqual(m["eclipse_s"], 2136.6, places=1)
        self.assertAlmostEqual(m["open_sky_s"], 1472.9, places=1)
        self.assertEqual(m["n_samples"], 8)
        self.assertAlmostEqual(m["exposure_s"], 670.1, places=1)
        self.assertAlmostEqual(m["readout_per_sample_s"], 92.857, places=3)
        self.assertAlmostEqual(m["objective_s"], 5360.4, places=1)
        self.assertAlmostEqual(m["beta_target_deg"], 21.87, places=2)
        self.assertAlmostEqual(m["umbra_half_deg"], 67.74, places=2)
        self.assertAlmostEqual(m["target_clear_half_deg"], 102.93, places=2)


class SeasonSweepTests(unittest.TestCase):
    """The sweep feeds the landscape, so its spans must stay inside the pass."""

    @classmethod
    def setUpClass(cls) -> None:
        from formslab.orbit.imaging.season import SeasonSpec, sweep_season

        cls.spec = SeasonSpec()
        cls.cases = sweep_season(cls.spec)

    def test_every_window_span_lies_inside_the_eclipse(self) -> None:
        """Guards a full-height spike in the season landscape.

        An arc window can report a start an ulp before umbra entry; wrapping
        that into [0, 2pi) turns it into nearly a whole orbit, which drew as a
        vertical bar spanning the entire panel.
        """
        for case in self.cases:
            if case is None:
                continue
            for name in ("open_window", "visible_window"):
                start, end = case.window_span_min(getattr(case, name))
                ecl_min = case.eclipse_s / 60.0
                self.assertGreaterEqual(start, 0.0, f"{case.date} {name}")
                self.assertLessEqual(
                    end, ecl_min + 1e-9,
                    f"{case.date} {name}: {end:.2f} min past a "
                    f"{ecl_min:.2f} min eclipse",
                )
                self.assertLessEqual(start, end + 1e-9)

    def test_spans_partition_every_pass_in_the_season(self) -> None:
        for case in self.cases:
            if case is None:
                continue
            total = sum(t1 - t0 for _s, t0, t1 in case.state_spans_s)
            self.assertAlmostEqual(total, case.eclipse_s, places=6,
                                   msg=f"{case.date}")

    def test_sweep_covers_every_sampled_date(self) -> None:
        self.assertEqual(len(self.cases), len(self.spec.dates))


class PassCaseFailureTests(unittest.TestCase):
    def test_infeasible_pass_raises(self) -> None:
        """A window too short to schedule is an error, not a silent None."""
        with self.assertRaises(ValueError):
            build_pass_case(
                date=datetime(2027, 3, 29, tzinfo=timezone.utc),
                min_exposure_s=100_000.0,
            )

    def test_no_umbra_raises(self) -> None:
        """Cygnus X-1 out of season: no open-sky window at this geometry."""
        # A polar orbit with the target near the orbit normal drives the
        # target-clear arc to zero.
        with self.assertRaises(ValueError):
            build_pass_case(i=math.radians(0.1), fov_half=math.radians(89.0))


class OptimizeContractTests(unittest.TestCase):
    def test_readout_per_sample(self) -> None:
        self.assertAlmostEqual(Instrument().readout_per_sample, 92.857142857, places=6)

    def test_returns_none_when_window_too_short(self) -> None:
        inst = Instrument()
        self.assertIsNone(optimize(inst.startup_s, inst))
        self.assertIsNone(optimize(0.0, inst))


if __name__ == "__main__":
    unittest.main()
