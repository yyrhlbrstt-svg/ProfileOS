"""The showroom: a busy shop's month, in its own folder, never the shop's own."""

from __future__ import annotations

import pytest


@pytest.fixture
def showroom(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "show" / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(tmp_path / "show" / "config"))
    reload_settings()
    yield tmp_path / "show"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    monkeypatch.delenv("PROFILEOS_CONFIG_DIR", raising=False)
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
    assert {job.status for job in jobs} == set(JobStatus) - {JobStatus.LOST}
    assert any(is_two_stage(job) for job in jobs)
    cheques = list(default_cheques())
    # The won jobs are covered to the shekel: deposit and cheques.
    for job in jobs:
        if job.status in (JobStatus.WON, JobStatus.IN_PRODUCTION):
            assert job_account(job, cheques).uncovered == pytest.approx(0, abs=0.5)
    keys = {insight.key.split(":")[0] for insight in scan()}
    assert {"bf-fix", "bf-wait"} <= keys
    assert not any(key.startswith("backup") for key in keys)


def test_it_is_built_only_in_its_own_folder(showroom, tmp_path):
    from profileos.showroom import build

    with pytest.raises(RuntimeError):
        build(tmp_path / "somewhere-else")


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
