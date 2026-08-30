"""What goes on a window besides the window.

Every package in this trade models the frame, the glass and the hardware, and
then a fabricator in Israel quotes a bedroom window and adds the rolling
shutter, the insect screen, the sill and the trim by hand on a separate sheet.
That is not a small omission: on a typical dwelling the shutters are a large
share of the price, they change the structural opening the builder has to
leave, and a box height that is wrong is a wall that has to be re-cut.

So accessories are first-class here. They size themselves from the opening
they belong to, they produce their own cut list and their own hardware, and
they flow into the bill of materials, the quotation and the job pack with
everything else — one order, one price, one set of dimensions for the builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AccessoryKind(StrEnum):
    """The families of thing that hang off an opening."""

    SHUTTER = "shutter"
    SCREEN = "screen"
    SILL = "sill"
    TRIM = "trim"

    @property
    def hebrew(self) -> str:
        return {
            "shutter": "תריס",
            "screen": "רשת",
            "sill": "אדן",
            "trim": "מסגרת",
        }[self.value]


@dataclass(frozen=True)
class AccessoryCut:
    """One length of accessory profile to be cut."""

    role: str
    hebrew: str
    profile_id: str
    length: float
    quantity: int = 1

    @property
    def total_length(self) -> float:
        return self.length * self.quantity


@dataclass(frozen=True)
class AccessoryPart:
    """One bought item: a motor, a set of rollers, a pair of end caps."""

    code: str
    hebrew: str
    quantity: float = 1
    unit: str = "pc"
    supplier: str | None = None
    note: str = ""


@dataclass
class Accessory:
    """One fitted accessory, sized and costed.

    ``structural_opening`` is the hole the builder has to leave. It is the part
    of this that has to be right before anything is manufactured, so it is
    carried on the accessory itself rather than derived later by whoever
    happens to be drawing the wall.
    """

    kind: AccessoryKind
    code: str
    hebrew: str
    width: float
    height: float
    quantity: int = 1
    cuts: list[AccessoryCut] = field(default_factory=list)
    parts: list[AccessoryPart] = field(default_factory=list)
    mass: float = 0.0
    #: Extra opening the accessory needs above the window, for a shutter box.
    head_allowance: float = 0.0
    #: Extra opening either side, for guides.
    side_allowance: float = 0.0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def area(self) -> float:
        """Face area [m^2] — how shutters and screens are priced."""
        return self.width * self.height / 1_000_000.0

    @property
    def total_area(self) -> float:
        return self.area * self.quantity

    def structural_opening(self, window_width: float, window_height: float) -> tuple[float, float]:
        """The hole in the wall this accessory needs, with the window in it."""
        return (
            window_width + 2 * self.side_allowance,
            window_height + self.head_allowance,
        )

    def describe(self) -> str:
        return f"{self.hebrew} ⁦{self.width:.0f} × {self.height:.0f}⁩ מ״מ"


@dataclass
class AccessorySet:
    """Every accessory on one opening, and what they add up to."""

    accessories: list[Accessory] = field(default_factory=list)

    def __iter__(self):
        return iter(self.accessories)

    def __len__(self) -> int:
        return len(self.accessories)

    def add(self, accessory: Accessory | None) -> "AccessorySet":
        if accessory is not None:
            self.accessories.append(accessory)
        return self

    def of_kind(self, kind: AccessoryKind) -> list[Accessory]:
        return [a for a in self.accessories if a.kind is kind]

    @property
    def mass(self) -> float:
        return sum(a.mass * a.quantity for a in self.accessories)

    @property
    def warnings(self) -> list[str]:
        return [w for a in self.accessories for w in a.warnings]

    def head_allowance(self) -> float:
        """The tallest head allowance any accessory asks for."""
        return max((a.head_allowance for a in self.accessories), default=0.0)

    def side_allowance(self) -> float:
        return max((a.side_allowance for a in self.accessories), default=0.0)

    def structural_opening(self, width: float, height: float) -> tuple[float, float]:
        """The hole the builder leaves once every accessory is accounted for."""
        return (width + 2 * self.side_allowance(), height + self.head_allowance())

    def cuts(self) -> list[AccessoryCut]:
        return [cut for a in self.accessories for cut in a.cuts]

    def parts(self) -> list[AccessoryPart]:
        return [part for a in self.accessories for part in a.parts]

    def summary(self) -> dict[str, Any]:
        return {
            "count": len(self.accessories),
            "shutters": len(self.of_kind(AccessoryKind.SHUTTER)),
            "screens": len(self.of_kind(AccessoryKind.SCREEN)),
            "mass_kg": round(self.mass, 1),
            "pieces": sum(cut.quantity for cut in self.cuts()),
            "parts": len(self.parts()),
            "warnings": len(self.warnings),
        }


__all__ = [
    "Accessory",
    "AccessoryCut",
    "AccessoryKind",
    "AccessoryPart",
    "AccessorySet",
]
