"""Thermal consumers built on top of geometric view-factor products.

Pipeline
--------
loading  ->  background   (W/m^2 per patch)
         ->  env          (Tenv [K] — TVAC boundary condition)
         ->  steady       (K per patch, equilibrium)
         ->  transient    (K per patch, orbit-integrated)

``pipeline`` chains these per facet for a whole spacecraft (``CubeSat``,
``view`` -> ``flux`` -> ``env``/``steady``/``transient``). Import it as a
module: its ``steady``/``transient`` work on layers, not single backgrounds.
``passes`` reduces one orbit to radiator pass measures (``evaluate_pass``).
``transient`` needs scipy and ``plots`` needs matplotlib (the ``orbit`` extra);
``plots`` is not imported here, so the rest works on a base install.
"""

from .materials import (                                     # noqa: F401
    SIGMA_SB,
    # Surface coating catalogue  (NA-104-STAR-001-R001, Table 4-3)
    Coating,
    SOLAR_CELL, CLEAR_HARD_ANODISED, WHITE_PAINT_A276_Z93,
    WHITE_SOLDERMASK, SURTEC_650, CLEAR_ANODISED,
    BLACK_HARD_ANODISED, KAPTON_1MIL, GREEN_SOLDERMASK,
    PEEK_COATING, PCL_SBAND_ANTENNA,
    # Bulk material properties
    Material,
    SOLAR_PANEL_DEP_6U,
    # Deployable solar panel wings
    SOLAR_PANEL_ETA_ELECTRICAL,
    SOLAR_PANEL_CELL_ALPHA_SOLAR,
    SOLAR_PANEL_CELL_EPSILON,
    SOLAR_PANEL_BACK_HOT,
    SOLAR_PANEL_BACK_COLD,
    SOLAR_PANEL_BACK_ALPHA_SOLAR,
    SOLAR_PANEL_BACK_EPSILON,
    SOLAR_PANEL_SUBSTRATE_AREAL_CAPACITANCE,
    # Bus radiator plates
    RADIATOR_ALPHA_SOLAR,
    RADIATOR_EPSILON,
)
from .background import Background, background                # noqa: F401
from .solver import (Thermal, Env,                            # noqa: F401
                     steady, steady_two_sided,
                     transient, environment)
from .serialize import save_temperatures, load_temperatures   # noqa: F401
from .passes import (BracketResult, RadiatorMeasures,          # noqa: F401
                     ThermalPassResult, evaluate_pass)
