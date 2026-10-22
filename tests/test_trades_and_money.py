"""Plumbing jobs, the price book, work lines on quotes, and what a job is owed."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.erp.collection import Cheque, ChequeBook
from profileos.projects import JobFile, JobLine, LineKind, Payment, Trade
from profileos.projects.account import invoices_missing_allocation, job_account
from profileos.projects.model import InvoiceRecord
from profileos.quoting.editor import QuoteDraft
from profileos.quoting.pricebook import PriceBook, PriceItem

RATES = {"profile": 5.5, "glass_m2": 60, "hardware": 12, "gasket_m": 1.1}


class TestTrade:
    def test_a_job_knows_its_trade_and_survives_the_round_trip(self):
        job = JobFile(job_id="J-1", name="דירה", trade=Trade.PLUMBING)
        again = JobFile.model_validate_json(job.model_dump_json())
        assert again.trade is Trade.PLUMBING
        assert Trade.BOTH.includes(Trade.PLUMBING)
        assert not Trade.ALUMINIUM.includes(Trade.PLUMBING)

    def test_an_old_job_file_is_aluminium(self):
        job = JobFile.model_validate({"job_id": "J-1", "name": "ישן"})
        assert job.trade is Trade.ALUMINIUM and job.lines == []


class TestPriceBook:
    def test_a_new_book_has_the_kinds_of_work_and_no_invented_prices(self, tmp_path):
        book = PriceBook(tmp_path / "pricebook.json").load()
        assert len(book) > 20
        assert all(item.price == 0 for item in book)
        assert book.for_fixture("basin").code == "PL-BASIN"

    def test_a_price_is_kept_with_its_source(self, tmp_path):
        path = tmp_path / "pricebook.json"
        book = PriceBook(path).load()
        book.save()
        book.set_price("PL-BLOCKAGE", 350, source="מחירון 2026")
        again = PriceBook(path).load()
        item = again.get("PL-BLOCKAGE")
        assert item.price == 350 and item.source == "מחירון 2026"
        assert again.get("PL-LEAK").price == 0

    def test_two_copies_do_not_erase_each_others_items(self, tmp_path):
        path = tmp_path / "pricebook.json"
        first = PriceBook(path).load()
        first.save()
        here, there = PriceBook(path).load(), PriceBook(path).load()
        there.add(PriceItem(code="PL-900", name="ניקוי מרזב", trade="plumbing"))
        here.set_price("AL-CRANE", 450)
        final = PriceBook(path).load()
        assert final.get("PL-900") is not None and final.get("AL-CRANE").price == 450


class TestQuoteLines:
    def test_work_lines_are_printed_and_counted(self):
        draft = QuoteDraft.start([], project_name="אינסטלציה", fallback_rates=RATES)
        draft.add_extra_line("פתיחת סתימה", unit_price=350)
        draft.add_extra_line("צנרת ניקוז — הנחה", quantity=6, unit="מ״א", unit_price=120)
        assert draft.totals()["net"] == 350 + 720
        assert [row["unit"] for row in draft.customer_lines()] == ["יח׳", "מ״א"]

    def test_an_unpriced_line_is_named(self):
        draft = QuoteDraft.start([], project_name="x", fallback_rates=RATES)
        draft.add_extra_line("איתור נזילה")
        assert [line.description for line in draft.unpriced_extras()] == ["איתור נזילה"]

    def test_undo_takes_a_line_back(self):
        draft = QuoteDraft.start([], project_name="x", fallback_rates=RATES)
        draft.add_extra_line("מנוף", unit_price=450)
        draft.undo()
        assert draft.extra_lines == [] and draft.totals()["net"] == 0

    def test_a_plumbing_quotation_has_no_window_drawings(self):
        from profileos.quoting.document import render_quotation

        draft = QuoteDraft.start([], project_name="x", fallback_rates=RATES)
        draft.add_extra_line("החלפת דוד מים חמים", unit_price=2400)
        html = render_quotation(draft)
        assert "החלפת דוד מים חמים" in html
        assert "<h2>" in html and 'class="pair"' not in html


class TestAccount:
    def _job(self) -> JobFile:
        return JobFile(
            job_id="J-7", name="וילה", quote_total=10_000,
            lines=[
                JobLine(kind=LineKind.EXTRA, description="חלון נוסף", unit_price=2_000,
                        approved="אושר בוואטסאפ"),
                JobLine(kind=LineKind.EXTRA, description="רשת", unit_price=300),
            ],
            payments=[Payment(amount=5_000), Payment(amount=1_000, withheld=300)],
        )

    def test_what_is_owed_counts_agreed_extras_payments_and_withholding(self):
        account = job_account(self._job())
        assert account.total_net == 12_000
        assert account.total_gross == pytest.approx(14_160)
        assert account.pending_extras_net == 300
        assert account.received == 6_300
        assert account.uncovered == pytest.approx(7_860)

    def test_cheques_with_the_jobs_number_are_on_their_way(self):
        book = ChequeBook()
        book.add(Cheque(customer="משה", amount=4_000, job_id="J-7", due=date(2026, 12, 1)))
        book.add(Cheque(customer="משה", amount=999, job_id="J-8"))
        account = job_account(self._job(), book)
        assert account.in_cheques == 4_000
        assert account.uncovered == pytest.approx(3_860)

    def test_a_business_invoice_without_allocation_number_is_caught(self):
        job = self._job()
        job.invoices = [InvoiceRecord(number="1001", net=12_000, business_customer=True)]
        assert invoices_missing_allocation(job)
        job.invoices[0].allocation_number = "123456789"
        assert not invoices_missing_allocation(job)


def test_a_materials_order_reads_down_a_phone_screen():
    from profileos.sharing import material_list_message

    message = material_list_message(
        [("pipe", "צינור PPR 25", "24", "מ׳", ""), ("fitting", "זווית 25", "0", "יח׳", ""),
         ("fitting", "ברז כדורי 3/4", "2", "יח׳", "פליז")],
        brand_name="דאדי", job_name="וילה", site="בית אל",
    )
    assert "⁦24 מ׳⁩ — צינור PPR 25" in message
    assert "זווית" not in message  # nothing to order
    assert "(פליז)" in message and "בית אל" in message


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


def test_a_plumbing_job_goes_from_fixtures_to_a_quotation(shop):
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.quoting.pricebook import default_pricebook
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    book = default_pricebook()
    book.save()
    book.set_price("PL-BASIN", 450, source="מחירון הבדיקה")

    store = default_store()
    job = store.create("דירה בעפרה", trade=Trade.PLUMBING)
    window = MainWindow(LIGHT)
    window.session.set_job(store.load(job.job_id))
    plumbing = window.go_to_page("Plumbing")
    for box in plumbing.counts.values():
        box.setValue(0)
    plumbing.counts["basin"].setValue(2)
    plumbing.counts["shower"].setValue(1)
    plumbing.send_to_quote()

    quote = window.page("Quotation")
    lines = {line.description: line for line in quote.draft.extra_lines}
    assert lines["התקנת כיור רחצה"].quantity == 2
    assert lines["התקנת כיור רחצה"].unit_price == 450
    assert lines["התקנת מקלחת"].unit_price == 0  # no price entered: never invented
    saved = store.load(job.job_id)
    assert saved.plumbing.fixtures == {"basin": 2, "shower": 1}
    assert saved.trade is Trade.PLUMBING
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def test_the_account_window_shows_what_is_owed_and_records_payments(shop):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.ui.money_dialogs import JobAccountDialog

    QApplication.instance() or QApplication([])
    store = default_store()
    job = store.create("וילה")
    job.quote_total = 10_000
    store.save(job)
    dialog = JobAccountDialog(job.job_id, store)
    assert "11,800" in dialog.headline.text()
    dialog._change(lambda j: j.payments.append(Payment(amount=11_800)), "התשלום")
    assert "שולם במלואו" in dialog.headline.text()
    assert store.load(job.job_id).payments[0].amount == 11_800


class TestField:
    def test_the_next_working_day_skips_the_weekend(self):
        from profileos.projects.agenda import next_working_day

        assert next_working_day(date(2026, 10, 22)) == date(2026, 10, 25)  # Thu → Sun
        assert next_working_day(date(2026, 10, 25)) == date(2026, 10, 26)

    def test_a_reminder_names_the_day_and_the_visit(self):
        from profileos.projects.agenda import Appointment, VisitKind, reminder_text

        visit = Appointment(on=date(2026, 10, 25), kind=VisitKind.SURVEY, time="09:00",
                            customer_name="משה")
        text = reminder_text(visit, brand_name="דאדי", today=date(2026, 10, 22))
        assert "שלום משה" in text and "ביום ראשון" in text and "מדידה" in text
        assert "⁦09:00⁩" in text

    def test_the_diary_keeps_visits_and_who_was_told(self, tmp_path):
        from profileos.projects.agenda import Agenda, Appointment

        path = tmp_path / "agenda.json"
        diary = Agenda(path).load()
        visit = diary.add(Appointment(on=date(2026, 10, 25), customer_name="דוד"))
        assert [a.customer_name for a in diary.unreminded(date(2026, 10, 25))] == ["דוד"]
        diary.mark_reminded(visit.appointment_id)
        again = Agenda(path).load()
        assert again.unreminded(date(2026, 10, 25)) == []
        again.move(visit.appointment_id, date(2026, 10, 26))
        assert Agenda(path).load().unreminded(date(2026, 10, 26))  # a new day, a new reminder

    def test_plumbing_faults_go_to_the_plumber(self):
        from profileos.service import Symptom
        from profileos.service.model import Severity

        assert Symptom.BLOCKAGE.trade == "plumbing"
        assert Symptom.DROPPED.trade == "aluminium"
        assert Symptom.BURST.likely_severity is Severity.BLOCKING
