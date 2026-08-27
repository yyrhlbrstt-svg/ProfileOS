"""The numbers the owner asks for on a Sunday morning.

Every screen in this suite answers a question about one job. This module
answers questions about the shop: whether the year is better than last year,
which customers are worth the trouble, how many quotations turn into orders and
how long they take to, where the work actually goes, and which jobs are late.

Two disciplines run through all of it.

The first is that a report says what it counted. A win rate over four
quotations is not a win rate, it is four quotations, and it is labelled that
way rather than printed as ⁦25%⁩ next to a figure computed from four hundred.

The second is that nothing here invents a number that is not in the files. A
job with no quotation total contributes nothing to revenue rather than an
estimate of what it might have been, and the count of such jobs is reported so
nobody reads a total as complete when it is not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from .core.logging_setup import get_logger
from .projects.model import JobFile, JobStatus

_log = get_logger("reports")

#: Below this many jobs, a percentage is arithmetic rather than information.
THIN_EVIDENCE = 8

HEBREW_MONTHS = (
    "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
)


def _as_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


def _won(job: JobFile) -> bool:
    return job.status in (
        JobStatus.WON, JobStatus.IN_PRODUCTION, JobStatus.INSTALLED
    )


def _decided(job: JobFile) -> bool:
    """Whether the customer has answered — won or lost, not still waiting."""
    return _won(job) or job.status is JobStatus.LOST


@dataclass
class Figure:
    """One reported number, with what it rests on attached to it."""

    label: str
    value: float
    #: How many records produced it, so a percentage over four is readable.
    sample: int = 0
    unit: str = ""
    note: str = ""

    @property
    def is_thin(self) -> bool:
        return 0 < self.sample < THIN_EVIDENCE

    def format(self) -> str:
        if self.unit == "%":
            body = f"⁦{self.value:.0f}%⁩"
        elif self.unit == "₪":
            body = f"⁦{self.value:,.0f}⁩ ₪"
        elif self.unit == "ימים":
            body = f"⁦{self.value:.0f}⁩ ימים"
        else:
            body = f"⁦{self.value:,.1f}⁩ {self.unit}".strip()
        if self.is_thin:
            body += f" (מתוך ⁦{self.sample}⁩ בלבד)"
        return body


@dataclass
class Period:
    """A stretch of time a report covers, named the way a person says it."""

    start: date
    end: date
    label: str = ""

    def contains(self, when: date | None) -> bool:
        return when is not None and self.start <= when <= self.end

    def describe(self) -> str:
        return self.label or (
            f"⁦{self.start.strftime('%d/%m/%Y')}⁩–⁦{self.end.strftime('%d/%m/%Y')}⁩"
        )


def year(which: int) -> Period:
    return Period(date(which, 1, 1), date(which, 12, 31), f"שנת ⁦{which}⁩")


def month(which: int, of_year: int) -> Period:
    start = date(of_year, which, 1)
    end = (
        date(of_year + 1, 1, 1) if which == 12 else date(of_year, which + 1, 1)
    ) - timedelta(days=1)
    return Period(start, end, f"{HEBREW_MONTHS[which - 1]} ⁦{of_year}⁩")


def last_days(count: int, *, ending: date | None = None) -> Period:
    end = ending or date.today()
    return Period(end - timedelta(days=count), end, f"⁦{count}⁩ הימים האחרונים")


# --------------------------------------------------------------------------- #
# Sales
# --------------------------------------------------------------------------- #
@dataclass
class SalesReport:
    """What was quoted, what was ordered, and what it was worth."""

    period: Period
    quoted_count: int = 0
    quoted_value: float = 0.0
    won_count: int = 0
    won_value: float = 0.0
    lost_count: int = 0
    lost_value: float = 0.0
    open_count: int = 0
    open_value: float = 0.0
    #: Jobs with no money on them at all — counted, never estimated.
    unpriced: int = 0

    @property
    def decided(self) -> int:
        return self.won_count + self.lost_count

    def win_rate(self) -> Figure:
        """Share of decided quotations that turned into orders."""
        rate = (self.won_count / self.decided * 100.0) if self.decided else 0.0
        return Figure("אחוז סגירה", rate, sample=self.decided, unit="%")

    def value_win_rate(self) -> Figure:
        """The same question in money, which usually gives a different answer.

        A shop can win most of its quotations and lose most of its money, or
        the other way round. Both numbers belong on the page.
        """
        decided_value = self.won_value + self.lost_value
        rate = (self.won_value / decided_value * 100.0) if decided_value else 0.0
        return Figure("אחוז סגירה בכסף", rate, sample=self.decided, unit="%")

    def average_order(self) -> Figure:
        value = (self.won_value / self.won_count) if self.won_count else 0.0
        return Figure("הזמנה ממוצעת", value, sample=self.won_count, unit="₪")

    def rows(self) -> list[tuple[str, str, str]]:
        return [
            ("הצעות שנשלחו", f"⁦{self.quoted_count}⁩", f"⁦{self.quoted_value:,.0f}⁩ ₪"),
            ("הוזמנו", f"⁦{self.won_count}⁩", f"⁦{self.won_value:,.0f}⁩ ₪"),
            ("לא נסגרו", f"⁦{self.lost_count}⁩", f"⁦{self.lost_value:,.0f}⁩ ₪"),
            ("ממתינות לתשובה", f"⁦{self.open_count}⁩", f"⁦{self.open_value:,.0f}⁩ ₪"),
        ]

    def warnings(self) -> list[str]:
        found: list[str] = []
        if self.unpriced:
            found.append(
                f"⁦{self.unpriced}⁩ תיקים בתקופה בלי סכום הצעה — הם נספרו "
                "ולא נכללו בכסף"
            )
        if 0 < self.decided < THIN_EVIDENCE:
            found.append(
                f"רק ⁦{self.decided}⁩ הצעות הוכרעו בתקופה — אחוז הסגירה כאן "
                "הוא חשבון ולא מגמה"
            )
        return found

    def describe(self) -> str:
        return (
            f"{self.period.describe()}: הוזמנו ⁦{self.won_count}⁩ תיקים "
            f"בשווי ⁦{self.won_value:,.0f}⁩ ₪ · {self.win_rate().format()}"
        )


def sales(jobs: Iterable[JobFile], period: Period) -> SalesReport:
    """Quotations and orders inside a period, by the date each was quoted.

    A job is placed in the period by when it was quoted, not by when it was
    won, so a quotation sent in December and ordered in January still counts
    against December's effort — which is the question "did that month's
    quoting work" actually asks.
    """
    report = SalesReport(period=period)
    for job in jobs:
        when = _as_date(job.quoted_on) or _as_date(job.created)
        if not period.contains(when):
            continue
        if job.status is JobStatus.ENQUIRY:
            continue

        value = float(job.quote_total or 0.0)
        if not value:
            report.unpriced += 1

        report.quoted_count += 1
        report.quoted_value += value
        if _won(job):
            report.won_count += 1
            report.won_value += value
        elif job.status is JobStatus.LOST:
            report.lost_count += 1
            report.lost_value += value
        else:
            report.open_count += 1
            report.open_value += value
    return report


def by_month(jobs: Iterable[JobFile], of_year: int) -> list[dict[str, Any]]:
    """Twelve rows, one per month, so a year reads as a shape not a total."""
    kept = list(jobs)
    out: list[dict[str, Any]] = []
    for number in range(1, 13):
        report = sales(kept, month(number, of_year))
        out.append({
            "month": number,
            "label": HEBREW_MONTHS[number - 1],
            "quoted": report.quoted_count,
            "quoted_value": round(report.quoted_value, 2),
            "won": report.won_count,
            "won_value": round(report.won_value, 2),
            "win_rate_pct": round(report.win_rate().value, 1),
        })
    return out


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
@dataclass
class CustomerLine:
    """One customer's whole history with the shop, on one row."""

    customer_id: str
    name: str
    jobs: int = 0
    won: int = 0
    lost: int = 0
    value: float = 0.0
    last_seen: date | None = None
    #: Booked cost, when hours and materials have been recorded against it.
    cost: float = 0.0

    @property
    def win_rate(self) -> float:
        decided = self.won + self.lost
        return round(self.won / decided * 100.0, 1) if decided else 0.0

    @property
    def margin(self) -> float | None:
        """``None`` rather than zero when nothing was ever costed.

        A customer whose jobs were never costed has an unknown margin. Showing
        that as ⁦0%⁩ would put them at the bottom of a sorted list beside the
        customers who genuinely lose money, which is the opposite of true.
        """
        if not self.cost or not self.value:
            return None
        return round((self.value - self.cost) / self.value * 100.0, 1)

    @property
    def is_dormant(self) -> bool:
        if self.last_seen is None:
            return True
        return (date.today() - self.last_seen).days > 365


