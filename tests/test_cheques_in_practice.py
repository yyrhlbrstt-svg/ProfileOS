"""Cheques as a shop takes them: five at once from the job, and told apart
once they are money."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.erp.collection import (
    Cheque, ChequeBook, ChequeState, add_months, cheque_series, next_number,
)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


class TestASeries:
    def test_one_a_month_with_consecutive_numbers(self):
        cheques = cheque_series(count=5, amount=2_000, first_due=date(2026, 1, 31),
                                first_number="0012345", customer="כהן", job_id="J-1")
        assert [c.due for c in cheques] == [
            date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
            date(2026, 4, 30), date(2026, 5, 31)]
        assert [c.number for c in cheques] == ["0012345", "0012346", "0012347", "0012348", "0012349"]
        assert {c.job_id for c in cheques} == {"J-1"} and sum(c.amount for c in cheques) == 10_000

    def test_a_number_that_is_not_digits_is_not_guessed(self):
        assert next_number("12-345", 1) == ""
        assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


class TestClearing:
    def test_only_a_deposited_cheque_becomes_money(self):
        cheque = Cheque(customer="כהן", amount=1_000, due=date(2026, 1, 1))
        with pytest.raises(ProfileOSError, match="רק צ׳ק שהופקד"):
            cheque.clear()
        cheque.deposit(date(2026, 1, 2)).clear(date(2026, 1, 4))
        assert cheque.state is ChequeState.CLEARED and cheque.state.is_money

    def test_a_batch_deposited_up_to_a_day_clears_together(self):
        book = ChequeBook()
        old = book.add(Cheque(customer="א", amount=1_000, due=date(2026, 1, 1)))
        new = book.add(Cheque(customer="ב", amount=500, due=date(2026, 1, 1)))
        held = book.add(Cheque(customer="ג", amount=700, due=date(2026, 1, 1)))
        old.deposit(date(2026, 1, 5))
        new.deposit(date(2026, 1, 9))
        cleared = book.clear_deposited(up_to=date(2026, 1, 8))
        assert cleared == [old] and new.state is ChequeState.DEPOSITED
        assert held.state is ChequeState.HELD


def test_a_cheque_deposited_a_week_ago_and_never_cleared_is_a_question(shop):
    from profileos.insights import scan

    book = ChequeBook()
    cheque = book.add(Cheque(customer="כהן", amount=3_000, due=date(2026, 10, 1)))
    cheque.deposit(date(2026, 10, 10))
    found = scan(date(2026, 10, 22), jobs=[], customers=[], tasks=None, cheques=book,
                 service=None, store=None, agenda=None)
    lines = [i for i in found if i.key.startswith("unconfirmed:")]
    assert lines and "⁦3,000 ₪⁩" in lines[0].detail
    cheque.clear(date(2026, 10, 12))
    found = scan(date(2026, 10, 22), jobs=[], customers=[], tasks=None, cheques=book,
                 service=None, store=None, agenda=None)
    assert not [i for i in found if i.key.startswith("unconfirmed:")]


def test_five_cheques_from_the_jobs_account(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from profileos.erp.collection import default_cheques
    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.projects.account import job_account
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    job = JobFile(job_id="J-2026-0001", name="וילה", customer_name="משפחת לוי",
                  status=JobStatus.WON, quote_total=50_000)
    default_store().save(job)
    seen = {}

    def fill(dialog):
        seen["customer"] = dialog.customer.text()          # filled from the job
        dialog.count.setValue(5)
        dialog.amount.setValue(10_000)
        dialog.due.setDate(date(2026, 11, 1))
        dialog.number.setText("4501")
        seen["preview"] = dialog.preview.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.NewChequeDialog, "exec", fill)
    window = MainWindow(LIGHT)
    projects = window.go_to_page("Projects")
    assert projects._take_cheques(default_store().load("J-2026-0001")) == 5
    assert seen["customer"] == "משפחת לוי"
    assert "⁦5⁩ צ׳קים" in seen["preview"] and "⁦50,000 ₪⁩" in seen["preview"]
    book = default_cheques()
    assert sorted(c.number for c in book) == ["4501", "4502", "4503", "4504", "4505"]
    assert {c.job_id for c in book} == {"J-2026-0001"}
    account = job_account(default_store().load("J-2026-0001"), list(book))
    assert account.in_cheques == 50_000

    # The first one goes to the bank, comes back as money: marked from the page.
    collection = window.go_to_page("Collection")
    first = min(collection.book(), key=lambda c: c.due)
    first.due = date.today() - timedelta(days=2)
    first.deposit(date.today() - timedelta(days=1))
    collection._save()
    collection.refresh()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    collection.cheques_table.clearSelection()
    collection.mark_cleared()
    reread = {c.number: c.state for c in default_cheques()}
    assert reread["4501"] is ChequeState.CLEARED
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def test_a_date_reads_day_month_year_in_a_right_to_left_program():
    """Qt lays a date's sections out in the program's direction: in Hebrew
    "dd/MM/yyyy" showed as 2026/11/01 on every date field."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication, QDateEdit

    from profileos.ui.widgets import install_number_field_polish

    app = QApplication.instance() or QApplication([])
    before = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        install_number_field_polish(app)
        field = QDateEdit()
        field.setDisplayFormat("dd/MM/yyyy")
        field.setDate(date(2026, 11, 1))
        QApplication.sendEvent(field, QEvent(QEvent.Type.Polish))
        assert field.text() == "01/11/2026"
        field.lineEdit().setText("15/12/2026")
        field.interpretText()
        assert field.date().toPython() == date(2026, 12, 15)
    finally:
        app.setLayoutDirection(before)


