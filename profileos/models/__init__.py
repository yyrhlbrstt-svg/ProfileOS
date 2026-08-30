"""Domain models shared by every ProfileOS engine."""

from __future__ import annotations

from .machines import (
    Axis,
    Clamp,
    ClampType,
    MachineDefinition,
    MachineKind,
    Tool,
    ToolLibrary,
    ToolType,
)
from .materials import (
    BUILTIN_MATERIALS,
    DEFAULT_MATERIAL_ID,
    Material,
    MaterialClass,
    all_materials,
    get_material,
)
from .orders import (
    CutItem,
    CutOrientation,
    CutPiece,
    Priority,
    Project,
    RemnantBar,
    StockBar,
)
from .profile import (
    Face,
    MachiningMacro,
    OuterDimensions,
    ProfileDefinition,
    ProfileRole,
    ProfileSectionGeometry,
    ThermalBreakStrip,
    Vertex,
    bulge_to_arc,
)
from .results import GeometryReport, SectionProperties, WallThicknessReport

__all__ = [
    # materials
    "Material",
    "MaterialClass",
    "BUILTIN_MATERIALS",
    "DEFAULT_MATERIAL_ID",
    "get_material",
    "all_materials",
    # profile
    "Face",
    "ProfileRole",
    "Vertex",
    "OuterDimensions",
    "ThermalBreakStrip",
    "ProfileSectionGeometry",
    "MachiningMacro",
    "ProfileDefinition",
    "bulge_to_arc",
    # machines
    "MachineKind",
    "ToolType",
    "Tool",
    "ToolLibrary",
    "ClampType",
    "Clamp",
    "Axis",
    "MachineDefinition",
    # orders
    "CutOrientation",
    "Priority",
    "CutItem",
    "CutPiece",
    "RemnantBar",
    "StockBar",
    "Project",
    # results
    "SectionProperties",
    "WallThicknessReport",
    "GeometryReport",
]
