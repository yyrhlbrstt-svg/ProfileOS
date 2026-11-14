"""A delivery date from the shop's real backlog, never from a guess."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from profileos.projects import JobFile, JobStatus
from profileos.promise import promise


class Book:
    def __init__(self, entries=()):
        self.entries = list(entries)

    def __iter__(self):
        return iter(self.entries)

    def for_job(self, job_id):
        return [e for e in self.entries if e.job_id == job_id]


def entry(on, hours, job_id="J-OLD", operation="הרכבה"):
    return SimpleNamespace(on=on, hours=hours, job_id=job_id, operation=operation,
                           rework=False)


def job(job_id, status, hours):
    found = JobFile(job_id=job_id, name=job_id, customer_name="לקוח", status=status)
    found.norm_hours = {"assembly": hours} if hours else {}
    return found


SETTINGS = SimpleNamespace(workshop_hours_per_week=0.0, material_lead_working_days=0)
# Sunday 1 November 2026: an ordinary week, no festival.
TODAY = date(2026, 11, 1)


def test_no_pace_no_date():
    said = promise(new_hours=10, jobs=[], timebook=Book(), settings=SETTINGS, today=TODAY)
    assert not said.known and said.install is None
    assert "חסר קצב" in said.headline()
    assert "שעות עבודה בשבוע" in said.lines()[0]


def test_the_owner_s_weekly_hours_give_a_date_until_the_floor_measures():
    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=0)
    said = promise(new_hours=32, jobs=[], timebook=Book(), settings=settings, today=TODAY)
    # 88 h a week = 16 h a full day: two days' work starting Monday 2/11.
    assert said.pace.source == "settings" and said.pace.per_day == 16.0
    assert said.ready == date(2026, 11, 3)
    assert said.install == date(2026, 11, 4)
    assert "⁦04/11/2026⁩" in said.headline()


def test_the_queue_comes_first_and_the_job_being_quoted_is_not_counted_twice():
    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=0)
    jobs = [job("J-1", JobStatus.WON, 48), job("J-2", JobStatus.IN_PRODUCTION, 40),
            job("J-3", JobStatus.QUOTED, 500), job("J-4", JobStatus.INSTALLED, 500)]
    book = Book([entry(TODAY - timedelta(days=2), 24, job_id="J-2")])
    said = promise(new_hours=16, jobs=jobs, timebook=book, settings=settings, today=TODAY,
                   exclude_job="J-1")
    # J-2: 40 h less 24 booked = 16 h ahead; then 16 h of the new job.
    assert said.jobs_ahead == 1 and said.backlog_hours == 16
    assert said.ready == date(2026, 11, 3)


def test_a_job_without_hours_is_named_not_silently_counted_as_nothing():
    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=0)
    said = promise(new_hours=8, jobs=[job("J-9", JobStatus.WON, 0)], timebook=Book(),
                   settings=settings, today=TODAY)
    assert any("⁦J-9⁩" in note for note in said.lines())


def test_the_measured_pace_wins_once_there_are_enough_days():
    days = [TODAY - timedelta(days=offset) for offset in range(1, 29)]
    worked = [day for day in days if day.weekday() not in (4, 5)][:16]
    book = Book([entry(day, 20) for day in worked])
    settings = SimpleNamespace(workshop_hours_per_week=10.0, material_lead_working_days=0)
    said = promise(new_hours=8, jobs=[], timebook=book, settings=settings, today=TODAY)
    assert said.pace.source == "measured" and said.pace.days == 16
    assert said.pace.per_day > 10


def test_material_wait_and_the_israeli_week():
    # Thursday 5 November: the next working days skip the weekend.
    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=3)
    said = promise(new_hours=4, jobs=[], timebook=Book(), settings=settings,
                   today=date(2026, 11, 5))
    for day in (said.ready, said.install):
        assert day.weekday() != 5                      # never a Saturday
    assert said.ready >= date(2026, 11, 10)            # material in after three working days
    assert not any("זמן הגעת" in note for note in said.notes)


def test_no_festival_is_promised():
    # Erev Yom Kippur 2027 is 10 October; Yom Kippur itself is 11 October.
    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=0)
    said = promise(new_hours=8, jobs=[], timebook=Book(), settings=settings,
                   today=date(2027, 10, 8))
    assert said.install != date(2027, 10, 11)


def test_asked_when_the_program_answers_from_the_queue(monkeypatch):
    from profileos import ask
    from profileos.core import config

    settings = SimpleNamespace(workshop_hours_per_week=88.0, material_lead_working_days=0)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    jobs = [job("J-1", JobStatus.WON, 32), job("J-2", JobStatus.INSTALLED, 16)]
    said = ask.answer("מתי אפשר להבטיח התקנה?", {"jobs": jobs, "timebook": Book()},
                      today=TODAY)
    assert said is not None and said.page == "Quotation"
    assert said.title.startswith("אפשר להבטיח התקנה מיום")
    assert any("⁦32⁩ שעות" in row or "⁦16⁩ שעות" in row for row in said.rows)


def test_the_quotation_page_says_when(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from profileos.core.config import get_settings, reload_settings, save_settings
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(tmp_path / "config"))
    reload_settings()
    try:
        settings = get_settings()
        settings.workshop_hours_per_week = 88.0
        save_settings(settings)
        QApplication.instance() or QApplication([])
        window = MainWindow(LIGHT)
        page = window.go_to_page("Quotation")
        page._show_promise(SimpleNamespace(builds=[]))
        assert page.promise_head.text().startswith("אפשר להבטיח התקנה מיום")
        assert "קצב ⁦16⁩ שעות ביום (לפי ההגדרות)" in page.promise_detail.text()
        assert "קצב המפעל" in page.promise_detail.toolTip()
        window.close()
    finally:
        monkeypatch.undo()
        reload_settings()
