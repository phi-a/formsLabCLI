"""Thermo-optical and thermal material properties for the DarkNESS CubeSat.

All surface coating values are sourced directly from the NanoAvionics
Satellite Thermal Analysis Report for the DarkNESS mission:

    Document : NA-104-STAR-001-R001  Revision 1  08/05/2024
    Table 4-3 : Thermo-optical material properties
    Table 5-3 : Components Thermal Capacitances
"""

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Physical constant
# ---------------------------------------------------------------------------

SIGMA_SB = 5.670374419e-8   # W / (m² K⁴)  Stefan-Boltzmann constant


# ---------------------------------------------------------------------------
# Surface coatings  —  Table 4-3, NA-104-STAR-001-R001
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Coating:
    """Thermo-optical properties of a surface treatment."""
    name:    str
    alpha:   float   # solar absorptivity  [-]
    epsilon: float   # IR emissivity       [-]


# ---------------------------------------------------------------------------
# Bulk material properties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Material:
    """Bulk thermo-physical properties of a structural material."""
    name:  str
    k:     float   # thermal conductivity  [W / (m K)]
    Cp:    float   # specific heat         [J / (kg K)]
    rho:   float   # density               [kg / m³]


SOLAR_CELL           = Coating("Solar Cell",             alpha=0.91, epsilon=0.91)
CLEAR_HARD_ANODISED  = Coating("Clear-hard Anodised",    alpha=0.62, epsilon=0.83)
WHITE_PAINT_A276_Z93 = Coating("White Paint A276_Z93",   alpha=0.20, epsilon=0.88)
WHITE_SOLDERMASK     = Coating("White Soldermask",       alpha=0.26, epsilon=0.88)
SURTEC_650           = Coating("Surtec 650",             alpha=0.16, epsilon=0.04)
CLEAR_ANODISED       = Coating("Clear Anodised",         alpha=0.34, epsilon=0.71)
BLACK_HARD_ANODISED  = Coating("Black-hard Anodised",    alpha=0.71, epsilon=0.83)
KAPTON_1MIL          = Coating("Kapton 1mil",            alpha=0.38, epsilon=0.67)
GREEN_SOLDERMASK     = Coating("Green Soldermask",       alpha=0.58, epsilon=0.88)
PEEK_COATING         = Coating("PEEK",                   alpha=0.30, epsilon=0.95)
PCL_SBAND_ANTENNA    = Coating("PCL SBand Antenna",      alpha=0.20, epsilon=0.60)


# ---------------------------------------------------------------------------
# Deployable solar panel wings  —  DarkNESS M6P
# ---------------------------------------------------------------------------

# GaAs triple-junction cells at AM0 beginning-of-life.
# NanoAvionics' professional model applies alpha_optical = 0.91 to the solar
# cell nodes and separately subtracts electrical output as a negative heat
# source (Section 5.4 / 7.2).  The solver does not model electrical
# extraction as a separate term, so alpha_thermal = alpha_optical - eta is
# used so that only heat deposited in the substrate enters the energy balance.
SOLAR_PANEL_ETA_ELECTRICAL = 0.28   # [-]  GaAs TJ BOL efficiency (AM0)

# Front face: Solar Cell coating (Table 4-3); emissivity from coating catalogue.
# Electrical efficiency deducted from optical absorptivity so only heat deposited
# in the substrate enters the energy balance.
SOLAR_PANEL_CELL_ALPHA_SOLAR = SOLAR_CELL.alpha - SOLAR_PANEL_ETA_ELECTRICAL  # 0.63
SOLAR_PANEL_CELL_EPSILON     = SOLAR_CELL.epsilon                              # 0.91

# Back face: coating is not formally baselined in NA-104-STAR-001-R001.
# Two brackets are used to bound the thermal response:
#   HOT  — Clear-hard Anodised AA7075 T6 backplate (high absorptivity / high emissivity)
#   COLD — Surtec 650 chromate conversion coating   (low absorptivity / very low emissivity)

SOLAR_PANEL_BACK_HOT  = CLEAR_HARD_ANODISED           # α=0.62  ε=0.83  (nominal / hot case)
SOLAR_PANEL_BACK_COLD = SURTEC_650                    # α=0.16  ε=0.04  (cold case bracket)
# Note: these two assignments were swapped until 2026-10 (HOT pointed at
# Surtec 650). The comments and the bracket description above were right.

# Convenience scalars kept for backward compatibility (hot bracket values).
SOLAR_PANEL_BACK_ALPHA_SOLAR = SOLAR_PANEL_BACK_HOT.alpha    # 0.62
SOLAR_PANEL_BACK_EPSILON     = SOLAR_PANEL_BACK_HOT.epsilon  # 0.83

# Areal thermal capacitance of one deployable leaf (Table 5-3, whole-stack).
# Per-leaf totals: SP Deployable PCB 138.1 J/K  +  Backplate 144.2 J/K
#                  +  Solar Cells 14.3 J/K  =  296.6 J/K
# Divided by nominal leaf area (~0.077 m²) gives ~3 850 J / (m² K).
SOLAR_PANEL_SUBSTRATE_AREAL_CAPACITANCE = 3850.0   # J / (m² K)  whole-stack (PCB + backplate + cells)

# Bulk material: Solar Panel Dep 6U substrate stack.
# Source: Solar Panel Dep 6U datasheet (per-panel mass / geometry, k from PCB laminate).
SOLAR_PANEL_DEP_6U = Material("Solar Panel Dep 6U", k=1.79, Cp=1084, rho=2009)


# ---------------------------------------------------------------------------
# Bus radiator plates  —  DarkNESS M6P  +Y / -Y faces
# ---------------------------------------------------------------------------

# Yp and Yn Radiator Plates: AA6061 T6 with White Paint A276_Z93 (Table 4-1).
RADIATOR_ALPHA_SOLAR = WHITE_PAINT_A276_Z93.alpha    # 0.20
RADIATOR_EPSILON     = WHITE_PAINT_A276_Z93.epsilon  # 0.88
