"""Measured bigger than it was sold: said, priced at the sold rate, billed once."""

from __future__ import annotations

import os
from datetime import date

import pytest

from profileos.delivery.remeasure import compare, not_compared, unbilled, worth_asking
from profileos.delivery.survey import SurveyBook, sheet_for_job
from profileos.elements.model import ElementSchedule, Opening
from profileos.projects import JobFile, JobStatus, default_store
from profileos.quoting.revisions import Line, RevisionBook


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _sold_job() -> JobFile:
    """Two windows sold: W1 1500×1200 ×2 at ₪3,600 each (₪2,000/m²), W2 900×1000."""
    job = JobFile(job_id="J-2026-0011", name="בית לוי", customer_name="לוי",
                  status=JobStatus.WON, quote_total=9_000)
    job.schedule = ElementSchedule(name="בית לוי", openings=[
        Opening(element_id="W1", name="W1", reference="W1", width=1500, height=1200, quantity=2),
        Opening(element_id="W2", name="W2", reference="W2", width=900, height=1000),
    ])
    job.accepted_revision = 1
    default_store().save(job)
    book = RevisionBook().load()
    book.for_job(job.job_id).record_issued([
        Line("W1", "W1 — חלון", 2, "pc", 3_600.0, "element", width=1500, height=1200),
        Line("W2", "W2 — חלון", 1, "pc", 1_800.0, "element", width=900, height=1000),
    ], 9_000.0)
    book.save()
    return job


def _measure(sheet, reference, width, height):
    entry = sheet.opening(reference)
    entry.width_head = entry.width_middle = entry.width_sill = width
    entry.height_left = entry.height_middle = entry.height_right = height
    entry.diagonal_a = entry.diagonal_b = round((width ** 2 + height ** 2) ** 0.5)
    entry.clearance_per_side = 10.0
    entry.measured_by = "יוסי"
    entry.measured_on = date.today()


def test_bigger_in_the_wall_is_priced_at_the_rate_it_was_sold(shop):
    job = _sold_job()
    book = SurveyBook().load()
    sheet = sheet_for_job(job, book)
    _measure(sheet, "W1", 1560, 1240)      # frame 1540 × 1220
    _measure(sheet, "W2", 920, 1020)       # frame 900 × 1000 — as sold
    revision = RevisionBook().load().for_job(job.job_id).get(1)
    changes = compare(job, sheet, revision)
    assert [c.reference for c in changes] == ["W1"]
    w1 = changes[0]
    assert w1.rate_m2 == pytest.approx(2_000.0)
    # (1.540 × 1.220 − 1.500 × 1.200) m² × ₪2,000 = ₪157.60 a piece, two pieces.
    assert w1.per_piece == pytest.approx(157.60) and w1.amount == pytest.approx(315.20)
    assert "⁦1500×1200⁩" in w1.describe() and "⁦1540×1220⁩" in w1.describe()
    assert worth_asking(changes)

    # The job's openings were since set to the measured size: the sold size
    # still comes from the version the customer accepted.
    job.schedule.openings[0].width, job.schedule.openings[0].height = 1540, 1220
    assert compare(job, sheet, revision)[0].quoted == (1500, 1200)


def test_the_extra_is_added_once_for_approval_and_the_home_page_stops_asking(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from profileos.insights import scan
    from profileos.projects.account import job_account
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    job = _sold_job()
    book = SurveyBook().load()
    sheet = sheet_for_job(job, book)
    _measure(sheet, "W1", 1560, 1240)
    book.save()

    found = [i for i in scan(date.today()) if i.key.startswith("remeasured:")]
    assert found and "⁦315 ₪⁩" in found[0].detail and found[0].page == "Survey"

    shown = []

    def click_add(box):
        shown.append(box.text())
        box.clickedButton = lambda: next(
            b for b in box.buttons() if "הוספת תוספת" in b.text())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", click_add)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load(job.job_id))
    page = window.go_to_page("Survey")
    page.open_sheet()
    page.compare_to_quote()
    assert shown and "⁦1540×1220⁩" in shown[0]
    saved = default_store().load(job.job_id)
    extras = [line for line in saved.lines if line.origin == "measure:W1"]
    assert len(extras) == 1 and extras[0].unit_price == pytest.approx(157.60)
    assert not extras[0].approved                          # waits for the customer
    assert job_account(saved, []).pending_extras_net == pytest.approx(315.20)

    page.compare_to_quote()                                  # asked again: not doubled
    assert len([l for l in default_store().load(job.job_id).lines
                if l.origin == "measure:W1"]) == 1
    assert not [i for i in scan(date.today()) if i.key.startswith("remeasured:")]
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_an_opening_that_could_not_be_compared_is_named_not_counted_as_matching(shop):
    job = _sold_job()
    sheet = sheet_for_job(job, SurveyBook().load())
    _measure(sheet, "W1", 1520, 1220)      # frame 1500 × 1200 — as sold
    _measure(sheet, "W2", 1200, 1300)      # much bigger, but no diagonals taken
    w2 = sheet.opening("W2")
    w2.diagonal_a = w2.diagonal_b = None
    revision = RevisionBook().load().for_job(job.job_id).get(1)
    assert compare(job, sheet, revision) == []
    left = not_compared(job, sheet, revision)
    assert [marking for marking, _why in left] == ["W2"]
    assert "אלכסונים" in left[0][1]


def test_an_opening_added_after_the_quote_is_named_as_not_compared(shop):
    from profileos.elements.model import Opening

    job = _sold_job()
    job.schedule.openings.append(Opening(element_id="W3", name="W3", reference="W3",
                                         width=600, height=600))
    default_store().save(job)
    sheet = sheet_for_job(job, SurveyBook().load())
    _measure(sheet, "W3", 900, 900)
    revision = RevisionBook().load().for_job(job.job_id).get(1)
    assert compare(job, sheet, revision) == []
    assert not_compared(job, sheet, revision) == [("W3", "לא תומחר בשורה משלו בגרסה שאושרה")]
