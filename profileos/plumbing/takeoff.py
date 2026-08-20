"""What the job actually costs to buy: pipe, fittings, insulation, fixtures.

A design that stops at pipe sizes is half a job. The other half is the list
the merchant is phoned with, and it is a different document: it counts stock
lengths rather than metres, it adds the insulation nobody remembers until the
inspector asks, and it groups by what is ordered rather than by where it runs.

Two decisions worth stating:

*Stock lengths, not metres.* Copper comes in 5 m lengths and PPR in 4 m; a run
of 11 m of 22 mm is three lengths bought and two metres in the offcut bin, and
the list says so. A take-off in metres is a take-off that under-orders.

*Waste is named, not folded in.* The allowance is a line of its own so the
person reading can argue with it. A percentage buried inside a quantity is a
number nobody can check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..core.errors import ProfileOSError
from .fixtures import FixtureSchedule
from .pipes import ServiceType


class TakeoffError(ProfileOSError):
    """A take-off that cannot be produced as asked."""


#: The stock length [m] each material is delivered in.
STOCK_LENGTH: dict[str, float] = {
    "copper": 5.0,
    "ppr": 4.0,
    "pex": 100.0,      # coil
    "steel": 6.0,
    "pvc": 4.0,
    "hdpe": 6.0,
    "multilayer": 50.0,  # coil
}

#: How the services read in Hebrew on the list.
SERVICE_HEBREW: dict[str, str] = {
    "cold_water": "מים קרים",
    "hot_water": "מים חמים",
    "heating_flow": "חימום — הלוך",
    "heating_return": "חימום — חוזר",
    "chilled_water": "מים מקוררים",
    "drainage": "דלוחין וניקוז",
    "fire_sprinkler": "מתזים",
    "compressed_air": "אוויר דחוס",
    "gas": "גז",
}


@dataclass
class PipeRun:
    """One measured run of pipe on the job."""

    service: ServiceType
    designation: str
    length_m: float
    material: str = "copper"
    #: Insulation thickness on this run [mm]; zero for a run left bare.
    insulation_mm: float = 0.0
    fittings: dict[str, int] = field(default_factory=dict)
    valves: int = 0
    name: str = ""

    def __post_init__(self) -> None:
        if self.length_m < 0:
            raise TakeoffError("אורך קו לא יכול להיות שלילי", run=self.name)

    @property
    def insulated(self) -> bool:
        return self.insulation_mm > 0


@dataclass
class TakeoffLine:
    """One line on the merchant's list."""

    kind: str
    description: str
    quantity: float
    unit: str
    note: str = ""

    def as_row(self) -> tuple[str, str, str, str, str]:
        formatted = (
            f"{self.quantity:,.0f}" if float(self.quantity).is_integer()
            else f"{self.quantity:,.1f}"
        )
        return (self.kind, self.description, formatted, self.unit, self.note)


@dataclass
class Takeoff:
    """The whole list, and the totals worth stating on their own."""

    lines: list[TakeoffLine] = field(default_factory=list)
    waste_pct: float = 10.0

    @property
    def pipe_metres(self) -> float:
        return round(
            sum(line.quantity for line in self.lines
                if line.kind == "צנרת" and line.unit == "מ'"),
            1,
        )

    @property
    def insulation_metres(self) -> float:
        return round(
            sum(line.quantity for line in self.lines if line.kind == "בידוד"), 1
        )

    @property
    def fitting_count(self) -> int:
        return int(sum(line.quantity for line in self.lines if line.kind == "אביזרים"))

    def rows(self) -> list[tuple[str, str, str, str, str]]:
        return [line.as_row() for line in self.lines]

    def summary(self) -> dict[str, float | int]:
        return {
            "pipe_m": self.pipe_metres,
            "insulation_m": self.insulation_metres,
            "fittings": self.fitting_count,
            "lines": len(self.lines),
            "waste_pct": self.waste_pct,
        }


def _stock_lengths(metres: float, material: str) -> tuple[int, float]:
    """How many stock lengths cover ``metres``, and what is left over."""
    length = STOCK_LENGTH.get(material, 6.0)
    if metres <= 0:
        return 0, 0.0
    count = math.ceil(metres / length)
    return count, round(count * length - metres, 2)


