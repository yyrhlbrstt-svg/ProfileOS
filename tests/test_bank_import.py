"""The bank's export, matched to the drawer and the jobs — applied only where ticked, once."""

from __future__ import annotations

import os
from datetime import date

import pytest

from profileos.erp.bankimport import guess_columns, match, read_statement
from profileos.erp.collection import Cheque, ChequeBook, ChequeState


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


STATEMENT = """תנועות בחשבון 12-345-678901
תאריך,תאריך ערך,תיאור,אסמכתא,חובה,זכות,יתרה
20/10/2026,20/10/2026,הפקדת שיק,0004501,,"10,000.00","55,000.00"
21/10/2026,21/10/2026,העברה משה כהן,77123,,"5,900.00","60,900.00"
22/10/2026,22/10/2026,שיק 100231,100231,"18,500.00",,"42,400.00"
22/10/2026,22/10/2026,זיכוי לא ידוע,88,,"1,234.00","43,634.00"
,,יתרה לסוף תקופה,,,,"43,634.00"
"""


def _drawer() -> ChequeBook:
    book = ChequeBook()
    book.add(Cheque(customer="לוי", amount=10_000, due=date(2026, 10, 15), number="4501",
                    job_id="J-1")).deposit(date(2026, 10, 16))
    book.add(Cheque(customer="קליל", amount=18_500, due=date(2026, 10, 22), number="100231",
                    outgoing=True))
    return book


def _job():
    from profileos.projects import JobFile, JobStatus

    return JobFile(job_id="J-7", name="וילה", customer_name="משה כהן",
                   status=JobStatus.INSTALLED, quote_total=5_000)       # 5,900 with VAT


def test_a_statement_is_read_by_the_columns_named(tmp_path):
    path = tmp_path / "bank.csv"
    path.write_text(STATEMENT, encoding="utf-8")
    from profileos.migration.reader import read_table

    headers = read_table(path, expected=["תאריך", "זכות", "חובה"]).headers
    columns = guess_columns(headers)
    assert columns["credit"] == "זכות" and columns["debit"] == "חובה"
    lines, skipped = read_statement(path, columns)
    assert [l.amount for l in lines] == [10_000, 5_900, -18_500, 1_234] and skipped == 1


def test_each_line_is_set_against_what_the_shop_expects(tmp_path):
    path = tmp_path / "bank.csv"
    path.write_text(STATEMENT, encoding="utf-8")
    lines, _ = read_statement(path, {"date": "תאריך", "description": "תיאור",
                                     "reference": "אסמכתא", "credit": "זכות", "debit": "חובה"})
    reading = match(lines, cheques=list(_drawer()), jobs=[_job()])
    kinds = {m.kind: m for m in reading.matches}
    assert kinds["clear"].sure and kinds["clear"].target.number == "4501"
    assert kinds["paid_out"].sure and kinds["paid_out"].target.customer == "קליל"
    assert kinds["payment"].target.job_id == "J-7" and kinds["payment"].sure
    assert [l.amount for l in reading.unexplained] == [1_234]
    again = match(lines, cheques=list(_drawer()), jobs=[_job()],
                  seen={m.line.key for m in reading.matches})
    assert again.already == 3 and not again.matches


def test_applied_from_the_collection_page_once(shop, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.erp.collection import default_cheques
    from profileos.projects import default_store
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    book = default_cheques()
    for cheque in _drawer():
        book.add(cheque)
    book.save()
    default_store().save(_job())
    path = tmp_path / "bank.csv"
    path.write_text(STATEMENT, encoding="utf-8")

    def accept_sure(dialog):
        dialog.match()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.BankImportDialog, "exec", accept_sure)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Collection")
    assert page.import_bank(path) == 3
    states = {c.number: c.state for c in default_cheques()}
    assert states["4501"] is ChequeState.CLEARED and states["100231"] is ChequeState.CLEARED
    [payment] = default_store().load("J-7").payments
    assert payment.amount == 5_900 and payment.on == "2026-10-21"
    assert page.import_bank(path) == 0                         # the same statement again
    assert len(default_store().load("J-7").payments) == 1
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_two_equal_transfers_are_two_lines_and_never_pay_more_than_owed(tmp_path):
    path = tmp_path / "bank.csv"
    path.write_text("""תאריך,תיאור,אסמכתא,חובה,זכות
21/10/2026,העברה משה כהן,77123,,"5,900.00"
21/10/2026,העברה משה כהן,77123,,"5,900.00"
""", encoding="utf-8")
    lines, _ = read_statement(path, {"date": "תאריך", "description": "תיאור",
                                     "reference": "אסמכתא", "credit": "זכות", "debit": "חובה"})
    assert len({line.key for line in lines}) == 2
    reading = match(lines, jobs=[_job()])
    # The job owes 5,900: the first transfer pays it, the second is unexplained.
    assert [m.kind for m in reading.matches] == ["payment"]
    assert [l.amount for l in reading.unexplained] == [5_900]


def test_a_line_applied_once_is_remembered_even_if_the_records_were_put_back(shop, tmp_path):
    from profileos.erp.bankimport import applied_keys, remember

    path = tmp_path / "bank.csv"
    path.write_text(STATEMENT, encoding="utf-8")
    columns = {"date": "תאריך", "description": "תיאור", "reference": "אסמכתא",
               "credit": "זכות", "debit": "חובה"}
    lines, _ = read_statement(path, columns)
    first = match(lines, cheques=list(_drawer()), jobs=[_job()], seen=applied_keys())
    remember(m.line.key for m in first.matches)
    # The same statement against a drawer that still expects everything:
    # only the memory stops it being applied again.
    again = match(lines, cheques=list(_drawer()), jobs=[_job()], seen=applied_keys())
    assert again.already == 3 and not again.matches


def test_an_unticked_line_is_left_alone(shop, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.erp.bankimport import applied_keys
    from profileos.erp.collection import default_cheques
    from profileos.projects import default_store
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    book = default_cheques()
    for cheque in _drawer():
        book.add(cheque)
    book.save()
    default_store().save(_job())
    path = tmp_path / "bank.csv"
    path.write_text(STATEMENT, encoding="utf-8")

    def untick_payment(dialog):
        reading = dialog.match()
        for row, found in enumerate(reading.matches):
            if found.kind == "payment":
                dialog.table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.BankImportDialog, "exec", untick_payment)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Collection")
    assert page.import_bank(path) == 2
    assert not default_store().load("J-7").payments
    assert len(applied_keys()) == 2
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