def by_customer(
    jobs: Iterable[JobFile], *, costs: dict[str, float] | None = None
) -> list[CustomerLine]:
    """Every customer, best first by what they have actually ordered."""
    lines: dict[str, CustomerLine] = {}
    booked = costs or {}

    for job in jobs:
        key = job.customer_id or job.customer_name or "—"
        line = lines.setdefault(
            key, CustomerLine(customer_id=job.customer_id, name=job.customer_name or key)
        )
        line.jobs += 1
        when = _as_date(job.updated) or _as_date(job.created)
        if when and (line.last_seen is None or when > line.last_seen):
            line.last_seen = when
        if _won(job):
            line.won += 1
            line.value += float(job.quote_total or 0.0)
            line.cost += float(booked.get(job.job_id, 0.0))
        elif job.status is JobStatus.LOST:
            line.lost += 1

    return sorted(lines.values(), key=lambda line: line.value, reverse=True)


# --------------------------------------------------------------------------- #
# The pipeline, and what is late
# --------------------------------------------------------------------------- #
@dataclass
class Pipeline:
    """Live work by stage, and what it is worth."""

    counts: dict[str, int] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    overdue: list[tuple[str, str, int]] = field(default_factory=list)
    due_this_week: list[tuple[str, str, int]] = field(default_factory=list)
    undated: int = 0

    @property
    def open_value(self) -> float:
        return round(sum(self.values.values()), 2)

    def rows(self) -> list[tuple[str, str, str]]:
        return [
            (
                JobStatus(key).hebrew,
                f"⁦{self.counts[key]}⁩",
                f"⁦{self.values.get(key, 0.0):,.0f}⁩ ₪",
            )
            for key in self.counts
        ]

    def describe(self) -> str:
        late = f" · ⁦{len(self.overdue)}⁩ באיחור" if self.overdue else ""
        return (
            f"⁦{sum(self.counts.values())}⁩ תיקים פתוחים בשווי "
            f"⁦{self.open_value:,.0f}⁩ ₪{late}"
        )


