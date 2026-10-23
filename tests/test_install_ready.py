"""Two working days before an installation, what the job's records say is missing."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.erp.collection import Cheque, ChequeBook
from profileos.projects import JobFile, JobStatus
from profileos.projects.agenda import Agenda, Appointment, VisitKind
from profileos.projects.install_check import not_ready
from profileos.projects.model import CustomerNeed, MaterialOrder, MaterialOrderState

TODAY = date(2026, 10, 25)            # a Sunday: the next working day is Monday


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _job() -> JobFile:
    from profileos.elements.model import ElementSchedule, Opening

    job = JobFile(job_id="J-1", name="וילה", customer_name="לוי",
                  status=JobStatus.IN_PRODUCTION, quote_total=20_000)
    job.schedule = ElementSchedule(name="x", openings=[Opening(name="W1", width=1200, height=1400)])
    return job


def test_everything_the_records_know_is_missing_is_named():
    job = _job()
    job.material_orders.append(MaterialOrder(supplier="קליל"))
    job.waiting_for.append(CustomerNeed(need="colour", detail="גוון לתריסים"))
    missing = not_ready(job)
    assert any("קליל" in m for m in missing)
    assert any("גוון לתריסים" in m for m in missing)
    assert any("מקדמה" in m for m in missing)

    job.material_orders[0].state = MaterialOrderState.RECEIVED
    job.waiting_for[0].received_on = TODAY.isoformat()
    book = ChequeBook([Cheque(customer="לוי", amount=5_000, due=TODAY, job_id="J-1")])
    assert not_ready(job, cheques=list(book)) == []


def test_a_shop_that_does_not_scan_is_not_told_its_frames_are_unfinished():
    from profileos.mes import ItemKind, ProductionItem, Stage, WorkOrder

    job = _job()
    job.material_orders.append(MaterialOrder(supplier="קליל", state=MaterialOrderState.RECEIVED))
    book = [Cheque(customer="לוי", amount=5_000, due=TODAY, job_id="J-1")]
    assert not_ready(job, cheques=book, work_order=None) == []
    order = WorkOrder(project_id="J-1")
    for n in range(3):
        order.add(ProductionItem(item_id=f"P{n}", kind=ItemKind.ELEMENT))
    order.find("P0").advance(Stage.CUT)
    missing = not_ready(job, cheques=book, work_order=order)
    assert missing == ["⁦3⁩ מתוך ⁦3⁩ פריטים ברצפת הייצור עוד לא עברו בדיקה"]


def test_the_home_page_says_it_for_monday_not_for_next_week(shop):
    from profileos.insights import scan

    job = _job()
    agenda = Agenda()
    soon = Appointment(on=TODAY + timedelta(days=1), kind=VisitKind.INSTALLATION, job_id="J-1",
                       customer_name="לוי")
    later = Appointment(on=TODAY + timedelta(days=8), kind=VisitKind.INSTALLATION, job_id="J-1")
    survey = Appointment(on=TODAY + timedelta(days=1), kind=VisitKind.SURVEY, job_id="J-1")
    for visit in (soon, later, survey):
        agenda._records()[visit.appointment_id] = visit
    found = [i for i in scan(TODAY, jobs=[job], customers=[], tasks=None, cheques=ChequeBook(),
                             service=None, store=None, agenda=agenda) if i.key.startswith("ready:")]
    assert len(found) == 1 and found[0].severity == "danger"
    assert "לוי" in found[0].title and "מחר" in found[0].title
    assert "לא נרשמה הזמנת חומר" in found[0].detail
