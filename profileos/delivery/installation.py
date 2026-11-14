"""Fitting: the half of the job that happens away from the workshop.

Production planning in this trade is well served and installation is not.
The shop knows to the minute how long a sash takes to assemble and has no
idea how long the same unit takes to fit on the third floor of a building
with no lift — so installation is promised in whole weeks, crews are sent out
short, and the customer is told "Sunday" by somebody looking at a wall
calendar.

The times here are per unit and per condition, because that is what actually
varies: the same window is quick in a new build with a clean opening and slow
in a renovation where the old frame has to come out first. Crews have sizes
and the heavy units need people, so a plan that puts a two-man crew on a
lift-slide is caught here rather than at the site.

Days come from the shop's own calendar, so the plan never lands on a festival.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from ..core.errors import ProfileOSError
from .packing import Handling, PackedUnit


class SiteCondition(StrEnum):
    """What the fitters are walking into."""

    NEW_BUILD = "new_build"
    RENOVATION = "renovation"
    OCCUPIED = "occupied"

    @property
    def hebrew(self) -> str:
        return {
            "new_build": "בנייה חדשה",
            "renovation": "שיפוץ — פירוק הישן",
            "occupied": "דירה מאוכלסת",
        }[self.value]

    @property
    def factor(self) -> float:
        """How much longer everything takes here."""
        return {"new_build": 1.0, "renovation": 1.6, "occupied": 1.9}[self.value]

    @property
    def note(self) -> str:
        return {
            "new_build": "",
            "renovation": "כולל פירוק המשקוף הישן ופינוי",
            "occupied": "כיסוי ריהוט, ניקיון בסוף כל יום, ושעות עבודה מוגבלות",
        }[self.value]


class Access(StrEnum):
    """How the unit gets to the opening."""

    GROUND = "ground"
    LIFT = "lift"
    STAIRS = "stairs"
    CRANE = "crane"
    SCAFFOLD = "scaffold"

    @property
    def hebrew(self) -> str:
        return {
            "ground": "קומת קרקע",
            "lift": "מעלית",
            "stairs": "מדרגות",
            "crane": "מנוף",
            "scaffold": "פיגום",
        }[self.value]

    def minutes_per_floor(self) -> float:
        """Carrying time to one floor, per unit."""
        return {
            "ground": 0.0, "lift": 3.0, "stairs": 9.0,
            "crane": 12.0, "scaffold": 7.0,
        }[self.value]


@dataclass
class InstallTimes:
    """How long fitting takes. The first numbers a shop should tune."""

    #: Setting and fixing the frame, per square metre of opening.
    minutes_per_m2: float = 26.0
    #: Every unit costs this whatever its size — measuring, shimming, sealing.
    minutes_per_unit: float = 25.0
    #: Sealant, per metre of perimeter.
    minutes_per_metre_seal: float = 2.5
    #: Hanging and adjusting one opening leaf.
    minutes_per_sash: float = 12.0
    #: Fitting a shutter, on top of the window it hangs on.
    minutes_per_shutter: float = 35.0
    minutes_per_screen: float = 12.0
    #: Getting to the site and setting up, once a day.
    setup_minutes_per_day: float = 45.0
    #: Snagging and handover at the end of the job.
    handover_minutes: float = 60.0


@dataclass
class Crew:
    """One fitting team."""

    name: str
    people: int = 2
    #: Hours a day this crew is actually on the tools.
    hours_per_day: float = 8.0
    can_crane: bool = False
    skills: tuple[str, ...] = ()

    @property
    def person_hours_per_day(self) -> float:
        return self.people * self.hours_per_day


@dataclass
class InstallTask:
    """One unit, planned."""

    unit: PackedUnit
    minutes: float
    people: int
    day: date | None = None
    crew: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def person_minutes(self) -> float:
        return self.minutes * self.people


@dataclass
class InstallPlan:
    """The fitting half of the job, on real days."""

    job_id: str = ""
    site: str = ""
    condition: SiteCondition = SiteCondition.NEW_BUILD
    access: Access = Access.GROUND
    tasks: list[InstallTask] = field(default_factory=list)
    days: list[tuple[date, list[InstallTask]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> float:
        return round(sum(task.minutes for task in self.tasks), 1)

    @property
    def hours(self) -> float:
        return round(self.minutes / 60.0, 1)

    @property
    def person_hours(self) -> float:
        return round(sum(task.person_minutes for task in self.tasks) / 60.0, 1)

    @property
    def finish(self) -> date | None:
        return self.days[-1][0] if self.days else None

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("תנאי האתר", self.condition.hebrew),
            ("גישה", self.access.hebrew),
            ("יחידות", f"⁦{len(self.tasks)}⁩"),
            ("שעות עבודה", f"⁦{self.hours:.1f}⁩"),
            ("שעות-אדם", f"⁦{self.person_hours:.1f}⁩"),
            ("ימי הרכבה", f"⁦{len(self.days)}⁩"),
            ("סיום צפוי", self.finish.strftime("%d/%m/%Y") if self.finish else "—"),
        ]


def unit_minutes(
    unit: PackedUnit,
    times: InstallTimes,
    condition: SiteCondition,
    access: Access,
    *,
    sashes: int = 1,
) -> float:
    """How long one unit takes to fit, here, in these conditions."""
    minutes = times.minutes_per_unit
    minutes += unit.area * times.minutes_per_m2
    minutes += 2 * (unit.width + unit.height) / 1000.0 * times.minutes_per_metre_seal
    minutes += sashes * times.minutes_per_sash
    for accessory in unit.accessories:
        if "תריס" in accessory:
            minutes += times.minutes_per_shutter
        elif "רשת" in accessory:
            minutes += times.minutes_per_screen
    minutes += unit.floor * access.minutes_per_floor()
    return minutes * condition.factor


def plan_installation(
    units: list[PackedUnit],
    *,
    crew: Crew | None = None,
    times: InstallTimes | None = None,
    condition: SiteCondition = SiteCondition.NEW_BUILD,
    access: Access = Access.GROUND,
    start: date | None = None,
    calendar: Any = None,
    job_id: str = "",
    site: str = "",
) -> InstallPlan:
    """Lay the fitting out on real working days.

    The plan follows the same order as the packing list, because a crew that
    fits in a different order from the one the lorry was loaded in is a crew
    carrying units twice.
    """
    from ..erp.scheduling import Calendar

    crew = crew or Crew(name="צוות א׳")
    times = times or InstallTimes()
    calendar = calendar or Calendar.israeli()
    plan = InstallPlan(job_id=job_id, site=site, condition=condition, access=access)

    if crew.people < 1:
        raise ProfileOSError("צוות חייב לפחות אדם אחד")

    from .packing import _sort_key

    for unit in sorted(units, key=_sort_key):
        for _ in range(unit.quantity):
            single = PackedUnit(
                mark=unit.mark, description=unit.description,
                width=unit.width, height=unit.height, mass=unit.mass,
                quantity=1, location=unit.location, floor=unit.floor,
                sequence=unit.sequence, accessories=unit.accessories,
                glass_panes=unit.glass_panes, element_id=unit.element_id,
            )
            needed = single.handling.people
            task = InstallTask(
                unit=single,
                minutes=unit_minutes(single, times, condition, access),
                people=needed,
            )
            if needed > crew.people:
                task.notes.append(
                    f"{single.mark} דורש ⁦{needed}⁩ אנשים — הצוות מונה ⁦{crew.people}⁩"
                )
            if single.handling is Handling.CRANE and not crew.can_crane:
                task.notes.append(f"{single.mark} דורש מנוף — הזמן מראש")
            # Four identical windows raise the same warning four times; the
            # foreman needs to read it once.
            for note in task.notes:
                if note not in plan.warnings:
                    plan.warnings.append(note)
            plan.tasks.append(task)

    # -- lay it on days ------------------------------------------------------- #
    def budget_for(when: date) -> float:
        """What is really available on this day.

        The crew's own day is only the ceiling. A short Friday or a festival
        eve gives less, and a plan that ignores that puts eight hours of work
        into a two-hour afternoon — which is the whole reason the calendar is
        wired in here rather than only into the workshop schedule.
        """
        available = min(crew.hours_per_day, calendar.hours_on(when)) * 60.0
        return max(available - times.setup_minutes_per_day, 30.0)

    full_day = crew.hours_per_day * 60.0 - times.setup_minutes_per_day
    day = calendar.next_working_day(start or date.today())
    remaining = budget_for(day)
    current: list[InstallTask] = []
    for task in plan.tasks:
        # Move on when the day is used up — and also when the day is a short
        # one that this unit was never going to fit into, rather than booking
        # eight hours of work into a festival-eve afternoon.
        would_fit_a_full_day = task.minutes <= full_day
        # Keep stepping while the unit does not fit: a run of short days —
        # a Friday, then a festival eve — must not have a five-hour window
        # dropped into it just because the first one was rejected.
        for _ in range(30):
            if task.minutes <= remaining:
                break
            if current or would_fit_a_full_day:
                if current:
                    plan.days.append((day, current))
                day = calendar.next_working_day(
                    date.fromordinal(day.toordinal() + 1)
                )
                current, remaining = [], budget_for(day)
            else:
                break
        if task.minutes > full_day:
            note = (
                f"{task.unit.mark} לוקח ⁦{task.minutes / 60.0:.1f}⁩ שעות — "
                "יותר מיום עבודה אחד, ההרכבה שלו נמשכת ליום הבא"
            )
            task.notes.append(note)
            if note not in plan.warnings:
                plan.warnings.append(note)
        task.day = day
        task.crew = crew.name
        current.append(task)
        remaining -= task.minutes
    if current:
        # The handover is part of the last day, not an afterthought.
        plan.days.append((day, current))

    if plan.days and times.handover_minutes:
        plan.warnings.append(
            f"יום המסירה כולל ⁦{times.handover_minutes:.0f}⁩ דקות מסירה ובדק"
        )
    if condition.note:
        plan.warnings.append(condition.note)
    return plan


__all__ = [
    "Access",
    "Crew",
    "InstallPlan",
    "InstallTask",
    "InstallTimes",
    "SiteCondition",
    "plan_installation",
    "unit_minutes",
]
