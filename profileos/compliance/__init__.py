"""What the window has to satisfy, and what this software can honestly say.

Four separate physics — heat, sound, wind, safety — read together as one
sheet, with every figure carrying how far it can be trusted.
"""

from __future__ import annotations

from .acoustic import (
    AcousticEstimate,
    OPENING_PENALTY,
    SHUTTER_BOX_PENALTY,
    SealClass,
    estimate_acoustic,
    pane_reduction,
    unit_reduction,
)
from .report import ComplianceReport, Finding, Site, Verdict, check_compliance
from .standards import STANDARDS, Confidence, Standard, standard, standards_for
from .thermal import FrameClass, Spacer, WindowThermal, window_u_value
from .wind import (
    AIR_CLASSES,
    FacadeZone,
    PerformanceClasses,
    Terrain,
    WATER_CLASSES,
    WIND_CLASSES,
    WindCase,
    design_pressure,
    peak_velocity_pressure,
    required_classes,
)

__all__ = [
    "AIR_CLASSES",
    "AcousticEstimate",
    "ComplianceReport",
    "Confidence",
    "FacadeZone",
    "Finding",
    "FrameClass",
    "OPENING_PENALTY",
    "PerformanceClasses",
    "SHUTTER_BOX_PENALTY",
    "STANDARDS",
    "SealClass",
    "Site",
    "Spacer",
    "Standard",
    "Terrain",
    "Verdict",
    "WATER_CLASSES",
    "WIND_CLASSES",
    "WindCase",
    "WindowThermal",
    "check_compliance",
    "design_pressure",
    "estimate_acoustic",
    "pane_reduction",
    "peak_velocity_pressure",
    "required_classes",
    "standard",
    "standards_for",
    "unit_reduction",
    "window_u_value",
]
