"""Loading the lorry in the order the units come off it.

The last thing that happens in the workshop is also the one nobody plans: the
finished units are stood against a wall in the order they were glazed, loaded
in that order, and arrive at a site where the fitters want the ground floor
first and the far bedroom last. So the lorry is unloaded onto the pavement,
sorted, and half of it is carried twice — and the one unit that was needed at
eight in the morning is at the front of the truck against the headboard.

A packing list fixes that for the cost of thinking about it once. Units are
grouped by where they are fitted, ordered so the first one out is the first
one needed, and each one carries what belongs with it — its glass, its
shutter, its screen, its trims — so nothing arrives on the second lorry.

Weight and length are checked against the vehicle, because a load that does
not fit is discovered at the yard gate, not at the site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.errors import ProfileOSError


class Handling(StrEnum):
    """How many people this unit needs, which is what plans the day."""

    ONE_PERSON = "one"
    TWO_PEOPLE = "two"
    FOUR_PEOPLE = "four"
    CRANE = "crane"

    @property
    def hebrew(self) -> str:
        return {
            "one": "אדם אחד",
            "two": "שניים",
            "four": "ארבעה",
            "crane": "מנוף או זכוכית ואקום",
        }[self.value]

    @property
    def people(self) -> int:
        return {"one": 1, "two": 2, "four": 4, "crane": 4}[self.value]


#: Above these a unit stops being something two fitters carry up a stairwell.
TWO_PERSON_KG = 35.0
FOUR_PERSON_KG = 90.0
CRANE_KG = 180.0
#: A pane past this is a vacuum-lifter job whatever it weighs.
CRANE_AREA_M2 = 4.0


def handling_for(mass: float, area: float) -> Handling:
    """How a unit of this weight and size actually gets carried."""
    if mass >= CRANE_KG or area >= CRANE_AREA_M2:
        return Handling.CRANE
    if mass >= FOUR_PERSON_KG:
        return Handling.FOUR_PEOPLE
    if mass >= TWO_PERSON_KG:
        return Handling.TWO_PEOPLE
    return Handling.ONE_PERSON


@dataclass
class PackedUnit:
    """One finished thing on the lorry, with everything that belongs to it."""

    mark: str
    description: str
    width: float
    height: float
    mass: float
    quantity: int = 1
    #: Where it is fitted. The whole point of the list is this field.
    location: str = ""
    floor: int = 0
    #: The order it is wanted in on site, low first.
    sequence: int = 0
    accessories: list[str] = field(default_factory=list)
    glass_panes: int = 0
    element_id: str = ""
    note: str = ""

    @property
    def area(self) -> float:
        return self.width * self.height / 1_000_000.0

    @property
    def total_mass(self) -> float:
        return self.mass * self.quantity

    @property
    def handling(self) -> Handling:
        return handling_for(self.mass, self.area)

    @property
    def longest_edge(self) -> float:
        return max(self.width, self.height)

    def describe(self) -> str:
        where = f" · {self.location}" if self.location else ""
        return f"{self.mark} ⁦{self.width:.0f}×{self.height:.0f}⁩{where}"


@dataclass(frozen=True)
class Vehicle:
    """What the shop delivers in."""

    name: str
    hebrew: str
    payload_kg: float
    deck_length_mm: float
    deck_height_mm: float = 2200.0

    def fits(self, unit: PackedUnit) -> bool:
        return unit.longest_edge <= max(self.deck_length_mm, self.deck_height_mm)


#: What a fabricator here actually has in the yard.
VEHICLES: tuple[Vehicle, ...] = (
    Vehicle("van", "מסחרית סגורה", 800.0, 3000.0, 1800.0),
    Vehicle("pickup", "טנדר עם מסגרת", 1200.0, 4000.0, 2400.0),
    Vehicle("truck_7t", "משאית ⁦7⁩ טון", 4000.0, 6200.0, 2600.0),
    Vehicle("truck_12t", "משאית ⁦12⁩ טון", 8000.0, 7500.0, 2800.0),
    Vehicle("glass_frame", "משאית עם מסגרת זכוכית", 3000.0, 6000.0, 3200.0),
)


def vehicle(name: str) -> Vehicle:
    for candidate in VEHICLES:
        if candidate.name == name:
            return candidate
    raise ProfileOSError(
        f"אין רכב בשם {name}. הקיימים: " + ", ".join(v.name for v in VEHICLES)
    )


@dataclass
class Load:
    """One lorry-load, in the order it is unloaded."""

    number: int
    vehicle: Vehicle
    units: list[PackedUnit] = field(default_factory=list)

    @property
    def mass(self) -> float:
        return round(sum(unit.total_mass for unit in self.units), 1)

    @property
    def pieces(self) -> int:
        return sum(unit.quantity for unit in self.units)

    @property
    def utilisation(self) -> float:
        if not self.vehicle.payload_kg:
            return 0.0
        return round(self.mass / self.vehicle.payload_kg * 100.0, 1)

    @property
    def people_needed(self) -> int:
        """The largest crew any one unit on this load asks for."""
        return max((unit.handling.people for unit in self.units), default=1)

    @property
    def needs_crane(self) -> bool:
        return any(unit.handling is Handling.CRANE for unit in self.units)

    def describe(self) -> str:
        return (
            f"הובלה ⁦{self.number}⁩ · {self.vehicle.hebrew} · "
            f"⁦{self.pieces}⁩ יחידות · ⁦{self.mass:,.0f}⁩ ק״ג "
            f"(⁦{self.utilisation:.0f}%⁩)"
        )


@dataclass
class PackingList:
    """Everything going to one site, split into loads."""

    job_id: str = ""
    job_name: str = ""
    site: str = ""
    loads: list[Load] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def units(self) -> list[PackedUnit]:
        return [unit for load in self.loads for unit in load.units]

    @property
    def mass(self) -> float:
        return round(sum(load.mass for load in self.loads), 1)

    @property
    def pieces(self) -> int:
        return sum(load.pieces for load in self.loads)

    def summary(self) -> dict[str, Any]:
        return {
            "loads": len(self.loads),
            "pieces": self.pieces,
            "mass_kg": self.mass,
            "crane": any(load.needs_crane for load in self.loads),
            "people": max((load.people_needed for load in self.loads), default=1),
        }


def _sort_key(unit: PackedUnit) -> tuple:
    """First out is first needed: low floors first, then the site's own order.

    Within a floor the heaviest goes on last so it comes off first — nobody
    wants to lift a hundred-kilo slider over three windows to reach it.
    """
    return (unit.floor, unit.sequence, -unit.total_mass, unit.mark)


def pack(
    units: list[PackedUnit],
    *,
    vehicle_name: str = "truck_7t",
    job_id: str = "",
    job_name: str = "",
    site: str = "",
) -> PackingList:
    """Split the finished work into loads, in the order the site wants it."""
    lorry = vehicle(vehicle_name)
    packing = PackingList(job_id=job_id, job_name=job_name, site=site)
    if not units:
        return packing

    ordered = sorted(units, key=_sort_key)

    # A load is filled until the next unit would put it over the payload, and
    # the ordering is preserved inside it — a lorry sorted by weight is a
    # lorry that gets unloaded onto the pavement.
    current = Load(number=1, vehicle=lorry)
    for unit in ordered:
        if not lorry.fits(unit):
            packing.warnings.append(
                f"{unit.mark}: ⁦{unit.longest_edge:,.0f}⁩ מ״מ ארוך מ{lorry.hebrew} — "
                "נדרש רכב אחר"
            )
        if current.units and current.mass + unit.total_mass > lorry.payload_kg:
            packing.loads.append(current)
            current = Load(number=current.number + 1, vehicle=lorry)
        current.units.append(unit)
    packing.loads.append(current)

    for load in packing.loads:
        if load.needs_crane:
            packing.warnings.append(
                f"הובלה ⁦{load.number}⁩ כוללת יחידה שדורשת מנוף או שואב ואקום"
            )
        if load.utilisation > 100:
            packing.warnings.append(
                f"הובלה ⁦{load.number}⁩ במשקל ⁦{load.mass:,.0f}⁩ ק״ג — "
                f"מעל המותר ל{lorry.hebrew}"
            )
    return packing


def units_from_builds(
    builds: list[Any],
    *,
    locations: dict[str, tuple[str, int, int]] | None = None,
) -> list[PackedUnit]:
    """Turn built elements into things on a lorry.

    ``locations`` maps an element mark to (where it is fitted, floor, order on
    site). Anything not in it is packed after everything that is, because a
    unit whose place nobody recorded should not be the first one off.
    """
    locations = locations or {}
    packed: list[PackedUnit] = []
    for build in builds:
        opening = build.opening
        glass_mass = sum(panel.mass * panel.quantity for panel in build.glass)
        frame_mass = build.total_profile_length / max(opening.quantity, 1) / 1000.0 * 2.2

        accessories: list[str] = []
        accessory_mass = 0.0
        try:
            from ..accessories import accessories_for

            for accessory in accessories_for(opening):
                accessories.append(accessory.hebrew)
                accessory_mass += accessory.mass
        except Exception:  # noqa: BLE001 - a packing list beats a fitting
            pass

        # The element carries where it goes, so a job reopened next month
        # still loads in the right order without anybody re-entering it.
        place = getattr(opening, "metadata", {}).get("place") or {}
        where, floor, sequence = locations.get(
            opening.name,
            (
                place.get("location", ""),
                int(place.get("floor", 0)),
                int(place.get("sequence", 9999)),
            ),
        )
        packed.append(PackedUnit(
            mark=opening.name,
            description=f"{opening.kind.value} ⁦{opening.width:.0f}×{opening.height:.0f}⁩",
            width=opening.width,
            height=opening.height,
            mass=round(glass_mass + frame_mass + accessory_mass, 1),
            quantity=opening.quantity,
            location=where,
            floor=floor,
            sequence=sequence,
            accessories=accessories,
            glass_panes=sum(panel.quantity for panel in build.glass),
            element_id=opening.element_id,
        ))
    return packed


__all__ = [
    "CRANE_AREA_M2",
    "CRANE_KG",
    "FOUR_PERSON_KG",
    "Handling",
    "Load",
    "PackedUnit",
    "PackingList",
    "TWO_PERSON_KG",
    "VEHICLES",
    "Vehicle",
    "handling_for",
    "pack",
    "units_from_builds",
    "vehicle",
]
