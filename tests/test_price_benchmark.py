"""A quotation far from the shop's own won prices per m² is pointed out."""

from __future__ import annotations

from profileos.projects import JobFile, JobStatus
from profileos.quoting.benchmark import benchmark


def _won(n: int, price: float, area: float = 10.0) -> JobFile:
    from profileos.elements.model import ElementSchedule, Opening

    side = (area * 1e6) ** 0.5
    job = JobFile(job_id=f"J-{n}", name="x", status=JobStatus.INSTALLED, quote_total=price)
    job.schedule = ElementSchedule(name="x", openings=[Opening(name="W", width=side, height=side)])
    return job


def test_a_price_far_below_what_the_shop_wins_at_is_said():
    jobs = [_won(n, price) for n, price in enumerate((14_000, 15_000, 16_000, 15_500, 14_500), 1)]
    bench = benchmark(jobs)
    assert bench.count == 5 and round(bench.median) == 1_500
    low = bench.judge(9_000, 10.0)
    assert "נמוך ב־⁦40%⁩" in low and "⁦900 ₪⁩" in low
    assert bench.judge(15_000, 10.0) == ""
    assert "גבוה" in bench.judge(30_000, 10.0)


def test_few_jobs_or_another_trade_compare_nothing():
    assert benchmark([_won(n, 15_000) for n in range(1, 5)]) is None
    plumbing = [_won(n, 15_000) for n in range(1, 7)]
    for job in plumbing:
        job.trade = "plumbing"
    assert benchmark(plumbing) is None
