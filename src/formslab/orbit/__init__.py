"""Orbit tools: a simple orbit and radiative-environment model.

Subpackages, lowest layer first. A layer imports only from layers above it
in this list.

    propagate    constants, Sun ephemeris, circular-orbit geometry (beta,
                 eclipse, LVLH/ECI frames, J2 RAAN drift)
    geometry     attitude laws, SO(3) tools, CubeSat body geometry
    viewfactor   Earth-disk / Sun / spacecraft view factors over an orbit
    thermal      radiative background, environment temperature, steady and
                 transient panel temperatures; ``pipeline`` chains them
    visibility   inertial-target visibility arcs within eclipse
    imaging      image-pass optimisation and scheduling

Nothing is imported here, so ``import orbit`` stays cheap.
"""