def pipeline(jobs: Iterable[JobFile], *, today: date | None = None) -> Pipeline:
    """What is live, what it is worth, and what has passed its date."""
    now = today or date.today()
    out = Pipeline()
    week = now + timedelta(days=7)

    for job in jobs:
        if not job.status.is_open:
            continue
        key = job.status.value
        out.counts[key] = out.counts.get(key, 0) + 1
        out.values[key] = out.values.get(key, 0.0) + float(job.quote_total or 0.0)

        due = _as_date(job.due_date)
        if due is None:
            out.undated += 1
            continue
        if due < now:
            out.overdue.append((job.job_id, job.name, (now - due).days))
        elif due <= week:
            out.due_this_week.append((job.job_id, job.name, (due - now).days))

    out.counts = {
        status.value: out.counts[status.value]
        for status in JobStatus
        if status.value in out.counts
    }
    out.overdue.sort(key=lambda row: row[2], reverse=True)
    out.due_this_week.sort(key=lambda row: row[2])
    return out


# --------------------------------------------------------------------------- #
# Where the work goes
# --------------------------------------------------------------------------- #
def where_the_time_went(
    timebook: Any, *, jobs: Iterable[JobFile] = ()
) -> dict[str, Any]:
    """Hours by operation and by person, with rework separated out.

    Rework is the figure worth the whole report. A shop that spends ⁦12%⁩ of
    its hours doing work a second time is paying for a problem somewhere
    upstream — a measurement, a drawing, a supplier — and cannot see it in any
    other number it keeps.
    """
    by_operation = timebook.by_operation()
    by_person = timebook.by_person()
    total = round(sum(by_operation.values()), 2)
    known = {job.job_id: job for job in jobs}

    per_job: dict[str, float] = defaultdict(float)
    for entry in timebook:
        if entry.job_id:
            per_job[entry.job_id] += entry.hours

    heaviest = sorted(per_job.items(), key=lambda pair: pair[1], reverse=True)[:10]
    return {
        "total_hours": total,
        "rework_pct": timebook.rework_share(),
        "by_operation": by_operation,
        "by_person": by_person,
        "heaviest_jobs": [
            {
                "job_id": job_id,
                "name": known[job_id].name if job_id in known else job_id,
                "hours": round(hours, 2),
            }
            for job_id, hours in heaviest
        ],
    }


# --------------------------------------------------------------------------- #
# One page
# --------------------------------------------------------------------------- #
def dashboard(
    jobs: Iterable[JobFile], *, on: date | None = None,
    costs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The Sunday-morning page: this year, last year, the pipeline, the top ten."""
    today = on or date.today()
    kept = list(jobs)

    this_year = sales(kept, year(today.year))
    previous = sales(kept, year(today.year - 1))
    live = pipeline(kept, today=today)
    customers = by_customer(kept, costs=costs)

    growth: float | None = None
    if previous.won_value:
        growth = round(
            (this_year.won_value - previous.won_value) / previous.won_value * 100.0,
            1,
        )

    return {
        "on": today.isoformat(),
        "this_year": this_year,
        "last_year": previous,
        "growth_pct": growth,
        "pipeline": live,
        "months": by_month(kept, today.year),
        "top_customers": customers[:10],
        "dormant_customers": [line.name for line in customers if line.is_dormant][:10],
        "headlines": [
            Figure("הוזמן השנה", this_year.won_value, this_year.won_count, "₪"),
            this_year.win_rate(),
            this_year.average_order(),
            Figure("צבר פתוח", live.open_value, sum(live.counts.values()), "₪"),
            Figure("באיחור", float(len(live.overdue)), len(live.overdue), "תיקים"),
        ],
    }


__all__ = [
    "THIN_EVIDENCE",
    "CustomerLine",
    "Figure",
    "Period",
    "Pipeline",
    "SalesReport",
    "by_customer",
    "by_month",
    "dashboard",
    "last_days",
    "month",
    "pipeline",
    "sales",
    "where_the_time_went",
    "year",
]
