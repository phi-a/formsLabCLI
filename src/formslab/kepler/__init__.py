"""Orbit files: classical Keplerian elements, propagated by two-body motion.

    constants   physical constants
    sun         low-precision Sun ephemeris
    kepler      `Elements`, Kepler's equation, position and velocity, umbra, beta
    file        the `.orbit` file: its grammar, its errors, its tokens

Standard library only, so the GUI can show an orbit on a base install; the
heavier models (view factors, thermal) are `formslab.orbit`, behind the extra.
`constants` and `sun` are copies of `formslab.orbit.propagate`'s, because that
package imports nothing outside itself (test_orbit_layout) and this one must not
need numpy; test_kepler holds the copies identical.
"""
