"""The showroom: a busy shop's month, in its own folder, never the shop's own."""

from __future__ import annotations

import pytest


@pytest.fixture
def showroom(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "show" / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(tmp_path / "show" / "config"))
    monkeypatch.setenv("PROFILEOS_SHOWROOM", "1")
    reload_settings()
    yield tmp_path / "show"
    # The environment back as it was (the run's own settings folder), then
    # re-read: removing the variable alone keeps the folder last read.
    monkeypatch.undo()
    reload_settings()


def test_the_showroom_has_a_shops_month_in_it(showroom):
    from profileos.erp.collection import default_cheques
    from profileos.insights import scan
    from profileos.projects import JobStatus, default_store
    from profileos.projects.account import job_account
    from profileos.projects.blindframes import is_two_stage
    from profileos.showroom import build

    build(showroom)
    jobs = default_store().all()
    assert {job.status for job in jobs} == set(JobStatus)
    assert any(is_two_stage(job) for job in jobs)
    cheques = list(default_cheques())
    # The won jobs are covered to the shekel: deposit and cheques.
    for job in jobs:
        if job.status in (JobStatus.WON, JobStatus.IN_PRODUCTION):
            assert job_account(job, cheques).uncovered == pytest.approx(0, abs=0.5)
    keys = {insight.key.split(":")[0] for insight in scan()}
    assert {"bf-fix", "bf-wait"} <= keys
    assert not any(key.startswith("backup") for key in keys)


def test_it_is_built_only_in_its_own_folder(showroom, tmp_path, monkeypatch):
    from profileos.projects import default_store
    from profileos.showroom import build

    with pytest.raises(RuntimeError):
        build(tmp_path / "somewhere-else")
    with pytest.raises(RuntimeError):
        build(tmp_path)                          # an ancestor of the data folder
    build(showroom)
    jobs = len(default_store().all())
    with pytest.raises(RuntimeError, match="already holds records"):
        build(showroom)                          # never twice over the same folder
    assert len(default_store().all()) == jobs
    monkeypatch.delenv("PROFILEOS_SHOWROOM")
    with pytest.raises(RuntimeError):
        build(showroom)                          # only in a showroom window


def test_an_old_showroom_is_replaced_and_a_strange_folder_is_kept(tmp_path):
    from datetime import date, timedelta

    from profileos.core.storage import write_json
    from profileos.showroom import MARKER, _prepare

    folder = tmp_path / "ProfileOS הדגמה"
    folder.mkdir()
    write_json(folder / MARKER, {"built_on": date.today().isoformat()})
    assert _prepare(folder, date.today()) is False                 # today's: opened as is
    write_json(folder / MARKER, {"built_on": (date.today() - timedelta(days=3)).isoformat()})
    assert _prepare(folder, date.today()) is True
    assert (tmp_path / "ProfileOS הדגמה (ישן)" / MARKER).is_file() and not folder.exists()
    folder.mkdir()
    (folder / "something.txt").write_text("not ours")
    assert _prepare(folder, date.today()) is True
    kept = [p for p in tmp_path.iterdir() if "לא הושלם" in p.name]
    assert kept and (kept[0] / "something.txt").read_text() == "not ours"


def test_the_showroom_window_says_so(showroom, monkeypatch):
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("PROFILEOS_SHOWROOM", "1")
    window = MainWindow(LIGHT)
    assert "הדגמה" in window.windowTitle() and not hasattr(window, "showroom_button")
    window.close(); window.deleteLater()
    monkeypatch.delenv("PROFILEOS_SHOWROOM")
    window = MainWindow(LIGHT)
    assert "הדגמה" not in window.windowTitle()
    opened = []
    import profileos.showroom as showroom_module

    monkeypatch.setattr(showroom_module, "open_showroom", lambda folder=None: opened.append(1))
    assert window.open_showroom() and opened
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_the_showroom_window_opens_on_a_job_in_production(showroom):
    from PySide6.QtWidgets import QApplication

    from profileos.showroom import build, showcase_job
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    build(showroom)
    job_id = showcase_job()
    assert job_id == "J-2026-0148"
    window = MainWindow(LIGHT)
    assert window.open_showcase(job_id)
    assert window.session.job.job_id == job_id and window.session.builds
    assert not window.open_showcase(job_id)       # never over a job already open
    window.close()


def test_lost_jobs_are_dated_when_they_were_quoted(showroom):
    from datetime import date

    from profileos.projects import JobStatus, default_store
    from profileos.showroom import build

    build(showroom)
    lost = [job for job in default_store().all() if job.status is JobStatus.LOST]
    assert lost and all(job.quoted_on and job.quoted_on < date.today().isoformat()
                        for job in lost)


def test_the_showroom_shop_is_a_sample_shop_never_the_real_one(showroom, monkeypatch):
    import profileos.branding as branding
    from profileos.showroom import build

    monkeypatch.setattr(branding, "_active_brand_id", None)
    build(showroom)
    brand = branding.active_brand()
    assert brand.name == "אלומיניום לדוגמה" and "דאדי" not in brand.window_title()
