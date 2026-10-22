"""Small things that cost the shop calls, money or face every week."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from profileos.projects import JobFile, JobStatus


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    data = tmp_path / "data"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(data))
    reload_settings()
    data.mkdir(parents=True, exist_ok=True)
    yield data
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


class TestQuoteChasesEndWithTheDecision:
    def test_winning_closes_the_chase_calls(self, shop):
        from profileos.projects.followups import Outcome, default_tasks

        job = JobFile(job_id="J-1", name="וילה", status=JobStatus.QUOTED)
        book = default_tasks()
        made = book.chase_quote(job)
        assert made and len(book.about("job:J-1")) == len(made)
        closed = book.settle_quote("J-1", Outcome.DONE, result="נסגר")
        assert len(closed) == len(made)
        assert not [t for t in default_tasks().open_tasks if t.about == "job:J-1"]

    def test_from_the_projects_page(self, shop, monkeypatch):
        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication

        from profileos.projects import default_store
        from profileos.projects.followups import default_tasks
        from profileos.ui.main_window import MainWindow
        from profileos.ui.theme import LIGHT

        QApplication.instance() or QApplication([])
        job = JobFile(job_id="J-2026-0001", name="וילה", status=JobStatus.QUOTED)
        default_store().save(job)
        default_tasks().chase_quote(job)
        window = MainWindow(LIGHT)
        page = window.go_to_page("Projects")
        page.refresh()
        page.jobs_table.selectRow(0)
        page.status_combo.setCurrentIndex(page.status_combo.findData("won"))
        page.advance_status()
        assert default_store().load("J-2026-0001").status is JobStatus.WON
        assert not [t for t in default_tasks().open_tasks if t.about == "job:J-2026-0001"]
        window.close()
        window.deleteLater()
        QCoreApplication.processEvents()


class TestMessages:
    def test_cheque_figures_are_isolated(self):
        from profileos.sharing import cheque_message

        text = cheque_message(customer_name="משה", amount=12500, due=date(2026, 11, 1),
                              brand_name="דאדי", bounced=True)
        assert "⁦12,500 ₪⁩" in text and "⁦01/11/2026⁩" in text

    def test_the_balance_from_the_records(self):
        from types import SimpleNamespace

        from profileos.sharing import balance_message

        account = SimpleNamespace(total_gross=59000.0, extras_net=2000.0, paid=30000.0,
                                  withheld=0.0, cleared=10000.0, received=40000.0,
                                  in_cheques=5000.0, uncovered=14000.0)
        first = balance_message(brand_name="דאדי", customer_name="משה", job_name="וילה",
                                account=account, payment_note="העברה לחשבון 12-345-678")
        assert "⁦59,000 ₪⁩" in first and "⁦40,000 ₪⁩" in first
        assert "⁦5,000 ₪⁩" in first and "יתרה לתשלום: ⁦14,000 ₪⁩" in first
        assert "תזכורת נוספת" not in first and "12-345-678" in first
        second = balance_message(brand_name="דאדי", account=account, reminders=1)
        assert "תזכורת נוספת" in second


def test_sending_the_balance_is_recorded_and_shown(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    from profileos.insights import scan
    from profileos.projects import Payment, default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    job = JobFile(job_id="J-2026-0001", name="וילה", status=JobStatus.INSTALLED,
                  customer_name="משה")
    job.quote_total = 10000.0
    job.payments.append(Payment(amount=5000.0))
    default_store().save(job)
    owing = next(i for i in scan() if "לא שולם" in i.title)
    assert "עוד לא נשלחה תזכורת" in owing.detail

    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    page.refresh()
    page.jobs_table.selectRow(0)
    page.send_balance()
    assert opened and "wa.me" in opened[0]
    assert default_store().load("J-2026-0001").balance_reminders == [date.today().isoformat()]
    owing = next(i for i in scan() if "לא שולם" in i.title)
    assert "תזכורת נשלחה היום" in owing.detail
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def test_a_job_is_found_by_the_customers_phone(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_customers, default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    customers = default_customers()
    customer = customers.add("משה כהן", phone="052-123-4567")
    store = default_store()
    store.save(JobFile(job_id="J-2026-0001", name="וילה", customer_id=customer.customer_id,
                       customer_name="משה כהן"))
    store.save(JobFile(job_id="J-2026-0002", name="משרד", customer_name="אחר"))
    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    for typed in ("1234567", "052-1234", "+972521234567"):
        page.search.setText(typed)
        page.refresh()
        assert [job.job_id for job in page._jobs] == ["J-2026-0001"], typed
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def test_reprinting_a_won_jobs_quote_keeps_the_agreed_price(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import Trade, default_store
    from profileos.projects.followups import default_tasks
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    store = default_store()
    job = store.create("דירה", trade=Trade.PLUMBING)
    job = store.load(job.job_id)
    job.record_quote(10_000.0)
    job.advance(JobStatus.QUOTED)
    job.advance(JobStatus.WON)
    store.save(job)
    window = MainWindow(LIGHT)
    window.session.set_job(store.load(job.job_id))
    quote = window.go_to_page("Quotation")
    quote.start_draft()
    quote.draft.add_extra_line("עבודה", unit_price=12_500)
    quote._record_in_job()
    saved = store.load(job.job_id)
    assert saved.quote_total == 10_000.0 and saved.status is JobStatus.WON
    assert not [t for t in default_tasks().open_tasks if t.about == f"job:{job.job_id}"]
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()



def test_the_customer_list_shows_what_each_one_still_owes(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWidgets import QApplication

    from profileos.projects import Payment, default_customers, default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    customer = default_customers().add("משפחת לוי", phone="052-8841200")
    job = JobFile(job_id="J-2026-0001", name="חלונות", customer_id=customer.customer_id,
                  customer_name=customer.name, status=JobStatus.INSTALLED, quote_total=10_000)
    job.payments = [Payment(amount=9_000)]
    default_store().save(job)
    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    page.refresh()
    table = page.customers_table
    headers = [table.model().headerData(i, Qt.Orientation.Horizontal) for i in range(table.model().columnCount())]
    column = headers.index("יתרה")
    assert table.model().index(0, column).data() == "⁦2,800 ₪⁩"
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


class TestThePunchList:
    def test_it_is_kept_on_the_job(self, shop):
        from profileos.projects import Snag, default_store

        job = JobFile(job_id="J-2026-0001", name="וילה", status=JobStatus.INSTALLED)
        job.snags = [Snag(description="ידית תקועה", where="W3"),
                     Snag(description="שריטה", fixed_on="2026-10-20")]
        default_store().save(job)
        again = default_store().load("J-2026-0001")
        assert [s.description for s in again.open_snags] == ["ידית תקועה"]

    def test_the_message_lists_what_is_left_and_what_was_done(self):
        from profileos.projects import Snag
        from profileos.sharing import snag_message

        left = [Snag(description="ידית תקועה", where="W3")]
        done = [Snag(description="שריטה", fixed_on="2026-10-20")]
        text = snag_message(brand_name="דאדי", customer_name="לוי", job_name="וילה",
                            open_snags=left, fixed_snags=done, visit="27/10")
        assert "• ידית תקועה (⁦W3⁩)" in text and "✓ שריטה" in text and "⁦27/10⁩" in text
        finished = snag_message(brand_name="דאדי", open_snags=[], fixed_snags=left + done)
        assert "כל התיקונים" in finished and "בוצעו" in finished

    def test_a_repair_waiting_a_week_is_on_the_home_page(self, shop):
        from profileos.insights import scan
        from profileos.projects import Payment, Snag

        today = date(2026, 10, 22)
        job = JobFile(job_id="J-1", name="וילה", customer_name="לוי",
                      status=JobStatus.INSTALLED, quote_total=10_000)
        job.payments = [Payment(amount=9_000)]
        job.snags = [Snag(description="ידית תקועה", raised_on="2026-10-12")]
        found = scan(today, jobs=[job], customers=[], tasks=None, cheques=None,
                     service=None, store=None, agenda=None)
        snags = [i for i in found if i.key.startswith("snags:")]
        assert snags and "ידית תקועה" in snags[0].detail
        owing = [i for i in found if i.key.startswith("owing:")][0]
        assert "תיקון אחד פתוח" in owing.detail
        job.snags[0].fixed_on = "2026-10-21"
        found = scan(today, jobs=[job], customers=[], tasks=None, cheques=None,
                     service=None, store=None, agenda=None)
        assert not [i for i in found if i.key.startswith("snags:")]

    def test_from_the_projects_page(self, shop, monkeypatch):
        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication, QDialog

        from profileos.projects import default_store
        from profileos.ui import money_dialogs
        from profileos.ui.main_window import MainWindow
        from profileos.ui.theme import LIGHT

        QApplication.instance() or QApplication([])
        default_store().save(JobFile(job_id="J-2026-0001", name="וילה",
                                     status=JobStatus.INSTALLED))

        def work(dialog):
            dialog.description.setText("ידית תקועה")
            dialog.where.setText("W3")
            dialog.add()
            dialog.description.setText("שריטה")
            dialog.add()
            dialog.table.selectRow(1)
            dialog.mark_fixed()
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(money_dialogs.SnagListDialog, "exec", work)
        window = MainWindow(LIGHT)
        page = window.go_to_page("Projects")
        page.refresh()
        page.jobs_table.selectRow(0)
        page.manage_snags()
        saved = default_store().load("J-2026-0001")
        assert len(saved.snags) == 2
        assert [s.description for s in saved.open_snags] == ["ידית תקועה"]
        window.close()
        window.deleteLater()
        QCoreApplication.processEvents()


class TestEveryQuotationKeepsItsVersion:
    def test_the_engine(self):
        from profileos.quoting.revisions import Line, QuoteHistory

        history = QuoteHistory(job_id="J-1")
        door = Line("D1", "דלת כניסה", 1, "יח׳", 8_000)
        window = Line("", "חלון אמבטיה", 2, "יח׳", 1_500)   # no code: matched only if identical
        first, new = history.record_issued([door, window], 11_000)
        assert new and first.number == 1 and first.is_sent
        same, new = history.record_issued([window, door], 11_000)
        assert not new and same is first                  # nothing changed: no new version
        bigger = Line("D1", "דלת כניסה", 1, "יח׳", 9_500)
        second, new = history.record_issued([bigger, window], 12_500)
        assert new and second.number == 2
        assert "⁦+1,500 ₪⁩" in second.reason and "עודכנו במחיר ⁦1⁩" in second.reason
        assert "הוסרו" not in second.reason                 # the identical uncoded line is the same line

    def test_from_the_quotation_page_and_the_decision(self, shop):
        pytest.importorskip("PySide6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication

        from profileos.projects import Trade, default_store
        from profileos.quoting.revisions import default_revisions
        from profileos.ui import money_dialogs
        from profileos.ui.main_window import MainWindow
        from profileos.ui.theme import LIGHT

        QApplication.instance() or QApplication([])
        store = default_store()
        job = store.create("דירה", trade=Trade.PLUMBING)
        window = MainWindow(LIGHT)
        window.session.set_job(store.load(job.job_id))
        quote = window.go_to_page("Quotation")
        quote.start_draft()
        quote.draft.add_extra_line("החלפת צנרת", unit_price=10_000)
        quote._record_in_job()
        quote._record_in_job()                               # printed twice, same price
        quote.draft.add_extra_line("דוד שמש", unit_price=4_000)
        quote._record_in_job()
        history = next(h for h in default_revisions() if h.job_id == job.job_id)
        assert [r.number for r in history.revisions] == [1, 2]
        assert "נוספו ⁦1⁩" in history.revisions[1].reason

        page = window.go_to_page("Projects")
        page.refresh()
        page.jobs_table.selectRow(0)
        page.status_combo.setCurrentIndex(page.status_combo.findData("won"))
        page.advance_status()
        assert store.load(job.job_id).accepted_revision == 2

        shown: list[str] = []

        def look(dialog):
            dialog.table.selectRow(1)
            shown.append(dialog.changes.text())
            shown.append(dialog.table.model().index(1, 0).data())
            return 0

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(money_dialogs.RevisionsDialog, "exec", look)
        try:
            page.show_revisions()
        finally:
            monkeypatch.undo()
        assert "דוד שמש" in shown[0] and "אושרה" in shown[1]
        window.close()
        window.deleteLater()
        QCoreApplication.processEvents()


def test_a_row_chosen_before_the_table_is_shown_stays_chosen():
    """Qt's selectRow does nothing on a hidden table in a right-to-left
    window: the job just saved, the newest version, silently unselected."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from profileos.ui.widgets import DataTable

    app = QApplication.instance() or QApplication([])
    before = app.layoutDirection()
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    try:
        table = DataTable(["א", "ב"])
        table.set_rows([["1", "2"], ["3", "4"], ["5", "6"]])
        table.selectRow(2)
        assert [i.row() for i in table.selectionModel().selectedRows()] == [2]
        assert table.currentRow() == 2
    finally:
        app.setLayoutDirection(before)
