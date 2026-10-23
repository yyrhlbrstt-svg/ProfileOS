"""The offcut rack is used before a new bar is bought, and kept true after the cut."""

from __future__ import annotations

import os

import pytest

from profileos.models.orders import RemnantBar
from profileos.nesting.rack import (
    add_offcuts, change_rack, load_rack, rack_for, record_cut, reserve,
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


def _job(job_id="J-1", width=2000.0, height=1400.0):
    from profileos.elements.model import ElementSchedule, Opening
    from profileos.projects import JobFile, JobStatus

    job = JobFile(job_id=job_id, name="וילה", status=JobStatus.WON, system_id="klil-7300")
    job.schedule = ElementSchedule(name="x", system_id="klil-7300", openings=[
        Opening(name="W1", width=width, height=height, quantity=1, system_id="klil-7300")])
    return job


def _frame_code(job) -> str:
    from profileos.projects.materials import propose

    return next(l.code for l in propose(job, mass_per_metre=lambda c: None).lines
                if l.code.endswith("FRAME"))


def test_an_offcut_on_the_rack_means_one_bar_less_to_buy(shop):
    from profileos.projects.materials import propose

    job = _job()
    code = _frame_code(job)
    without = propose(job, mass_per_metre=lambda c: None)
    bars = sum(l.quantity for l in without.lines if l.code == code)
    add_offcuts([RemnantBar(profile_id=code, length=5_500)])
    planned = propose(job, mass_per_metre=lambda c: None, rack=rack_for(job.job_id))
    assert sum(l.quantity for l in planned.lines if l.code == code) == bars - 1
    assert [p for _r, p, _l in planned.from_rack] == [code]
    assert any("מהמדף" in w for w in planned.warnings)

    # Set aside for this job: another job does not count on it; this one does.
    reserve([planned.from_rack[0][0]], job_id=job.job_id)
    other = propose(_job("J-2"), mass_per_metre=lambda c: None, rack=rack_for("J-2"))
    assert not other.from_rack
    again = propose(job, mass_per_metre=lambda c: None, rack=rack_for(job.job_id))
    assert again.from_rack


def test_the_cut_takes_its_offcuts_off_the_rack_and_puts_the_new_ones_on(shop):
    from profileos.elements import collect_cut_items
    from profileos.models.orders import Project
    from profileos.nesting import nest_project
    from profileos.projects.materials import build_job

    job = _job()
    code = _frame_code(job)
    add_offcuts([RemnantBar(profile_id=code, length=5_500),
                 RemnantBar(profile_id=code, length=300)])          # too short to keep
    assert len(load_rack()) == 1
    builds, _ = build_job(job)
    report = nest_project(Project(name="x", items=collect_cut_items(builds)),
                          inventory=rack_for(job.job_id))
    taken, added = record_cut(report, job_id=job.job_id)
    assert taken == 1
    left = load_rack().all()
    assert all(o.length >= 500 for o in left) and len(left) == added
    assert all(o.source_project == job.job_id for o in left)


def test_a_damaged_rack_is_refused_and_left_alone(shop):
    from profileos.nesting.rack import rack_path

    rack_path().write_text('{"remnants": [', encoding="utf-8")
    with pytest.raises(Exception, match="מדף השאריות לא נקרא"):
        change_rack(lambda rack: rack.add(RemnantBar(profile_id="X", length=1000)))
    assert rack_path().read_text(encoding="utf-8") == '{"remnants": ['


def test_the_rack_from_the_cutting_page(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from profileos.ui.dialogs import RackDialog
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.page("Element").build_element()
    code = next(i.profile_id for b in window.session.builds for i in b.cut_items()
                if i.profile_id.endswith("FRAME"))

    rack = RackDialog(window)
    rack.code.setText(code)
    rack.length.setValue(6000)
    rack.count.setValue(2)
    assert rack.add() and rack.table.rowCount() == 1

    page = window.go_to_page("Nesting")
    page.run()
    assert page.stats is not None and len(load_rack().all()) == 1
    from profileos.nesting.rack import used_from_rack

    used = used_from_rack(window.session.nesting_report)
    assert used and used[0][1] == code
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    taken, _added = page.record_cut()
    assert taken == len(used)
    remaining = sum(o.quantity for o in load_rack().all() if o.length == 6000)
    assert remaining == 2 - len(used)
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_offcuts_set_aside_for_a_combined_order_can_be_cut_by_its_jobs(shop):
    from profileos.nesting.rack import release

    add_offcuts([RemnantBar(profile_id="F", length=5_000)])
    [offcut] = load_rack().all()
    assert reserve([offcut.remnant_id], job_id="CO-1", jobs=["J-A", "J-B"]) == 1
    assert rack_for("J-B").for_profile("F") and rack_for("J-A").for_profile("F")
    assert not rack_for("J-C").for_profile("F")
    # Set aside already: a second order cannot count on it.
    assert reserve([offcut.remnant_id], job_id="J-C") == 0
    assert release("CO-1") == 1 and rack_for("J-C").for_profile("F")


def test_the_same_cut_is_recorded_on_the_rack_once(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.page("Element").build_element()
    page = window.go_to_page("Nesting")
    page.run()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page.record_cut()
    after_first = len(load_rack().all())
    told = []
    monkeypatch.setattr(page, "report", lambda exc, context="": told.append(context))
    assert page.record_cut() == (0, 0) and told == ["כבר נרשם"]
    assert len(load_rack().all()) == after_first
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