def take_off(
    runs: Iterable[PipeRun],
    *,
    schedule: FixtureSchedule | None = None,
    waste_pct: float = 10.0,
) -> Takeoff:
    """Turn measured runs and a fixture schedule into a merchant's list."""
    if waste_pct < 0:
        raise TakeoffError("אחוז פחת לא יכול להיות שלילי", waste=waste_pct)

    runs = list(runs)
    takeoff = Takeoff(waste_pct=waste_pct)

    # -- pipe, by service and size --------------------------------------- #
    gathered: dict[tuple[str, str, str], float] = {}
    for run in runs:
        key = (run.service.value, run.designation, run.material)
        gathered[key] = gathered.get(key, 0.0) + run.length_m

    for (service, designation, material), metres in sorted(gathered.items()):
        with_waste = metres * (1.0 + waste_pct / 100.0)
        count, offcut = _stock_lengths(with_waste, material)
        service_name = SERVICE_HEBREW.get(service, service)
        takeoff.lines.append(TakeoffLine(
            kind="צנרת",
            description=f"{service_name} · {designation} · {material}",
            quantity=round(metres, 1),
            unit="מ'",
            note=(
                f"{count} יחידות באורך ⁦{STOCK_LENGTH.get(material, 6.0):g}⁩ מ' "
                f"(כולל ⁦{waste_pct:g}%⁩ פחת, שארית ⁦{offcut:g}⁩ מ')"
            ),
        ))

    # -- insulation, only where the run carries it ------------------------ #
    insulation: dict[tuple[str, float], float] = {}
    for run in runs:
        if run.insulated:
            key = (run.designation, run.insulation_mm)
            insulation[key] = insulation.get(key, 0.0) + run.length_m
    for (designation, thickness), metres in sorted(insulation.items()):
        takeoff.lines.append(TakeoffLine(
            kind="בידוד",
            description=f"שרוול בידוד {designation} · ⁦{thickness:g}⁩ מ\"מ",
            quantity=round(metres * (1.0 + waste_pct / 100.0), 1),
            unit="מ'",
            note="קווי מים חמים חייבים בידוד לפי ת\"י 1205",
        ))

    # A hot run left bare is a finding, not a missing line.
    bare = [run for run in runs
            if run.service is ServiceType.HOT_WATER and not run.insulated]
    if bare:
        takeoff.lines.append(TakeoffLine(
            kind="בידוד",
            description="קווי מים חמים ללא בידוד",
            quantity=round(sum(run.length_m for run in bare), 1),
            unit="מ'",
            note="לא תומחר — יש להוסיף בידוד או לנמק",
        ))

    # -- fittings and valves ---------------------------------------------- #
    fittings: dict[tuple[str, str], int] = {}
    for run in runs:
        for fitting, count in run.fittings.items():
            key = (fitting, run.designation)
            fittings[key] = fittings.get(key, 0) + count
    for (fitting, designation), count in sorted(fittings.items()):
        takeoff.lines.append(TakeoffLine(
            kind="אביזרים",
            description=f"{fitting.replace('_', ' ')} · {designation}",
            quantity=count,
            unit="יח'",
        ))

    valves: dict[str, int] = {}
    for run in runs:
        if run.valves:
            valves[run.designation] = valves.get(run.designation, 0) + run.valves
    for designation, count in sorted(valves.items()):
        takeoff.lines.append(TakeoffLine(
            kind="ברזים",
            description=f"ברז ניתוק · {designation}",
            quantity=count,
            unit="יח'",
        ))

    # -- the fixtures themselves ------------------------------------------- #
    if schedule is not None:
        for line in sorted(schedule.lines, key=lambda item: item.fixture.id):
            takeoff.lines.append(TakeoffLine(
                kind="כלים סניטריים",
                description=line.fixture.hebrew,
                quantity=line.quantity,
                unit="יח'",
                note=f"מחסום ⌀{line.fixture.trap_mm:.0f} מ\"מ" if line.fixture.trap_mm else "",
            ))

    return takeoff


__all__ = [
    "PipeRun",
    "STOCK_LENGTH",
    "SERVICE_HEBREW",
    "Takeoff",
    "TakeoffError",
    "TakeoffLine",
    "take_off",
]
