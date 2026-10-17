"""Work on screen is never only on screen.

Every change is written a moment later — to the open job, or to a draft when
no job is open — and the next launch offers a draft back. An opening that no
longer builds is kept in the job rather than dropped by the next save.
"""

from __future__ import annotations

import os

import pytest

from profileos.elements import ElementBuilder, Opening
from profileos.elements.model import ElementSchedule

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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


def _schedule(*openings: Opening) -> ElementSchedule:
    return ElementSchedule(name="בדיקה", openings=list(openings))


class TestDraft:
    def test_a_draft_comes_back_as_it_was_written(self, shop):
        from profileos.projects.draft import read_draft, write_draft

        write_draft(_schedule(Opening(name="W-01", width=1200, height=1400)))
        found = read_draft()
        assert found is not None
        schedule, saved_at = found
        assert [o.name for o in schedule.openings] == ["W-01"]
        assert saved_at

    def test_no_draft_is_no_offer(self, shop):
        from profileos.projects.draft import read_draft

        assert read_draft() is None

    def test_a_declined_draft_is_kept_aside_not_deleted(self, shop):
        from profileos.projects.draft import read_draft, set_draft_aside, write_draft

        write_draft(_schedule(Opening(name="W-01", width=1200, height=1400)))
        aside = set_draft_aside()
        assert aside is not None and aside.is_file()
        assert read_draft() is None


class TestSessionKeepsEverything:
    def test_an_opening_that_no_longer_builds_is_kept(self, shop):
        from profileos.ui.session import Session

        session = Session()
        problems = session.load_schedule(_schedule(
            Opening(name="W-01", width=1200, height=1400),
            Opening(name="קטן", width=50, height=50),
        ))
        assert problems and "קטן" in problems[0]
        assert [o.name for o in session.to_schedule().openings] == ["W-01", "קטן"]

    def test_saving_the_design_keeps_a_status_changed_elsewhere(self, shop):
        from profileos.projects import JobStatus, default_store
        from profileos.ui.session import Session

        store = default_store()
        job = store.create(name="דירה")
        store.save(job)

        session = Session()
        session.job = store.load(job.job_id)
        session.add_build(ElementBuilder().build(Opening(name="W-01", width=1200, height=1400)))

        # Meanwhile another screen moves the job on.
        other = store.load(job.job_id)
        other.advance(JobStatus.QUOTED, "הצעה נשלחה")
        store.save(other)

        session.save_design_to_job()
        saved = store.load(job.job_id)
        assert saved.status is JobStatus.QUOTED
        assert [o.name for o in saved.schedule.openings] == ["W-01"]


class TestWindowAutosave:
    @pytest.fixture
    def window(self, shop):
        pytest.importorskip("PySide6")
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtWidgets import QApplication

        from profileos.ui.main_window import MainWindow
        from profileos.ui.theme import LIGHT

        QApplication.instance() or QApplication([])
        win = MainWindow(LIGHT)
        yield win
        win.close()
        win.deleteLater()
        QCoreApplication.processEvents()

    def test_work_with_no_project_is_written_as_a_draft(self, window):
        from profileos.projects.draft import read_draft

        window.session.add_build(
            ElementBuilder().build(Opening(name="W-01", width=1200, height=1400))
        )
        window.autosave()
        found = read_draft()
        assert found is not None
        assert [o.name for o in found[0].openings] == ["W-01"]
        assert "טיוטה" in window.sidebar.safety.text()

    def test_work_in_a_project_goes_into_the_project(self, window):
        from profileos.projects import default_store
        from profileos.projects.draft import read_draft

        store = default_store()
        job = store.create(name="דירה")
        store.save(job)
        window.session.job = job
        window.session.add_build(
            ElementBuilder().build(Opening(name="W-02", width=900, height=1200))
        )
        window.autosave()
        assert [o.name for o in store.load(job.job_id).schedule.openings] == ["W-02"]
        assert read_draft() is None
        assert "נשמרו" in window.sidebar.safety.text()
