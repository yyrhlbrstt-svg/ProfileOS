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


class TestWhatTheReviewFound:
    def frame_code(self) -> str:
        from profileos.projects.materials import propose

        return next(l.code for l in propose(a_job(), mass_per_metre=lambda c: None).lines
                    if l.code.endswith("FRAME"))

    def test_a_list_without_a_unit_is_read_per_metre_like_the_quotation(self, shop):
        from profileos.projects.materials import propose
        from profileos.quoting.pricelists import PriceRow, default_price_lists

        code = self.frame_code()
        book = default_price_lists()
        edition = book.record([PriceRow(code, 40.0, "")], supplier="קליל", source="x",
                              discount_pct=20)
        book.accept(edition.edition_id)
        frame = next(l for l in propose(a_job(), mass_per_metre=lambda c: None).lines
                     if l.code == code)
        assert frame.unit_price == pytest.approx(40.0 * 0.8 * frame.length_mm / 1000)

    def test_a_price_per_bar_when_the_list_says_so(self, shop):
        from profileos.projects.materials import propose

        code = self.frame_code()
        price_list({code: (180.0, "מוט")})
        frame = next(l for l in propose(a_job(), mass_per_metre=lambda c: None).lines
                     if l.code == code)
        assert frame.unit_price == pytest.approx(180.0 * 0.8)

    def test_what_the_bill_could_not_size_is_said(self, shop, monkeypatch):
        from profileos.projects import materials
        from profileos.quoting import bom as bom_module

        real = bom_module.build_bom

        def warned(*args, **kwargs):
            bill = real(*args, **kwargs)
            bill.warnings.append("W1: accessories not sized (תריס)")
            return bill

        monkeypatch.setattr(bom_module, "build_bom", warned)
        proposal = materials.propose(a_job(), mass_per_metre=lambda c: None)
        assert any("accessories not sized" in w for w in proposal.warnings)

    def test_an_order_from_another_supplier_keeps_the_price_warning(self, shop, monkeypatch):
        from types import SimpleNamespace

        from profileos.quoting import pricelists
        from profileos.quoting.pricelists import PriceRow, default_price_lists, impact

        book = default_price_lists()
        first = book.record([PriceRow("F", 40.0, "מטר")], supplier="קליל", source="א")
        book.accept(first.edition_id)
        second = book.record([PriceRow("F", 44.0, "מטר")], supplier="קליל", source="ב",
                             valid_from=(date.today() + timedelta(days=10)).isoformat())
        monkeypatch.setattr(pricelists, "job_materials", lambda job: [
            SimpleNamespace(code="F", quantity=100.0, unit=SimpleNamespace(value="m"))])
        job = a_job()
        job.material_orders.append(MaterialOrder(supplier="ספק הפרזול"))
        assert [c.job.job_id for c in impact(book, second, [job]).to_order] == [job.job_id]

    def test_only_won_jobs_are_told_to_order(self, shop):
        from profileos.insights import scan
        from profileos.projects import default_store
        from profileos.projects.model import StatusEvent

        job = a_job(status=JobStatus.IN_PRODUCTION)
        job.history = [StatusEvent(status=JobStatus.IN_PRODUCTION,
                                   at=(date.today() - timedelta(days=30)).isoformat())]
        default_store().save(job)
        assert not any("לא הוזמן" in i.title for i in scan())


