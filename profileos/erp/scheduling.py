"""Capacity and delivery planning.

A promised date is a claim about the shop's capacity, and most shops make it by
looking at a wall planner. This turns it into arithmetic: what each job needs
in hours at each work centre, what each work centre has available on each
working day, and therefore the earliest date the last operation can finish.

Finite capacity, not infinite
-----------------------------
The distinction is the whole point. Infinite-capacity planning asks how long
the work takes and adds it to today; it produces dates that are always
achievable on paper and frequently missed. Finite capacity asks when the
machine is actually free, so a saw already committed to three jobs pushes the
fourth out — which is what happens on the floor whether or not the plan says so.

The Israeli working week
------------------------
Sunday to Thursday, with Friday a half day in many aluminium shops and
Saturday closed. Baking a Monday-to-Friday week into a scheduler quietly moves
every promised date by a day or two, so the working week is data.

Backward from a due date
------------------------
Given a date the customer needs, the scheduler works backwards to say when the
job has to start — and if that is in the past, it says so plainly rather than
producing a plan that begins yesterday.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Iterable

from ..core.errors import ProfileOSError


class SchedulingError(ProfileOSError):
    """A job cannot be scheduled as asked."""


class Operation(StrEnum):
    """The stations a job passes through, in order."""

    CUTTING = "cutting"
    MACHINING = "machining"
    GLAZING_ORDER = "glazing_order"
    ASSEMBLY = "assembly"
    GLAZING = "glazing"
    PACKING = "packing"


#: The order work moves in on the shop floor.
#:
#: Ordering glass is *not* in this chain. It is placed the day the job is
#: released and runs alongside cutting, machining and assembly — which is what
#: a shop actually does, because the supplier's lead time is dead time and
#: nobody spends it waiting. Putting it in the sequence adds the whole lead
#: time to every promised date for no reason. What it does constrain is
#: glazing: the unit cannot be glazed until both the frame is assembled and
#: the glass has arrived, whichever is later.
OPERATION_SEQUENCE: tuple[Operation, ...] = (
    Operation.CUTTING,
    Operation.MACHINING,
    Operation.ASSEMBLY,
    Operation.GLAZING,
    Operation.PACKING,
)


@dataclass(frozen=True)
class Calendar:
    """Which days the shop works, and for how long.

    ``weekday_hours`` is indexed the way :meth:`date.weekday` counts —
    Monday 0 through Sunday 6.
    """

    #: Sunday to Thursday full days, Friday a half day, Saturday closed.
    weekday_hours: tuple[float, ...] = (8.5, 8.5, 8.5, 8.5, 4.0, 0.0, 8.5)
    holidays: frozenset[date] = frozenset()

    def hours_on(self, day: date) -> float:
        if day in self.holidays:
            return 0.0
        return self.weekday_hours[day.weekday()]

    def is_working(self, day: date) -> bool:
        return self.hours_on(day) > 0.0

    def next_working_day(self, day: date) -> date:
        cursor = day
        for _ in range(370):
            if self.is_working(cursor):
                return cursor
            cursor += timedelta(days=1)
        raise SchedulingError(
            "No working day found within a year; check the calendar's hours "
            "and holiday list",
        )

    def previous_working_day(self, day: date) -> date:
        cursor = day
        for _ in range(370):
            if self.is_working(cursor):
                return cursor
            cursor -= timedelta(days=1)
        raise SchedulingError("No working day found within a year going backwards")

    def working_days(self, start: date, count: int) -> list[date]:
        days: list[date] = []
        cursor = start
        while len(days) < count:
            if self.is_working(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days


@dataclass(frozen=True)
class WorkCentre:
    """A machine or a bench, and what it can do in a day."""

    code: str
    name: str
    operation: Operation
    #: How many of these there are. Two saws double the daily capacity.
    stations: int = 1
    #: Fraction of the calendar day actually available, after setup, breaks and
    #: breakdowns. A saw quoted at 8.5 h does not cut for 8.5 h.
    efficiency: float = 0.85
    #: Work that must wait after this station before the next can start —
    #: sealant curing, powder coating going out and coming back.
    cure_days: int = 0

    def capacity_on(self, day: date, calendar: Calendar) -> float:
        return calendar.hours_on(day) * self.stations * self.efficiency


#: A plausible small fabricator: one saw, one machining centre, two benches.
DEFAULT_WORK_CENTRES: tuple[WorkCentre, ...] = (
    WorkCentre("SAW", "מסור חיתוך", Operation.CUTTING),
    WorkCentre("CNC", "מרכז עיבוד", Operation.MACHINING),
    WorkCentre("GLS-ORD", "זכוכית בהזמנה", Operation.GLAZING_ORDER,
               stations=99, efficiency=1.0),
    WorkCentre("ASM", "שולחן הרכבה", Operation.ASSEMBLY, stations=2),
    WorkCentre("GLZ", "שולחן זיגוג", Operation.GLAZING, cure_days=1),
    WorkCentre("PACK", "אריזה", Operation.PACKING),
)


@dataclass(frozen=True)
class StandardTimes:
    """How long the work takes, per unit of the thing being worked on.

    These are the numbers a shop tunes first, so they are one editable object
    rather than constants sprinkled through the scheduler.
    """

    minutes_per_cut: float = 1.2
    minutes_per_machining: float = 0.8
    minutes_per_element_assembly: float = 22.0
    minutes_per_pane_glazing: float = 9.0
    minutes_per_element_packing: float = 6.0
    #: Calendar days a glass supplier takes. Not shop hours — waiting, not work.
    glass_lead_days: int = 10
    #: A minimum charge per operation, so a one-element job still books setup.
    setup_minutes: float = 20.0


@dataclass
class JobDemand:
    """What one job asks of the shop, in countable things."""

    job_id: str
    elements: int
    cuts: int
    machining_operations: int
    panes: int
    due: date | None = None
    name: str | None = None
    priority: int = 5

    def hours(self, times: StandardTimes) -> dict[Operation, float]:
        """Work content per operation, in hours."""
        minutes = {
            Operation.CUTTING: times.setup_minutes + self.cuts * times.minutes_per_cut,
            Operation.MACHINING: (
                times.setup_minutes
                + self.machining_operations * times.minutes_per_machining
            ),
            # Ordering glass is waiting, not work; it consumes no bench time.
            Operation.GLAZING_ORDER: 0.0,
            Operation.ASSEMBLY: (
                times.setup_minutes
                + self.elements * times.minutes_per_element_assembly
            ),
            Operation.GLAZING: times.setup_minutes + self.panes * times.minutes_per_pane_glazing,
            Operation.PACKING: (
                times.setup_minutes + self.elements * times.minutes_per_element_packing
            ),
        }
        return {operation: value / 60.0 for operation, value in minutes.items()}


@dataclass
class ScheduledOperation:
    """One operation of one job, placed on real days."""

    job_id: str
    operation: Operation
    work_centre: str
    start: date
    finish: date
    hours: float
    #: Hours booked per day, so the load report can be built from the plan.
    per_day: dict[date, float] = field(default_factory=dict)


@dataclass
class Schedule:
    """The plan, and what it implies for each job's delivery date."""

    operations: list[ScheduledOperation] = field(default_factory=list)
    completion: dict[str, date] = field(default_factory=dict)
    late: dict[str, int] = field(default_factory=dict)
    calendar: Calendar = field(default_factory=Calendar)
    warnings: list[str] = field(default_factory=list)

    @property
    def finish(self) -> date | None:
        return max(self.completion.values(), default=None)

    def load(self) -> dict[str, dict[date, float]]:
        """Hours booked per work centre per day."""
        result: dict[str, dict[date, float]] = {}
        for operation in self.operations:
            centre = result.setdefault(operation.work_centre, {})
            for day, hours in operation.per_day.items():
                centre[day] = centre.get(day, 0.0) + hours
        return result

    def utilisation(self, centres: Iterable[WorkCentre]) -> list[dict[str, object]]:
        """How hard each work centre is being worked over the plan's span."""
        load = self.load()
        rows: list[dict[str, object]] = []
        for centre in centres:
            booked = load.get(centre.code, {})
            if not booked:
                continue
            first, last = min(booked), max(booked)
            available = 0.0
            cursor = first
            while cursor <= last:
                available += centre.capacity_on(cursor, self.calendar)
                cursor += timedelta(days=1)
            used = sum(booked.values())
            rows.append(
                {
                    "code": centre.code,
                    "name": centre.name,
                    "hours": round(used, 2),
                    "available": round(available, 2),
                    "utilisation_pct": round(100.0 * used / available, 1) if available else 0.0,
                    "first": first.isoformat(),
                    "last": last.isoformat(),
                }
            )
        return rows

    def bottleneck(self, centres: Iterable[WorkCentre]) -> dict[str, object] | None:
        """The work centre holding everything else up."""
        rows = self.utilisation(centres)
        return max(rows, key=lambda row: row["utilisation_pct"]) if rows else None

    def summary(self) -> dict[str, object]:
        return {
            "jobs": len(self.completion),
            "operations": len(self.operations),
            "finish": self.finish.isoformat() if self.finish else None,
            "late_jobs": len([job for job, days in self.late.items() if days > 0]),
            "worst_lateness_days": max(self.late.values(), default=0),
            "warnings": len(self.warnings),
        }


