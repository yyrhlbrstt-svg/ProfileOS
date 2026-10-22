"""The home page finds what needs a look without being asked."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.erp.collection import Cheque, ChequeBook
from profileos.insights import dismiss, dismissed, scan
from profileos.projects import JobStatus
from profileos.projects.model import Customer, JobFile, StatusEvent

TODAY = date(2026, 10, 18)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    data = tmp_path / "data"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(data))
    reload_settings()
    data.mkdir(parents=True, exist_ok=True)
    # Process-wide notes left by other tests' backups and recoveries.
    from profileos.core import backup, storage

    monkeypatch.setattr(backup, "_STATUS", {"error": "", "last": "", "mirror_error": ""})
    monkeypatch.setattr(storage, "RECOVERED", [])
    yield data
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _job(job_id: str, status: JobStatus, *, since: date, **fields) -> JobFile:
    job = JobFile(job_id=job_id, name=f"עבודה {job_id}", status=status, **fields)
    job.history = [StatusEvent(status=status, at=since.isoformat())]
    return job


def _titles(found) -> str:
    return " | ".join(insight.title for insight in found)


def _scan(**given):
    given.setdefault("customers", [])
    given.setdefault("tasks", None)
    given.setdefault("cheques", None)
    given.setdefault("service", None)
    given.setdefault("store", None)
    given.setdefault("agenda", None)
    return scan(TODAY, **given)


def test_a_quotation_nobody_chased_is_found(shop):
    old = _job("J-1", JobStatus.QUOTED, since=TODAY - timedelta(days=10),
               quote_total=18000, quoted_on=(TODAY - timedelta(days=10)).isoformat(),
               customer_name="משה כהן")
    fresh = _job("J-2", JobStatus.QUOTED, since=TODAY - timedelta(days=2),
                 quoted_on=(TODAY - timedelta(days=2)).isoformat())
    found = _scan(jobs=[old, fresh])
    quotes = [i for i in found if i.key.startswith("quotes:")]
    assert len(quotes) == 1
    assert quotes[0].key == "quotes:J-1"
    assert "משה כהן" in quotes[0].detail
    assert "18,000" in quotes[0].detail


def test_a_won_job_that_never_reached_the_floor(shop):
    found = _scan(jobs=[_job("J-3", JobStatus.WON, since=TODAY - timedelta(days=20))])
    assert "לא נכנס לייצור" in _titles(found)


def test_a_late_job_is_urgent(shop):
    late = _job("J-4", JobStatus.IN_PRODUCTION, since=TODAY - timedelta(days=3),
                due_date=(TODAY - timedelta(days=1)).isoformat())
    found = _scan(jobs=[late])
    assert found[0].severity == "danger"
    assert "תאריך היעד" in found[0].title


def test_a_customer_with_work_and_no_phone(shop):
    job = _job("J-5", JobStatus.WON, since=TODAY, customer_id="C-0001")
    customer = Customer(customer_id="C-0001", name="דוד", phone="")
    assert "בלי טלפון" in _titles(_scan(jobs=[job], customers=[customer]))


def test_the_same_customer_twice(shop):
    customers = [
        Customer(customer_id="C-0001", name="משה כהן", phone="054-123-4567"),
        Customer(customer_id="C-0002", name="כהן משה"),
    ]
    assert "פעמיים" in _titles(_scan(jobs=[], customers=customers))


def test_cheques_to_bank_and_cheques_that_bounced(shop):
    book = ChequeBook()
    book.add(Cheque(customer="משה", amount=5000, due=TODAY - timedelta(days=1)))
    bounced = book.add(Cheque(customer="דוד", amount=3000, due=TODAY - timedelta(days=30)))
    bounced.bounce(TODAY - timedelta(days=2), "אין כיסוי")
    titles = _titles(_scan(jobs=[], cheques=book))
    assert "להפקיד" in titles
    assert "חזר ועדיין" in titles


def test_nothing_to_say_is_nothing_said(shop):
    found = _scan(jobs=[])
    assert not [i for i in found if i.severity in ("danger", "warning")]


def test_a_dismissed_line_stays_hidden_until_it_changes(shop):
    dismiss("quotes:J-1")
    assert "quotes:J-1" in dismissed()
    assert "quotes:J-1,J-9" not in dismissed()


def test_a_broken_rule_does_not_hide_the_others(shop, monkeypatch):
    import profileos.insights as insights

    def broken(_context, _today):
        raise RuntimeError("boom")

    monkeypatch.setattr(insights, "RULES", (broken,) + insights.RULES)
    found = _scan(jobs=[_job("J-3", JobStatus.WON, since=TODAY - timedelta(days=20))])
    assert "לא נכנס לייצור" in _titles(found)


def test_a_cheque_past_six_months_is_a_debt_not_a_deposit(shop):
    book = ChequeBook()
    book.add(Cheque(customer="משה", amount=5000, due=TODAY - timedelta(days=200)))
    titles = _titles(_scan(jobs=[], cheques=book))
    assert "חצי שנה" in titles
    assert "להפקיד" not in titles


def test_a_new_backup_failure_is_not_hidden_by_an_old_dismissal(shop, monkeypatch):
    from profileos.core import backup

    monkeypatch.setattr(backup, "_STATUS", {"error": "הדיסק מלא", "last": "", "mirror_error": ""})
    first = [i for i in _scan(jobs=[]) if i.title == "הגיבוי האוטומטי נכשל"][0]
    dismiss(first.key)
    monkeypatch.setattr(backup, "_STATUS", {"error": "אין הרשאה", "last": "", "mirror_error": ""})
    second = [i for i in _scan(jobs=[]) if i.title == "הגיבוי האוטומטי נכשל"][0]
    assert second.key not in dismissed()


def test_an_installed_job_not_fully_paid_is_found(shop):
    from profileos.projects import Payment

    job = _job("J-9", JobStatus.INSTALLED, since=TODAY - timedelta(days=5),
               quote_total=10_000, customer_name="דוד")
    job.payments = [Payment(amount=9_000)]
    owing = [i for i in _scan(jobs=[job]) if i.key.startswith("owing:")]
    assert owing and "2,800" in owing[0].detail


def test_an_extra_nobody_agreed_is_found(shop):
    from profileos.projects import JobLine, LineKind

    job = _job("J-10", JobStatus.IN_PRODUCTION, since=TODAY)
    job.lines = [JobLine(kind=LineKind.EXTRA, description="חלון נוסף", unit_price=900)]
    assert "לא אושרה" in _titles(_scan(jobs=[job]))


def test_tomorrows_customers_who_were_not_told(shop):
    from profileos.projects.agenda import Agenda, Appointment, next_working_day

    diary = Agenda(shop / "agenda.json").load()
    diary.add(Appointment(on=next_working_day(TODAY), customer_name="רחל"))
    found = [i for i in _scan(jobs=[], agenda=diary) if i.key.startswith("remind:")]
    assert found and "רחל" in found[0].detail


def test_a_certificate_about_to_lapse_is_flagged_a_month_ahead(shop):
    from types import SimpleNamespace

    soon = SimpleNamespace(
        withholding_certificate_until=(TODAY + timedelta(days=20)).isoformat(),
        books_certificate_until=(TODAY - timedelta(days=1)).isoformat(),
        backup_mirror=None,
    )
    found = _scan(jobs=[], settings=soon)
    titles = _titles(found)
    assert "אישור ניכוי מס במקור פג בעוד ⁦20⁩ ימים" in titles
    assert "אישור ניהול ספרים פג" in titles
    assert any(i.severity == "danger" and "ניהול ספרים" in i.title for i in found)


def _deposit(found):
    return [i for i in found if i.key.startswith("deposit:")]


def test_work_under_way_without_a_deposit_is_found(shop):
    from profileos.projects.model import MaterialOrder

    just_won = _job("J-20", JobStatus.WON, since=TODAY - timedelta(days=1),
                    quote_total=20_000, customer_name="לוי")
    assert not _deposit(_scan(jobs=[just_won]))             # a day's grace
    waited = _job("J-21", JobStatus.WON, since=TODAY - timedelta(days=4),
                  quote_total=20_000, customer_name="כהן")
    found = _deposit(_scan(jobs=[waited]))
    assert found and found[0].severity == "warning" and "⁦23,600 ₪⁩" in found[0].detail
    # Material ordered and nothing in hand: money has gone out.
    just_won.material_orders = [MaterialOrder(supplier="קליל")]
    found = _deposit(_scan(jobs=[just_won]))
    assert found[0].severity == "danger" and "חומר כבר הוזמן" in found[0].detail


def test_a_deposit_or_a_cheque_in_the_drawer_is_enough(shop):
    from profileos.projects import Payment

    paid = _job("J-22", JobStatus.IN_PRODUCTION, since=TODAY - timedelta(days=9),
                quote_total=20_000)
    paid.payments = [Payment(amount=5_000)]
    promised = _job("J-23", JobStatus.IN_PRODUCTION, since=TODAY - timedelta(days=9),
                    quote_total=20_000)
    book = ChequeBook()
    book.add(Cheque(number="1001", bank="12", amount=5_000, customer="כהן",
                    due=TODAY + timedelta(days=30), job_id="J-23"))
    assert not _deposit(_scan(jobs=[paid, promised], cheques=book))
    nothing = _job("J-24", JobStatus.IN_PRODUCTION, since=TODAY, quote_total=20_000)
    assert _deposit(_scan(jobs=[nothing], cheques=book))


def test_two_jobs_handed_to_one_family_are_one_review_request(shop):
    jobs = [
        _job(f"J-{n}", JobStatus.INSTALLED, since=TODAY - timedelta(days=3),
             customer_name="יוסי לוי", customer_id="C-1")
        for n in (1, 2)
    ]
    found = [i for i in _scan(jobs=jobs) if i.key.startswith("review:")]
    assert found and found[0].detail.count("יוסי לוי") == 1
    assert "לקוח אחד" in found[0].title


def test_an_ordered_job_is_worth_its_approved_extras_in_the_reports():
    from profileos import reports
    from profileos.projects import JobLine, LineKind

    job = JobFile(job_id="J-1", name="x", customer_name="לוי", status=JobStatus.WON,
                  quote_total=3_228.0, quoted_on=TODAY.isoformat())
    job.lines.append(JobLine(kind=LineKind.EXTRA, description="מעקה", unit_price=200,
                             approved="אושר בטלפון"))
    job.lines.append(JobLine(kind=LineKind.EXTRA, description="לא אושר", unit_price=999))
    assert reports.by_customer([job])[0].value == pytest.approx(3_428.0)
    assert reports.pipeline([job]).open_value == pytest.approx(3_428.0)
    period = reports.month(TODAY.month, TODAY.year)
    assert reports.sales([job], period).won_value == pytest.approx(3_428.0)
