"""Ordering a job's material: whole bars, priced from the list, sent and kept."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from profileos.elements.model import ElementSchedule, Opening
from profileos.projects import (
    JobFile,
    JobStatus,
    MaterialOrder,
    MaterialOrderLine,
    MaterialOrderState,
)


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


def a_job(status=JobStatus.WON, job_id="J-2026-0001") -> JobFile:
    job = JobFile(job_id=job_id, name="וילה", status=status, system_id="klil-7300")
    job.schedule = ElementSchedule(name="x", system_id="klil-7300", openings=[
        Opening(name="W1", width=1500, height=1200, quantity=4, system_id="klil-7300"),
    ])
    return job


def price_list(codes: dict[str, tuple[float, str]]) -> None:
    from profileos.quoting.pricelists import PriceRow, default_price_lists

    book = default_price_lists()
    edition = book.record([PriceRow(code, price, unit, f"פריט {code}")
                           for code, (price, unit) in codes.items()],
                          supplier="קליל", source="מחירון.xlsx", discount_pct=20)
    book.accept(edition.edition_id)


class TestTheProposal:
    def test_whole_bars_priced_from_the_list_after_the_discount(self, shop):
        from profileos.projects.materials import propose

        job = a_job()
        first = propose(job, mass_per_metre=lambda c: None)
        frame = next(line for line in first.lines if line.code.endswith("FRAME"))
        assert frame.unit == "bar" and frame.quantity == int(frame.quantity) > 0
        assert frame.length_mm in (6000.0, 6500.0)
        assert frame.unit_price == 0 and frame in first.unpriced

        price_list({frame.code: (40.0, "מטר"), "GK-IN": (3.0, "מטר")})
        priced = propose(job, mass_per_metre=lambda c: None)
        frame = next(line for line in priced.lines if line.code == frame.code)
        # 40 ₪ a metre, 20% off, a whole bar of its stock length.
        assert frame.unit_price == pytest.approx(40.0 * 0.8 * frame.length_mm / 1000)
        assert frame.supplier == "קליל" and frame.price_source == "מחירון.xlsx"
        assert priced.suppliers() == ["קליל"]

    def test_a_price_per_kilo_with_the_catalogue_weight(self, shop):
        from profileos.projects.materials import propose

        job = a_job()
        code = next(l.code for l in propose(job, mass_per_metre=lambda c: None).lines
                    if l.code.endswith("FRAME"))
        price_list({code: (30.0, 'ק"ג')})
        frame = next(l for l in propose(job, mass_per_metre=lambda c: 1.5).lines if l.code == code)
        assert frame.unit_price == pytest.approx(30.0 * 0.8 * 1.5 * frame.length_mm / 1000)

    def test_gasket_in_whole_metres_and_no_glass(self, shop):
        from profileos.projects.materials import propose

        lines = propose(a_job(), mass_per_metre=lambda c: None).lines
        gaskets = [line for line in lines if line.category == "gasket"]
        assert gaskets and all(line.quantity == int(line.quantity) for line in gaskets)
        assert all(line.description and "gasket" not in line.description for line in gaskets)
        assert not [line for line in lines if line.category == "glass"]

    def test_a_job_with_nothing_drawn(self):
        from profileos.projects.materials import propose

        job = JobFile(job_id="J", name="x")
        proposal = propose(job)
        assert proposal.lines == [] and proposal.warnings


class TestKeptOnTheJob:
    def test_an_order_round_trips_with_the_job(self):
        job = a_job()
        job.material_orders.append(MaterialOrder(
            supplier="קליל", needed_by="2026-11-01",
            lines=[MaterialOrderLine(code="F", quantity=6, unit="מוטות", length_mm=6000,
                                     unit_price=192.0, price_source="מחירון.xlsx"),
                   MaterialOrderLine(code="X", quantity=2)],
        ))
        again = JobFile.model_validate_json(job.model_dump_json())
        order = again.material_orders[0]
        assert order.total == pytest.approx(1152.0) and len(order.unpriced) == 1
        assert again.material_ordered and order.open

    def test_a_cancelled_order_is_not_an_order(self):
        job = a_job()
        job.material_orders.append(MaterialOrder(supplier="קליל",
                                                 state=MaterialOrderState.CANCELLED))
        assert not job.material_ordered

    def test_the_message_to_the_supplier(self):
        from profileos.projects.materials import ProposedLine, order_message, to_order

        order = to_order([
            ProposedLine("profile", "KL-7301", "משקוף", 6, "bar", 6000.0, 192.0),
            ProposedLine("gasket", "GK-IN", "גומיית זיגוג פנימית", 34, "m"),
        ], supplier="קליל", needed_by="2026-11-01", note="צבע לבן 9016")
        text = order_message(order, brand_name="דאדי בע״מ", job=a_job())
        assert "⁦6⁩ מוטות ⁦KL-7301⁩ משקוף — ⁦6⁩ מ׳" in text
        assert "⁦34⁩ מ׳ ⁦GK-IN⁩" in text
        assert "⁦01/11/2026⁩" in text and "צבע לבן 9016" in text
        assert order.order_id in text


class TestTheHomePage:
    def test_won_and_not_ordered_then_late(self, shop):
        from profileos.insights import scan
        from profileos.projects import default_store

        store = default_store()
        job = a_job()
        store.save(job)
        from profileos.projects.model import StatusEvent

        old = a_job(job_id="J-2026-0002")
        old.history = [StatusEvent(status=JobStatus.WON,
                                   at=(date.today() - timedelta(days=5)).isoformat())]
        store.save(old)
        titles = [i.title for i in scan()]
        assert any("לא הוזמן" in t for t in titles)

        for each in (job, old):
            each.material_orders.append(MaterialOrder(
                supplier="קליל",
                needed_by=(date.today() - timedelta(days=1)).isoformat()))
            store.save(each)
        found = scan()
        assert not any("עוד לא הוזמן" in i.title for i in found)
        late = next(i for i in found if "לא הגיעו בזמן" in i.title)
        assert "קליל" in late.detail

    def test_an_ordered_job_is_not_told_to_order_before_an_increase(self, shop, monkeypatch):
        from types import SimpleNamespace

        from profileos.quoting import pricelists
        from profileos.quoting.pricelists import PriceRow, default_price_lists, impact

        book = default_price_lists()
        first = book.record([PriceRow("F", 40.0, "מטר")], supplier="קליל", source="א")
        book.accept(first.edition_id)
        later = (date.today() + timedelta(days=10)).isoformat()
        second = book.record([PriceRow("F", 44.0, "מטר")], supplier="קליל", source="ב",
                             valid_from=later)
        monkeypatch.setattr(pricelists, "job_materials", lambda job: [
            SimpleNamespace(code="F", quantity=100.0, unit=SimpleNamespace(value="m"))])
        job = a_job()
        assert [c.job.job_id for c in impact(book, second, [job]).to_order] == [job.job_id]
        job.material_orders.append(MaterialOrder(supplier="קליל"))
        assert impact(book, second, [job]).to_order == []


def test_ordering_from_the_projects_page(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.ui import background, money_dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(background, "run_in_background",
                        lambda work, done: done(work(), None))
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    default_store().save(a_job())

    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    page.refresh()
    page.jobs_table.selectRow(0)
    assert "עוד לא הוזמן" in page.job_summary.text()

    def answer(self):
        self.supplier.setCurrentText("קליל")
        # The rack already holds the beads: untick the first line.
        self.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table.item(1, 3).setText("7")
        self._finish(self.SEND)
        return self.result()

    monkeypatch.setattr(money_dialogs.MaterialOrderDialog, "exec", answer)
    page.order_material()
    saved = default_store().load("J-2026-0001")
    [order] = saved.material_orders
    assert order.supplier == "קליל" and order.lines[0].quantity == 7
    assert opened and "wa.me" in opened[0]

    def arrived(self):
        self.table.selectRow(0)
        self._set("received")
        return 1

    monkeypatch.setattr(money_dialogs.MaterialOrdersDialog, "exec", arrived)
    page.jobs_table.selectRow(0)
    page.manage_material_orders()
    order = default_store().load("J-2026-0001").material_orders[0]
    assert order.state is MaterialOrderState.RECEIVED and order.received_on
    page.jobs_table.selectRow(0)
    assert "הכול הגיע" in page.job_summary.text()
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()
