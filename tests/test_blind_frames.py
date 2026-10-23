"""Two-stage installation: blind frames set and checked, the windows measured from them."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.projects.blindframes import (
    Stage, describe, is_two_stage, record_check, record_installed, set_two_stage, stage,
    windows_not_ready,
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


def _job(job_id="J-2026-0021"):
    from profileos.elements.model import ElementSchedule, Opening
    from profileos.projects import JobFile, JobStatus

    job = JobFile(job_id=job_id, name="בניין ברחוב הזית", customer_name="קבלן כהן",
                  status=JobStatus.WON, system_id="klil-7300")
    job.schedule = ElementSchedule(name="x", system_id="klil-7300", openings=[
        Opening(element_id=f"EL-{mark}", name=mark, reference=mark, width=1200, height=1400,
                system_id="klil-7300")
        for mark in ("W1", "W2", "W3")])
    return job


def test_the_stages_follow_the_site():
    job = _job()
    assert stage(job) is None and describe(job) == "התקנה בשלב אחד"
    set_two_stage(job, True)
    assert is_two_stage(job) and stage(job) is Stage.TO_INSTALL
    with pytest.raises(ProfileOSError, match="מי התקין"):
        record_installed(job, ["W1"], by="")
    record_installed(job, ["W1", "W2"], by="יוסי", level_mark="1.00+ קומה א")
    assert stage(job) is Stage.PARTLY
    assert "W3" in windows_not_ready(job)[0]
    record_installed(job, ["W3"], by="יוסי")
    assert stage(job) is Stage.WAITING_PLASTER
    assert windows_not_ready(job) == ["המשקופים העיוורים עוד לא נבדקו אחרי הטיח"]
    with pytest.raises(ProfileOSError, match="מה צריך לתקן"):
        record_check(job, ["W2"], ok=False)
    record_check(job, ["W2"], ok=False, note="טיח בתוך המשקוף")
    assert stage(job) is Stage.PROBLEMS and "טיח" in windows_not_ready(job)[0]
    record_check(job, ["W1", "W2", "W3"], ok=True)
    assert stage(job) is Stage.READY_TO_MEASURE and not windows_not_ready(job)
    assert "⁦3⁩ מתוך ⁦3⁩" in describe(job)


def test_a_blind_frame_already_installed_is_not_erased():
    job = _job()
    set_two_stage(job, True)
    record_installed(job, ["W1"], by="יוסי")
    with pytest.raises(ProfileOSError, match="W1"):
        set_two_stage(job, False)
    assert is_two_stage(job)


def test_the_windows_are_not_ready_to_fit_before_the_blind_frames_are():
    from profileos.projects.install_check import not_ready

    job = _job()
    set_two_stage(job, True)
    assert any("משקופים עיוורים" in m for m in not_ready(job))


def test_two_sheets_the_wall_never_overwritten_by_the_blind_frames(shop):
    from profileos.delivery.survey import BLIND_FRAME, SurveyBook, sheet_for_job, window_sheet

    job = _job()
    book = SurveyBook().load()
    wall = sheet_for_job(job, book, clearance_per_side=15.0)
    wall.opening("W1").width_head = 1260.0
    assert window_sheet(job, book) is wall               # one stage: the wall's is the one
    set_two_stage(job, True)
    assert window_sheet(job, book) is None               # two: not measured from the frames yet
    frames = sheet_for_job(job, book, clearance_per_side=5.0, stage=BLIND_FRAME)
    assert frames is not wall and frames.opening("W1").width_head is None
    book.save()
    again = SurveyBook().load()
    assert window_sheet(job, again).survey_id == frames.survey_id
    assert sheet_for_job(job, again).opening("W1").width_head == 1260.0


def test_the_deduction_from_a_blind_frame_is_the_series_own(shop):
    from profileos.delivery.survey import BLIND_FRAME, series_clearance
    from profileos.systems import DIRECTORY, FIGURES, Confirmation
    from profileos.systems.confirmation import default_confirmations

    before = {entry.id: entry for entry in DIRECTORY}
    try:
        confirmation = Confirmation(entry_id="klil-7300", source="הוראות הרכבה קליל, עמ׳ 12",
                                    entered_by="דאדי")
        for figure in FIGURES:
            if figure.default is not None:
                confirmation.values[figure.key] = figure.default
        confirmation.values["installation_clearance"] = 12.0
        default_confirmations().load().record(confirmation)
        job = _job()
        assert series_clearance(job) == (12.0, "הוראות הרכבה קליל, עמ׳ 12")
        # Not entered: measured from a blind frame, nothing is assumed.
        assert series_clearance(job, BLIND_FRAME) is None
        confirmation.values["blind_frame_deduction"] = 4.0
        default_confirmations().load().record(confirmation)
        assert series_clearance(job, BLIND_FRAME) == (4.0, "הוראות הרכבה קליל, עמ׳ 12")
    finally:
        DIRECTORY._entries.update(before)


def test_the_home_page_chases_the_plaster_and_the_early_order(shop):
    from profileos.insights import _blind_frames
    from profileos.projects import MaterialOrder

    job = _job()
    set_two_stage(job, True)
    long_ago = date.today() - timedelta(days=45)
    record_installed(job, ["W1", "W2", "W3"], by="יוסי", on=long_ago)
    job.material_orders.append(MaterialOrder(supplier="קליל"))

    class NoSheets:
        def for_job(self, job_id):
            return []

    found = {i.key.split(":")[0]: i for i in _blind_frames(
        {"jobs": [job], "surveys": NoSheets()}, date.today())}
    assert "⁦45⁩ ימים" in found["bf-wait"].detail
    assert found["bf-early"].page == "Survey"
    record_check(job, ["W2"], ok=False, note="לא בפלס")
    found = {i.key.split(":")[0]: i for i in _blind_frames(
        {"jobs": [job], "surveys": NoSheets()}, date.today())}
    assert "לא בפלס" in found["bf-fix"].detail


def test_set_and_checked_from_the_projects_page(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.projects import default_store
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    default_store().save(_job())

    def work_the_site(dialog):
        dialog.two_stage.setChecked(True)
        dialog.table.selectAll()
        dialog.installer.setEditText("יוסי")
        dialog.level_mark.setText("1.00+")
        assert dialog.mark_installed() == 3
        dialog.table.clearSelection()
        dialog.table.selectRow(1)
        dialog.note.setText("טיח בתוך המשקוף")
        assert dialog.mark_checked(False) == 1
        # Meanwhile, on another screen: the job's status moved on.
        other = default_store().load("J-2026-0021")
        other.site_address = "הזית 4, בית אל"
        default_store().save(other)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.BlindFramesDialog, "exec", work_the_site)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    monkeypatch.setattr(page, "_selected_job", lambda: default_store().load("J-2026-0021"))
    assert page.manage_blind_frames()
    saved = default_store().load("J-2026-0021")
    assert saved.site_address == "הזית 4, בית אל"          # the other change is kept
    assert stage(saved) is Stage.PROBLEMS
    assert windows_not_ready(saved)[0].endswith("טיח בתוך המשקוף")
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_the_survey_page_measures_the_windows_from_the_blind_frames(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    job = _job()
    set_two_stage(job, True)
    record_installed(job, ["W1", "W2", "W3"], by="יוסי")
    record_check(job, ["W1", "W2", "W3"], ok=True)
    default_store().save(job)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load(job.job_id))
    page = window.go_to_page("Survey")
    page.open_sheet()
    assert page._survey.stage == "blind_frame"             # the stage chose the sheet
    page.survey_stage.setCurrentIndex(0)                   # the wall's, asked for
    assert page._survey.stage == ""
    said = []
    monkeypatch.setattr(page, "report", lambda exc, title: said.append(str(exc)))
    assert page.compare_to_quote() == []
    assert said and "מהמשקופים העיוורים" in said[0]
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_the_reminder_before_blind_frames_asks_for_the_level_line():
    from profileos.projects.agenda import Appointment, VisitKind, reminder_text

    visit = Appointment(customer_name="קבלן כהן", on=date.today() + timedelta(days=1),
                        time="07:30", kind=VisitKind.BLIND_FRAMES)
    text = reminder_text(visit, brand_name="דאדי")
    assert "משקופים העיוורים" in text and "קו המטר" in text


def test_phone_figures_go_to_the_sheet_their_dates_belong_to():
    from datetime import datetime, timezone

    from profileos.mobile.measure import SiteMeasurement
    from profileos.projects.blindframes import phone_records_for

    job = _job()
    set_two_stage(job, True)
    day = date.today()
    record_installed(job, ["W1", "W2", "W3"], by="יוסי", on=day - timedelta(days=60))
    record_check(job, ["W1", "W2", "W3"], ok=True, on=day - timedelta(days=5))

    def taken(days_ago):
        return SiteMeasurement(reference="W1", project_id=job.job_id,
                               measured_at=datetime.now(timezone.utc) - timedelta(days=days_ago))

    wall, between, frames = taken(90), taken(30), taken(1)
    records = [wall, between, frames]
    assert phone_records_for(job, "", records) == [wall]
    assert phone_records_for(job, "blind_frame", records) == [frames]
    one_stage = _job()
    assert phone_records_for(one_stage, "", records) == records
    assert phone_records_for(one_stage, "blind_frame", records) == []


def test_two_openings_with_one_marking_are_not_both_marked():
    job = _job()
    job.schedule.openings[1].reference = "W1"              # two called W1
    set_two_stage(job, True)
    with pytest.raises(ProfileOSError, match="לא נבחרו"):
        record_installed(job, ["W1"], by="יוסי")
    record_installed(job, [job.schedule.openings[1].element_id], by="יוסי")
    from profileos.projects.blindframes import BlindFrame

    assert [bool(BlindFrame.of(o).installed) for o in job.schedule.openings] == [False, True, False]


def test_measured_sizes_applied_keep_the_blind_frames_and_the_series(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.delivery.survey import BLIND_FRAME, SurveyBook, sheet_for_job
    from profileos.projects import default_store
    from profileos.projects.blindframes import BlindFrame
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    job = _job()
    job.schedule.openings[0].quantity = 2
    set_two_stage(job, True)
    record_installed(job, ["W1", "W2", "W3"], by="יוסי", level_mark="קו המטר")
    record_check(job, ["W1", "W2", "W3"], ok=True)
    default_store().save(job)
    book = SurveyBook().load()
    sheet = sheet_for_job(job, book, clearance_per_side=5.0, stage=BLIND_FRAME)
    entry = sheet.opening("W1")
    entry.width_head = entry.width_middle = entry.width_sill = 1210.0
    entry.height_left = entry.height_middle = entry.height_right = 1410.0
    entry.diagonal_a = entry.diagonal_b = round((1210 ** 2 + 1410 ** 2) ** 0.5)
    entry.measured_by, entry.measured_on = "יוסי", date.today()
    book.save()

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.go_to_page("Projects")
    window.session.set_job(default_store().load(job.job_id))
    for opening in default_store().load(job.job_id).schedule.openings:
        from profileos.elements.builder import ElementBuilder

        window.session.add_build(ElementBuilder.for_system("klil-7300").build(opening))
    page = window.go_to_page("Survey")
    page.open_sheet()
    assert page._survey.stage == BLIND_FRAME
    page.apply_to_elements()
    saved = default_store().load(job.job_id)
    w1 = next(o for o in saved.schedule.openings if o.reference == "W1")
    assert (w1.width, w1.height) == (1200.0, 1400.0)
    assert w1.system_id == "klil-7300" and w1.quantity == 2
    assert BlindFrame.of(w1).level_mark == "קו המטר"
    assert stage(saved) is Stage.READY_TO_MEASURE
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_the_design_saved_never_writes_an_old_blind_frame_record_over_the_new(shop):
    from profileos.elements.builder import ElementBuilder
    from profileos.elements.model import Opening
    from profileos.projects import default_store
    from profileos.projects.blindframes import BlindFrame
    from profileos.ui.session import Session

    job = _job()
    set_two_stage(job, True)
    default_store().save(job)
    session = Session()
    session.set_job(default_store().load(job.job_id))
    for opening in session.job.schedule.openings:
        session.add_build(ElementBuilder.for_system("klil-7300").build(opening))
    # Meanwhile, from the projects page: the blind frames went in.
    on_disk = default_store().load(job.job_id)
    record_installed(on_disk, ["W1", "W2", "W3"], by="יוסי")
    default_store().save(on_disk)
    # And a new opening drawn on screen.
    session.add_build(ElementBuilder.for_system("klil-7300").build(
        Opening(element_id="EL-W4", name="W4", reference="W4", width=900, height=1200,
                system_id="klil-7300")))
    saved = session.save_design_to_job()
    frames_now = {o.reference: BlindFrame.of(o) for o in saved.schedule.openings}
    assert frames_now["W1"].installed_by == "יוסי"
    assert frames_now["W4"] is not None and not frames_now["W4"].installed
    assert stage(saved) is Stage.PARTLY


def test_a_survey_kept_before_sheets_had_a_stage_is_the_walls(shop):
    from profileos.delivery.survey import Survey

    old = Survey.from_dict({"survey_id": "SV-OLD", "job_id": "J-1", "openings": []})
    assert old.stage == ""
