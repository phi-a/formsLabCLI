"""Orbits and the satellite's radiative environment.

Subpackages, lowest layer first. A layer imports only from layers above it
in this list.

    propagate    constants, Sun ephemeris, two-body motion from Keplerian
                 elements (`kepler`), the `Orbit` the models sweep (LVLH/ECI
                 frames, umbra) and circular-orbit formulas (beta, eclipse
                 arc, J2 RAAN drift)
    geometry     attitude laws, SO(3) tools, CubeSat body geometry
    viewfactor   Earth-disk / Sun / spacecraft view factors over an orbit
    thermal      radiative background, environment temperature, steady and
                 transient panel temperatures; ``pipeline`` chains them
    visibility   inertial-target visibility arcs within eclipse (circular orbits)
    imaging      image-pass optimisation and scheduling (circular orbits)

On top, ``file``: the `.orbit` file (docs/ORBIT.md), read into `Elements`, or by
``file.load`` into an `Orbit`. Nothing is imported here, so ``import
formslab.orbit`` stays cheap.
"""
