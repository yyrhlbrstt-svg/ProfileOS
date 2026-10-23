"""The floor's scans outlive the window, and two screens never erase each other."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from profileos.core.storage import StorageError
from profileos.mes import ItemKind, ProductionItem, Stage, WorkOrder
from profileos.mes.store import (
    load_work_order, merge, save_work_order, scans, work_order_path,
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


def _order(order_id: str = "WO-1") -> WorkOrder:
    order = WorkOrder(work_order_id=order_id, project_id="J-1", name="וילה")
    for n in (1, 2):
        order.add(ProductionItem(item_id=f"W1-P0{n}", kind=ItemKind.PROFILE_PIECE,
                                 description=f"קורה {n}", project_id="J-1"))
    return order


def test_a_scan_is_on_disk_and_reads_back_whole(shop):
    order = _order()
    order.scan("W1-P01", Stage.CUT, operator="יוסי", station="מסור")
    save_work_order(order, "J-1")
    again = load_work_order("J-1")
    item = again.find("W1-P01")
    assert item.stage is Stage.CUT and item.history[-1].operator == "יוסי"
    assert again.find("W1-P02").stage is Stage.PLANNED and scans(again) == 1


def test_two_screens_scanning_the_same_order_keep_both(shop):
    save_work_order(_order(), "J-1")
    office, tablet = load_work_order("J-1"), load_work_order("J-1")
    office.scan("W1-P01", Stage.CUT, station="משרד")
    tablet.scan("W1-P02", Stage.CUT, station="טאבלט")
    save_work_order(office, "J-1")
    save_work_order(tablet, "J-1")
    disk = load_work_order("J-1")
    assert disk.find("W1-P01").stage is Stage.CUT
    assert disk.find("W1-P02").stage is Stage.CUT


def test_the_latest_scan_decides_the_stage(shop):
    disk, mine = _order(), _order()
    disk.find("W1-P01").advance(Stage.CUT)
    later = mine.find("W1-P01")
    later.advance(Stage.CUT)
    later.advance(Stage.MACHINED)
    later.history[-1] = type(later.history[-1])(
        stage=Stage.MACHINED, at=datetime.now(timezone.utc) + timedelta(minutes=5))
    merged = merge(disk, mine)
    assert merged.find("W1-P01").stage is Stage.MACHINED


def test_a_new_release_keeps_the_old_order_beside_it(shop):
    first = _order("WO-1")
    first.scan("W1-P01", Stage.CUT)
    save_work_order(first, "J-1")
    save_work_order(_order("WO-2"), "J-1", replace=True)
    path = work_order_path("J-1")
    kept = json.loads(path.with_name("work_order-WO-1.json").read_text(encoding="utf-8"))
    assert kept["work_order_id"] == "WO-1"
    assert load_work_order("J-1").work_order_id == "WO-2"


def test_a_screen_on_the_old_order_is_refused_not_merged_in(shop):
    save_work_order(_order("WO-1"), "J-1")
    stale = load_work_order("J-1")
    save_work_order(_order("WO-2"), "J-1", replace=True)
    with pytest.raises(Exception, match="שוחררה מחדש"):
        save_work_order(stale, "J-1")


def test_a_damaged_order_is_refused_not_read_as_none(shop):
    path = work_order_path("J-1")
    path.parent.mkdir(parents=True)
    path.write_text('{"work_order_id": "WO', encoding="utf-8")
    with pytest.raises(StorageError):
        load_work_order("J-1")


def test_the_floor_page_remembers_and_moves_the_job_to_production(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QMessageBox

    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    job = JobFile(job_id="J-2026-0001", name="וילה", status=JobStatus.WON)
    default_store().save(job)

    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load("J-2026-0001"))
    window.page("Element").build_element()
    floor = window.go_to_page("Shop floor")
    floor.release()
    floor.item.setCurrentIndex(0)
    first = floor.item.currentData()
    floor.stage.setCurrentIndex(floor.stage.findData("cut"))
    floor.scan()
    assert default_store().load("J-2026-0001").status is JobStatus.IN_PRODUCTION
    window.close(); window.deleteLater(); QCoreApplication.processEvents()

    # Next morning: a fresh window, the same job — the cut is still there.
    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load("J-2026-0001"))
    floor = window.go_to_page("Shop floor")
    floor.refresh()
    assert window.session.work_order.find(first).stage is Stage.CUT

    # Releasing again asks first, and "no" leaves the order as it is.
    window.page("Element").build_element()
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    floor.release()
    assert asked and "⁦1⁩ סריקות" in asked[0]
    assert load_work_order("J-2026-0001").find(first).stage is Stage.CUT
    window.close(); window.deleteLater(); QCoreApplication.processEvents()


def test_the_tablet_works_from_the_order_saved_in_the_job(shop):
    """No one has to "load" an order onto the phone: it sees what was
    released in the jobs being built, and its scans land in the job."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.mobile.state import configure
    from profileos.projects import JobFile, JobStatus, default_store

    state = configure(registry_path=shop / "devices.json",
                      measurement_path=shop / "measurements.json")
    default_store().save(JobFile(job_id="J-1", name="וילה", status=JobStatus.IN_PRODUCTION))
    order = _order()
    order.find("W1-P01").barcode = "PRJ|J-1|W1|W1-P01"
    save_work_order(order, "J-1")

    client = TestClient(app)
    code = state.registry.issue_code("טאבלט מסור", scopes=("jobs",))
    paired = client.post("/m/api/pair", json={"code": code.code}).json()
    headers = {"X-Device-Id": paired["device_id"], "X-Device-Token": paired["token"]}

    floor = client.get("/m/api/jobs", headers=headers).json()
    assert floor["total"] == 2 and floor["name"] == "וילה"
    answer = client.post("/m/api/scan", headers=headers,
                         json={"payload": "PRJ|J-1|W1|W1-P01", "stage": "cut"})
    assert answer.status_code == 200, answer.text
    item = load_work_order("J-1").find("W1-P01")
    assert item.stage is Stage.CUT and item.history[-1].operator == "טאבלט מסור"


def test_a_lock_left_by_a_program_that_died_is_taken_over(shop):
    import time as _time

    path = work_order_path("J-1")
    path.parent.mkdir(parents=True)
    lock = path.with_name(path.name + ".lock")
    lock.write_text("99999")
    old = _time.time() - 120
    os.utime(lock, (old, old))
    save_work_order(_order(), "J-1")
    assert load_work_order("J-1") is not None and not lock.exists()


def test_another_jobs_order_is_not_left_on_screen_to_scan_into(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    for job_id in ("J-A", "J-B"):
        default_store().save(JobFile(job_id=job_id, name=job_id, status=JobStatus.WON))
    window = MainWindow(LIGHT)
    window.session.set_job(default_store().load("J-A"))
    window.page("Element").build_element()
    floor = window.go_to_page("Shop floor")
    floor.release()
    window.session.set_job(default_store().load("J-B"))
    floor.refresh()
    assert window.session.work_order is None and floor.item.count() == 0
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
