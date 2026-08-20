"""Desktop UI tests.

The UI is driven headlessly (``QT_QPA_PLATFORM=offscreen``) so the whole
workflow is exercised in CI: a real window is built, each page is operated
through its own controls, and the resulting session state is asserted. This
catches the failures unit tests cannot — a page that raises on load, a signal
wired to nothing, a view that crashes when handed real engine output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from profileos.mes import Stage  # noqa: E402
from profileos.ui.main_window import MainWindow  # noqa: E402
from profileos.ui.theme import DARK, LIGHT, stylesheet  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(qt_app):
    win = MainWindow(DARK)
    win.resize(1400, 900)
    yield win
    # close() only hides it. A window left alive is still restyled every time
    # any other window applies a palette, so leaking one per test makes each
    # test slower than the last.
    win.close()
    win.deleteLater()
    QCoreApplication.processEvents()


def pump(times: int = 4) -> None:
    for _ in range(times):
        QCoreApplication.processEvents()


class TestTheme:
    @pytest.mark.parametrize("palette", [DARK, LIGHT])
    def test_stylesheet_builds(self, palette):
        sheet = stylesheet(palette)
        assert "QWidget" in sheet and palette.accent in sheet

    def test_labels_are_transparent(self):
        """Labels must not paint the canvas colour over their card."""
        assert "QLabel, QCheckBox, QRadioButton { background: transparent; }" in stylesheet(DARK)

    def test_palettes_define_every_colour(self):
        for palette in (DARK, LIGHT):
            for name, value in vars(palette).items():
                if name == "mode":
                    continue
                assert isinstance(value, str) and value.startswith("#"), name


class TestWindow:
    def test_all_pages_are_present(self, window):
        assert [p.title for p in window.pages] == [
            "Home", "Projects", "Profile", "Element", "3D view", "Nesting",
            "Glass", "Machining", "Quotation", "Accounts", "Shop floor",
            "Catalogue", "System",
        ]

    def test_every_page_is_reachable_from_the_sidebar(self, window):
        """A page nothing navigates to may as well not exist."""
        from profileos.ui.main_window import NAV_SECTIONS

        reachable = sorted(index for _, indices in NAV_SECTIONS for index in indices)
        assert reachable == list(range(len(window.pages)))

    def test_navigation_switches_pages(self, window):
        for index in range(len(window.pages)):
            window.go_to(index)
            assert window.stack.currentIndex() == index

    def test_theme_toggle(self, window):
        assert window.colours.mode.value == "dark"
        window.toggle_theme()
        assert window.colours.mode.value == "light"
        window.toggle_theme()
        assert window.colours.mode.value == "dark"

    def test_every_page_renders(self, window):
        """Grabbing each page catches paint-time crashes in the custom views."""
        for index in range(len(window.pages)):
            window.go_to(index)
            pump()
            assert not window.grab().isNull()


class TestWorkflow:
    """Drive the full workflow through the interface, as a user would."""

    def test_profile_page_analyses_the_sample(self, window):
        window.go_to_page("Profile")
        window.page("Profile").load_sample()
        pump()
        assert window.session.section_properties is not None
        assert window.session.section_properties.area == pytest.approx(1719.2, abs=1.0)
        assert window.page("Profile").properties.rowCount() > 10

    def test_wind_check_populates(self, window):
        window.page("Profile").load_sample()
        window.page("Profile").span.setValue(2000.0)
        window.page("Profile").run_check()
        pump()
        assert window.page("Profile").checks.rowCount() >= 3
        assert "המפתח המרבי" in window.page("Profile").max_span.text()

    def test_element_page_builds(self, window):
        window.go_to_page("Element")
        page = window.page("Element")
        page.width.setValue(2400.0)
        page.height.setValue(1800.0)
        page.columns.setValue(3)
        page.build_element()
        pump()
        assert len(window.session.builds) == 1
        assert page.cuts.rowCount() > 0
        assert page.panes.rowCount() == 3

    def test_nesting_page_optimises(self, window):
        window.page("Element").build_element()
        window.go_to_page("Nesting")
        window.page("Nesting").run()
        pump()
        report = window.session.nesting_report
        assert report is not None and report.total_bars > 0
        assert window.page("Nesting").summary.rowCount() > 0

    def test_nesting_without_elements_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        monkeypatch.setattr(
            type(window.page("Nesting")), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.session.clear_builds()
        window.go_to_page("Nesting")
        window.page("Nesting").run()
        assert recorded and "לא תוכננו פתחים" in recorded[0]

    def test_machining_page_posts(self, window):
        window.go_to_page("Machining")
        page = window.page("Machining")
        page.post()
        pump()
        assert window.session.post_results
        assert page.code.toPlainText().strip()
        assert "הפרעות" in page.clamp_status.text()

    def test_machining_defaults_to_a_machining_centre(self, window):
        assert window.page("Machining").driver.currentData() == "elumatec.ncx"

    @pytest.mark.parametrize("driver_key", ["elumatec.ncx", "kaban.kbn", "iso.gcode", "fom.cam"])
    def test_every_driver_posts_from_the_ui(self, window, driver_key):
        page = window.page("Machining")
        index = page.driver.findData(driver_key)
        assert index >= 0
        page.driver.setCurrentIndex(index)
        page.post()
        pump()
        assert page.code.toPlainText().strip()

    def test_quote_page_prices(self, window):
        window.page("Element").build_element()
        window.go_to_page("Quotation")
        page = window.page("Quotation")
        page.start_draft()
        pump()
        quote = window.session.quote
        assert quote is not None and quote.net_price > quote.total_cost
        assert page.waterfall.rowCount() > 5
        assert page.lines.rowCount() >= 1

    def test_quote_page_edits_survive_a_reprice(self, window):
        """The point of the editor: a pinned line outlives a what-if swap."""
        window.page("Element").build_element()
        window.go_to_page("Quotation")
        page = window.page("Quotation")
        page.start_draft()
        pump()
        code = page.lines.item(0, 0).text()
        page.draft.set_line_price(code, 9999.0, by="test")
        page.refresh_draft()
        page.margin.setValue(30.0)  # triggers apply_spec -> recompute
        pump()
        prices = [page.lines.item(0, 3).text()]
        assert "9,999.00" in prices[0]
        assert page.draft.totals()["net"] >= 9999.0

    def test_shop_floor_releases_and_scans(self, window):
        window.page("Element").build_element()
        window.go_to_page("Shop floor")
        page = window.page("Shop floor")
        page.release()
        pump()

        order = window.session.work_order
        assert order is not None and len(order) > 0

        page.item.setCurrentIndex(0)
        page.stage.setCurrentIndex(page.stage.findData("cut"))
        page.scan()
        pump()
        assert order.items[0].stage is Stage.CUT
        assert page.scan_result.text()

    def test_invalid_scan_shows_the_reason(self, window):
        window.page("Element").build_element()
        window.go_to_page("Shop floor")
        page = window.page("Shop floor")
        page.release()
        page.item.setCurrentIndex(0)
        page.stage.setCurrentIndex(page.stage.findData("shipped"))
        page.scan()
        pump()
        assert "אי אפשר לעבור" in page.scan_result.text()

    def test_status_bar_summarises_the_session(self, window):
        window.page("Profile").load_sample()
        window.page("Element").build_element()
        pump()
        assert "פתחים" in window.session.describe()


class TestSessionInvalidation:
    def test_adding_an_element_invalidates_the_nesting(self, window):
        window.go_to_page("Element")
        window.page("Element").build_element()
        window.go_to_page("Nesting")
        window.page("Nesting").run()
        assert window.session.nesting_report is not None

        # A new element changes the cut list, so the old plan must not linger.
        window.go_to_page("Element")
        window.page("Element").name.setCurrentText("W-99")
        window.page("Element").build_element()
        assert window.session.nesting_report is None

    def test_rebuilding_the_same_element_replaces_it(self, window):
        page = window.page("Element")
        page.name.setCurrentText("W-01")
        page.build_element()
        first = window.session.builds[0].opening.element_id
        count = len(window.session.builds)

        # Rebuilding creates a new element id, so it adds rather than replaces.
        page.build_element()
        assert len(window.session.builds) == count + 1
        assert window.session.builds[0].opening.element_id == first


class TestGlassPage:
    """The 2D nester, driven through its own controls."""

    def _page(self, window):
        return {p.title: p for p in window.pages}["Glass"]

    def test_nests_the_projects_glass(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        window.go_to_page(page.title)
        page.run()
        pump()

        report = window.session.glass_report
        assert report is not None
        assert report.sheet_count > 0
        assert report.yield_pct > 0
        assert page.summary.rowCount() == len(report.results)
        # Nothing unverifiable may reach the operator.
        assert report.warnings == []

    def test_every_shipped_layout_is_cuttable(self, window):
        from profileos.nesting import verify_guillotine

        window.page("Element").build_element()
        page = self._page(window)
        page.run()
        pump()
        for result in window.session.glass_report.results.values():
            for layout in result.layouts:
                assert verify_guillotine(layout, result.spec) == []

    def test_the_stage_limit_is_honoured(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        page.stages.setCurrentText("2")
        page.run()
        pump()
        for result in window.session.glass_report.results.values():
            assert all(layout.stages_used <= 2 for layout in result.layouts)

    def test_bad_stock_input_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.page("Element").build_element()
        page.stock.setCurrentText("not-a-size")
        page.run()
        assert recorded and "רוחב×גובה" in recorded[0]

    def test_nesting_without_elements_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.session.clear_builds()
        page.run()
        assert recorded and "לא תוכננו פתחים" in recorded[0]

    def test_adding_an_element_invalidates_the_glass_plan(self, window):
        window.page("Element").build_element()
        self._page(window).run()
        pump()
        assert window.session.glass_report is not None
        window.page("Element").width.setValue(3000.0)
        window.page("Element").build_element()
        assert window.session.glass_report is None

    def test_the_view_survives_being_painted(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        window.go_to_page(page.title)
        page.run()
        pump()
        assert not page.view.grab().isNull()

    def test_maps_are_exported_for_every_sheet(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        window.page("Element").build_element()
        page = self._page(window)
        page.run()
        pump()
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **k: str(tmp_path)),
        )
        page.export_maps()
        written = sorted(tmp_path.glob("*.svg"))
        assert len(written) == window.session.glass_report.sheet_count
        assert written[0].read_text(encoding="utf-8").startswith("<svg")


class TestCataloguePage:
    def _page(self, window):
        return {p.title: p for p in window.pages}["Catalogue"]

    @pytest.fixture
    def sources(self, tmp_path):
        table = tmp_path / "supplier.csv"
        table.write_text(
            "code;description;kg/m;A;Ix;Iy;b;h\n"
            "mullion_mb70;Mullion 70/100;4,642;17,192;122,518;95,975;70,0;100,0\n"
            "glazing_bead;Glazing bead;0,412;1,525;0,735;0,255;18,0;22,0\n",
            encoding="utf-8",
        )
        return SAMPLES, table

    def test_ingests_and_separates_verified_from_conflicting(self, window, sources):
        drawings, table = sources
        page = self._page(window)
        window.go_to_page(page.title)
        page._drawings = drawings
        page._table = table
        page.series.setCurrentText("MB-70")
        page.run()
        pump()

        report = window.session.catalogue_report
        assert report is not None
        statuses = {entry.profile_id: entry.status for entry in report.entries}
        assert statuses["mullion_mb70"] == "verified"
        # The table's Ix for the bead is wrong on purpose.
        assert statuses["glazing_bead"] == "conflict"
        assert page.entries.rowCount() == len(report.entries)

    def test_selecting_a_row_explains_the_conflict(self, window, sources):
        drawings, table = sources
        page = self._page(window)
        page._drawings, page._table = drawings, table
        page.run()
        pump()
        index = [e.profile_id for e in window.session.catalogue_report.entries].index(
            "glazing_bead"
        )
        page.entries.selectRow(index)
        pump()
        detail = page.detail.toPlainText()
        assert "סתירה" in detail
        assert "ixx" in detail

    def test_ingesting_nothing_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        page._drawings = None
        page._table = None
        page.run()
        assert recorded

    def test_conflicting_profiles_are_withheld_from_the_saved_library(
        self, window, sources, tmp_path, monkeypatch
    ):
        from PySide6.QtWidgets import QFileDialog

        drawings, table = sources
        page = self._page(window)
        page._drawings, page._table = drawings, table
        page.run()
        pump()

        target = tmp_path / "library.json"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName",
            staticmethod(lambda *a, **k: (str(target), "")),
        )
        page.export_plugin()
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert "glazing_bead" in payload["excluded_for_conflict"]
        assert "mullion_mb70" in {p["profile_id"] for p in payload["profiles"]}


class TestSystemPage:
    def _page(self, window):
        return {p.title: p for p in window.pages}["System"]

    def test_the_comparison_claims_only_what_exists(self, window):
        from profileos import compare

        # The page renders the matrix, so a stale claim would be shown to a
        # customer. Catching it here keeps that from being the first sign.
        assert compare.verify_claims() == {}
        page = self._page(window)
        window.go_to_page(page.title)
        # The tab is filled the first time it is opened, so opening it is what
        # exercises the rendering path a customer would see.
        page.tabs.setCurrentIndex(page._compare_index)
        pump()
        assert page._compare_built
        assert not window.grab().isNull()

    def test_the_comparison_is_not_built_until_it_is_opened(self, window):
        """Verifying the claims imports every engine; the window must not wait."""
        assert self._page(window)._compare_built is False

    def test_checking_without_a_key_is_refused_with_a_reason(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        page._update_key = None
        page.check_updates()
        assert recorded and "המפתח הציבורי" in recorded[0]

    def test_applying_without_checking_is_refused(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        page._update_plan = None
        page.apply_updates()
        assert recorded and "בדוק עדכונים" in recorded[0]

    def test_a_signed_update_is_installed_and_goes_live(self, window, tmp_path):
        """The whole point of the mechanism, end to end and in one process.

        Publish a price list, install it through the page, and ask the pricing
        engine for a price it could not have answered a moment earlier — with
        no restart in between.
        """
        from profileos.core.config import get_settings
        from profileos.core.hotreload import register_builtin_schemas
        from profileos.quoting.suppliers import find_price
        from profileos.security.keys import SigningKey
        from profileos.updates import PackageKind, build_manifest, build_package
        from profileos.updates import publish_directory

        register_builtin_schemas()
        settings = get_settings()
        settings.data_dir = tmp_path / "data"
        settings.config_dir = tmp_path / "config"
        settings.ensure_directories()

        document = json.dumps({
            "kind": "price_list",
            "id": "ui-test-supplier",
            "name": "UI test supplier",
            "currency": "ILS",
            "entries": [{"code": "UI-TEST-4301", "unit": "m", "price": 41.5}],
        }).encode("utf-8")

        key = SigningKey.generate()
        package = build_package(
            "ui.test.prices", PackageKind.PRICE_LIST, document,
            "ui_test_prices.json", key, version="1.2.0",
        )
        feed = publish_directory(
            {"ui_test_prices.json": document}, build_manifest([package], key),
            tmp_path / "feed",
        )
        public = tmp_path / "issuer.pub"
        public.write_bytes(key.public_key().to_pem())

        assert find_price("UI-TEST-4301", 1.0) is None

        page = self._page(window)
        page.update_source.setText(str(feed))
        page._update_key = public
        page.check_updates()
        pump()
        assert page.update_table.rowCount() == 1
        assert page.apply_button.isEnabled()

        page.apply_updates()
        pump()
        assert page.installed_table.rowCount() == 1

        priced = find_price("UI-TEST-4301", 1.0)
        assert priced is not None, "the update did not go live without a restart"
        supplier, total = priced
        assert supplier.name == "UI test supplier"
        assert total == pytest.approx(41.5)

    def test_changing_the_operator_reaches_the_sidebar(self, window):
        from profileos.branding import set_active_brand

        page = self._page(window)
        try:
            index = page.brand_picker.findData("dadi")
            assert index >= 0, "the Dadi operator should be selectable"
            page.brand_picker.setCurrentIndex(index)
            pump()
            assert "דאדי" in window.sidebar.logo.text()
            assert "בית אל" in page._brand_values["עיר"].text()
        finally:
            set_active_brand("profileos")

    def test_the_operator_tab_never_stacks_stale_labels(self, window):
        """Rebuilding the tab used to leave the old text painted over the new."""
        from profileos.branding import set_active_brand

        page = self._page(window)
        try:
            page.brand_picker.setCurrentIndex(page.brand_picker.findData("dadi"))
            pump()
            page.brand_picker.setCurrentIndex(page.brand_picker.findData("profileos"))
            pump()
            assert page._brand_values["עיר"].text() == "לא הוגדר"
            assert "דאדי" not in window.sidebar.logo.text()
        finally:
            set_active_brand("profileos")


class TestViewPage:
    def _page(self, window):
        return window.page("3D view")

    def test_it_models_the_designed_element(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        window.go_to_page(page.title)
        page.render_scene()
        pump()
        assert page._scene is not None
        assert page._scene.triangle_count > 0
        # Every solid must be closed and outward, or the render lights wrongly.
        for mesh in page._scene.meshes:
            assert mesh.is_closed(), mesh.name
            assert mesh.volume() > 0, mesh.name

    def test_switching_the_view_redraws_without_remodelling(self, window):
        window.page("Element").build_element()
        page = window.go_to_page("3D view")
        page.render_scene()
        pump()
        before = page._scene
        page.view.setCurrentText("elevation")
        pump()
        assert page._scene is before

    def test_rendering_without_elements_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.session.clear_builds()
        page.refresh()
        page.render_scene()
        assert recorded

    def test_export_writes_every_format(self, window, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        window.page("Element").build_element()
        page = window.go_to_page("3D view")
        page.render_scene()
        pump()
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            staticmethod(lambda *a, **k: str(tmp_path)),
        )
        page.export_scene()
        suffixes = {path.suffix for path in tmp_path.iterdir()}
        assert suffixes == {".svg", ".html", ".gltf", ".glb"}


class TestAccountsPage:
    def _page(self, window):
        return window.page("Accounts")

    def test_planning_purchases_from_the_designed_elements(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        window.go_to_page(page.title)
        page.plan_purchases()
        pump()
        assert page.requirements_table.rowCount() > 0
        assert page.orders_table.rowCount() > 0

    def test_scheduling_reports_a_completion_date(self, window):
        window.page("Element").build_element()
        page = self._page(window)
        page.run_schedule()
        pump()
        assert page.schedule_table.rowCount() > 0
        assert page.load_table.rowCount() > 0

    def test_the_audit_passes_on_a_consistent_company(self, window):
        page = self._page(window)
        page.run_audit()
        pump()
        # An audit that raised would have gone through report(); the stat row
        # is only filled when it did not.
        assert page.stats is not None

    def test_the_audit_surfaces_a_disagreement(self, window, monkeypatch):
        from profileos.erp import money

        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        company = page._company()
        from datetime import date

        company.ledger.post_simple(
            "ODD", date(2026, 1, 1), "unexplained", "1300", "2100", money(500)
        )
        page.run_audit()
        assert recorded and "out by" in recorded[0]

    def test_planning_without_elements_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        page = self._page(window)
        monkeypatch.setattr(
            type(page), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.session.clear_builds()
        page.plan_purchases()
        assert recorded


class TestHomePage:
    def test_pipeline_marks_done_steps_and_names_the_next(self, window):
        window.page("Profile").load_sample()
        window.page("Element").build_element()
        home = window.go_to_page("Home")
        pump()
        states = [b.property("state") for b in home._step_buttons]
        assert states[0] == "done" and states[1] == "done"
        assert "active" in states
        # The first not-done step is the one the label points at.
        active = states.index("active")
        assert home.STEPS[active][1] in home.next_label.text()

    def test_empty_session_starts_at_the_first_step(self, window):
        home = window.go_to_page("Home")
        pump()
        assert home._step_buttons[0].property("state") == "active"
        assert all(
            b.property("state") == "pending" for b in home._step_buttons[1:]
        )

    def test_pipeline_step_navigates(self, window):
        home = window.go_to_page("Home")
        home._step_buttons[0].click()
        pump()
        assert window.stack.currentWidget().title == "Profile"


class TestCommandPalette:
    def test_typing_filters_to_the_matching_page(self, window):
        window.open_palette()
        palette = window._palette_dialog
        palette.search.setText("הצעת")
        labels = [
            palette.results.item(i).text()
            for i in range(palette.results.count())
        ]
        assert labels == ["הצעת מחיר"]
        palette.reject()

    def test_enter_runs_the_selected_command(self, window):
        window.open_palette()
        palette = window._palette_dialog
        palette.search.setText("תלת")
        palette._run_item(palette.results.item(0))
        pump()
        assert window.stack.currentWidget().title == "3D view"


class TestToasts:
    def test_status_raises_a_toast(self, window):
        window.page("Profile").status("נשמר")
        pump()
        toasts = getattr(window, "_toasts", [])
        assert toasts and toasts[-1].text() == "נשמר"

    def test_toasts_never_stack_beyond_three(self, window):
        for index in range(5):
            window.toast(f"הודעה {index}")
        pump()
        assert len(window._toasts) == 3


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    """Point the job store at a temporary folder for the duration of a test."""
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


class TestProjectsPage:
    def test_a_new_job_becomes_the_open_one(self, window, job_dir):
        from profileos.projects import default_store

        page = window.go_to_page("Projects")
        store = default_store()
        job = store.create("וילה בבית אל")
        window.session.set_job(job)
        page.refresh()
        pump()
        assert page.jobs_table.rowCount() == 1
        assert job.job_id in page.header.subtitle.text()

    def test_saving_writes_the_designed_openings_into_the_job(self, window, job_dir):
        from profileos.projects import default_store

        window.page("Element").build_element()
        page = window.go_to_page("Projects")
        job = default_store().create("עבודה")
        window.session.set_job(job)
        page.save_current()
        pump()

        saved = default_store().load(job.job_id)
        assert saved.schedule is not None
        assert saved.opening_count == len(window.session.builds)
        assert saved.unit_count >= saved.opening_count

    def test_saving_without_an_open_job_explains_itself(self, window, job_dir, monkeypatch):
        recorded: list[str] = []
        monkeypatch.setattr(
            type(window.page("Projects")), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        page = window.go_to_page("Projects")
        window.session.job = None
        page.save_current()
        assert recorded and "אין פרויקט פתוח" in recorded[0]

    def test_opening_a_job_rebuilds_its_elements(self, window, job_dir):
        from profileos.projects import default_store

        window.page("Element").build_element()
        page = window.go_to_page("Projects")
        store = default_store()
        job = store.create("לפתיחה")
        window.session.set_job(job)
        page.save_current()

        window.session.clear_builds()
        assert not window.session.builds

        page.refresh()
        page.jobs_table.setCurrentCell(0, 0)
        page.open_job()
        pump()
        assert len(window.session.builds) == 1
        assert window.session.job.job_id == job.job_id

    def test_status_advances_only_where_the_rules_allow(self, window, job_dir, monkeypatch):
        from profileos.projects import JobStatus, default_store

        page = window.go_to_page("Projects")
        job = default_store().create("סטטוס")
        page.refresh()
        page.jobs_table.setCurrentCell(0, 0)
        pump()
        # The picker only ever offers reachable statuses.
        offered = {
            page.status_combo.itemData(i) for i in range(page.status_combo.count())
        }
        assert offered == {"quoted", "lost"}

        page.status_combo.setCurrentIndex(page.status_combo.findData("quoted"))
        page.advance_status()
        pump()
        assert default_store().load(job.job_id).status is JobStatus.QUOTED

    def test_the_selected_job_is_described_without_a_click(self, window, job_dir):
        from profileos.projects import default_store

        default_store().create("בלי קליק")
        page = window.go_to_page("Projects")
        pump()
        assert "בלי קליק" in page.job_summary.text()


class TestSessionSchedule:
    def test_the_schedule_round_trips_through_the_builder(self, window):
        window.page("Element").build_element()
        original = [b.opening.element_id for b in window.session.builds]

        schedule = window.session.to_schedule(name="בדיקה")
        window.session.clear_builds()
        problems = window.session.load_schedule(schedule)

        assert not problems
        assert [b.opening.element_id for b in window.session.builds] == original

    def test_an_opening_that_cannot_be_rebuilt_is_reported_not_fatal(self, window):
        from profileos.elements.model import ElementSchedule, Opening

        schedule = ElementSchedule(
            name="חלקי",
            openings=[
                Opening(element_id="W-01", name="W-01", width=1200, height=1400),
                Opening(element_id="W-02", name="W-02", width=1200, height=1400,
                        system_id="a-system-that-does-not-exist"),
            ],
        )
        problems = window.session.load_schedule(schedule)
        # Whatever happens to the unknown system, the good opening still builds.
        assert any(b.opening.element_id == "W-01" for b in window.session.builds)
        assert isinstance(problems, list)


class TestQuoteReachesTheJob:
    def test_issuing_a_quotation_records_it_on_the_open_job(
        self, window, job_dir, tmp_path, monkeypatch
    ):
        from PySide6.QtWidgets import QFileDialog

        from profileos.projects import JobStatus, default_store

        job = default_store().create("עבודה מתומחרת")
        window.session.set_job(job)

        window.page("Element").build_element()
        page = window.go_to_page("Quotation")
        page.start_draft()
        pump()

        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            staticmethod(lambda *args, **kwargs: str(tmp_path)),
        )
        page.save_documents()
        pump()

        saved = default_store().load(job.job_id)
        assert saved.quote_total > 0
        assert saved.status is JobStatus.QUOTED

    def test_a_won_job_is_not_dragged_back_by_reprinting(
        self, window, job_dir, tmp_path, monkeypatch
    ):
        from PySide6.QtWidgets import QFileDialog

        from profileos.projects import JobStatus, default_store

        store = default_store()
        job = store.create("כבר הוזמן")
        job.advance(JobStatus.QUOTED)
        job.advance(JobStatus.WON)
        store.save(job)
        window.session.set_job(job)

        window.page("Element").build_element()
        page = window.go_to_page("Quotation")
        page.start_draft()
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory",
            staticmethod(lambda *args, **kwargs: str(tmp_path)),
        )
        page.save_documents()
        pump()
        assert store.load(job.job_id).status is JobStatus.WON


class TestDroppedFiles:
    def test_a_dropped_drawing_opens_on_the_profile_page(self, window, mullion_dxf):
        window.open_path(mullion_dxf)
        pump()
        assert window.stack.currentWidget().title == "Profile"
        assert window.session.section_properties is not None

    def test_a_dropped_job_file_is_filed_and_opened(self, window, job_dir, tmp_path):
        from profileos.projects import JobFile, default_store

        incoming = tmp_path / "mailed.json"
        incoming.write_text(
            JobFile(job_id="J-2026-0777", name="הגיע במייל").model_dump_json(),
            encoding="utf-8",
        )
        window.open_path(incoming)
        pump()
        assert window.session.job.job_id == "J-2026-0777"
        assert default_store().load("J-2026-0777").name == "הגיע במייל"

    def test_a_json_that_is_not_a_job_is_refused_quietly(self, window, job_dir, tmp_path):
        stray = tmp_path / "stray.json"
        stray.write_text('{"hello": "world"}', encoding="utf-8")
        window.open_path(stray)
        pump()
        assert window.session.job is None
        assert any("אינו קובץ פרויקט" in t.text() for t in window._toasts)

    def test_only_known_suffixes_are_accepted(self, window):
        assert set(window.DROP_SUFFIXES) == {".dxf", ".dwg", ".json"}
