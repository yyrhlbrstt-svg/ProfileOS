"""Openings that are not rectangles, which is most of the interesting ones.

Every screen in this suite so far has assumed a window is a box. Most of them
are. The ones that are not — the arched head over a stairwell, the raked head
under a mono-pitch roof, the gable triangle, the round window in a stone wall —
are the ones a customer remembers, the ones a competitor quotes and wins, and
the ones a shop gets wrong because it worked the price out on the enclosing
rectangle and cut the head square.

This module does the geometry those need, and it does three things a
spreadsheet will not.

**Area is the real area.** An arched head priced on its bounding box is priced
on glass and coating the shop will never buy; a gable triangle on its box is
priced at twice what it is. Every figure here is the enclosed polygon, which
is what the glass costs and what the anodising bath charges for.

**A corner has an angle and a saw has a limit.** A raked head meets its jamb
at something other than ninety degrees, and the mitre a saw is set to is half
the included angle. Where that lies outside what the machine can swing, it is
refused by name rather than posted and discovered at the saw.

**Bending is somebody else's operation on somebody else's machine.** A curved
member has a developed length, a minimum radius, and a length of straight leg
the bender needs to grip. The developed length is arithmetic and is computed.
The minimum radius and the grip allowance are figures that belong to the
profile and to the bender, and if nobody has confirmed them the curved member
is reported as unorderable rather than given a plausible length. A bar cut to
a guessed developed length is a bar in the skip, and a bent one costs four
times as much to guess at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("elements.shapes")

#: What most double-mitre saws in a small shop will actually swing. It is a
#: machine figure and is overridden from the machine's own record wherever one
#: exists; this is the fallback, and it is a fallback that refuses work rather
#: than one that accepts it.
DEFAULT_MIN_MITRE_DEG = 22.5
DEFAULT_MAX_MITRE_DEG = 90.0


class Shape(StrEnum):
    """The outlines a fabricator is actually asked for."""

    RECTANGLE = "rectangle"
    #: One jamb taller than the other, straight head between them.
    RAKED = "raked"
    #: Both jambs equal, apex in the middle — a gable.
    TRIANGLE = "triangle"
    #: A segmental or semicircular head on straight jambs.
    ARCHED = "arched"
    #: A full half-circle sitting on a chord, with no straight jambs.
    HALF_ROUND = "half_round"
    CIRCLE = "circle"

    @property
    def hebrew(self) -> str:
        return {
            "rectangle": "מלבן",
            "raked": "משופע",
            "triangle": "משולש",
            "arched": "קשת",
            "half_round": "חצי עיגול",
            "circle": "עיגול",
        }[self.value]

    @property
    def is_curved(self) -> bool:
        return self in (Shape.ARCHED, Shape.HALF_ROUND, Shape.CIRCLE)


@dataclass(frozen=True)
class Bending:
    """What the bender needs, and what the profile will stand.

    Nothing here has a default. A minimum radius is a property of a particular
    extrusion in a particular alloy and temper, on a particular bender's
    machine, and the number that gets guessed is the number that cracks the
    outer face on the shop floor.
    """

    #: Smallest radius this profile may be bent to [mm], from the maker.
    minimum_radius: float | None = None
    #: Straight length the bender needs at each end to grip [mm].
    grip_allowance: float | None = None
    source: str = ""

    @property
    def is_confirmed(self) -> bool:
        return (
            self.minimum_radius is not None
            and self.grip_allowance is not None
            and bool(self.source.strip())
        )

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.minimum_radius is None:
            found.append("לא ידוע רדיוס כיפוף מזערי לפרופיל")
        if self.grip_allowance is None:
            found.append("לא ידועה תוספת אחיזה לכופף")
        if not self.source.strip():
            found.append("לנתוני הכיפוף אין מקור")
        return found


@dataclass
class Corner:
    """One joint between two members, and what the saw has to be set to."""

    name: str
    #: The angle inside the frame at this corner [deg].
    included: float

    @property
    def mitre(self) -> float:
        """What each of the two members is cut at [deg].

        A mitred joint splits the included angle between the two members, so a
        square corner is two ⁦45°⁩ cuts and a ⁦60°⁩ gable apex is two ⁦30°⁩ ones.
        """
        return round(self.included / 2.0, 2)

    def describe(self) -> str:
        return (
            f"{self.name}: פינה ⁦{self.included:.1f}°⁩ · "
            f"חיתוך ⁦{self.mitre:.1f}°⁩ לכל צד"
        )


@dataclass
class Member:
    """One frame member of a shaped opening."""

    role: str
    #: Straight length, or the developed length along the neutral axis for a
    #: curved member. ``None`` when it cannot be known.
    length: float | None
    angle_start: float = 90.0
    angle_end: float = 90.0
    #: Bending radius at the neutral axis [mm], for a curved member.
    radius: float | None = None
    is_curved: bool = False
    note: str = ""

    @property
    def is_orderable(self) -> bool:
        return self.length is not None and self.length > 0

    def describe(self) -> str:
        if not self.is_orderable:
            return f"{self.role}: אורך לא ידוע"
        body = f"{self.role}: ⁦{self.length:.1f}⁩ מ״מ"
        if self.is_curved and self.radius:
            body += f" · רדיוס ⁦{self.radius:.0f}⁩ מ״מ (אורך מפותח)"
        else:
            body += (
                f" · ⁦{self.angle_start:.1f}°⁩ / ⁦{self.angle_end:.1f}°⁩"
            )
        return body


@dataclass
class Outline:
    """A shaped opening worked out: its area, its members and its corners."""

    shape: Shape
    width: float
    height: float
    #: The second height, for a raked opening [mm].
    height_right: float | None = None
    #: Rise of an arched head above its springing [mm].
    rise: float | None = None
    members: list[Member] = field(default_factory=list)
    corners: list[Corner] = field(default_factory=list)
    #: The enclosed outline as points, for the drawing and the glass shape.
    points: list[tuple[float, float]] = field(default_factory=list)
    area_mm2: float = 0.0
    perimeter: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def area(self) -> float:
        """Enclosed area [m²] — the real one, not the bounding box."""
        return round(self.area_mm2 / 1_000_000.0, 4)

    @property
    def bounding_area(self) -> float:
        return round(self.width * self.height / 1_000_000.0, 4)

    @property
    def waste_against_box(self) -> float:
        """How much of the enclosing rectangle is not the opening.

        The number that says how badly a price worked out on the bounding box
        is wrong — and, separately, how much glass offcut the shape will make.
        """
        box = self.bounding_area
        return round((box - self.area) / box * 100.0, 1) if box else 0.0

    @property
    def has_curved_members(self) -> bool:
        return any(member.is_curved for member in self.members)

    @property
    def is_orderable(self) -> bool:
        return bool(self.members) and all(
            member.is_orderable for member in self.members
        )

    def member(self, role: str) -> Member:
        for entry in self.members:
            if entry.role == role:
                return entry
        raise ProfileOSError(f"אין מוט בתפקיד {role} בפתח הזה")

    def describe(self) -> str:
        return (
            f"{self.shape.hebrew} ⁦{self.width:g}×{self.height:g}⁩ · "
            f"שטח ⁦{self.area:.3f}⁩ מ״ר "
            f"(⁦{self.waste_against_box:.0f}%⁩ פחות ממלבן חוסם)"
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("צורה", self.shape.hebrew),
            ("מידה חוסמת", f"⁦{self.width:g}×{self.height:g}⁩ מ״מ"),
            ("שטח אמיתי", f"⁦{self.area:.3f}⁩ מ״ר"),
            ("שטח מלבן חוסם", f"⁦{self.bounding_area:.3f}⁩ מ״ר"),
            ("היקף", f"⁦{self.perimeter:.0f}⁩ מ״מ"),
            ("מוטות", f"⁦{len(self.members)}⁩"),
        ]


def _polygon_area(points: Iterable[tuple[float, float]]) -> float:
    """Shoelace. Always positive, whichever way round the points run."""
    kept = list(points)
    if len(kept) < 3:
        return 0.0
    total = 0.0
    for index in range(len(kept)):
        x1, y1 = kept[index]
        x2, y2 = kept[(index + 1) % len(kept)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def arc_radius(width: float, rise: float) -> float:
    """Radius of a circular arc of the given chord and rise.

    ``R = (c²/4 + r²) / (2r)`` — the standard relation, and the one every arch
    on a joinery drawing is set out from.
    """
    if rise <= 0:
        raise ProfileOSError("קשת בלי גובה אינה קשת")
    if width <= 0:
        raise ProfileOSError("קשת בלי מיתר אינה קשת")
    return (width * width / 4.0 + rise * rise) / (2.0 * rise)


def arc_length(width: float, rise: float) -> float:
    """Length along a circular arc of the given chord and rise [mm]."""
    radius = arc_radius(width, rise)
    half_chord = min(width / 2.0, radius)
    half_angle = math.asin(half_chord / radius)
    if rise > width / 2.0:  # a major arc: more than a half circle
        half_angle = math.pi - half_angle
    return 2.0 * half_angle * radius


def _segment_area(width: float, rise: float) -> float:
    """Area between a chord and its arc [mm²]."""
    radius = arc_radius(width, rise)
    half_angle = arc_length(width, rise) / (2.0 * radius)
    return radius * radius * (half_angle - math.sin(half_angle) * math.cos(half_angle))


def _arc_points(
    width: float, rise: float, base: float, *, steps: int = 48
) -> list[tuple[float, float]]:
    """Points along an arched head, left to right."""
    radius = arc_radius(width, rise)
    centre_y = base + rise - radius
    half_angle = arc_length(width, rise) / (2.0 * radius)
    points: list[tuple[float, float]] = []
    for index in range(steps + 1):
        angle = math.pi / 2.0 + half_angle - 2.0 * half_angle * index / steps
        points.append((
            width / 2.0 + radius * math.cos(angle),
            centre_y + radius * math.sin(angle),
        ))
    return points


def outline(
    shape: Shape | str,
    *,
    width: float,
    height: float,
    height_right: float | None = None,
    rise: float | None = None,
    bending: Bending | None = None,
    min_mitre: float = DEFAULT_MIN_MITRE_DEG,
    max_mitre: float = DEFAULT_MAX_MITRE_DEG,
) -> Outline:
    """Work out a shaped opening: its area, its members and its corners.

    ``height`` is the left-hand height for a raked opening and the overall
    height for everything else. ``rise`` is how far an arched head stands
    above its springing line — not the overall height, which is the springing
    plus the rise.
    """
    shape = Shape(shape) if not isinstance(shape, Shape) else shape
    if width <= 0 or height <= 0:
        raise ProfileOSError("מידות הפתח חייבות להיות חיוביות")

    result = Outline(
        shape=shape, width=width, height=height,
        height_right=height_right, rise=rise,
    )

    if shape is Shape.RECTANGLE:
        _rectangle(result)
    elif shape is Shape.RAKED:
        _raked(result)
    elif shape is Shape.TRIANGLE:
        _triangle(result)
    elif shape in (Shape.ARCHED, Shape.HALF_ROUND):
        _arched(result, bending)
    elif shape is Shape.CIRCLE:
        _circle(result, bending)

    result.area_mm2 = round(result.area_mm2, 2)
    result.perimeter = round(
        sum(
            member.length for member in result.members
            if member.length is not None
        ),
        1,
    )

    for corner in result.corners:
        if corner.mitre < min_mitre - 1e-6:
            result.warnings.append(
                f"{corner.name}: חיתוך ⁦{corner.mitre:.1f}°⁩ מתחת למינימום "
                f"של המסור (⁦{min_mitre:g}°⁩) — הפינה הזאת לא תיחתך כאן"
            )
        elif corner.mitre > max_mitre + 1e-6:
            result.warnings.append(
                f"{corner.name}: חיתוך ⁦{corner.mitre:.1f}°⁩ מעל טווח המסור"
            )

    _log.info(
        "Outline %s %.0fx%.0f: %.3f m2, %d members",
        shape.value, width, height, result.area, len(result.members),
    )
    return result


def _rectangle(result: Outline) -> None:
    width, height = result.width, result.height
    result.points = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    result.area_mm2 = width * height
    result.members = [
        Member("סף", width, 45.0, 45.0),
        Member("משקוף עליון", width, 45.0, 45.0),
        Member("עמוד שמאל", height, 45.0, 45.0),
        Member("עמוד ימין", height, 45.0, 45.0),
    ]
    result.corners = [
        Corner(name, 90.0) for name in
        ("שמאל תחתון", "ימין תחתון", "ימין עליון", "שמאל עליון")
    ]


def _raked(result: Outline) -> None:
    """One jamb taller than the other — the mono-pitch head."""
    width, left = result.width, result.height
    right = result.height_right
    if right is None or right <= 0:
        raise ProfileOSError("פתח משופע צריך גובה שני")
    result.height = max(left, right)

    result.points = [(0.0, 0.0), (width, 0.0), (width, right), (0.0, left)]
    result.area_mm2 = _polygon_area(result.points)

    rise = right - left
    slope = math.degrees(math.atan2(abs(rise), width))
    head_length = math.hypot(width, rise)

    # At the low end the head leans into the jamb, at the high end away from
    # it — the two corners are not the same angle, and cutting both the same
    # is the standard way a raked head arrives short on one side.
    low_included = 90.0 - slope if rise > 0 else 90.0 + slope
    high_included = 180.0 - low_included

    result.members = [
        Member("סף", width, 45.0, 45.0),
        Member(
            "משקוף משופע", round(head_length, 1),
            round(low_included / 2.0, 2), round(high_included / 2.0, 2),
            note=f"שיפוע ⁦{slope:.2f}°⁩",
        ),
        Member("עמוד שמאל", left, 45.0, round(
            (low_included if rise > 0 else high_included) / 2.0, 2
        )),
        Member("עמוד ימין", right, 45.0, round(
            (high_included if rise > 0 else low_included) / 2.0, 2
        )),
    ]
    result.corners = [
        Corner("שמאל תחתון", 90.0),
        Corner("ימין תחתון", 90.0),
        Corner(
            "ימין עליון", high_included if rise > 0 else low_included
        ),
        Corner(
            "שמאל עליון", low_included if rise > 0 else high_included
        ),
    ]


def _triangle(result: Outline) -> None:
    """A gable: two rakes meeting at an apex over a sill."""
    width, height = result.width, result.height
    result.points = [(0.0, 0.0), (width, 0.0), (width / 2.0, height)]
    result.area_mm2 = _polygon_area(result.points)

    slope = math.degrees(math.atan2(height, width / 2.0))
    rafter = math.hypot(width / 2.0, height)
    apex = 180.0 - 2.0 * slope

    result.members = [
        Member("סף", width, round(slope / 2.0, 2), round(slope / 2.0, 2)),
        Member(
            "רגל שמאל", round(rafter, 1),
            round(slope / 2.0, 2), round(apex / 2.0, 2),
        ),
        Member(
            "רגל ימין", round(rafter, 1),
            round(apex / 2.0, 2), round(slope / 2.0, 2),
        ),
    ]
    result.corners = [
        Corner("שמאל תחתון", round(slope, 2)),
        Corner("ימין תחתון", round(slope, 2)),
        Corner("קודקוד", round(apex, 2)),
    ]


def _arched(result: Outline, bending: Bending | None) -> None:
    """A segmental or semicircular head on straight jambs."""
    width, height = result.width, result.height
    rise = result.rise
    if result.shape is Shape.HALF_ROUND:
        rise = width / 2.0
        result.rise = rise
    if rise is None or rise <= 0:
        raise ProfileOSError("קשת צריכה גובה קשת (rise)")

    springing = height - rise
    if springing < 0:
        raise ProfileOSError(
            "גובה הקשת גדול מגובה הפתח — הקשת אינה נכנסת בו"
        )

    head = _arc_points(width, rise, springing)
    result.points = [(0.0, 0.0), (width, 0.0)] + list(reversed(head))
    result.area_mm2 = width * springing + _segment_area(width, rise)

    radius = arc_radius(width, rise)
    developed = arc_length(width, rise)

    curved = Member(
        "קשת", round(developed, 1), 90.0, 90.0,
        radius=round(radius, 1), is_curved=True,
    )
    if bending is None or not bending.is_confirmed:
        curved.length = None
        curved.note = "לא ניתן להזמין — חסרים נתוני כיפוף"
        problems = (bending or Bending()).problems()
        result.warnings.extend(problems)
        result.warnings.append(
            f"האורך המפותח מחושב ⁦{developed:.0f}⁩ מ״מ ברדיוס "
            f"⁦{radius:.0f}⁩ מ״מ, אך אין להזמין לפיו בלי אישור הכופף"
        )
    else:
        if bending.minimum_radius and radius < bending.minimum_radius:
            curved.length = None
            curved.note = "רדיוס קטן מהמותר לפרופיל"
            result.warnings.append(
                f"רדיוס הקשת ⁦{radius:.0f}⁩ מ״מ קטן מהמזערי לפרופיל "
                f"(⁦{bending.minimum_radius:.0f}⁩ מ״מ) — הפרופיל ייסדק"
            )
        else:
            grip = bending.grip_allowance or 0.0
            curved.length = round(developed + 2.0 * grip, 1)
            curved.note = (
                f"כולל ⁦{grip:g}⁩ מ״מ אחיזה בכל קצה · {bending.source}"
            )

    result.members = [
        Member("סף", width, 45.0, 45.0),
        Member("עמוד שמאל", round(springing, 1), 45.0, 90.0),
        Member("עמוד ימין", round(springing, 1), 45.0, 90.0),
        curved,
    ]
    result.corners = [
        Corner("שמאל תחתון", 90.0),
        Corner("ימין תחתון", 90.0),
    ]
    if springing < 1e-6:
        result.warnings.append(
            "הקשת יושבת ישירות על הסף — אין עמודים ישרים, ודאו שהמערכת "
            "מאפשרת חיבור קשת לסף"
        )


def _circle(result: Outline, bending: Bending | None) -> None:
    """A round window: one member, closed on itself."""
    diameter = min(result.width, result.height)
    if abs(result.width - result.height) > 1e-6:
        result.warnings.append(
            f"פתח עגול נלקח בקוטר הקטן (⁦{diameter:g}⁩ מ״מ) — הרוחב והגובה "
            "שהוזנו אינם שווים"
        )
    result.width = result.height = diameter
    radius = diameter / 2.0

    steps = 96
    result.points = [
        (
            radius + radius * math.cos(2.0 * math.pi * index / steps),
            radius + radius * math.sin(2.0 * math.pi * index / steps),
        )
        for index in range(steps)
    ]
    result.area_mm2 = math.pi * radius * radius

    developed = math.pi * diameter
    ring = Member(
        "טבעת", round(developed, 1), 0.0, 0.0,
        radius=round(radius, 1), is_curved=True,
        note="מוט אחד סגור על עצמו — חיבור אחד בלבד",
    )
    if bending is None or not bending.is_confirmed:
        ring.length = None
        ring.note = "לא ניתן להזמין — חסרים נתוני כיפוף"
        result.warnings.extend((bending or Bending()).problems())
        result.warnings.append(
            f"ההיקף מחושב ⁦{developed:.0f}⁩ מ״מ ברדיוס ⁦{radius:.0f}⁩ מ״מ, "
            "אך אין להזמין לפיו בלי אישור הכופף"
        )
    elif bending.minimum_radius and radius < bending.minimum_radius:
        ring.length = None
        ring.note = "רדיוס קטן מהמותר לפרופיל"
        result.warnings.append(
            f"רדיוס ⁦{radius:.0f}⁩ מ״מ קטן מהמזערי לפרופיל "
            f"(⁦{bending.minimum_radius:.0f}⁩ מ״מ) — הפרופיל ייסדק"
        )
    else:
        grip = bending.grip_allowance or 0.0
        ring.length = round(developed + 2.0 * grip, 1)
        ring.note = (
            f"מוט אחד סגור על עצמו · כולל ⁦{grip:g}⁩ מ״מ אחיזה בכל קצה · "
            f"{bending.source}"
        )
    result.members = [ring]


#: Said wherever a shaped opening's glass is listed. The cell grid inside a
#: shaped frame is still rectangular, so the pane sizes a cut list shows are
#: the rectangles those cells occupy — and the panes that touch the shaped
#: edge are not rectangles at all. They are ordered to a template or to a
#: CAD outline, never to a width and a height, and a glazier sent a width and
#: a height for an arched head will send back a rectangle.
SHAPED_GLASS_NOTE = (
    "מידות השמשות ברשימה הן מלבני התאים. השמשות הנוגעות בקצה המעוצב "
    "אינן מלבניות ומוזמנות לפי תבנית או קו CAD — לא לפי רוחב וגובה."
)


def price_area(outline_: Outline) -> float:
    """The area a shaped opening should be priced on [m²].

    Named separately from :attr:`Outline.area` because this is the decision it
    exists for: a shaped opening priced on its bounding box is priced on glass
    and coating nobody will buy, and priced on its true area it is priced on
    material the shop pays a shape premium to cut. The area is the true one;
    the premium belongs in the price list, where somebody can see it.
    """
    return outline_.area


__all__ = [
    "DEFAULT_MAX_MITRE_DEG",
    "SHAPED_GLASS_NOTE",
    "DEFAULT_MIN_MITRE_DEG",
    "Bending",
    "Corner",
    "Member",
    "Outline",
    "Shape",
    "arc_length",
    "arc_radius",
    "outline",
    "price_area",
]