def test_the_order_dialog_guards_what_is_sent(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from profileos.projects.materials import Proposal, ProposedLine
    from profileos.ui import money_dialogs

    QApplication.instance() or QApplication([])
    warned: list[str] = []
    monkeypatch.setattr(money_dialogs, "_warn", lambda parent, title, exc: warned.append(str(exc)))
    proposal = Proposal(lines=[
        ProposedLine("profile", "F", "משקוף", 4, "bar", 6000.0, 192.0, "x", "קליל"),
        ProposedLine("hardware", "H", "ידית", 6, "pc", 0.0, 25.0, "y", "ספק הפרזול"),
        ProposedLine("gasket", "G", "גומייה", 30, "m"),
    ])
    dialog = money_dialogs.MaterialOrderDialog(a_job(), proposal)
    dialog.supplier.setCurrentText("קליל")
    ticked = [dialog.table.item(r, 0).checkState() == Qt.CheckState.Checked for r in range(3)]
    # Another supplier's priced line is not sent to this one; unpriced goes along.
    assert ticked == [True, False, True]

    dialog.table.item(0, 3).setText("")
    dialog._finish(dialog.RECORD)
    assert warned and "F" in warned[0] and dialog.result() == 0

    dialog.table.item(0, 3).setText("5")
    dialog._finish(dialog.RECORD)
    assert [line.code for line in dialog.values()["lines"]] == ["F", "G"]


def test_an_order_moves_forward_only(monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from profileos.ui import money_dialogs

    QApplication.instance() or QApplication([])
    warned: list[str] = []
    monkeypatch.setattr(money_dialogs, "_warn", lambda parent, title, exc: warned.append(str(exc)))
    job = a_job()
    job.material_orders.append(MaterialOrder(supplier="קליל"))
    dialog = money_dialogs.MaterialOrdersDialog(job)
    dialog.table.selectRow(0)
    dialog._set("received")
    dialog._set("cancelled")
    assert dialog.changes == [(job.material_orders[0].order_id, "received")] and warned
    # The job itself is untouched until the page saves.
    assert job.material_orders[0].state is MaterialOrderState.SENT


def test_the_selection_follows_the_job_after_a_save(shop, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    from profileos.projects import default_store
    from profileos.ui import background, money_dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(background, "run_in_background", lambda work, done: done(work(), None))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: None)
    store = default_store()
    store.save(a_job(job_id="J-2026-0001"))
    store.save(a_job(job_id="J-2026-0002"))
    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    page.refresh()
    row = next(i for i, j in enumerate(page._jobs) if j.job_id == "J-2026-0001")
    page.jobs_table.selectRow(row)

    def record(self):
        self.supplier.setCurrentText("קליל")
        self._finish(self.RECORD)
        return self.result()

    monkeypatch.setattr(money_dialogs.MaterialOrderDialog, "exec", record)
    page.order_material()
    assert page._selected_job().job_id == "J-2026-0001"
    assert "J-2026-0001" in page.job_summary.text() and "הזמנה אחת" in page.job_summary.text()
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()


class TestExactBarsAndPrices:
    """The bar the supplier sells, and the price for that bar — nothing assumed."""

    @pytest.mark.parametrize("text,mm", [
        ("6", 6000.0), ("6.5", 6500.0), ("6,5", 6500.0), ("650", 6500.0), ("6500", 6500.0),
        ("7", 7000.0), ("50", None), ("9", None), ("", None), ("מוט", None),
    ])
    def test_a_bar_length_however_the_list_writes_it(self, text, mm):
        from profileos.quoting.pricelists import bar_length

        assert bar_length(text) == mm

    def test_the_length_column_is_read(self, tmp_path):
        from profileos.quoting.pricelists import rows_from_file

        path = tmp_path / "l.csv"
        path.write_text('מק"ט;מחיר;יחידה;אורך מוט\nF;210;מוט;6.5\nS;180;מוט;שש\n',
                        encoding="utf-8")
        rows, problems, matched = rows_from_file(path)
        assert "length" in matched
        assert {r.code: r.length_mm for r in rows} == {"F": 6500.0, "S": 0.0}
        assert any("S" in p and "לא ברור" in p for p in problems)

    def test_a_bar_price_of_known_length_quotes_per_metre_and_orders_per_bar(self, shop):
        from profileos.projects.materials import propose
        from profileos.quoting.pricelists import PriceRow, default_price_lists
        from profileos.quoting.suppliers import find_price

        job = a_job()
        code = next(l.code for l in propose(job, mass_per_metre=lambda c: None).lines
                    if l.code.endswith("FRAME"))
        book = default_price_lists()
        edition = book.record([PriceRow(code, 210.0, "מוט", "משקוף", length_mm=6500.0)],
                              supplier="קליל", source="x", discount_pct=20)
        book.accept(edition.edition_id)
        # A quotation counts metres: the bar's price spread over its length.
        assert find_price(code, 13.0, unit="m", currency="ILS")[1] == pytest.approx(
            13.0 * 210.0 / 6.5 * 0.8)
        proposal = propose(job, mass_per_metre=lambda c: None)
        frame = next(l for l in proposal.lines if l.code == code)
        # Cut from the supplier's 6.5 m bar, priced at exactly its bar price.
        assert frame.length_mm == 6500.0 and frame.length_source.startswith("קליל")
        assert frame.unit_price == pytest.approx(210.0 * 0.8)

    def test_a_suppliers_bar_length_said_once(self, shop):
        from profileos.projects.materials import propose
        from profileos.quoting.pricelists import PriceRow, default_price_lists

        job = a_job()
        code = next(l.code for l in propose(job, mass_per_metre=lambda c: None).lines
                    if l.code.endswith("FRAME"))
        book = default_price_lists()
        first = book.record([PriceRow(code, 40.0, "מטר")], supplier="קליל", source="א",
                            bar_length_mm=7000.0)
        book.accept(first.edition_id)
        second = book.record([PriceRow(code, 42.0, "מטר")], supplier="קליל", source="ב")
        assert second.bar_length_mm == 7000.0          # carried over
        frame = next(l for l in propose(job, mass_per_metre=lambda c: None).lines
                     if l.code == code)
        assert frame.length_mm == 7000.0
        with pytest.raises(Exception):
            book.record([PriceRow(code, 1.0, "מטר")], supplier="קליל", source="ג",
                        bar_length_mm=60.0)

    def test_without_a_length_the_general_setting_is_named(self, shop):
        from profileos.projects.materials import propose

        proposal = propose(a_job(), mass_per_metre=lambda c: None)
        assert any("הגדרת המפעל" in w for w in proposal.warnings)
        assert all(not l.length_source for l in proposal.lines if l.unit == "bar")

    def test_a_bar_price_with_no_length_is_flagged(self, shop):
        from profileos.projects.materials import propose
        from profileos.quoting.pricelists import PriceRow, default_price_lists

        job = a_job()
        code = next(l.code for l in propose(job, mass_per_metre=lambda c: None).lines
                    if l.code.endswith("FRAME"))
        book = default_price_lists()
        edition = book.record([PriceRow(code, 210.0, "מוט")], supplier="קליל", source="x")
        book.accept(edition.edition_id)
        proposal = propose(job, mass_per_metre=lambda c: None)
        frame = next(l for l in proposal.lines if l.code == code)
        assert frame.unit_price == pytest.approx(210.0)
        assert any("למוט, בלי אורך" in w for w in proposal.warnings)

    def test_two_suppliers_two_lengths_the_shop_decides(self, shop):
        from profileos.quoting.pricelists import PriceRow, bar_lengths, default_price_lists

        book = default_price_lists()
        for supplier, length in (("קליל", 6000.0), ("אקסטל", 6500.0)):
            edition = book.record([PriceRow("F", 40.0, "מטר", length_mm=length)],
                                  supplier=supplier, source="x")
            book.accept(edition.edition_id)
        lengths, _sources = bar_lengths(book)
        assert "F" not in lengths


def test_a_changed_quantity_is_priced_again(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from profileos.projects.materials import Proposal, ProposedLine
    from profileos.quoting.suppliers import PriceBreak, PriceEntry, Supplier, register_supplier
    from profileos.ui import money_dialogs

    QApplication.instance() or QApplication([])
    # A list with a quantity break: 10 handles or more are cheaper.
    supplier = Supplier(id="shop:hw", name="פרזול", currency="ILS", entries=[
        PriceEntry(code="H", price=25.0, unit="pc", metadata={"unit_known": True},
                   breaks=[PriceBreak(min_quantity=10, price=20.0)])])
    from profileos.core.registry import SUPPLIERS

    SUPPLIERS.add(supplier.id, supplier, source="shop")
    line = ProposedLine("hardware", "H", "ידית", 6, "pc")
    from profileos.projects.materials import price_line

    price_line(line)
    assert line.unit_price == pytest.approx(25.0)
    dialog = money_dialogs.MaterialOrderDialog(a_job(), Proposal(lines=[line]))
    dialog.supplier.setCurrentText("פרזול")
    dialog.table.item(0, 3).setText("12")
    [chosen] = dialog.chosen()
    assert chosen.quantity == 12 and chosen.unit_price == pytest.approx(20.0)
    assert "20.00" in dialog.table.item(0, 4).text()


def test_the_cutting_page_cuts_from_the_suppliers_bar(shop):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from profileos.quoting.pricelists import PriceRow, default_price_lists
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    window.page("Element").build_element()
    codes = {cut.profile_id for build in window.session.builds for cut in build.cuts}
    book = default_price_lists()
    edition = book.record([PriceRow(code, 40.0, "מטר", length_mm=6500.0) for code in codes],
                          supplier="קליל", source="x")
    book.accept(edition.edition_id)
    page = window.go_to_page("Nesting")
    page.run()
    report = window.session.nesting_report
    assert report.results
    assert {round(layout.stock_length) for result in report.results.values()
            for layout in result.layouts} == {6500}
    window.close()
    window.deleteLater()
    QCoreApplication.processEvents()
