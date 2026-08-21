"""Rolling shutters, sized from the roll rather than from a rule of thumb.

The one number that matters on a shutter is the box, and it is the one number
most quotes guess. The curtain rolls onto an octagonal shaft, so the coil
grows as an Archimedean spiral: winding a length ``L`` of slat of thickness
``t`` onto a shaft of diameter ``d`` produces a coil of diameter

    D = sqrt(d^2 + 4 t L / pi)

which is exact for a tightly wound strip, because the cross-sectional area of
the wound material, ``t L``, equals the area of the annulus it occupies. Add
the clearance the curtain needs to turn and the wall of the box, and the box
height falls out. Getting this wrong by twenty millimetres means a builder
casts a lintel that the shutter does not fit under, and that is a wall opened
back up.

Everything else follows from the same three inputs: the curtain weight from
the slat's own mass per square metre, the motor from the weight and the drum
radius, and the guides from the height plus the box.

The slat range is what is stocked in Israel. Figures are the manufacturers'
published ones where they are published and typical where they are not, and
the difference is recorded on the slat itself rather than left for somebody to
assume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..core.errors import ProfileOSError
from .model import Accessory, AccessoryCut, AccessoryKind, AccessoryPart


class SlatKind(StrEnum):
    """The curtain itself."""

    PVC_39 = "pvc_39"
    PVC_45 = "pvc_45"
    PVC_55 = "pvc_55"
    ALU_39 = "alu_39"
    ALU_45 = "alu_45"
    ALU_55 = "alu_55"
    ALU_77 = "alu_77"


class Drive(StrEnum):
    """How the curtain is moved."""

    STRAP = "strap"
    CRANK = "crank"
    MOTOR = "motor"
    MOTOR_REMOTE = "motor_remote"
    MOTOR_SMART = "motor_smart"

    @property
    def hebrew(self) -> str:
        return {
            "strap": "רצועה",
            "crank": "מוט הפעלה",
            "motor": "מנוע עם מפסק",
            "motor_remote": "מנוע עם שלט",
            "motor_smart": "מנוע חכם",
        }[self.value]

    @property
    def is_motorised(self) -> bool:
        return self.value.startswith("motor")


class BoxPosition(StrEnum):
    """Where the box sits relative to the wall."""

    BUILT_IN = "built_in"
    SURFACE = "surface"
    INTEGRATED = "integrated"

    @property
    def hebrew(self) -> str:
        return {
            "built_in": "ארגז בנוי",
            "surface": "ארגז חיצוני",
            "integrated": "ארגז משולב במשקוף",
        }[self.value]


@dataclass(frozen=True)
class Slat:
    """One curtain profile: what it weighs and how thick it rolls."""

    slat_id: str
    hebrew: str
    #: Visible height of one slat once hung [mm].
    pitch: float
    #: Rolled thickness — what decides the coil, not the visible face [mm].
    thickness: float
    #: Curtain mass [kg/m^2].
    mass: float
    #: Widest curtain this slat is made for [mm].
    max_width: float
    insulated: bool = False
    #: What the figures rest on: a catalogue, or the typical range.
    source: str = "טיפוסי — לא מקטלוג היצרן"

    @property
    def is_aluminium(self) -> bool:
        return self.slat_id.startswith("alu")


#: The curtains stocked in Israel. PVC for bedrooms, extruded aluminium where
#: the span or the security asks for it, insulated (foam-filled) aluminium
#: where the shutter is also doing thermal work.
SLATS: tuple[Slat, ...] = (
    Slat("pvc_39", "PVC ⁦39⁩", 39.0, 8.5, 4.5, 1800),
    Slat("pvc_45", "PVC ⁦45⁩", 45.0, 9.0, 5.0, 2200),
    Slat("pvc_55", "PVC ⁦55⁩", 55.0, 13.0, 6.5, 2800),
    Slat("alu_39", "אלומיניום ⁦39⁩ מוקצף", 39.0, 9.0, 3.2, 2400, insulated=True),
    Slat("alu_45", "אלומיניום ⁦45⁩ מוקצף", 45.0, 9.5, 3.6, 3000, insulated=True),
    Slat("alu_55", "אלומיניום ⁦55⁩ מוקצף", 55.0, 14.0, 4.4, 4000, insulated=True),
    Slat("alu_77", "אלומיניום ⁦77⁩ אקסטרודד", 77.0, 19.0, 9.0, 6000),
)

#: Octagonal shafts, by across-flats size [mm]. The curtain winds on the
#: circumscribed circle, which is what the coil formula needs.
SHAFTS: tuple[float, ...] = (40.0, 50.0, 60.0, 70.0, 78.0, 102.0)

#: Box sizes a shop actually buys, inside height [mm].
BOX_SIZES: tuple[float, ...] = (137.0, 150.0, 165.0, 180.0, 205.0, 240.0, 250.0, 300.0)

#: Tubular motors, by rated torque [Nm], with the shaft each suits.
MOTORS: tuple[tuple[float, str, float], ...] = (
    (10.0, "מנוע צינורי ⁦10⁩ ניוטון-מטר", 40.0),
    (20.0, "מנוע צינורי ⁦20⁩ ניוטון-מטר", 50.0),
    (30.0, "מנוע צינורי ⁦30⁩ ניוטון-מטר", 60.0),
    (40.0, "מנוע צינורי ⁦40⁩ ניוטון-מטר", 70.0),
    (50.0, "מנוע צינורי ⁦50⁩ ניוטון-מטר", 78.0),
    (80.0, "מנוע צינורי ⁦80⁩ ניוטון-מטר", 102.0),
    (120.0, "מנוע צינורי ⁦120⁩ ניוטון-מטר", 102.0),
    (170.0, "מנוע תעשייתי ⁦170⁩ ניוטון-מטר", 102.0),
)

#: Clearance between the coil and the inside of the box [mm]. The curtain has
#: to turn, and the guides have to enter, without touching.
BOX_CLEARANCE = 22.0
#: How much of the curtain never unrolls, so it is always on the shaft [mm].
RESIDUAL_TURNS_MM = 250.0
#: Guides run past the box to the sill.
GUIDE_DEPTH_MM = 22.0
#: A motor is required above this curtain weight, whatever was asked for [kg].
MANUAL_LIMIT_KG = 22.0
#: A strap cannot lift more than this [kg].
STRAP_LIMIT_KG = 14.0


def slat(slat_id: str | SlatKind) -> Slat:
    """One curtain profile by name."""
    wanted = str(slat_id)
    for candidate in SLATS:
        if candidate.slat_id == wanted:
            return candidate
    raise ProfileOSError(
        f"אין תריס בשם {wanted}. הקיימים: " + ", ".join(s.slat_id for s in SLATS)
    )


def coil_diameter(curtain_length: float, thickness: float, shaft: float) -> float:
    """Diameter of the rolled-up curtain [mm].

    The wound strip occupies an annulus whose area equals its own cross
    section, ``t * L``. Solving ``pi/4 (D^2 - d^2) = t L`` gives the diameter
    the box has to swallow. This is the calculation a rule of thumb replaces,
    and the reason rules of thumb build the wrong lintel.
    """
    if curtain_length <= 0 or thickness <= 0 or shaft <= 0:
        raise ProfileOSError("אורך, עובי וציר חייבים להיות חיוביים")
    return math.sqrt(shaft**2 + 4.0 * thickness * curtain_length / math.pi)


def choose_shaft(curtain_length: float, mass: float, width: float) -> float:
    """The shaft this curtain needs, by weight and by span.

    A long shaft sags under a heavy curtain, and a sagging shaft drags the
    curtain against one guide. Both the load and the span push the size up.
    """
    for shaft in SHAFTS:
        # A rough serviceability rule: the shaft's own stiffness scales with
        # the fourth power of its size, so a heavier or wider curtain steps up.
        capacity = (shaft / 40.0) ** 3 * 900.0
        demand = mass * (width / 1000.0)
        if demand <= capacity and width <= shaft * 45.0:
            return shaft
    return SHAFTS[-1]


def choose_box(coil: float) -> float:
    """The smallest stock box the coil fits in, with clearance."""
    needed = coil + BOX_CLEARANCE
    for size in BOX_SIZES:
        if size >= needed:
            return size
    return needed


def choose_motor(mass: float, coil: float) -> tuple[float, str] | None:
    """Torque to lift this curtain off the largest coil radius, plus margin.

    The worst case is the shutter fully down and the curtain hanging from the
    coil at its smallest — but the motor also has to start the roll at the
    largest radius, which is where the torque demand peaks.
    """
    radius = coil / 2000.0  # [m]
    demand = mass * 9.81 * radius * 1.4  # [Nm], with a starting margin
    for torque, name, _shaft in MOTORS:
        if torque >= demand:
            return torque, name
    return None


@dataclass(frozen=True)
class ShutterSpec:
    """What was asked for. Everything else is computed."""

    slat_id: str = "alu_45"
    drive: Drive = Drive.MOTOR
    box: BoxPosition = BoxPosition.BUILT_IN
    #: Guides are usually the same colour as the window.
    finish: str = ""
    #: A shutter fitted outside the frame is wider than the window.
    guide_width: float = 45.0
    quantity: int = 1


def size_shutter(
    width: float,
    height: float,
    spec: ShutterSpec | None = None,
    *,
    quantity: int = 1,
) -> Accessory:
    """Size one rolling shutter for an opening of this size.

    ``width`` and ``height`` are the window's, not the shutter's: the curtain
    runs inside guides that sit outside the frame, and the box sits above it.
    What comes back is the shutter as it will be ordered — the curtain, the
    box, the guides, the shaft, the drive — together with the hole in the wall
    it all needs, which is the number the builder is waiting for.
    """
    spec = spec or ShutterSpec()
    profile = slat(spec.slat_id)

    if width <= 0 or height <= 0:
        raise ProfileOSError("מידות התריס חייבות להיות חיוביות")

    curtain_width = width + 2 * (spec.guide_width - GUIDE_DEPTH_MM)
    curtain_length = height + RESIDUAL_TURNS_MM
    area = curtain_width * height / 1_000_000.0
    mass = area * profile.mass

    shaft = choose_shaft(curtain_length, mass, curtain_width)
    coil = coil_diameter(curtain_length, profile.thickness, shaft)
    box = choose_box(coil)
    slat_count = math.ceil(height / profile.pitch)

    warnings: list[str] = []
    notes: list[str] = []

    if curtain_width > profile.max_width:
        warnings.append(
            f"⁦{curtain_width:.0f}⁩ מ״מ רחב מדי ל{profile.hebrew} "
            f"(עד ⁦{profile.max_width:.0f}⁩ מ״מ) — פצל לשני תריסים או עבור לשלב כבד יותר"
        )

    drive = spec.drive
    if not drive.is_motorised and mass > MANUAL_LIMIT_KG:
        warnings.append(
            f"וילון של ⁦{mass:.1f}⁩ ק״ג כבד מדי להפעלה ידנית — נדרש מנוע"
        )
    if drive is Drive.STRAP and mass > STRAP_LIMIT_KG:
        warnings.append(f"רצועה מרימה עד ⁦{STRAP_LIMIT_KG:.0f}⁩ ק״ג בלבד")

    parts: list[AccessoryPart] = []
    if drive.is_motorised:
        chosen = choose_motor(mass, coil)
        if chosen is None:
            warnings.append("אין מנוע במלאי לוילון בעומס הזה")
        else:
            torque, motor_name = chosen
            parts.append(AccessoryPart(
                f"MOT-{torque:.0f}", motor_name, 1, "pc",
                note=f"נדרש ⁦{mass * 9.81 * coil / 2000.0 * 1.4:.1f}⁩ ניוטון-מטר",
            ))
            if drive is Drive.MOTOR:
                parts.append(AccessoryPart("SW-1", "מפסק תריס", 1, "pc"))
            elif drive is Drive.MOTOR_REMOTE:
                parts.append(AccessoryPart("RC-1", "שלט רחוק", 1, "pc"))
            else:
                parts.append(AccessoryPart("RC-SMART", "בקר חכם ⁦Wi-Fi⁩", 1, "pc"))
            notes.append("נדרשת הכנת חשמל ⁦230V⁩ בארגז — לפני הטיח")
    elif drive is Drive.STRAP:
        parts.append(AccessoryPart("STRAP", "רצועה וגלגלת", 1, "set"))
        parts.append(AccessoryPart("STRAP-BOX", "בית רצועה שקוע", 1, "pc"))
    else:
        parts.append(AccessoryPart("CRANK", "מוט הפעלה ומעביר זווית", 1, "set"))

    parts.append(AccessoryPart(f"SHAFT-{shaft:.0f}", f"ציר מתומן ⁦{shaft:.0f}⁩", 1, "pc"))
    parts.append(AccessoryPart("SUSP", "רצועות תלייה", max(2, round(curtain_width / 500)), "pc"))
    parts.append(AccessoryPart("STOP", "בולמי גומי", 2, "pc"))
    parts.append(AccessoryPart("ENDCAP", "פלנצ׳ים ומיסבים", 1, "pair"))
    if spec.box is BoxPosition.BUILT_IN:
        parts.append(AccessoryPart("HATCH", "דלת שירות לארגז", 1, "pc"))

    guide_length = height + box
    cuts = [
        AccessoryCut("guide", "מוביל", f"SHUT-GUIDE-{spec.guide_width:.0f}",
                     guide_length, 2),
        AccessoryCut("box", "ארגז", f"SHUT-BOX-{box:.0f}", curtain_width + 40.0, 1),
        AccessoryCut("bottom_rail", "שלב תחתון", "SHUT-BOTTOM", curtain_width - 4.0, 1),
        AccessoryCut("slats", "שלבים", f"SHUT-{profile.slat_id.upper()}",
                     curtain_width - 4.0, slat_count),
    ]

    accessory = Accessory(
        kind=AccessoryKind.SHUTTER,
        code=f"SHUT-{profile.slat_id}-{drive.value}",
        hebrew=f"תריס גלילה {profile.hebrew} · {drive.hebrew}",
        width=curtain_width,
        height=height,
        quantity=quantity * spec.quantity,
        cuts=cuts,
        parts=parts,
        mass=mass,
        head_allowance=box,
        side_allowance=spec.guide_width - GUIDE_DEPTH_MM,
        warnings=warnings,
        notes=notes,
        metadata={
            "slat": profile.slat_id,
            "slat_source": profile.source,
            "slat_count": slat_count,
            "curtain_length_mm": round(curtain_length, 1),
            "coil_diameter_mm": round(coil, 1),
            "shaft_mm": shaft,
            "box_mm": box,
            "box_position": spec.box.value,
            "drive": drive.value,
            "mass_kg": round(mass, 2),
            "insulated": profile.insulated,
        },
    )
    accessory.notes.append(
        f"ארגז ⁦{box:.0f}⁩ מ״מ ({spec.box.hebrew}) — גליל ⁦{coil:.0f}⁩ מ״מ על ציר ⁦{shaft:.0f}⁩"
    )
    return accessory


__all__ = [
    "BOX_SIZES",
    "BoxPosition",
    "Drive",
    "MOTORS",
    "SHAFTS",
    "SLATS",
    "Slat",
    "SlatKind",
    "ShutterSpec",
    "choose_box",
    "choose_motor",
    "choose_shaft",
    "coil_diameter",
    "size_shutter",
    "slat",
]