def test_a_won_job_with_no_price_is_said(shop):
    """Deposit and balance checks are silent on a job worth ₪0 — say why."""
    from profileos.insights import scan
    from profileos.projects import JobFile, JobStatus

    job = JobFile(job_id="J-1", name="מטבח", customer_name="לוי")
    job.status = JobStatus.WON
    found = scan(date(2026, 10, 22), jobs=[job], customers=[], tasks=None,
                 cheques=ChequeBook(), service=None, store=None, agenda=None)
    lines = [i for i in found if i.key.startswith("unpriced:")]
    assert lines and "לוי" in lines[0].detail
    job.record_quote(10_000)
    found = scan(date(2026, 10, 22), jobs=[job], customers=[], tasks=None,
                 cheques=ChequeBook(), service=None, store=None, agenda=None)
    assert not [i for i in found if i.key.startswith("unpriced:")]


class TestChequesTheShopWrites:
    def test_a_cheque_to_a_supplier_is_never_money_in(self):
        from profileos.projects import JobFile, JobStatus
        from profileos.projects.account import job_account

        book = ChequeBook()
        book.add(Cheque(customer="כהן", amount=10_000, due=date(2026, 11, 1), job_id="J-1"))
        book.add(Cheque(customer="קליל", amount=7_000, due=date(2026, 11, 3), job_id="J-1",
                        outgoing=True))
        job = JobFile(job_id="J-1", name="x", status=JobStatus.WON, quote_total=20_000)
        assert job_account(job, list(book)).in_cheques == 10_000
        assert book.in_hand == 10_000 and [c.customer for c in book.to_pay()] == ["קליל"]
        assert book.summary()["to_pay"] == 7_000 and book.summary()["count"] == 1

    def test_the_supplier_banks_it_and_the_shop_sees_it_debited(self):
        cheque = Cheque(customer="קליל", amount=7_000, due=date(2026, 11, 3), outgoing=True)
        with pytest.raises(ProfileOSError, match="מפקיד הספק"):
            cheque.deposit(date(2026, 11, 4))
        cheque.clear(date(2026, 11, 5))
        assert cheque.state is ChequeState.CLEARED

    def test_the_week_the_account_goes_below_zero_is_said(self, shop):
        from profileos.core.config import get_settings
        from profileos.insights import scan

        book = ChequeBook()
        book.add(Cheque(customer="כהן", amount=5_000, due=date(2026, 10, 27)))
        book.add(Cheque(customer="קליל", amount=12_000, due=date(2026, 11, 2), outgoing=True))
        flow = book.two_sided(date(2026, 10, 22), opening=4_000)
        assert [row["balance"] for row in flow] == [9_000, -3_000]
        settings = get_settings()
        try:
            settings.bank_balance, settings.bank_balance_on = 4_000, "2026-10-22"
            found = {i.key.split(":")[0]: i for i in scan(
                date(2026, 10, 22), jobs=[], customers=[], tasks=None, cheques=book,
                service=None, store=None, agenda=None)}
            assert found["short"].severity == "danger" and "⁦-3,000 ₪⁩" in found["short"].detail
            assert "⁦01/11⁩" in found["short"].title

            # A supplier cheque dated before the balance was read is already
            # inside it: not taken off a second time.
            book.add(Cheque(customer="זכוכית", amount=50_000, due=date(2026, 10, 20),
                            outgoing=True))
            assert [r["balance"] for r in book.two_sided(
                date(2026, 10, 22), opening=4_000, balance_on=date(2026, 10, 22))] == [9_000, -3_000]
        finally:
            settings.bank_balance, settings.bank_balance_on = None, ""


def test_cheques_to_a_supplier_from_the_collection_page(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.erp.collection import default_cheques
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    def fill(dialog):
        assert dialog.outgoing and dialog.windowTitle() == "צ׳קים לספק"
        dialog.customer.setText("קליל")
        dialog.count.setValue(3)
        dialog.amount.setValue(4_000)
        dialog.due.setDate(date.today() + timedelta(days=3))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.NewChequeDialog, "exec", fill)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Collection")
    assert page.new_cheque(outgoing=True) == 3
    book = default_cheques()
    assert len(book.to_pay()) == 3 and book.in_hand == 0
    page.bank_balance.setValue(5_000)
    page.save_bank_balance()
    texts = [page.flow_table.item(r, 3).text() for r in range(page.flow_table.rowCount())]
    assert texts[0] == "⁦1,000⁩"
    first = min(book.to_pay(), key=lambda c: c.due)
    row = next(i for i, c in enumerate(page._cheques) if c.cheque_id == first.cheque_id)
    page.cheques_table.selectRow(row)
    page.mark_cleared()
    assert default_cheques().get(first.cheque_id).state is ChequeState.CLEARED
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
