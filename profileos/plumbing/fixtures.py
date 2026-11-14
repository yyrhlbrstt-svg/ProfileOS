"""Fixtures, loading units and the demand they actually place on a main.

Every plumbing design begins the same way: count what is connected, then work
out how much water will really be drawn at once. Nobody sizes a riser for
every tap in a building running together, because they never do — the
probability that they do falls as the building grows, and that falling
probability is the whole of the method.

Two families of number are kept apart on purpose:

*Loading units* (LU) weigh a fixture by how much water it draws and how often
it is used, and convert to a simultaneous flow through a demand curve. This is
the supply side.

*Drainage fixture units* (DFU) weigh the same fixture by what it discharges,
and size the drain and its vent from a table. This is the waste side.

The two are not interchangeable and a fixture carries both, because a WC is
modest on the supply and heavy on the drain.

The Israeli practice this follows is the one in ת"י 1205 and the tables the
trade works from; the demand curve is Hunter's, in the piecewise form the
plumbing codes tabulate rather than the original continuous probability
statement — that is what a plumber can check by hand against a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from ..core.errors import ProfileOSError


class FixtureError(ProfileOSError):
    """A fixture schedule that cannot be sized as given."""


class SupplyKind(StrEnum):
    """How the fixtures on a system are flushed, which changes the demand.

    A building of flush valves draws far more at once than the same building of
    cistern-fed WCs, because a valve is an open bore for its whole cycle while
    a cistern refills slowly through a small orifice.
    """

    TANK = "tank"
    VALVE = "valve"


@dataclass(frozen=True)
class Fixture:
    """One sanitary fixture, weighed for both sides of the system."""

    id: str
    name: str
    hebrew: str
    #: Loading units on the cold and hot supplies.
    cold_lu: float = 0.0
    hot_lu: float = 0.0
    #: Drainage fixture units discharged.
    dfu: float = 0.0
    #: The trap this fixture needs [mm]. Sets the smallest branch drain.
    trap_mm: float = 40.0
    #: Flow the fixture needs to work at all [l/s], and the pressure at its
    #: inlet to deliver it [kPa]. These size the last metre, not the riser.
    flow_lps: float = 0.1
    min_pressure_kpa: float = 100.0
    #: True when the fixture draws hot water, so it counts on that riser too.
    has_hot: bool = False

    @property
    def total_lu(self) -> float:
        """Loading units on a combined cold-and-hot service."""
        return self.cold_lu + self.hot_lu


#: The fixtures an Israeli dwelling or small commercial job is actually built
#: from. Loading and drainage units follow the values the trade tables carry;
#: they are conventional figures, not measurements of a particular product.
FIXTURES: tuple[Fixture, ...] = (
    Fixture("wc-tank", "WC, cistern", "אסלה עם מיכל הדחה",
            cold_lu=2.5, dfu=4.0, trap_mm=100.0, flow_lps=0.13, min_pressure_kpa=100.0),
    Fixture("wc-valve", "WC, flush valve", "אסלה עם מדיח לחץ",
            cold_lu=10.0, dfu=6.0, trap_mm=100.0, flow_lps=1.7, min_pressure_kpa=170.0),
    Fixture("basin", "Wash basin", "כיור רחצה",
            cold_lu=1.5, hot_lu=1.5, dfu=1.0, trap_mm=40.0, flow_lps=0.1,
            min_pressure_kpa=100.0, has_hot=True),
    Fixture("sink", "Kitchen sink", "כיור מטבח",
            cold_lu=3.0, hot_lu=3.0, dfu=2.0, trap_mm=40.0, flow_lps=0.2,
            min_pressure_kpa=100.0, has_hot=True),
    Fixture("shower", "Shower", "מקלחת",
            cold_lu=3.0, hot_lu=3.0, dfu=2.0, trap_mm=50.0, flow_lps=0.2,
            min_pressure_kpa=100.0, has_hot=True),
    Fixture("bath", "Bath", "אמבטיה",
            cold_lu=4.0, hot_lu=4.0, dfu=3.0, trap_mm=50.0, flow_lps=0.3,
            min_pressure_kpa=100.0, has_hot=True),
    Fixture("bidet", "Bidet", "בידה",
            cold_lu=1.5, hot_lu=1.5, dfu=1.0, trap_mm=40.0, flow_lps=0.1,
            min_pressure_kpa=100.0, has_hot=True),
    Fixture("washing-machine", "Washing machine", "מכונת כביסה",
            cold_lu=3.0, dfu=3.0, trap_mm=50.0, flow_lps=0.2, min_pressure_kpa=100.0),
    Fixture("dishwasher", "Dishwasher", "מדיח כלים",
            cold_lu=1.5, dfu=2.0, trap_mm=40.0, flow_lps=0.15, min_pressure_kpa=100.0),
    Fixture("urinal", "Urinal", "משתנה",
            cold_lu=3.0, dfu=2.0, trap_mm=50.0, flow_lps=0.3, min_pressure_kpa=100.0),
    Fixture("floor-drain", "Floor drain", "מחסום רצפה",
            dfu=2.0, trap_mm=50.0, flow_lps=0.0, min_pressure_kpa=0.0),
    Fixture("hose-bib", "Hose bib", "ברז גינה",
            cold_lu=2.5, dfu=0.0, trap_mm=0.0, flow_lps=0.3, min_pressure_kpa=100.0),
    Fixture("cleaners-sink", "Cleaner's sink", "כיור שירות",
            cold_lu=3.0, hot_lu=3.0, dfu=3.0, trap_mm=75.0, flow_lps=0.3,
            min_pressure_kpa=100.0, has_hot=True),
)

FIXTURES_BY_ID: dict[str, Fixture] = {fixture.id: fixture for fixture in FIXTURES}


def fixture(fixture_id: str) -> Fixture:
    """One fixture by id, or a message naming what is available."""
    found = FIXTURES_BY_ID.get(fixture_id)
    if found is None:
        raise FixtureError(
            f"אין כלי סניטרי בשם {fixture_id!r}",
            known=", ".join(sorted(FIXTURES_BY_ID)),
        )
    return found


#: Hunter's demand curve, tabulated: (loading units, simultaneous flow l/s).
#: Two curves, because a flush-valve building and a cistern building of the
#: same loading do not draw the same water at the same moment.
_TANK_CURVE: tuple[tuple[float, float], ...] = (
    (0, 0.0), (3, 0.19), (6, 0.32), (10, 0.44), (15, 0.57), (20, 0.69),
    (30, 0.88), (40, 1.07), (50, 1.20), (75, 1.51), (100, 1.77), (150, 2.21),
    (200, 2.58), (300, 3.15), (400, 3.66), (500, 4.10), (750, 5.05),
    (1000, 5.87), (1500, 7.25), (2000, 8.39), (3000, 10.4), (5000, 13.6),
)
_VALVE_CURVE: tuple[tuple[float, float], ...] = (
    (0, 0.0), (5, 0.95), (10, 1.58), (15, 2.02), (20, 2.34), (30, 2.90),
    (40, 3.34), (50, 3.72), (75, 4.48), (100, 5.11), (150, 6.18), (200, 7.06),
    (300, 8.51), (400, 9.71), (500, 10.7), (750, 12.9), (1000, 14.8),
    (1500, 18.0), (2000, 20.7), (3000, 25.2), (5000, 32.5),
)


def demand_flow(loading_units: float, kind: SupplyKind = SupplyKind.TANK) -> float:
    """Simultaneous demand [l/s] for a loading, by interpolation on the curve.

    Below the first tabulated point the curve is followed linearly to the
    origin; above the last it is extrapolated on the final slope, and the
    caller is expected to notice that a job of that size wants a proper
    hydraulic model rather than a table.
    """
    if loading_units < 0:
        raise FixtureError("יחידות עומס לא יכולות להיות שליליות", loading_units=loading_units)
    curve = _VALVE_CURVE if kind is SupplyKind.VALVE else _TANK_CURVE
    if loading_units <= curve[0][0]:
        return 0.0
    for (lu_low, flow_low), (lu_high, flow_high) in zip(curve, curve[1:]):
        if loading_units <= lu_high:
            span = lu_high - lu_low
            if span <= 0:
                return flow_high
            fraction = (loading_units - lu_low) / span
            return round(flow_low + fraction * (flow_high - flow_low), 3)

    # Past the table: hold the last slope rather than pretend the curve ends.
    (lu_low, flow_low), (lu_high, flow_high) = curve[-2], curve[-1]
    slope = (flow_high - flow_low) / (lu_high - lu_low)
    return round(flow_high + slope * (loading_units - lu_high), 3)


@dataclass
class ScheduleLine:
    """A count of one fixture in a schedule."""

    fixture: Fixture
    quantity: int = 1

    @property
    def cold_lu(self) -> float:
        return self.fixture.cold_lu * self.quantity

    @property
    def hot_lu(self) -> float:
        return self.fixture.hot_lu * self.quantity

    @property
    def dfu(self) -> float:
        return self.fixture.dfu * self.quantity


@dataclass
class FixtureSchedule:
    """What is connected, and what it therefore demands.

    The schedule is the document the whole design hangs from: the supply flows,
    the drain sizes and the take-off all follow from it, so it is kept as one
    object rather than recomputed from scratch at each stage.
    """

    name: str = ""
    kind: SupplyKind = SupplyKind.TANK
    lines: list[ScheduleLine] = field(default_factory=list)

    # -- building it --------------------------------------------------------- #
    def add(self, fixture_id: str, quantity: int = 1) -> "FixtureSchedule":
        if quantity <= 0:
            raise FixtureError("כמות חייבת להיות חיובית", fixture=fixture_id)
        found = fixture(fixture_id)
        for line in self.lines:
            if line.fixture.id == found.id:
                line.quantity += quantity
                return self
        self.lines.append(ScheduleLine(found, quantity))
        return self

    @classmethod
    def of(cls, counts: dict[str, int], *, kind: SupplyKind = SupplyKind.TANK,
           name: str = "") -> "FixtureSchedule":
        schedule = cls(name=name, kind=kind)
        for fixture_id, quantity in counts.items():
            if quantity:
                schedule.add(fixture_id, quantity)
        return schedule

    def repeated(self, times: int) -> "FixtureSchedule":
        """The same schedule ``times`` over — one flat becomes a whole riser."""
        if times <= 0:
            raise FixtureError("מספר החזרות חייב להיות חיובי", times=times)
        return FixtureSchedule(
            name=self.name,
            kind=self.kind,
            lines=[ScheduleLine(line.fixture, line.quantity * times) for line in self.lines],
        )

    # -- what it demands ------------------------------------------------------ #
    @property
    def fixture_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def cold_lu(self) -> float:
        return round(sum(line.cold_lu for line in self.lines), 2)

    @property
    def hot_lu(self) -> float:
        return round(sum(line.hot_lu for line in self.lines), 2)

    @property
    def total_lu(self) -> float:
        return round(self.cold_lu + self.hot_lu, 2)

    @property
    def dfu(self) -> float:
        return round(sum(line.dfu for line in self.lines), 2)

    @property
    def largest_trap(self) -> float:
        """The biggest trap in the schedule [mm] — the branch cannot be smaller."""
        return max((line.fixture.trap_mm for line in self.lines), default=0.0)

    @property
    def min_pressure_kpa(self) -> float:
        """The most demanding fixture inlet pressure in the schedule [kPa]."""
        return max((line.fixture.min_pressure_kpa for line in self.lines), default=0.0)

    def cold_demand(self) -> float:
        """Simultaneous cold demand [l/s]."""
        return demand_flow(self.cold_lu, self.kind)

    def hot_demand(self) -> float:
        return demand_flow(self.hot_lu, self.kind)

    def total_demand(self) -> float:
        """Demand on a combined service — *not* the two branches added.

        Cold and hot peaks do not coincide, so a combined main is sized from
        the combined loading through the curve once. Adding the two branch
        demands would size the main for a moment that does not happen.
        """
        return demand_flow(self.total_lu, self.kind)

    def summary(self) -> dict[str, float | int | str]:
        return {
            "fixtures": self.fixture_count,
            "cold_lu": self.cold_lu,
            "hot_lu": self.hot_lu,
            "total_lu": self.total_lu,
            "dfu": self.dfu,
            "cold_lps": self.cold_demand(),
            "hot_lps": self.hot_demand(),
            "total_lps": self.total_demand(),
            "largest_trap_mm": self.largest_trap,
            "min_pressure_kpa": self.min_pressure_kpa,
            "kind": self.kind.value,
        }

    def rows(self) -> list[tuple[str, str, int, float, float, float]]:
        """One row per fixture, for a table: name, hebrew, count, LU cold/hot, DFU."""
        return [
            (line.fixture.id, line.fixture.hebrew, line.quantity,
             line.cold_lu, line.hot_lu, line.dfu)
            for line in sorted(self.lines, key=lambda l: l.fixture.id)
        ]


#: A dwelling as it is actually fitted out in Israel: two bathrooms, a kitchen,
#: a laundry point. Used as the starting point on the installation screen so
#: the first number appears without anybody typing a schedule.
TYPICAL_DWELLING: dict[str, int] = {
    "wc-tank": 2,
    "basin": 2,
    "shower": 1,
    "bath": 1,
    "sink": 1,
    "washing-machine": 1,
    "dishwasher": 1,
    "floor-drain": 2,
}


def typical_dwelling(count: int = 1, *, kind: SupplyKind = SupplyKind.TANK) -> FixtureSchedule:
    """``count`` typical dwellings, as one schedule."""
    return FixtureSchedule.of(
        {key: value * count for key, value in TYPICAL_DWELLING.items()},
        kind=kind,
        name=f"{count} דירות" if count > 1 else "דירה",
    )


__all__ = [
    "FIXTURES",
    "FIXTURES_BY_ID",
    "Fixture",
    "FixtureError",
    "FixtureSchedule",
    "ScheduleLine",
    "SupplyKind",
    "TYPICAL_DWELLING",
    "demand_flow",
    "fixture",
    "typical_dwelling",
]
