"""Drainage and venting: sizing the waste side from fixture units.

Waste pipes are not sized by flow the way a supply is. A drain runs part full
by design — the air above the water is what lets the system breathe — so the
trade sizes drains from tabulated capacities in drainage fixture units against
the pipe's size and its fall, and sizes the vents that keep the traps sealed
from the same units and the length of the vent run.

Three rules sit above the tables and override them, because they come from how
the thing physically works rather than from arithmetic:

* A drain never reduces in the direction of flow. A pipe that narrows
  downstream is a blockage waiting for a wet wipe.
* A branch is never smaller than the largest trap discharging into it.
* A WC discharges to 100 mm. No table saves a 75 mm WC branch.

The capacities here are the conventional trade figures. They agree with the
tables an Israeli plumber works from under ת"י 1205 and with the model codes
those tables descend from, but the authority having jurisdiction over a given
job is the one whose table governs — so every result says which table it came
from and every check that is a matter of judgement is reported, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import ProfileOSError
from .fixtures import FixtureSchedule


class DrainageError(ProfileOSError):
    """A drainage arrangement that cannot be sized as asked."""


#: Horizontal drain capacity in drainage fixture units, by nominal size [mm]
#: and fall. Falls are the three the trade lays: 1:100, 1:50 and 1:25.
#: A dash means the size is not permitted at that fall at all.
HORIZONTAL_DFU: dict[float, dict[float, float | None]] = {
    50.0:  {0.01: None, 0.02: 21.0,   0.04: 26.0},
    75.0:  {0.01: 20.0, 0.02: 27.0,   0.04: 36.0},
    100.0: {0.01: 180.0, 0.02: 216.0, 0.04: 250.0},
    125.0: {0.01: 390.0, 0.02: 480.0, 0.04: 575.0},
    150.0: {0.01: 700.0, 0.02: 840.0, 0.04: 1000.0},
    200.0: {0.01: 1600.0, 0.02: 1920.0, 0.04: 2300.0},
    250.0: {0.01: 2900.0, 0.02: 3500.0, 0.04: 4200.0},
    300.0: {0.01: 4600.0, 0.02: 5600.0, 0.04: 6700.0},
}

#: Stack capacity: (total DFU on the stack, DFU permitted on any one branch
#: interval). A stack is limited twice — by the whole load it carries and by
#: how much may join it at one floor, because a slug entering at one level is
#: what breaks the seal on the floor below.
STACK_DFU: dict[float, tuple[float, float]] = {
    50.0:  (24.0, 6.0),
    75.0:  (60.0, 16.0),
    100.0: (500.0, 90.0),
    125.0: (1100.0, 200.0),
    150.0: (1900.0, 350.0),
    200.0: (3600.0, 600.0),
    250.0: (5600.0, 1000.0),
    300.0: (8400.0, 1500.0),
}

#: Maximum developed length [m] of a vent of a given size, by the DFU it
#: serves. Read: at this many units, a vent of this size may run this far.
VENT_LENGTH: dict[float, list[tuple[float, float]]] = {
    32.0: [(8.0, 15.0), (10.0, 9.0)],
    40.0: [(12.0, 30.0), (20.0, 15.0)],
    50.0: [(20.0, 60.0), (42.0, 45.0), (60.0, 30.0)],
    75.0: [(42.0, 90.0), (240.0, 60.0), (500.0, 30.0)],
    100.0: [(500.0, 120.0), (1100.0, 90.0), (2000.0, 60.0)],
    150.0: [(1900.0, 300.0), (3600.0, 210.0), (5600.0, 125.0)],
}

#: The fall a drain must have at least, by size. Small pipes need more fall to
#: stay self-cleansing; large ones need less and would run too fast with more.
MINIMUM_FALL: dict[float, float] = {
    50.0: 0.02, 75.0: 0.02, 100.0: 0.01, 125.0: 0.01,
    150.0: 0.01, 200.0: 0.008, 250.0: 0.008, 300.0: 0.008,
}

SIZES: tuple[float, ...] = tuple(sorted(HORIZONTAL_DFU))


def _nearest_fall(fall: float) -> float:
    """The tabulated fall at or below the one asked for.

    Rounding *down* is the safe direction: a pipe laid at 1.5% is sized on the
    1% column, so the extra fall is spare capacity rather than a promise the
    table never made.
    """
    if fall <= 0:
        raise DrainageError("שיפוע חייב להיות חיובי", fall=fall)
    columns = sorted(HORIZONTAL_DFU[100.0])
    below = [column for column in columns if column <= fall + 1e-9]
    return below[-1] if below else columns[0]


@dataclass
class DrainResult:
    """One drain sized, with everything that decided it."""

    size_mm: float | None
    dfu: float
    fall: float
    capacity_dfu: float = 0.0
    #: What forced the size up beyond what the table alone would give.
    governed_by: str = "table"
    notes: list[str] = field(default_factory=list)
    rejected: list[tuple[float, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.size_mm is not None

    @property
    def spare_dfu(self) -> float:
        return round(self.capacity_dfu - self.dfu, 1)

    @property
    def utilisation(self) -> float:
        return round(self.dfu / self.capacity_dfu * 100.0, 1) if self.capacity_dfu else 0.0

    def describe(self) -> str:
        if self.size_mm is None:
            return f"אין קוטר שמתאים ל־{self.dfu:g} יחידות ניקוז בשיפוע {self.fall:.1%}"
        return (
            f"⌀{self.size_mm:.0f} מ\"מ בשיפוע {self.fall:.1%} — "
            f"{self.dfu:g} מתוך {self.capacity_dfu:g} יחידות ({self.utilisation:.0f}%)"
        )


def size_horizontal_drain(
    dfu: float,
    *,
    fall: float = 0.02,
    largest_trap_mm: float = 0.0,
    serves_wc: bool = False,
    minimum_mm: float = 0.0,
) -> DrainResult:
    """The smallest horizontal drain that carries ``dfu`` at ``fall``.

    ``largest_trap_mm`` and ``serves_wc`` are the two rules that beat the
    table: a branch is never smaller than what discharges into it, and a WC
    always gets 100 mm.
    """
    if dfu < 0:
        raise DrainageError("יחידות ניקוז לא יכולות להיות שליליות", dfu=dfu)

    column = _nearest_fall(fall)
    floor_mm = max(largest_trap_mm, minimum_mm, 100.0 if serves_wc else 0.0)
    result = DrainResult(size_mm=None, dfu=dfu, fall=column)
    if column != fall:
        result.notes.append(
            f"השיפוע {fall:.2%} חושב לפי טור {column:.0%} — הקיבולת שמורה לצד הבטוח"
        )

    for size in SIZES:
        capacity = HORIZONTAL_DFU[size][column]
        if capacity is None:
            result.rejected.append((size, f"אינו מותר בשיפוע {column:.0%}"))
            continue
        if size < floor_mm:
            reason = (
                "קטן מהמחסום הגדול ביותר" if largest_trap_mm >= size
                else "אסלה מחייבת 100 מ\"מ" if serves_wc else "קטן מהמינימום שנדרש"
            )
            result.rejected.append((size, reason))
            continue
        if capacity < dfu:
            result.rejected.append(
                (size, f"קיבולת {capacity:g} יחידות, נדרשות {dfu:g}")
            )
            continue

        result.size_mm = size
        result.capacity_dfu = capacity
        table_size = _table_only_size(dfu, column)
        if table_size is not None and size > table_size:
            result.governed_by = (
                "אסלה" if serves_wc and floor_mm == 100.0 else "מחסום"
            )
        minimum = MINIMUM_FALL.get(size)
        if minimum is not None and fall < minimum - 1e-9:
            result.notes.append(
                f"שיפוע {fall:.2%} נמוך מהמינימום {minimum:.1%} לקוטר ⌀{size:.0f}"
            )
        return result

    result.notes.append("אף קוטר בטבלה אינו מספיק — פצלו את הקו או הגדילו את השיפוע")
    return result


def _table_only_size(dfu: float, column: float) -> float | None:
    """What the capacity table alone would have chosen, ignoring the rules."""
    for size in SIZES:
        capacity = HORIZONTAL_DFU[size][column]
        if capacity is not None and capacity >= dfu:
            return size
    return None


@dataclass
class StackResult:
    """A stack sized against both of its limits."""

    size_mm: float | None
    total_dfu: float
    branch_dfu: float
    total_capacity: float = 0.0
    branch_capacity: float = 0.0
    governed_by: str = "total"
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.size_mm is not None

    def describe(self) -> str:
        if self.size_mm is None:
            return "אין קוטר מפל שמתאים לעומס הזה"
        governed = "העומס הכולל" if self.governed_by == "total" else "העומס בקומה אחת"
        return (
            f"מפל ⌀{self.size_mm:.0f} מ\"מ — {self.total_dfu:g} יחידות בסך הכול, "
            f"{self.branch_dfu:g} בקומה. נקבע לפי {governed}."
        )


def size_stack(
    total_dfu: float,
    *,
    branch_dfu: float = 0.0,
    serves_wc: bool = True,
) -> StackResult:
    """The smallest stack carrying the whole load *and* the worst floor."""
    if total_dfu < 0 or branch_dfu < 0:
        raise DrainageError("יחידות ניקוז לא יכולות להיות שליליות")
    if branch_dfu > total_dfu:
        raise DrainageError(
            "העומס בקומה אחת אינו יכול לעלות על העומס הכולל של המפל",
            branch_dfu=branch_dfu, total_dfu=total_dfu,
        )

    floor_mm = 100.0 if serves_wc else 0.0
    result = StackResult(size_mm=None, total_dfu=total_dfu, branch_dfu=branch_dfu)
    for size in sorted(STACK_DFU):
        total_capacity, branch_capacity = STACK_DFU[size]
        if size < floor_mm:
            continue
        if total_capacity < total_dfu or branch_capacity < branch_dfu:
            continue
        result.size_mm = size
        result.total_capacity = total_capacity
        result.branch_capacity = branch_capacity
        # Which limit actually chose this size: the one that would have
        # rejected the size below it.
        smaller = [s for s in sorted(STACK_DFU) if s < size and s >= floor_mm]
        if smaller:
            previous_total, previous_branch = STACK_DFU[smaller[-1]]
            if previous_branch < branch_dfu and previous_total >= total_dfu:
                result.governed_by = "branch"
        if serves_wc and size == 100.0 and total_dfu < 24.0:
            result.notes.append("אסלה מחייבת מפל 100 מ\"מ גם בעומס נמוך")
        return result

    result.notes.append("העומס חורג מהטבלה — פצלו למפלים נפרדים")
    return result


@dataclass
class VentResult:
    """A vent sized for its load and the distance it has to run."""

    size_mm: float | None
    dfu: float
    length_m: float
    permitted_length_m: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.size_mm is not None

    def describe(self) -> str:
        if self.size_mm is None:
            return "אין קוטר אוורור שמתאים לאורך ולעומס האלה"
        return (
            f"אוורור ⌀{self.size_mm:.0f} מ\"מ — עד "
            f"⁦{self.permitted_length_m:.0f}⁩ מ' באורך פיתוח, נדרשו ⁦{self.length_m:.0f}⁩ מ'"
        )


def size_vent(dfu: float, length_m: float, *, drain_mm: float = 0.0) -> VentResult:
    """The smallest vent for ``dfu`` over ``length_m`` of developed run.

    A vent is never smaller than half its drain, and never below 32 mm: a
    smaller pipe closes with condensate and stops being a vent at all.
    """
    if dfu < 0 or length_m < 0:
        raise DrainageError("עומס ואורך חייבים להיות אי-שליליים")

    floor_mm = max(32.0, drain_mm / 2.0)
    result = VentResult(size_mm=None, dfu=dfu, length_m=length_m)
    for size in sorted(VENT_LENGTH):
        if size < floor_mm - 1e-9:
            continue
        permitted = 0.0
        for limit_dfu, limit_length in VENT_LENGTH[size]:
            if dfu <= limit_dfu:
                permitted = limit_length
                break
        if permitted <= 0 or permitted < length_m:
            continue
        result.size_mm = size
        result.permitted_length_m = permitted
        if size == floor_mm and drain_mm:
            result.notes.append(
                f"נקבע לפי חצי מקוטר הניקוז (⌀{drain_mm:.0f} מ\"מ)"
            )
        return result

    result.notes.append("האורך חורג מהטבלה — הוסיפו אוורור נוסף או הגדילו את הקוטר")
    return result


@dataclass
class DrainageDesign:
    """A whole waste arrangement sized in one go, from a fixture schedule."""

    branch: DrainResult
    stack: StackResult
    vent: VentResult
    drain: DrainResult
    schedule: FixtureSchedule

    @property
    def ok(self) -> bool:
        return all(part.ok for part in (self.branch, self.stack, self.vent, self.drain))

    def rows(self) -> list[tuple[str, str, str]]:
        """Each part, its size and its reasoning — one table for the screen."""
        return [
            ("ענף קומתי", self._size(self.branch.size_mm), self.branch.describe()),
            ("מפל", self._size(self.stack.size_mm), self.stack.describe()),
            ("אוורור", self._size(self.vent.size_mm), self.vent.describe()),
            ("קו יציאה", self._size(self.drain.size_mm), self.drain.describe()),
        ]

    @staticmethod
    def _size(value: float | None) -> str:
        return f"⌀{value:.0f}" if value else "—"

    def notes(self) -> list[str]:
        gathered: list[str] = []
        for part in (self.branch, self.stack, self.vent, self.drain):
            gathered.extend(part.notes)
        return list(dict.fromkeys(gathered))


def design_drainage(
    schedule: FixtureSchedule,
    *,
    floors: int = 1,
    fall: float = 0.02,
    vent_length_m: float = 20.0,
    house_drain_fall: float = 0.01,
) -> DrainageDesign:
    """Size a branch, a stack, its vent and the house drain from one schedule.

    ``schedule`` is the whole building; ``floors`` splits it into the load that
    joins the stack at one level, which is the limit that usually decides the
    stack size in a residential block.
    """
    if floors <= 0:
        raise DrainageError("מספר הקומות חייב להיות חיובי", floors=floors)

    total_dfu = schedule.dfu
    branch_dfu = round(total_dfu / floors, 2)
    serves_wc = any(
        line.fixture.trap_mm >= 100.0 and line.quantity for line in schedule.lines
    )
    largest_trap = schedule.largest_trap

    branch = size_horizontal_drain(
        branch_dfu, fall=fall, largest_trap_mm=largest_trap, serves_wc=serves_wc
    )
    stack = size_stack(total_dfu, branch_dfu=branch_dfu, serves_wc=serves_wc)
    vent = size_vent(total_dfu, vent_length_m, drain_mm=stack.size_mm or 0.0)
    drain = size_horizontal_drain(
        total_dfu,
        fall=house_drain_fall,
        largest_trap_mm=largest_trap,
        serves_wc=serves_wc,
        # A house drain never leaves the building smaller than the stack that
        # feeds it: the flow has nowhere to go but out.
        minimum_mm=stack.size_mm or 0.0,
    )
    return DrainageDesign(
        branch=branch, stack=stack, vent=vent, drain=drain, schedule=schedule
    )


__all__ = [
    "DrainResult",
    "DrainageDesign",
    "DrainageError",
    "HORIZONTAL_DFU",
    "MINIMUM_FALL",
    "STACK_DFU",
    "SIZES",
    "StackResult",
    "VENT_LENGTH",
    "VentResult",
    "design_drainage",
    "size_horizontal_drain",
    "size_stack",
    "size_vent",
]
