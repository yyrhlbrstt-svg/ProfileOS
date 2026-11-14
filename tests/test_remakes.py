"""Remakes: scrapped with a reason, back on the floor, counted by what repeats."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.core.errors import ProfileOSError
from profileos.mes.remakes import remake, remakes
from profileos.mes.store import load_work_order, order_from_dict, order_to_dict, save_work_order
from profileos.mes.tracking import ItemKind, ProductionItem, Stage, WorkOrder


def _order():
    order = WorkOrder(project_id="J-1", name="וילה")
    for n in range(3):
        order.add(ProductionItem(item_id=f"PC-{n}", kind=ItemKind.PROFILE_PIECE,
                                 description=f"משקוף W1 ⁦1280⁩ #{n}", project_id="J-1",
                                 barcode=f"BC-{n}"))
    order.find("PC-0").advance(Stage.CUT)
    return order


def test_a_scrapped_piece_goes_back_to_the_start_with_its_reason():
    order = _order()
    fresh = remake(order, "PC-0", reason="wrong_size", note="נחתך 1180", by="משה", cost=85,
                   on=date(2026, 11, 3))
    assert order.find("PC-0").stage is Stage.SCRAPPED
    assert "נחתך במידה שגויה — נחתך 1180" in order.find("PC-0").history[-1].note
    assert fresh.stage is Stage.PLANNED and fresh.description == order.find("PC-0").description
    assert fresh.metadata["remake"]["of"] == "PC-0" and len(order) == 4


def test_a_reason_is_needed_and_other_needs_words():
    order = _order()
    with pytest.raises(ProfileOSError, match="למה"):
        remake(order, "PC-1", reason="")
    with pytest.raises(ProfileOSError, match="מה קרה"):
        remake(order, "PC-1", reason="other")
    with pytest.raises(ProfileOSError, match="אין בפקודת"):
        remake(order, "PC-9", reason="broken")


def test_the_record_survives_the_file_and_a_merge(tmp_path):
    from profileos.projects import JobStore

    store = JobStore(tmp_path / "jobs")
    order = _order()
    save_work_order(order, "J-1", store)
    remake(order, "PC-1", reason="broken", on=date(2026, 11, 5))
    save_work_order(order, "J-1", store)
    again = load_work_order("J-1", store)
    assert any(item.metadata.get("remake") for item in again.items)
    assert order_from_dict(order_to_dict(again)).find("PC-1").stage is Stage.SCRAPPED


def test_the_month_is_counted_by_reason_and_unknown_cost_is_not_zero():
    order = _order()
    remake(order, "PC-0", reason="wrong_size", cost=85, on=date(2026, 11, 3))
    remake(order, "PC-1", reason="wrong_size", on=date(2026, 11, 4))
    remake(order, "PC-2", reason="broken", cost=240, on=date(2026, 10, 30))   # last month
    report = remakes([("J-1", order)], start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert report.count == 2 and report.known_cost == 85 and report.unpriced == 1
    assert report.by_reason()[0] == ("נחתך במידה שגויה", 2, 85)
    said = report.describe()
    assert "⁦2⁩ פריטים נפסלו" in said and "ל־⁦1⁩ לא נרשמה עלות" in said


def test_from_the_shop_floor_page(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.core.config import reload_settings
    from profileos.showroom import build, showcase_job
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    folder = tmp_path / "show"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(folder / "data"))
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(folder / "config"))
    monkeypatch.setenv("PROFILEOS_SHOWROOM", "1")
    reload_settings()
    try:
        QApplication.instance() or QApplication([])
        build(folder)
        window = MainWindow(LIGHT)
        assert window.open_showcase(showcase_job())
        page = window.go_to_page("Shop floor")
        page.refresh()
        before = len(window.session.work_order)
        page.item.setCurrentIndex(0)
        chosen = page.item.currentData()
        monkeypatch.setattr(dialogs.RemakeDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        monkeypatch.setattr(dialogs.RemakeDialog, "values",
                            lambda self: {"reason": "damaged", "note": "שריטה", "cost": 60.0})
        page.remake_item()
        on_disk = load_work_order(showcase_job())
        assert len(on_disk) == before + 1
        assert on_disk.find(chosen).stage is Stage.SCRAPPED
        assert "חזר לתחילת הרצפה" in page.scan_result.text()
        window.close()
    finally:
        monkeypatch.undo()
        reload_settings()
