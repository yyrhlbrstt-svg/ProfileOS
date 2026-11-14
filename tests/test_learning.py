"""What the program learned from the shop's own records — and says how much it read."""

from __future__ import annotations

from profileos.learning import ENOUGH_DECIDED, lessons
from profileos.projects import JobFile, JobStatus


def _job(n, status, total=20_000.0, source="גוגל", reason=""):
    from profileos.elements.model import ElementSchedule, Opening

    job = JobFile(job_id=f"J-{n}", name=f"j{n}", status=status, quote_total=total,
                  source=source, lost_reason=reason)
    job.schedule = ElementSchedule(name="x", openings=[Opening(name="W1", width=2000, height=1500)])
    return job


def test_with_too_little_it_says_it_is_still_learning():
    found = lessons([_job(1, JobStatus.WON)])
    rate = found[0]
    assert not rate.learned and f"⁦{ENOUGH_DECIDED - 1}⁩" in rate.detail
    assert found[-1].title.startswith("התוכנה מתעדכנת לבד")


def test_the_closing_rate_the_price_and_the_source_are_read_from_the_jobs():
    jobs = [_job(n, JobStatus.WON) for n in range(5)]
    jobs += [_job(9, JobStatus.LOST, reason="מחיר"), _job(10, JobStatus.LOST, reason="מחיר")]
    titles = [lesson.title for lesson in lessons(jobs)]
    assert titles[0] == "⁦71%⁩ מההצעות נסגרות"
    assert "⁦6,667 ₪⁩ למ״ר" in titles[1]                      # 20,000 / 3 m²
    assert any("מגוגל" in title for title in titles)
