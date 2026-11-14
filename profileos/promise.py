"""When can we promise it? A delivery date from the shop's real backlog.

The owner on the phone with a customer is asked one question before price:
"when?". The honest answer is not "three weeks" from memory — it is the work
already ordered, ahead in the queue, at the pace this shop really works, on
the Israeli week with its holidays and short Fridays, plus the wait for
material. Everything here comes from the shop's own records:

* the jobs ordered and not yet installed, each with the workshop hours its
  quotation's norms gave (``JobFile.norm_hours``) — times the shop's own
  factor once it has learned one — less the hours already booked on it;
* the pace: workshop hours actually booked per working day over the last
  four weeks on the shop floor. Until there are enough booked days, the
  weekly hours the owner typed in settings. Neither → no date, and the
  answer says what is missing instead of guessing;
* the material wait the owner set, or a plain note that it is not counted.

Nothing is stored: the date is worked out each time from the same books the
pages show, so it cannot drift from them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

#: Days of booked hours needed before the measured pace is trusted.
ENOUGH_DAYS = 10
#: How far back the pace is measured.
PACE_WINDOW_DAYS = 28
#: The full working day the calendar counts in.
FULL_DAY_HOURS = 8.5

_WEEKDAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")
_ORDERED = ("won", "in_production")


def say_day(day: date) -> str:
    return f"יום {_WEEKDAYS[day.weekday()]} ⁦{day:%d/%m/%Y}⁩"


@dataclass
class Pace:
    """Workshop hours in one full working day, and where the figure is from."""

    per_day: float
    source: str  # "measured" | "settings" | ""
    days: int = 0

    @property
    def known(self) -> bool:
        return self.per_day > 0

    def describe(self) -> str:
        if self.source == "measured":
            return (f"קצב המפעל ⁦{self.per_day:.0f}⁩ שעות עבודה ביום — "
                    f"לפי השעות שנרשמו ב־⁦{self.days}⁩ ימים בחודש האחרון")
        if self.source == "settings":
            return (f"קצב המפעל ⁦{self.per_day:.0f}⁩ שעות ביום — לפי השעות השבועיות "
                    "שהוגדרו (עד שיירשמו מספיק שעות ברצפת הייצור)")
        return ""


@dataclass
class Promise:
    ready: date | None
    install: date | None
    pace: Pace
    backlog_hours: float
    new_hours: float
    jobs_ahead: int
    factor: float = 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.ready is not None

    def headline(self) -> str:
        if self.install is None:
            return "אי אפשר עדיין לחשב מועד — חסר קצב המפעל"
        return f"אפשר להבטיח התקנה מ{say_day(self.install)}"

    def short(self) -> str:
        """Two short lines for a narrow card; :meth:`lines` has the rest."""
        if not self.pace.known:
            return "הגדירו שעות עבודה בשבוע — מערכת ← מפעיל ← קצב ומועדים"
        source = "נמדד" if self.pace.source == "measured" else "לפי ההגדרות"
        said = (f"לפניה ⁦{self.jobs_ahead}⁩ עבודות · ⁦{self.backlog_hours:,.0f}⁩ שעות\n"
                f"קצב ⁦{self.pace.per_day:.0f}⁩ שעות ביום ({source})")
        if self.notes:
            said += "\n" + ("⚠ " + self.notes[0].split(" — ")[0])
        return said

    def lines(self) -> list[str]:
        if not self.pace.known:
            return ["כדי לחשב מועד צריך לדעת כמה שעות עבודה יש במפעל בשבוע: "
                    "רשמו שעות ברצפת הייצור, או הגדירו ״שעות עבודה בשבוע״ "
                    "במערכת ← מפעיל ← קצב ומועדים."]
        said = [
            f"לפניה בתור ⁦{self.jobs_ahead}⁩ עבודות שהוזמנו — "
            f"⁦{self.backlog_hours:,.0f}⁩ שעות עבודה שנשארו",
            self.pace.describe(),
        ]
        if self.new_hours:
            said.append(f"העבודה הזו: כ־⁦{self.new_hours:,.0f}⁩ שעות במפעל")
        if self.factor != 1.0:
            said.append(f"השעות מתוקנות לפי הקצב שנלמד במפעל (⁦{self.factor:.2f}⁩ מהנורמה)")
        if self.ready is not None:
            said.append(f"הייצור מסתיים ב{say_day(self.ready)}")
        return said + self.notes


def measured_pace(entries: Iterable[Any], *, today: date, calendar: Any) -> Pace:
    """Workshop hours booked per full working day over the last four weeks."""
    from .quoting.labour_learning import NOT_WORKSHOP

    start = today - timedelta(days=PACE_WINDOW_DAYS)
    hours = 0.0
    days: set[date] = set()
    for entry in entries:
        on = getattr(entry, "on", None)
        if not isinstance(on, date) or not (start <= on < today):
            continue
        if getattr(entry, "operation", "") in NOT_WORKSHOP:
            continue
        hours += float(getattr(entry, "hours", 0.0) or 0.0)
        days.add(on)
    if len(days) < ENOUGH_DAYS or hours <= 0:
        return Pace(0.0, "", len(days))
    # The window's working time in full days: a short Friday counts as half,
    # a festival as what is really worked.
    full_days = sum(calendar.hours_on(start + timedelta(days=offset))
                    for offset in range(PACE_WINDOW_DAYS)) / FULL_DAY_HOURS
    if full_days <= 0:
        return Pace(0.0, "", len(days))
    return Pace(round(hours / full_days, 1), "measured", len(days))


def settings_pace(weekly_hours: float) -> Pace:
    """The owner's own figure: a week here is five full days and a half."""
    if weekly_hours <= 0:
        return Pace(0.0, "")
    return Pace(round(weekly_hours / 5.5, 1), "settings")


