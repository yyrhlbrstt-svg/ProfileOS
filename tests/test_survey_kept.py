"""A measurement typed in the office, or on the phone on site, is kept."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from profileos.delivery.survey import SurveyBook
from profileos.elements.model import ElementSchedule, Opening
from profileos.mobile.measure import MeasurementStore, SiteMeasurement, default_store_path
from profileos.projects import JobFile, JobStatus, default_store


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
    job = JobFile(job_id="J-2026-0007", name="בית כהן", status=JobStatus.WON)
    job.schedule = ElementSchedule(name="בית כהן", openings=[
        Opening(element_id="W1", name="W1", reference="W1", width=1200, height=1400),
        Opening(element_id="W2", name="W2", reference="W2", width=900, height=1000),
    ])
    default_store().save(job)
    return job


def test_measured_in_the_office_and_on_the_phone_and_still_there_tomorrow(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    job = _job()
    # On site: the fitter typed W2 on the phone, against this job.
    phone = MeasurementStore.load(default_store_path())
    phone.add(SiteMeasurement(
        reference="W2", project_id=job.job_id, widths=(905, 903, 904),
        heights=(1002, 1001, 1003), diagonals=(1350, 1352), measured_by="יוסי",
        measured_at=datetime.now(timezone.utc) - timedelta(hours=2)))

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load(job.job_id))
    page = window.go_to_page("Survey")
    page.open_sheet()
    w2 = page._survey.opening("W2")
    assert w2.smallest_width == 903 and w2.measured_by == "יוסי"
    assert "נמדד בטלפון" in w2.note

    # In the office: W1 typed by hand.
    page.survey_opening.setCurrentIndex(page.survey_opening.findData("W1"))
    for key, value in (("width_head", 1195), ("width_middle", 1196), ("width_sill", 1194),
                       ("height_left", 1398), ("height_middle", 1399), ("height_right", 1397)):
        page._boxes[key].setValue(value)
    page.record_measurement()
    assert "לא נשמר" not in page.survey_result.text()
    window.close(); window.deleteLater(); QCoreApplication.processEvents()

    # Next day, a fresh program: both are on the sheet.
    sheet = SurveyBook().load().for_job(job.job_id)
    assert len(sheet) == 1
    assert sheet[0].opening("W1").smallest_width == 1194
    assert sheet[0].opening("W2").smallest_width == 903

    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load(job.job_id))
    page = window.go_to_page("Survey")
    page.open_sheet()
    assert page._survey.opening("W1").smallest_width == 1194
    assert len(SurveyBook().load().for_job(job.job_id)) == 1      # not a second sheet
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_a_size_typed_in_the_office_is_not_overwritten_by_an_older_phone_one(shop):
    from datetime import date

    from profileos.delivery.survey import sheet_for_job

    job = _job()
    book = SurveyBook().load()
    sheet = sheet_for_job(job, book)
    w1 = sheet.opening("W1")
    w1.width_head = w1.width_middle = w1.width_sill = 1190
    w1.height_left = w1.height_middle = w1.height_right = 1390
    w1.measured_on = date.today()
    old = SiteMeasurement(reference="w1", project_id="", widths=(1210, 1210, 1210),
                          heights=(1410, 1410, 1410),
                          measured_at=datetime.now(timezone.utc) - timedelta(days=3))
    other_job = SiteMeasurement(reference="W2", project_id="J-OTHER", widths=(500, 500, 500),
                                heights=(500, 500, 500))
    assert sheet.take_from_phone([old, other_job]) == []
    assert w1.smallest_width == 1190 and not sheet.opening("W2").is_measured


def test_a_phone_mark_without_its_job_never_fills_another_jobs_opening(shop):
    """W1 is in every job: a record names its job, or it is not taken."""
    from profileos.delivery.survey import sheet_for_job

    job = _job()
    sheet = sheet_for_job(job, SurveyBook().load())
    loose = SiteMeasurement(reference="W1", widths=(900, 900, 900), heights=(2100, 2100, 2100))
    elsewhere = SiteMeasurement(reference="W1", project_id="J-2026-0099",
                                widths=(900, 900, 900), heights=(2100, 2100, 2100))
    assert sheet.take_from_phone([loose, elsewhere]) == []
    assert not sheet.opening("W1").is_measured


def test_a_correction_on_the_phone_the_same_afternoon_replaces_the_morning(shop):
    from profileos.delivery.survey import sheet_for_job

    job = _job()
    sheet = sheet_for_job(job, SurveyBook().load())
    morning = datetime.now(timezone.utc).replace(hour=7, minute=0)
    first = SiteMeasurement(reference="W1", project_id=job.job_id, widths=(1200, 1200, 1200),
                            heights=(1400, 1400, 1400), measured_at=morning)
    assert sheet.take_from_phone([first]) == ["W1"]
    again = SiteMeasurement(reference="W1", project_id=job.job_id, widths=(1180, 1180, 1180),
                            heights=(1400, 1400, 1400), measured_at=morning + timedelta(hours=4))
    assert sheet.take_from_phone([first, again]) == ["W1"]
    assert sheet.opening("W1").smallest_width == 1180
    assert sheet.take_from_phone([first]) == []            # the older one never wins back


def test_two_computers_measuring_different_windows_of_one_sheet_keep_both(shop):
    from profileos.delivery.survey import save_openings, sheet_for_job

    job = _job()
    sheet = sheet_for_job(job, SurveyBook().load())
    office = SurveyBook().load().get(sheet.survey_id)
    laptop = SurveyBook().load().get(sheet.survey_id)
    office.opening("W1").width_head = 1195
    laptop.opening("W2").width_head = 895
    save_openings(office, ["W1"])
    save_openings(laptop, ["W2"])
    disk = SurveyBook().load().get(sheet.survey_id)
    assert disk.opening("W1").width_head == 1195 and disk.opening("W2").width_head == 895


def test_the_phone_offers_the_open_jobs_and_their_marks(shop):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.mobile.state import configure

    state = configure(registry_path=shop / "devices.json",
                      measurement_path=shop / "measurements.json")
    _job()
    default_store().save(JobFile(job_id="J-OLD", name="ישן", status=JobStatus.INSTALLED))
    client = TestClient(app)
    code = state.registry.issue_code("טלפון מדידות", scopes=("measure",))
    paired = client.post("/m/api/pair", json={"code": code.code}).json()
    headers = {"X-Device-Id": paired["device_id"], "X-Device-Token": paired["token"]}
    projects = client.get("/m/api/projects", headers=headers).json()["projects"]
    assert [p["id"] for p in projects] == ["J-2026-0007"]
    assert projects[0]["openings"] == ["W1", "W2"]
