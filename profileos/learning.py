"""What the program has learned from this shop's own records.

Nothing here is a general figure: every line is read from the shop's own
jobs and hours, and says how many it was read from. Where there is not yet
enough to learn from, it says how many more are needed — a lesson drawn from
two jobs would be worse than none.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

#: Fewer decided quotations than this and no closing rate is drawn.
ENOUGH_DECIDED = 4


@dataclass(frozen=True)
class Lesson:
    title: str
    detail: str
    #: Learned (enough records), or still learning.
    learned: bool = True


def _won(job: Any) -> bool:
    return getattr(job.status, "value", job.status) in ("won", "in_production", "installed")


def lessons(jobs: Iterable[Any], *, timebook: Any = None) -> list[Lesson]:
    from .quoting.benchmark import ENOUGH, benchmark

    jobs = list(jobs)
    found: list[Lesson] = []

    won = [job for job in jobs if _won(job)]
    lost = [job for job in jobs if getattr(job.status, "value", job.status) == "lost"]
    decided = len(won) + len(lost)
    if decided >= ENOUGH_DECIDED:
        rate = len(won) / decided * 100
        reasons = Counter(job.lost_reason for job in lost if job.lost_reason)
        why = (f" — הסיבה הנפוצה להפסד: {reasons.most_common(1)[0][0]}" if reasons else "")
        found.append(Lesson(
            f"⁦{rate:.0f}%⁩ מההצעות נסגרות",
            f"לפי ⁦{decided}⁩ הצעות שהוכרעו{why}."))
    else:
        found.append(Lesson(
            "אחוז הסגירה — לומד",
            f"צריך ⁦{ENOUGH_DECIDED - decided}⁩ הצעות מוכרעות נוספות (זכייה או הפסד) כדי לדעת.",
            learned=False))

    mark = benchmark(jobs)
    if mark is not None:
        found.append(Lesson(
            f"⁦{mark.median:,.0f} ₪⁩ למ״ר — המחיר שלכם",
            f"החציון של ⁦{mark.count}⁩ עבודות שזכיתם בהן. הצעה חדשה שרחוקה ממנו מסומנת "
            "לפני שהיא יוצאת."))
    else:
        found.append(Lesson(
            "המחיר למ״ר שלכם — לומד",
            f"מ־⁦{ENOUGH}⁩ עבודות שזכיתם בהן התוכנה יודעת מה המחיר הרגיל שלכם, ומסמנת "
            "הצעה שחורגת ממנו.", learned=False))

    sources = Counter(job.source for job in won if getattr(job, "source", ""))
    if sources and len(won) >= 3:
        source, count = sources.most_common(1)[0]
        found.append(Lesson(
            f"רוב העבודות מגיעות מ{source}",
            f"⁦{count}⁩ מתוך ⁦{len(won)}⁩ העבודות שנסגרו — שם כדאי להשקיע."))

    if timebook is not None:
        from .quoting.labour_learning import calibrate

        calibration = calibrate(jobs, timebook)
        found.append(Lesson(
            f"קצב העבודה במפעל: ⁦{calibration.factor:.2f}⁩ מהנורמה" if calibration.enough
            else "קצב העבודה במפעל — לומד",
            calibration.describe(), learned=calibration.enough))

    from . import __version__

    found.append(Lesson(
        f"התוכנה מתעדכנת לבד · גרסה ⁦{__version__}⁩",
        "כל שיפור נבדק בכל הבדיקות לפני שהוא מגיע, יורד ברקע ונכנס בפתיחה הבאה — "
        "ואם גרסה לא נפתחת, התוכנה חוזרת לקודמת לבד."))
    return found


__all__ = ["ENOUGH_DECIDED", "Lesson", "lessons"]
