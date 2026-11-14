"""The shop's own pace, learned from the hours booked on finished jobs."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.erp.timesheets import TimeBook
from profileos.projects import JobFile, JobStatus
from profileos.quoting.labour_learning import ENOUGH_JOBS, calibrate


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _finished(n: int, norm: float, booked: float, book: TimeBook) -> JobFile:
    job = JobFile(job_id=f"J-{n}", name=f"עבודה {n}", status=JobStatus.INSTALLED,
                  norm_hours={"assembly": norm * 0.6, "cutting_machining": norm * 0.4})
    left = int(booked * 60)
    while left > 0:                                  # a day at a time, as it is booked
        book.book(person="יוסי", job_id=job.job_id, minutes=min(left, 480), operation="הרכבה")
        left -= 480
    book.book(person="יוסי", job_id=job.job_id, minutes=180, operation="התקנה באתר")  # site: not counted
    book.book(person="משה", job_id=job.job_id, minutes=60, operation="הרכבה", rework=True)  # not counted
    return job


def test_the_factor_is_booked_over_norm_with_the_spread(shop):
    book = TimeBook()
    jobs = [_finished(n, 10.0, booked, book)
            for n, booked in enumerate((12, 13, 14, 12.5, 13.5, 30), start=1)]
    jobs.append(JobFile(job_id="J-open", name="פתוח", status=JobStatus.WON,
                        norm_hours={"assembly": 5}))                       # not finished
    calibration = calibrate(jobs, book)
    assert len(calibration.jobs) == 6 and calibration.enough
    assert calibration.factor == pytest.approx(95 / 60, abs=0.01)
    low, high = calibration.spread
    assert low <= 1.3 <= high and high < 3.0                                # one outlier
    assert "⁦6⁩ עבודות" in calibration.describe()


def test_too_few_jobs_say_so_and_offer_nothing(shop):
    book = TimeBook()
    jobs = [_finished(n, 10.0, 15.0, book) for n in range(1, ENOUGH_JOBS)]
    calibration = calibrate(jobs, book)
    assert not calibration.enough and "צריך לפחות" in calibration.describe()


def test_an_applied_factor_prices_the_hours_and_the_home_page_stops_asking(shop):
    from profileos.core.config import get_settings
    from profileos.insights import scan
    from profileos.quoting.pricing import LabourRates
    from profileos.quoting.rates import shop_labour

    book = TimeBook()
    jobs = [_finished(n, 10.0, 15.0, book) for n in range(1, 7)]
    found = [i for i in scan(date.today(), jobs=jobs, customers=[], tasks=None, cheques=None,
                             service=None, store=None, agenda=None, timebook=book)
             if i.key.startswith("pace:")]
    assert found and "ארוכה" in found[0].title

    settings = get_settings()
    try:
        settings.labour_factor = 1.5
        assert shop_labour().hours_per_element == pytest.approx(LabourRates().hours_per_element * 1.5)
        found = [i for i in scan(date.today(), jobs=jobs, customers=[], tasks=None, cheques=None,
                                 service=None, store=None, agenda=None, timebook=book)
                 if i.key.startswith("pace:")]
        assert not found
    finally:
        settings.labour_factor = 1.0
