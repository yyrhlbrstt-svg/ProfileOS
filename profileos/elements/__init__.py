"""Parametric opening builder: windows, doors and curtain walls."""

from __future__ import annotations

from .builder import (
    CRITICAL_HEIGHT_MM,
    LARGE_PANE_AREA_M2,
    ElementBuild,
    ElementBuilder,
    GasketRun,
    GlassPanel,
    HardwareItem,
    MemberCut,
    Rect,
    build_elements,
    collect_cut_items,
    safety_glass_required,
)
from .model import (
    Cell,
    ElementKind,
    ElevationSet,
    HingeSide,
    Opening,
    OpeningType,
    Sash,
)
from .rules import (
    DEFAULT_SYSTEM_RULES,
    SYSTEM_RULES_SCHEMA,
    FrameRules,
    GasketRules,
    GlassRules,
    MullionRules,
    SashRules,
    SystemRules,
    get_system_rules,
    register_system_rules,
)

__all__ = [
    "ElevationSet",
    "Opening", "Cell", "Sash", "OpeningType", "HingeSide", "ElementKind",
    "SystemRules", "FrameRules", "SashRules", "GlassRules", "GasketRules",
    "MullionRules", "DEFAULT_SYSTEM_RULES", "get_system_rules",
    "register_system_rules", "SYSTEM_RULES_SCHEMA",
    "Rect", "MemberCut", "GlassPanel", "GasketRun", "HardwareItem",
    "ElementBuild", "ElementBuilder", "build_elements", "collect_cut_items",
    "safety_glass_required", "CRITICAL_HEIGHT_MM", "LARGE_PANE_AREA_M2",
]
