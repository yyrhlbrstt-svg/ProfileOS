"""Ask the program in Hebrew; the answer comes from the shop's own books."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from profileos.ask import answer
from profileos.erp.collection import Cheque, ChequeBook, ChequeState
from profileos.projects import JobFile, JobStatus
from profileos.projects.agenda import Agenda, Appointment, VisitKind

TODAY = date(2026, 11, 14)


def _context():
    book = ChequeBook()
    book.add(Cheque(customer="כהן", amount=10_000, due=TODAY - timedelta(days=3), number="11"))
    book.add(Cheque(customer="לוי", amount=4_000, due=TODAY + timedelta(days=20), number="12"))
    back = book.add(Cheque(customer="דוד", amount=2_500, due=TODAY - timedelta(days=9), number="13"))
    back.state = ChequeState.BOUNCED
    book.add(Cheque(customer="קליל", amount=5_000, due=TODAY + timedelta(days=9), number="14",
                    outgoing=True))
    owing = JobFile(job_id="J-1", name="וילה", customer_name="משפחת ברק",
                    status=JobStatus.INSTALLED, quote_total=10_000)
    quoted = JobFile(job_id="J-2", name="דירה", customer_name="שגיא",
                     status=JobStatus.QUOTED, quote_total=30_000,
                     quoted_on=(TODAY - timedelta(days=10)).isoformat())
    agenda = Agenda()
    agenda.add(Appointment(on=TODAY + timedelta(days=1), time="08:00",
                           kind=VisitKind.INSTALLATION, customer_name="משפחת ברק"))
    return {"cheques": book, "jobs": [owing, quoted], "agenda": agenda}


@pytest.mark.parametrize("question, says", [
    ("כמה כסף יש במגירה?", "⁦14,000 ₪⁩ בצ׳קים"),
    ("מה אפשר להפקיד היום", "⁦10,000 ₪⁩ אפשר להפקיד"),
    ("אילו צ׳קים חזרו?", "צ׳ק אחד חזר"),
    ("מי חייב לי הכי הרבה?", "⁦11,800 ₪⁩ לגבייה מפרויקט אחד"),
    ("מה יש מחר בשטח?", "ביקור אחד מחר"),
    ("אילו הצעות פתוחות יותר משבוע?", "הצעה פתוחה אחת בשווי ⁦30,000 ₪⁩"),
    ("מה עם משפחת ברק", "משפחת ברק: פרויקט אחד, חייבים ⁦11,800 ₪⁩"),
    ("כמה כסף ייכנס החודש?", "⁦14,000 ₪⁩ נכנסים ב־30 הימים הקרובים"),
    ("כמה אני צריך לשלם לספקים?", "⁦5,000 ₪⁩ בצ׳קים שנתתם"),
    ("מה יש השבוע בשטח?", "ביקור אחד בשבוע הקרוב"),
])
def test_a_question_is_answered_from_the_books(question, says):
    found = answer(question, _context(), today=TODAY)
    assert found is not None and says in found.title, found and found.title


def test_a_search_that_is_not_a_question_is_left_to_the_search():
    assert answer("פרופיל", _context(), today=TODAY) is None
    assert answer("הז", _context(), today=TODAY) is None


def test_the_palette_shows_the_answer_and_enter_goes_there(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from profileos.core.config import reload_settings
    from profileos.ui.main_window import MainWindow
    from profileos.ui.palette_search import CommandPalette
    from profileos.ui.theme import LIGHT

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    palette = CommandPalette(window)
    palette._context = _context()
    palette.search.setText("מי חייב לי הכי הרבה")
    palette._ask()
    assert palette.answer_card.isVisibleTo(palette) and "לגבייה" in palette.answer_title.text()
    QTest.keyClick(palette, Qt.Key.Key_Return)
    assert window.stack.currentWidget() is window.page("Projects")
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


@pytest.mark.parametrize("question", [
    "באיזה רחוב הם גרים", "אני חייב להזמין פרופילים", "מה אחוז הרווח שלי?",
    "כמה מכרנו בחודש שעבר?", "איך מדפיסים ברקוד?", "כמה עולה הובלה למרכז?",
])
def test_a_question_it_does_not_know_is_not_answered_wrongly(question):
    assert answer(question, _context(), today=TODAY) is None


def test_who_pays_me_is_the_customers_not_the_suppliers():
    found = answer("מי הלקוחות שצריכים לשלם לי?", _context(), today=TODAY)
    assert "לגבייה" in found.title


def test_a_cheque_book_that_could_not_be_read_is_said_not_answered_as_empty():
    context = {**_context(), "unread": ["cheques"]}
    found = answer("כמה כסף יש במגירה?", context, today=TODAY)
    assert "לא נקרא" in found.title


def test_a_page_named_in_the_palette_is_the_page_enter_opens(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from profileos.core.config import reload_settings
    from profileos.ui.main_window import MainWindow
    from profileos.ui.palette_search import CommandPalette
    from profileos.ui.theme import LIGHT

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    palette = CommandPalette(window)
    palette._context = _context()
    palette.search.setText("גבייה")
    QTest.keyClick(palette, Qt.Key.Key_Return)
    assert window.stack.currentWidget() is window.page("Collection")
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()
