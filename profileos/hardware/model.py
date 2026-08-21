"""Hardware: the parts that decide whether a sash still closes in five years.

The frame is aluminium and forgiving. The hardware is not: a hinge chosen one
size down works perfectly on the day it is fitted, and drops in the second
winter under a sash it was never rated for. That is the single most common
warranty call in this trade, and it is caused at the moment somebody picks a
part off a shelf because it is the one they always use.

So hardware here is chosen by load, not by habit. A part carries the sash
weight it is rated to, the leaf size it is made for, and where those numbers
came from. Selection asks the element what it weighs and refuses to fit
anything that cannot carry it — and when nothing in the library can, it says
so instead of quietly returning the largest.

The catalogue starts empty of manufacturers' figures, on purpose. Roto and
Giesse publish load charts; this software does not have them and will not
invent them, because an invented hinge rating is a sash on somebody's floor.
What it has is the structure, the arithmetic and the refusal — and a way to
enter a real chart in a few minutes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.errors import ProfileOSError


class PartKind(StrEnum):
    """What the part does, which is what it is chosen by."""

    HINGE = "hinge"
    FRICTION_STAY = "friction_stay"
    TILT_TURN_GEAR = "tilt_turn_gear"
    ESPAGNOLETTE = "espagnolette"
    HANDLE = "handle"
    ROLLER = "roller"
    LIFT_SLIDE_GEAR = "lift_slide_gear"
    MULTIPOINT_LOCK = "multipoint_lock"
    CYLINDER = "cylinder"
    DOOR_CLOSER = "door_closer"
    RESTRICTOR = "restrictor"
    CORNER_DRIVE = "corner_drive"
    STRIKE_PLATE = "strike_plate"

    @property
    def hebrew(self) -> str:
        return {
            "hinge": "ציר",
            "friction_stay": "זרוע חיכוך",
            "tilt_turn_gear": "מנגנון נטוי-נפתח",
            "espagnolette": "בריח היקפי",
            "handle": "ידית",
            "roller": "גלגלת",
            "lift_slide_gear": "מנגנון הרמה והזזה",
            "multipoint_lock": "מנעול רב-בריחי",
            "cylinder": "צילינדר",
            "door_closer": "מחזיר שמן",
            "restrictor": "מגביל פתיחה",
            "corner_drive": "מעביר פינה",
            "strike_plate": "כף נגדית",
        }[self.value]

    @property
    def carries_load(self) -> bool:
        """Whether this part's rating has to be checked against sash weight."""
        return self in (
            PartKind.HINGE,
            PartKind.FRICTION_STAY,
            PartKind.TILT_TURN_GEAR,
            PartKind.ROLLER,
            PartKind.LIFT_SLIDE_GEAR,
        )


class Confidence(StrEnum):
    """Where a part's ratings came from."""

    CATALOGUE = "catalogue"
    TYPICAL = "typical"
    UNKNOWN = "unknown"

    @property
    def hebrew(self) -> str:
        return {
            "catalogue": "מקטלוג היצרן",
            "typical": "ערך טיפוסי — לא מהיצרן",
            "unknown": "לא ידוע",
        }[self.value]

    @property
    def may_be_fitted(self) -> bool:
        """Whether a load-bearing part may be selected on this figure.

        Only a manufacturer's own chart. Everything else is a starting point
        for a conversation with the supplier, not a part number to order.
        """
        return self is Confidence.CATALOGUE


@dataclass(frozen=True)
class Part:
    """One item of hardware, and what it is rated to do."""

    code: str
    hebrew: str
    kind: PartKind
    maker: str = ""
    #: What one of these can carry [kg]. Zero means the rating is unknown.
    max_sash_kg: float = 0.0
    #: The leaf sizes it is made for [mm]. Zero means no limit is recorded.
    min_width: float = 0.0
    max_width: float = 0.0
    min_height: float = 0.0
    max_height: float = 0.0
    #: Which opening types it suits, by their engine names.
    opening_types: tuple[str, ...] = ()
    #: How many are fitted per sash.
    per_sash: int = 1
    unit: str = "pc"
    price: float = 0.0
    currency: str = "ILS"
    confidence: Confidence = Confidence.UNKNOWN
    source: str = ""
    notes: str = ""

    def fits(self, *, width: float, height: float, mass: float,
             opening_type: str = "") -> bool:
        """Whether this part may carry this leaf. Unknown is never yes."""
        if opening_type and self.opening_types and opening_type not in self.opening_types:
            return False
        if self.max_width and width > self.max_width:
            return False
        if self.min_width and width < self.min_width:
            return False
        if self.max_height and height > self.max_height:
            return False
        if self.min_height and height < self.min_height:
            return False
        if self.kind.carries_load:
            if not self.max_sash_kg:
                return False
            if mass > self.max_sash_kg * self.per_sash:
                return False
        return True

    def describe(self) -> str:
        rating = f" · עד ⁦{self.max_sash_kg:.0f}⁩ ק״ג" if self.max_sash_kg else ""
        maker = f" · {self.maker}" if self.maker else ""
        return f"{self.hebrew}{maker}{rating}"


@dataclass
class Selection:
    """What was chosen for one leaf, and what could not be."""

    parts: list[tuple[Part, int]] = field(default_factory=list)
    #: What is needed but nothing in the library can do.
    unmet: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sash_mass: float = 0.0

    @property
    def is_complete(self) -> bool:
        return not self.unmet

    @property
    def may_be_ordered(self) -> bool:
        """Whether every load-bearing choice rests on a real load chart."""
        return self.is_complete and all(
            part.confidence.may_be_fitted
            for part, _quantity in self.parts
            if part.kind.carries_load
        )

    @property
    def price(self) -> float:
        return round(sum(part.price * quantity for part, quantity in self.parts), 2)

    def rows(self) -> list[list[Any]]:
        return [
            [part.code, part.describe(), quantity, part.unit,
             part.confidence.hebrew]
            for part, quantity in self.parts
        ]


def sash_mass(width: float, height: float, glass_mass_per_m2: float = 25.0,
              frame_mass_per_m: float = 2.4) -> float:
    """What one leaf weighs [kg], which is what the hinge has to carry.

    The glass is most of it and the frame is the rest, so both are counted:
    a rating checked against the glass alone is a rating checked short by the
    weight of the sash itself.
    """
    if width <= 0 or height <= 0:
        raise ProfileOSError("מידות הכנף חייבות להיות חיוביות")
    area = width * height / 1_000_000.0
    perimeter = 2.0 * (width + height) / 1000.0
    return area * glass_mass_per_m2 + perimeter * frame_mass_per_m


__all__ = [
    "Confidence",
    "Part",
    "PartKind",
    "Selection",
    "sash_mass",
]
