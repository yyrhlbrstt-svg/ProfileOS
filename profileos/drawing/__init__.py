"""Shop drawings: elevations, wall sections, sheets and the package they form.

Everything is drawn in real millimetres and only meets paper at the sheet, so
the same elevation is issued at 1:20 on an A3 and 1:10 on an A1 without being
redrawn — and the stated scale is always the true one.

Output is DXF for the consultant to mark up, PDF for issue, and SVG for every
screen in the building.
"""

from __future__ import annotations

from .dimension import DimensionStyle, chain, leader, linear, overall
from .elevation import ElevationStyle, elevation, legend, opening_symbol
from .model import (
    Anchor,
    Arc,
    Circle,
    Drawing,
    Hatch,
    HatchPattern,
    Layer,
    Line,
    LineType,
    Polyline,
    STANDARD_LAYERS,
    Text,
    rectangle,
)
from .package import DrawingPackage, PackageInfo, build_package, detail_sheet, elevation_sheets
from .section import (
    Detail,
    RENDERED_BLOCK,
    STONE_CLAD_CONCRETE,
    SectionStyle,
    WallBuildUp,
    WallLayer,
    wall_section,
)
from .sheet import Revision, Sheet, SheetSize, TitleBlock, Viewport, grid_frames
from .svg import to_svg

__all__ = [
    "Anchor",
    "Arc",
    "Circle",
    "Detail",
    "DimensionStyle",
    "Drawing",
    "DrawingPackage",
    "ElevationStyle",
    "Hatch",
    "HatchPattern",
    "Layer",
    "Line",
    "LineType",
    "PackageInfo",
    "Polyline",
    "RENDERED_BLOCK",
    "Revision",
    "STANDARD_LAYERS",
    "STONE_CLAD_CONCRETE",
    "SectionStyle",
    "Sheet",
    "SheetSize",
    "Text",
    "TitleBlock",
    "Viewport",
    "WallBuildUp",
    "WallLayer",
    "build_package",
    "chain",
    "detail_sheet",
    "elevation",
    "elevation_sheets",
    "grid_frames",
    "leader",
    "legend",
    "linear",
    "opening_symbol",
    "overall",
    "rectangle",
    "to_svg",
    "wall_section",
]
