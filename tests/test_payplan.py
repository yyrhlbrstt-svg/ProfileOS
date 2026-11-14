"""Payment in stages: due as the job gets there, worked out from its records."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from profileos.erp.collection import Cheque, ChequeBook
from profileos.projects import JobFile, JobStatus
from profileos.projects.install_check import not_ready
from profileos.projects.model import Payment, PaymentMethod
from profileos.projects.payplan import shop_plan, view
from profileos.sharing import balance_message

PLAN = {"signing": 30.0, "before_install": 50.0, "handover": 20.0}


def _job(status=JobStatus.WON, net=10_000.0):
    return JobFile(job_id="J-7", name="וילה", customer_name="משפחת לוי",
                   status=status, quote_total=net)


def test_a_plan_must_add_up_to_a_hundred():
    assert shop_plan(SimpleNamespace(payment_plan=PLAN)) == PLAN
    assert shop_plan(SimpleNamespace(payment_plan={"signing": 30, "handover": 60})) == {}
    assert shop_plan(SimpleNamespace(payment_plan={})) == {}


def test_each_stage_falls_due_when_the_job_gets_there():
    job = _job()
    signed = view(job, plan=PLAN)
    gross = signed.stages[0].amount / 0.30
    assert signed.due_now == pytest.approx(gross * 0.30, abs=1)
    assert [s.reached for s in signed.stages] == [True, False, False]
    booked = view(job, plan=PLAN, install_booked=True)
    assert booked.due_now == pytest.approx(gross * 0.80, abs=1)
    job.status = JobStatus.INSTALLED
    done = view(job, plan=PLAN)
    assert done.due_now == pytest.approx(gross, abs=1)


def test_money_and_cheques_pay_the_stages_in_order():
    job = _job()
    gross = view(job, plan=PLAN).stages[0].amount / 0.30
    job.payments.append(Payment(amount=round(gross * 0.30), method=PaymentMethod.TRANSFER))
    book = ChequeBook()
    book.add(Cheque(customer="לוי", amount=round(gross * 0.25), job_id="J-7",
                    due=date.today() + timedelta(days=30), number="1"))
    plan = view(job, list(book), plan=PLAN, install_booked=True)
    assert plan.stages[0].open <= 0.5
    assert plan.current.key == "before_install"
    assert plan.due_now == pytest.approx(gross * 0.25, abs=2)
    assert "בחתימה ⁦30%⁩ ✓" in plan.describe() and "לגבות עכשיו" in plan.describe()


def test_the_crew_waits_for_the_payment_before_installation(monkeypatch):
    from profileos.core import config

    monkeypatch.setattr(config, "get_settings",
                        lambda: SimpleNamespace(payment_plan=PLAN))
    job = _job(JobStatus.IN_PRODUCTION)
    gross = view(job, plan=PLAN).stages[0].amount / 0.30
    job.payments.append(Payment(amount=round(gross * 0.30), method=PaymentMethod.TRANSFER))
    missing = not_ready(job)
    assert any(m.startswith("לגבות לפני ההתקנה") for m in missing)
    job.payments.append(Payment(amount=round(gross * 0.50), method=PaymentMethod.TRANSFER))
    assert not any("לגבות" in m for m in not_ready(job))


def test_without_a_plan_the_old_deposit_check_stands(monkeypatch):
    from profileos.core import config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(payment_plan={}))
    assert "לא התקבלה מקדמה ולא צ׳ק" in not_ready(_job(JobStatus.IN_PRODUCTION))


def test_the_message_asks_for_the_stage_that_is_due():
    job = _job()
    plan = view(job, plan=PLAN, install_booked=True)
    from profileos.projects.account import job_account

    account = job_account(job)
    text = balance_message(brand_name="אלומיניום לדוגמה", customer_name="משפחת לוי",
                           job_name="וילה", account=account,
                           stage=plan.current, due_now=plan.due_now)
    assert "לתשלום עכשיו — שלב ״בחתימה״" in text
    assert "יתרת העבודה בסך הכול" in text
