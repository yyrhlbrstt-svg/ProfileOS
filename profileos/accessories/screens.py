"""Insect screens, sills and trims — the rest of what actually gets fitted.

An Israeli window without a screen is a window somebody comes back to
complain about, and a screen is not free: it has its own frame to cut, its own
mesh to buy and, on a slider, its own rail on the window. Sills and trims are
smaller money and bigger arguments — the sill projection decides where the
water goes, and the trim is what hides the gap the builder left.

Each of these sizes itself from the opening and produces its own cut list, so
none of them is remembered separately or forgotten separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..core.errors import ProfileOSError
from .model import Accessory, AccessoryCut, AccessoryKind, AccessoryPart


class ScreenKind(StrEnum):
    """How the screen is carried."""

    SLIDING = "sliding"
    FIXED = "fixed"
    HINGED = "hinged"
    ROLL = "roll"
    PLEATED = "pleated"

    @property
    def hebrew(self) -> str:
        return {
            "sliding": "רשת הזזה",
            "fixed": "רשת קבועה",
            "hinged": "רשת על ציר",
            "roll": "רשת גלילה",
            "pleated": "רשת פליסה",
        }[self.value]


class MeshKind(StrEnum):
    """What the mesh is made of, which is what it is chosen for."""

    FIBREGLASS = "fibreglass"
    ALUMINIUM = "aluminium"
    PET_PET = "pet"
    STAINLESS = "stainless"

    @property
    def hebrew(self) -> str:
        return {
            "fibreglass": "פיברגלס",
            "aluminium": "אלומיניום",
            "pet": "פוליאסטר מחוזק",
            "stainless": "נירוסטה — נגד חתולים",
        }[self.value]

    @property
    def mass(self) -> float:
        """Mesh mass [kg/m^2]."""
        return {
            "fibreglass": 0.12,
            "aluminium": 0.22,
            "pet": 0.30,
            "stainless": 0.55,
        }[self.value]


#: The widest leaf each kind of screen is made in [mm]. Past it the screen
#: racks in its own frame and stops sliding, which is the complaint.
MAX_LEAF_WIDTH: dict[str, float] = {
    "sliding": 1200.0,
    "fixed": 1500.0,
    "hinged": 900.0,
    "roll": 1600.0,
    "pleated": 2600.0,
}
#: The tallest each kind is made in [mm].
MAX_HEIGHT: dict[str, float] = {
    "sliding": 2600.0,
    "fixed": 2600.0,
    "hinged": 2400.0,
    "roll": 2600.0,
    "pleated": 3000.0,
}


@dataclass(frozen=True)
class ScreenSpec:
    """What was asked for."""

    kind: ScreenKind = ScreenKind.SLIDING
    mesh: MeshKind = MeshKind.FIBREGLASS
    #: How many leaves the screen is split into; 0 means "work it out".
    leaves: int = 0
    finish: str = ""
    quantity: int = 1


def size_screen(
    width: float,
    height: float,
    spec: ScreenSpec | None = None,
    *,
    quantity: int = 1,
) -> Accessory:
    """Size an insect screen for an opening, splitting it if it is too wide."""
    spec = spec or ScreenSpec()
    if width <= 0 or height <= 0:
        raise ProfileOSError("מידות הרשת חייבות להיות חיוביות")

    kind = spec.kind
    max_leaf = MAX_LEAF_WIDTH[kind.value]
    leaves = spec.leaves or max(1, math.ceil(width / max_leaf))
    leaf_width = width / leaves

    warnings: list[str] = []
    if leaf_width > max_leaf:
        warnings.append(
            f"כנף רשת ⁦{leaf_width:.0f}⁩ מ״מ רחבה מדי ל{kind.hebrew} "
            f"(עד ⁦{max_leaf:.0f}⁩ מ״מ)"
        )
    if height > MAX_HEIGHT[kind.value]:
        warnings.append(
            f"⁦{height:.0f}⁩ מ״מ גבוה מדי ל{kind.hebrew} "
            f"(עד ⁦{MAX_HEIGHT[kind.value]:.0f}⁩ מ״מ)"
        )

    mesh_area = width * height / 1_000_000.0
    mass = mesh_area * spec.mesh.mass + (width + height) * 2 * leaves * 0.0004

    cuts: list[AccessoryCut] = []
    parts: list[AccessoryPart] = []

    if kind in (ScreenKind.SLIDING, ScreenKind.FIXED, ScreenKind.HINGED):
        cuts.append(AccessoryCut("screen_vertical", "מסגרת רשת — אנכי",
                                 "SCR-FRAME", height - 4.0, 2 * leaves))
        cuts.append(AccessoryCut("screen_horizontal", "מסגרת רשת — אופקי",
                                 "SCR-FRAME", leaf_width - 4.0, 2 * leaves))
        parts.append(AccessoryPart("SCR-CORNER", "פינות מסגרת", 4 * leaves, "pc"))
        parts.append(AccessoryPart("SCR-SPLINE", "גומי הידוק רשת",
                                   round((leaf_width + height) * 2 * leaves / 1000.0, 2), "m"))
        if kind is ScreenKind.SLIDING:
            cuts.append(AccessoryCut("screen_rail", "מסילת רשת",
                                     "SCR-RAIL", width, 1))
            parts.append(AccessoryPart("SCR-ROLLER", "גלגלות רשת", 4 * leaves, "pc"))
            parts.append(AccessoryPart("SCR-BRUSH", "מברשת אטימה",
                                       round(height * 2 * leaves / 1000.0, 2), "m"))
            parts.append(AccessoryPart("SCR-HANDLE", "ידית רשת", leaves, "pc"))
        elif kind is ScreenKind.HINGED:
            parts.append(AccessoryPart("SCR-HINGE", "צירי רשת", 2 * leaves, "pair"))
            parts.append(AccessoryPart("SCR-CATCH", "מגנט סגירה", leaves, "pc"))
        else:
            parts.append(AccessoryPart("SCR-CLIP", "קליפסים לקיבוע", 4 * leaves, "pc"))
    elif kind is ScreenKind.ROLL:
        cuts.append(AccessoryCut("screen_guide", "מוביל רשת גלילה",
                                 "SCR-ROLL-GUIDE", height, 2))
        cuts.append(AccessoryCut("screen_box", "ארגז רשת גלילה",
                                 "SCR-ROLL-BOX", width, 1))
        parts.append(AccessoryPart("SCR-ROLL-SPRING", "מנגנון קפיץ", 1, "set"))
        parts.append(AccessoryPart("SCR-ROLL-BAR", "מוט תחתון", 1, "pc"))
    else:  # pleated
        cuts.append(AccessoryCut("screen_guide", "מסילת פליסה",
                                 "SCR-PLEAT-RAIL", width, 2))
        cuts.append(AccessoryCut("screen_post", "עמוד פליסה",
                                 "SCR-PLEAT-POST", height, 1 + leaves))
        parts.append(AccessoryPart("SCR-PLEAT-KIT", "ערכת חוטים ומתחים", 1, "set"))

    parts.append(AccessoryPart(
        f"MESH-{spec.mesh.value}", f"רשת {spec.mesh.hebrew}",
        round(mesh_area * 1.15, 2), "m2",
        note="כולל ⁦15%⁩ פחת חיתוך",
    ))

    return Accessory(
        kind=AccessoryKind.SCREEN,
        code=f"SCR-{kind.value}-{spec.mesh.value}",
        hebrew=f"{kind.hebrew} · {spec.mesh.hebrew}",
        width=width,
        height=height,
        quantity=quantity * spec.quantity,
        cuts=cuts,
        parts=parts,
        mass=mass,
        warnings=warnings,
        metadata={
            "kind": kind.value,
            "mesh": spec.mesh.value,
            "leaves": leaves,
            "leaf_width_mm": round(leaf_width, 1),
            "mesh_area_m2": round(mesh_area, 3),
        },
    )


# --------------------------------------------------------------------------- #
# Sills and trims
# --------------------------------------------------------------------------- #

class SillKind(StrEnum):
    ALUMINIUM = "aluminium"
    STONE = "stone"
    NONE = "none"

    @property
    def hebrew(self) -> str:
        return {
            "aluminium": "אדן אלומיניום",
            "stone": "אדן אבן",
            "none": "ללא אדן",
        }[self.value]


#: Minimum fall on a sill so water leaves rather than sits [degrees].
MINIMUM_SILL_FALL_DEG = 5.0
#: How far the sill has to stand off the wall so the drip does not run back.
MINIMUM_PROJECTION_MM = 30.0


def size_sill(
    width: float,
    *,
    projection: float = 150.0,
    kind: SillKind = SillKind.ALUMINIUM,
    fall_deg: float = 7.0,
    quantity: int = 1,
) -> Accessory:
    """An external sill, with the checks that decide whether water leaves."""
    warnings: list[str] = []
    if fall_deg < MINIMUM_SILL_FALL_DEG:
        warnings.append(
            f"שיפוע ⁦{fall_deg:.0f}°⁩ קטן מהמינימום ⁦{MINIMUM_SILL_FALL_DEG:.0f}°⁩ — "
            "מים יעמדו על האדן"
        )
    if projection < MINIMUM_PROJECTION_MM:
        warnings.append(
            f"בליטה ⁦{projection:.0f}⁩ מ״מ קטנה מדי — הטפטוף יחזור אל הקיר"
        )

    # The sill runs past the opening on both sides so the water clears it.
    length = width + 60.0
    return Accessory(
        kind=AccessoryKind.SILL,
        code=f"SILL-{kind.value}-{projection:.0f}",
        hebrew=f"{kind.hebrew} ⁦{projection:.0f}⁩ מ״מ",
        width=length,
        height=projection,
        quantity=quantity,
        cuts=(
            [AccessoryCut("sill", "אדן", f"SILL-{projection:.0f}", length, 1)]
            if kind is SillKind.ALUMINIUM else []
        ),
        parts=[
            AccessoryPart("SILL-END", "סוגרי קצה", 2, "pair"),
            AccessoryPart("SILL-SEAL", "אטם סיליקון", 1, "pc"),
        ],
        mass=length * projection / 1_000_000.0 * (6.5 if kind is SillKind.ALUMINIUM else 55.0),
        warnings=warnings,
        metadata={"kind": kind.value, "projection_mm": projection, "fall_deg": fall_deg},
    )


def size_trim(
    width: float,
    height: float,
    *,
    face: float = 40.0,
    three_sided: bool = False,
    quantity: int = 1,
) -> Accessory:
    """The frame that hides the gap between the window and the builder's hole."""
    perimeter_cuts = [
        AccessoryCut("trim_vertical", "מסגרת — אנכי", f"TRIM-{face:.0f}", height, 2),
        AccessoryCut("trim_head", "מסגרת — עליון", f"TRIM-{face:.0f}", width, 1),
    ]
    if not three_sided:
        perimeter_cuts.append(
            AccessoryCut("trim_sill", "מסגרת — תחתון", f"TRIM-{face:.0f}", width, 1)
        )
    length = sum(cut.total_length for cut in perimeter_cuts)
    return Accessory(
        kind=AccessoryKind.TRIM,
        code=f"TRIM-{face:.0f}",
        hebrew=f"מסגרת היקפית ⁦{face:.0f}⁩ מ״מ",
        width=width,
        height=height,
        quantity=quantity,
        cuts=perimeter_cuts,
        parts=[AccessoryPart("TRIM-CLIP", "קליפסים", max(4, round(length / 400)), "pc")],
        mass=length / 1000.0 * face / 1000.0 * 2.7 * 1.4,
        metadata={"face_mm": face, "three_sided": three_sided},
    )


__all__ = [
    "MAX_HEIGHT",
    "MAX_LEAF_WIDTH",
    "MINIMUM_PROJECTION_MM",
    "MINIMUM_SILL_FALL_DEG",
    "MeshKind",
    "ScreenKind",
    "ScreenSpec",
    "SillKind",
    "size_screen",
    "size_sill",
    "size_trim",
]