def remaining_hours(job: Any, booked_on_job: float, factor: float) -> float | None:
    """Workshop hours still to do on an ordered job; ``None`` when the job
    never had its hours worked out (no priced quotation)."""
    norm = sum((getattr(job, "norm_hours", None) or {}).values())
    if norm <= 0:
        return None
    return max(0.0, norm * factor - booked_on_job)


def consume(hours: float, start: date, pace: Pace, calendar: Any,
            spare: float | None = None) -> tuple[date, float]:
    """Work ``hours`` from ``start`` on. Returns the day the work ends and the
    hours still free on that day. ``spare`` is what is left of ``start``
    itself when another job ended on it."""
    day = calendar.next_working_day(start)
    free = spare if spare is not None and day == start else (
        pace.per_day * calendar.hours_on(day) / FULL_DAY_HOURS)
    left = hours
    for _ in range(3650):
        if left <= free + 1e-6:
            return day, free - left
        left -= free
        day = calendar.next_working_day(day + timedelta(days=1))
        free = pace.per_day * calendar.hours_on(day) / FULL_DAY_HOURS
    return day, 0.0


def finish_after(hours: float, start: date, pace: Pace, calendar: Any) -> date:
    """The working day on which ``hours`` of work, begun on ``start``, ends."""
    return consume(hours, start, pace, calendar)[0]


def promise(*, new_hours: float = 0.0, jobs: Iterable[Any] | None = None,
            timebook: Any = None, settings: Any = None, today: date | None = None,
            exclude_job: str = "") -> Promise:
    """The earliest honest date for a new job of ``new_hours`` workshop hours.

    ``exclude_job`` leaves a job out of the queue — the one being quoted,
    when it is already in the book as ordered.
    """
    from .erp.scheduling import Calendar
    from .quoting.labour_learning import NOT_WORKSHOP, calibrate

    today = today or date.today()
    calendar = Calendar.israeli()
    if jobs is None:
        from .projects import default_store

        jobs = default_store().all()
    jobs = list(jobs)
    if timebook is None:
        from .erp.timesheets import default_timebook

        timebook = default_timebook()
    if settings is None:
        from .core.config import get_settings

        settings = get_settings()

    entries = list(timebook)
    pace = measured_pace(entries, today=today, calendar=calendar)
    if not pace.known:
        pace = settings_pace(float(getattr(settings, "workshop_hours_per_week", 0) or 0))

    calibration = calibrate(jobs, timebook)
    factor = calibration.factor if calibration.enough else 1.0

    backlog = 0.0
    ahead = 0
    unknown: list[str] = []
    for job in jobs:
        status = getattr(job.status, "value", job.status)
        if status not in _ORDERED or job.job_id == exclude_job:
            continue
        booked = sum(entry.hours for entry in timebook.for_job(job.job_id)
                     if entry.operation not in NOT_WORKSHOP)
        left = remaining_hours(job, booked, factor)
        if left is None:
            unknown.append(job.job_id)
            continue
        ahead += 1
        backlog += left

    notes: list[str] = []
    if unknown:
        listed = ", ".join(f"⁦{job_id}⁩" for job_id in unknown[:4])
        notes.append(f"⁦{len(unknown)}⁩ עבודות שהוזמנו בלי שעות מחושבות לא נספרו ({listed}) — "
                     "המועד בפועל עלול להיות מאוחר יותר")
    lead = int(getattr(settings, "material_lead_working_days", 0) or 0)
    if lead <= 0:
        notes.append("זמן הגעת פרופילים וזכוכית לא נכלל — אפשר להגדיר אותו במערכת ← מפעיל ← קצב ומועדים")

    new_hours = max(0.0, new_hours * factor)
    if not pace.known:
        return Promise(None, None, pace, round(backlog, 1), round(new_hours, 1), ahead,
                       factor, notes)

    start = calendar.next_working_day(today + timedelta(days=1))
    # The queue first, then this job from where the queue leaves the shop —
    # but not before its material is in (it arrives on the last lead day and
    # is worked from the next).
    day, spare = consume(backlog, start, pace, calendar) if backlog > 0 else (start, None)
    if spare is not None and spare <= 1e-6:
        day, spare = calendar.next_working_day(day + timedelta(days=1)), None
    if lead > 0:
        arrived = calendar.working_days(start, lead)[-1]
        first = calendar.next_working_day(arrived + timedelta(days=1))
        if first > day:
            day, spare = first, None
    ready = consume(new_hours, day, pace, calendar, spare)[0] if new_hours > 0 else day
    install = calendar.next_working_day(ready + timedelta(days=1))
    return Promise(ready, install, pace, round(backlog, 1), round(new_hours, 1), ahead,
                   factor, notes)


__all__ = ["ENOUGH_DAYS", "Pace", "Promise", "consume", "finish_after", "measured_pace", "promise",
           "remaining_hours", "say_day", "settings_pace"]
