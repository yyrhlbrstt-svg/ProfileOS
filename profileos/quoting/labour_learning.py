"""How long the work really takes in this shop, learned from its own hours.

Every quotation prices the workshop's labour from general norms — so long to
cut a piece, so long to assemble a frame, to fit a sash, to glaze a square
metre. Nobody's shop works at the norm. A shop 30% slower than it quotes
loses money on every job and never sees why; one 20% faster loses jobs on
price.

The hours booked against each finished job (the shop floor's hours tab) are
compared with what the norms said for that same job, and once there are
enough finished jobs the shop is shown its own factor — with the spread
between jobs, because one rush job is not a rule. Nothing changes until the
owner applies it; after that every quotation's hours are the norms times the
shop's factor, and the quotation says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

#: Fewer finished jobs than this and the factor is not offered.
ENOUGH_JOBS = 5

#: The shop floor's operation names, as the norms' activities. Site work
#: (installing, measuring) and repair are not in the workshop norms.
ACTIVITY_OF = {
    "חיתוך": "cutting_machining", "עיבוד CNC": "cutting_machining",
    "הרכבה": "assembly", "זיגוג": "glazing", "אריזה": "quality_packing",
    "פרזול": "hardware_fitting",
}
NOT_WORKSHOP = ("התקנה באתר", "מדידה", "תיקון")

ACTIVITY_HEBREW = {
    "cutting_machining": "חיתוך ועיבוד", "assembly": "הרכבה", "glazing": "זיגוג",
    "hardware_fitting": "פרזול", "quality_packing": "בדיקה ואריזה",
}


def norm_hours(builds: Iterable[Any]) -> dict[str, float]:
    """What the general norms say the workshop needs for these openings."""
    from .pricing import LabourRates

    return LabourRates().hours_for(list(builds))


def booked_hours(entries: Iterable[Any]) -> float:
    """Workshop hours booked on a job; site work and rework are left out —
    rework is not what a norm should learn from."""
    return round(sum(entry.hours for entry in entries
                     if not entry.rework and entry.operation not in NOT_WORKSHOP), 2)


@dataclass
class JobHours:
    job_id: str
    name: str
    norm: float
    booked: float

    @property
    def ratio(self) -> float:
        return self.booked / self.norm if self.norm > 0 else 0.0


@dataclass
class Calibration:
    jobs: list[JobHours] = field(default_factory=list)

    @property
    def enough(self) -> bool:
        return len(self.jobs) >= ENOUGH_JOBS

    @property
    def factor(self) -> float:
        """Booked over norm, over all the jobs together — a big job weighs
        more than a small one, as it does in the shop's month."""
        norm = sum(job.norm for job in self.jobs)
        return round(sum(job.booked for job in self.jobs) / norm, 2) if norm > 0 else 1.0

    @property
    def spread(self) -> tuple[float, float]:
        """The middle of the jobs' own ratios — from the lower to the upper quartile."""
        ratios = sorted(job.ratio for job in self.jobs)
        if not ratios:
            return (1.0, 1.0)
        low = ratios[len(ratios) // 4]
        high = ratios[(3 * len(ratios)) // 4 if len(ratios) > 1 else 0]
        return (round(low, 2), round(high, 2))

    def describe(self) -> str:
        if not self.jobs:
            return ("עוד אין עבודות גמורות עם שעות רשומות — השעות בהצעות לפי נורמות כלליות, "
                    "לא שלכם. רישום שעות ברצפת הייצור מלמד את התוכנה את הקצב שלכם.")
        if not self.enough:
            return (f"⁦{len(self.jobs)}⁩ עבודות עם שעות רשומות — צריך לפחות ⁦{ENOUGH_JOBS}⁩ "
                    "כדי ללמוד מהן. בינתיים השעות בהצעות לפי נורמות כלליות.")
        low, high = self.spread
        return (f"לפי ⁦{len(self.jobs)}⁩ עבודות גמורות: העבודה במפעל לוקחת ⁦{self.factor:.2f}⁩ "
                f"מהנורמה (רוב העבודות בין ⁦{low:.2f}⁩ ל־⁦{high:.2f}⁩).")


def calibrate(jobs: Iterable[Any], book: Any) -> Calibration:
    """Finished jobs with both the norm hours recorded and hours booked."""
    calibration = Calibration()
    for job in jobs:
        if getattr(job.status, "value", job.status) != "installed":
            continue
        norm = round(sum((getattr(job, "norm_hours", {}) or {}).values()), 2)
        if norm <= 0:
            continue
        booked = booked_hours(book.for_job(job.job_id))
        if booked <= 0:
            continue
        calibration.jobs.append(JobHours(job.job_id, job.name, norm, booked))
    return calibration


__all__ = [
    "ACTIVITY_HEBREW",
    "ACTIVITY_OF",
    "Calibration",
    "ENOUGH_JOBS",
    "JobHours",
    "NOT_WORKSHOP",
    "booked_hours",
    "calibrate",
    "norm_hours",
]