class Scheduler:
    """Places jobs on work centres, respecting what is already booked."""

    def __init__(
        self,
        centres: Iterable[WorkCentre] = DEFAULT_WORK_CENTRES,
        calendar: Calendar | None = None,
        times: StandardTimes | None = None,
    ) -> None:
        self.centres = {centre.operation: centre for centre in centres}
        self.calendar = calendar or Calendar()
        self.times = times or StandardTimes()
        #: Hours already committed, per work centre per day.
        self.booked: dict[str, dict[date, float]] = {}

    def free_on(self, centre: WorkCentre, day: date) -> float:
        capacity = centre.capacity_on(day, self.calendar)
        return max(0.0, capacity - self.booked.get(centre.code, {}).get(day, 0.0))

    def _book(
        self, centre: WorkCentre, hours: float, earliest: date
    ) -> tuple[date, date, dict[date, float]]:
        """Consume ``hours`` from the first free capacity at or after ``earliest``."""
        remaining = hours
        per_day: dict[date, float] = {}
        cursor = self.calendar.next_working_day(earliest)
        start = finish = cursor

        if hours <= 1e-9:
            return cursor, cursor, {}

        placed_anything = False
        for _ in range(3650):
            if remaining <= 1e-9:
                break
            free = self.free_on(centre, cursor)
            if free > 1e-9:
                take = min(free, remaining)
                per_day[cursor] = per_day.get(cursor, 0.0) + take
                day_book = self.booked.setdefault(centre.code, {})
                day_book[cursor] = day_book.get(cursor, 0.0) + take
                if not placed_anything:
                    start = cursor
                    placed_anything = True
                remaining -= take
                finish = cursor
            cursor = self.calendar.next_working_day(cursor + timedelta(days=1))
        else:
            raise SchedulingError(
                "Could not place an operation within ten years; the work "
                "centre has no usable capacity",
                centre=centre.code, hours=round(hours, 2),
            )
        return start, finish, per_day

    def schedule(
        self, jobs: Iterable[JobDemand], *, start: date | None = None
    ) -> Schedule:
        """Place every job, most urgent first.

        Ordering by due date then priority is the classic earliest-due-date
        rule: it is not optimal for every objective, but it minimises maximum
        lateness, which is the thing a customer notices.
        """
        plan = Schedule(calendar=self.calendar)
        begin = self.calendar.next_working_day(start or date.today())

        ordered = sorted(
            jobs,
            key=lambda job: (job.due or date.max, job.priority, job.job_id),
        )
        for job in ordered:
            hours = job.hours(self.times)
            earliest = begin

            # Glass is ordered on release and arrives while the frames are
            # being made. Recorded as an operation so it shows on the plan,
            # but it consumes no capacity and does not block cutting.
            glass_ready = begin
            if job.panes:
                glass_ready = self.calendar.next_working_day(
                    begin + timedelta(days=self.times.glass_lead_days)
                )
                order_centre = self.centres.get(Operation.GLAZING_ORDER)
                if order_centre is not None:
                    plan.operations.append(
                        ScheduledOperation(
                            job.job_id, Operation.GLAZING_ORDER, order_centre.code,
                            begin, glass_ready, 0.0,
                        )
                    )

            for operation in OPERATION_SEQUENCE:
                centre = self.centres.get(operation)
                if centre is None:
                    continue
                needed = hours.get(operation, 0.0)

                if operation is Operation.GLAZING and job.panes:
                    # Whichever is later: the frame being ready, or the glass
                    # turning up.
                    earliest = max(earliest, glass_ready)

                op_start, op_finish, per_day = self._book(centre, needed, earliest)
                plan.operations.append(
                    ScheduledOperation(
                        job.job_id, operation, centre.code,
                        op_start, op_finish, needed, per_day,
                    )
                )
                earliest = op_finish
                if centre.cure_days:
                    earliest = self.calendar.next_working_day(
                        earliest + timedelta(days=centre.cure_days)
                    )
                else:
                    earliest = self.calendar.next_working_day(earliest)

            completion = self.calendar.previous_working_day(earliest)
            plan.completion[job.job_id] = completion
            if job.due is not None:
                lateness = (completion - job.due).days
                plan.late[job.job_id] = max(0, lateness)
                if lateness > 0:
                    plan.warnings.append(
                        f"{job.name or job.job_id} finishes {completion.isoformat()}, "
                        f"{lateness} day(s) after the promised {job.due.isoformat()}"
                    )
            else:
                plan.late[job.job_id] = 0
        return plan

    def latest_start(self, job: JobDemand, due: date) -> date:
        """Work backwards: the last date this job can start and still be on time.

        Capacity already booked by other jobs is deliberately ignored here —
        this answers "how long does this job take end to end", which is the
        question asked when quoting a lead time, not when committing a slot.
        """
        hours = job.hours(self.times)
        cursor = self.calendar.previous_working_day(due)
        glazing_start: date | None = None
        for operation in reversed(OPERATION_SEQUENCE):
            centre = self.centres.get(operation)
            if centre is None:
                continue
            if centre.cure_days:
                cursor = self.calendar.previous_working_day(
                    cursor - timedelta(days=centre.cure_days)
                )
            remaining = hours.get(operation, 0.0)
            while remaining > 1e-9:
                capacity = centre.capacity_on(cursor, self.calendar)
                remaining -= capacity
                if remaining > 1e-9:
                    cursor = self.calendar.previous_working_day(cursor - timedelta(days=1))
            if operation is Operation.GLAZING:
                glazing_start = cursor
            cursor = self.calendar.previous_working_day(cursor - timedelta(days=1))

        # The glass has to be on site by the time glazing starts, and it was
        # ordered at release. Whichever constraint bites first sets the date.
        if job.panes and glazing_start is not None:
            glass_order_by = self.calendar.previous_working_day(
                glazing_start - timedelta(days=self.times.glass_lead_days)
            )
            cursor = min(cursor, glass_order_by)
        return cursor


