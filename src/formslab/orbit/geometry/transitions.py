"""Transition wrappers for attitude laws."""

import math
from dataclasses import dataclass

import numpy as np

from .so3 import SO3


def _wrap_2pi(u):
    return np.mod(u, 2 * math.pi)


@dataclass(frozen=True)
class _TransitionWindow:
    start: float
    span: float
    start_attitude: SO3
    end_attitude: SO3


class SlewModeSwitch:
    """Mode switch with finite-rate shortest-path attitude slews.

    The nominal sunlit/eclipse laws are preserved away from the mode
    boundaries. At eclipse entry and exit, a shortest-path SLERP window
    begins exactly at the mode boundary and then runs forward in argument of
    latitude. The window size is set from the required rotation angle and
    the supplied slew rate.

    Parameters
    ----------
    eclipse_law : callable
        Law used in eclipse.
    sunlit_law : callable
        Law used outside eclipse.
    slew_rate_rad_s, slew_rate_deg_s : float, optional
        Slew rate. Provide exactly one.
    transition_samples : int, optional
        Minimum number of samples to inject across each slew window when a
        propagator asks the law to refine its ``u`` grid.
    """
    def __init__(self, eclipse_law, sunlit_law, *,
                 slew_rate_rad_s=None, slew_rate_deg_s=None,
                 transition_samples=25):
        if (slew_rate_rad_s is None) == (slew_rate_deg_s is None):
            raise ValueError("Provide exactly one of slew_rate_rad_s or slew_rate_deg_s")

        slew_rate = slew_rate_rad_s
        if slew_rate is None:
            slew_rate = math.radians(slew_rate_deg_s)
        if slew_rate <= 0.0:
            raise ValueError("slew rate must be positive")

        self.eclipse_law = eclipse_law
        self.sunlit_law = sunlit_law
        self.slew_rate = float(slew_rate)
        self.transition_samples = max(3, int(transition_samples))
        self._transition_cache = {}

    def _solve_transition(self, boundary, from_law, to_law, orbit, span_cap):
        span = orbit.n * from_law(boundary, orbit).rotation_angle_to(
            to_law(boundary, orbit)
        ) / self.slew_rate
        span = min(max(span, 0.0), span_cap)

        start_att = from_law(boundary, orbit)
        end_att = to_law(boundary, orbit)
        for _ in range(2):
            if span <= 1e-12:
                break
            end_u = float(_wrap_2pi(boundary + span))
            start_att = from_law(boundary, orbit)
            end_att = to_law(end_u, orbit)
            new_span = orbit.n * start_att.rotation_angle_to(end_att) / self.slew_rate
            new_span = min(max(new_span, 0.0), span_cap)
            if abs(new_span - span) < 1e-12:
                span = new_span
                break
            span = new_span

        return _TransitionWindow(
            start=float(boundary),
            span=float(span),
            start_attitude=start_att,
            end_attitude=end_att,
        )

    def _transitions(self, orbit):
        cached = self._transition_cache.get(orbit)
        if cached is not None:
            return cached
        if orbit.nu <= 0.0:
            cached = ()
        else:
            entry_span_cap = 0.95 * (2.0 * orbit.nu)
            exit_span_cap = 0.95 * (2.0 * math.pi - 2.0 * orbit.nu)
            if min(entry_span_cap, exit_span_cap) <= 0.0:
                cached = ()
            else:
                boundary = orbit.uc_sun + math.pi
                cached = (
                    self._solve_transition(
                        float(_wrap_2pi(boundary - orbit.nu)),
                        self.sunlit_law, self.eclipse_law, orbit, entry_span_cap,
                    ),
                    self._solve_transition(
                        float(_wrap_2pi(boundary + orbit.nu)),
                        self.eclipse_law, self.sunlit_law, orbit, exit_span_cap,
                    ),
                )
        self._transition_cache[orbit] = cached
        return cached

    def _active_transition(self, u, orbit):
        for tr in self._transitions(orbit):
            if tr.span <= 1e-12:
                continue
            du = float(_wrap_2pi(u - tr.start))
            if du <= tr.span + 1e-12:
                return du, tr
        return None

    def refine_u_samples(self, u_arr, orbit):
        """Add extra samples across the transition windows."""
        refined = [np.asarray(u_arr, dtype=float).ravel()]
        base_step = 2 * math.pi / max(refined[0].size, 1)

        for tr in self._transitions(orbit):
            if tr.span <= 1e-12:
                continue
            m = max(self.transition_samples, int(math.ceil(tr.span / base_step)) + 1)
            offsets = np.linspace(0.0, tr.span, m)
            refined.append(_wrap_2pi(tr.start + offsets))

        return np.concatenate(refined)

    def __call__(self, u, orbit):
        active = self._active_transition(u, orbit)
        if active is None:
            return (self.eclipse_law if orbit.in_eclipse(u) else self.sunlit_law)(u, orbit)

        du, tr = active
        fraction = 1.0 if tr.span <= 1e-12 else du / tr.span
        return tr.start_attitude.slerp(tr.end_attitude, fraction)
