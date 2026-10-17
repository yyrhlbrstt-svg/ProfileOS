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
        assert window._save_state[0] == "ok"
        # Saved, but never backed up: the sidebar says the second part.
        assert "גיבוי" in window.sidebar.safety.text()


def test_the_window_opens_with_damaged_books_and_leaves_them_alone(shop):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    names = ("tasks.json", "timesheets.json", "exchange_rates.json",
             "stocktakes.json", "cheques.json", "service_calls.json")
    for name in names:
        (shop / name).write_text("{ cut off", encoding="utf-8")

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    for page in window.pages:
        window.go_to(window.pages.index(page))
        QCoreApplication.processEvents()
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()
    for name in names:
        assert (shop / name).read_text(encoding="utf-8") == "{ cut off"


def test_a_damaged_book_refuses_to_save(shop):
    from profileos.core.storage import StorageError
    from profileos.projects.followups import Kind, default_tasks

    (shop / "tasks.json").write_text("{ cut off", encoding="utf-8")
    book = default_tasks()
    assert book.damaged is not None
    with pytest.raises(StorageError):
        book.create(Kind.OTHER, "x")
    assert (shop / "tasks.json").read_text(encoding="utf-8") == "{ cut off"


def test_opening_a_job_never_deletes_an_unfiled_draft(shop, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from profileos.projects import default_store
    from profileos.projects.draft import drafts_set_aside, read_draft
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    store = default_store()
    job = store.create(name="קיים")
    store.save(job)

    window = MainWindow(LIGHT)
    window.autosave_enabled = True
    window.session.add_build(
        ElementBuilder().build(Opening(name="W-DRAFT", width=1200, height=1400))
    )
    window.autosave()
    assert read_draft() is not None

    # The person answers "keep it aside".
    def answer(box):
        for button in box.buttons():
            if button.text() == "שמור בצד כטיוטה":
                box.clickedButton = lambda b=button: b
        return 0

    monkeypatch.setattr(QMessageBox, "exec", answer)
    page = window.go_to_page("Projects")
    page.refresh()
    row = next(i for i, j in enumerate(page._jobs) if j.job_id == job.job_id)
    page.jobs_table.setCurrentCell(row, 0)
    page.open_job()
    window.autosave()

    aside = drafts_set_aside()
    assert aside, "the unfiled design was deleted"
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


def test_switching_jobs_writes_what_was_pending_for_the_first(shop):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    store = default_store()
    a, b = store.create(name="א"), store.create(name="ב")
    store.save(a)
    b = store.create(name="ב")
    store.save(b)

    window = MainWindow(LIGHT)
    window.autosave_enabled = True
    window.session.set_job(store.load(a.job_id))
    window.session.add_build(ElementBuilder().build(Opening(name="A-1", width=900, height=900)))
    assert window._autosave_timer.isActive()   # not yet written
    window.session.set_job(store.load(b.job_id))
    assert [o.name for o in store.load(a.job_id).schedule.openings] == ["A-1"]
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()
