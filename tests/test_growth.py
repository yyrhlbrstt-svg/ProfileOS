"""What brings work in: where it came from, why it was lost, reviews, the
yearly check, the morning summary and the crew's day."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.insights import morning_brief, scan
from profileos.projects import JobStatus, Payment
from profileos.projects.agenda import Agenda, Appointment, VisitKind
from profileos.projects.model import JobFile, StatusEvent
from profileos.reports import by_source, lost_reasons

TODAY = date(2026, 10, 22)


def _job(job_id, status, *, since=TODAY, **fields):
    job = JobFile(job_id=job_id, name=f"עבודה {job_id}", status=status, **fields)
    job.history = [StatusEvent(status=status, at=since.isoformat())]
    return job


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core import backup, storage
    from profileos.core.config import reload_settings

    data = tmp_path / "data"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(data))
    reload_settings()
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backup, "_STATUS", {"error": "", "last": "", "mirror_error": ""})
    monkeypatch.setattr(storage, "RECOVERED", [])
    yield data
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _scan(**given):
    for key in ("customers", "tasks", "cheques", "service", "store", "agenda"):
        given.setdefault(key, [] if key == "customers" else None)
    return scan(TODAY, **given)


def test_which_source_brings_work_that_closes():
    jobs = [
        _job("1", JobStatus.WON, source="מדרג", quote_total=10_000),
        _job("2", JobStatus.LOST, source="מדרג", quote_total=8_000),
        _job("3", JobStatus.INSTALLED, source="המלצה של לקוח", quote_total=30_000),
        _job("4", JobStatus.ENQUIRY),
    ]
    rows = {row["source"]: row for row in by_source(jobs)}
    assert rows["מדרג"]["win_rate_pct"] == 50.0 and rows["מדרג"]["thin"]
    assert rows["המלצה של לקוח"]["won_value"] == 30_000
    assert rows["לא ידוע"]["enquiries"] == 1
    assert by_source(jobs)[0]["source"] == "המלצה של לקוח"  # most value first


def test_lost_jobs_say_why():
    jobs = [_job("1", JobStatus.LOST, lost_reason="מחיר", quote_total=5_000),
            _job("2", JobStatus.LOST, lost_reason="מחיר", quote_total=7_000),
            _job("3", JobStatus.LOST)]
    rows = lost_reasons(jobs)
    assert rows[0] == {"reason": "מחיר", "count": 2, "value": 12_000}
    assert rows[1]["reason"] == "לא נרשמה סיבה"


def test_an_enquiry_waiting_two_days_for_a_quote(shop):
    job = _job("9", JobStatus.ENQUIRY, created=(TODAY - timedelta(days=3)).isoformat(),
               customer_name="רחל")
    found = [i for i in _scan(jobs=[job]) if i.key.startswith("enquiry:")]
    assert found and "רחל" in found[0].detail


def test_a_review_is_asked_only_of_a_paid_happy_customer(shop):
    paid = _job("P", JobStatus.INSTALLED, since=TODAY - timedelta(days=3), quote_total=1_000,
                payments=[Payment(amount=1_180)])
    unpaid = _job("U", JobStatus.INSTALLED, since=TODAY - timedelta(days=3), quote_total=1_000)
    keys = [i.key for i in _scan(jobs=[paid, unpaid]) if i.key.startswith("review:")]
    assert keys == ["review:P"]


def test_the_yearly_check_comes_round(shop):
    old = _job("Y", JobStatus.INSTALLED, since=TODAY - timedelta(days=360), customer_name="דוד")
    assert any(i.key == "checkup:Y" for i in _scan(jobs=[old]))
    old.checkup_offered = TODAY.isoformat()
    assert not any(i.key.startswith("checkup:") for i in _scan(jobs=[old]))


def test_the_morning_brief_fits_on_a_phone(shop):
    diary = Agenda(shop / "agenda.json").load()
    diary.add(Appointment(on=TODAY, kind=VisitKind.SURVEY, time="09:00", customer_name="משה"))
    jobs = [_job("1", JobStatus.QUOTED, quote_total=12_000),
            _job("2", JobStatus.INSTALLED, quote_total=10_000)]
    context = {"jobs": jobs, "agenda": diary, "cheques": []}
    found = _scan(jobs=jobs, agenda=diary)
    text = morning_brief(found, context, today=TODAY, brand_name="דאדי")
    assert "דאדי" in text and "⁦11,800 ₪⁩" in text  # owed: 10,000 + VAT
    assert "הצעות פתוחות: ⁦1⁩" in text and "משה" in text
    assert len(text.splitlines()) < 30


def test_the_crew_gets_the_day_with_waze_links():
    from profileos.sharing import crew_plan_message, waze_link

    visits = [Appointment(on=TODAY, kind=VisitKind.INSTALLATION, time="07:30",
                          customer_name="משה", address="הזית 5 בית אל", phone="050-111-2233")]
    text = crew_plan_message(visits, day="מחר", brand_name="דאדי")
    assert "1. ⁦07:30⁩ התקנה — משה" in text
    assert waze_link("הזית 5 בית אל") in text and "navigate=yes" in text


def test_the_standing_payment_line_is_on_every_quotation(shop):
    from profileos.core.config import get_settings
    from profileos.elements import Opening
    from profileos.quoting.document import render_quotation
    from profileos.quoting.editor import QuoteDraft

    get_settings().payment_note = "אפשר לשלם עד 12 תשלומים באשראי"
    draft = QuoteDraft.start([Opening(name="W", width=1200, height=1200)], project_name="x",
                             fallback_rates={"profile": 5.5, "glass_m2": 60, "hardware": 12,
                                             "gasket_m": 1.1})
    assert "12 תשלומים" in render_quotation(draft)