def demand_from_builds(
    builds: Iterable[object], job_id: str, *, due: date | None = None,
    name: str | None = None, priority: int = 5,
) -> JobDemand:
    """Count what a set of built elements asks of the shop.

    Duck-typed on the element builder's output so the scheduler does not have
    to import the thing that happens to feed it.
    """
    elements = cuts = machining = panes = 0
    for build in builds:
        opening = getattr(build, "opening", None)
        quantity = int(getattr(opening, "quantity", 1) or 1)
        elements += quantity
        cuts += sum(getattr(c, "quantity", 1) for c in getattr(build, "cuts", [])) * quantity
        panes += sum(getattr(p, "quantity", 1) for p in getattr(build, "glass", [])) * quantity
        machining += len(getattr(build, "cuts", [])) * 2 * quantity
    return JobDemand(
        job_id=job_id, elements=elements, cuts=cuts,
        machining_operations=machining, panes=panes,
        due=due, name=name, priority=priority,
    )


__all__ = [
    "SchedulingError",
    "Operation",
    "OPERATION_SEQUENCE",
    "Calendar",
    "WorkCentre",
    "DEFAULT_WORK_CENTRES",
    "StandardTimes",
    "JobDemand",
    "ScheduledOperation",
    "Schedule",
    "Scheduler",
    "demand_from_builds",
]
