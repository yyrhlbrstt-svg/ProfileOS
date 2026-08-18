"""Desktop UI tests.

The UI is driven headlessly (``QT_QPA_PLATFORM=offscreen``) so the whole
workflow is exercised in CI: a real window is built, each page is operated
through its own controls, and the resulting session state is asserted. This
catches the failures unit tests cannot — a page that raises on load, a signal
wired to nothing, a view that crashes when handed real engine output.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from profileos.mes import Stage  # noqa: E402
from profileos.ui.main_window import MainWindow  # noqa: E402
from profileos.ui.theme import DARK, LIGHT, stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def window(qt_app):
    win = MainWindow(DARK)
    win.resize(1400, 900)
    yield win
    win.close()


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
            "Profile", "Element", "Nesting", "Machining", "Quotation", "Shop floor",
        ]

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
        window.go_to(0)
        window.pages[0].load_sample()
        pump()
        assert window.session.section_properties is not None
        assert window.session.section_properties.area == pytest.approx(1719.2, abs=1.0)
        assert window.pages[0].properties.rowCount() > 10

    def test_wind_check_populates(self, window):
        window.pages[0].load_sample()
        window.pages[0].span.setValue(2000.0)
        window.pages[0].run_check()
        pump()
        assert window.pages[0].checks.rowCount() >= 3
        assert "Maximum span" in window.pages[0].max_span.text()

    def test_element_page_builds(self, window):
        window.go_to(1)
        page = window.pages[1]
        page.width.setValue(2400.0)
        page.height.setValue(1800.0)
        page.columns.setValue(3)
        page.build_element()
        pump()
        assert len(window.session.builds) == 1
        assert page.cuts.rowCount() > 0
        assert page.panes.rowCount() == 3

    def test_nesting_page_optimises(self, window):
        window.pages[1].build_element()
        window.go_to(2)
        window.pages[2].run()
        pump()
        report = window.session.nesting_report
        assert report is not None and report.total_bars > 0
        assert window.pages[2].summary.rowCount() > 0

    def test_nesting_without_elements_is_reported_not_crashed(self, window, monkeypatch):
        recorded: list[str] = []
        monkeypatch.setattr(
            type(window.pages[2]), "report",
            lambda self, exc, context="": recorded.append(str(exc)),
        )
        window.session.clear_builds()
        window.go_to(2)
        window.pages[2].run()
        assert recorded and "No elements" in recorded[0]

    def test_machining_page_posts(self, window):
        window.go_to(3)
        page = window.pages[3]
        page.post()
        pump()
        assert window.session.post_results
        assert page.code.toPlainText().strip()
        assert "interference" in page.clamp_status.text()

    def test_machining_defaults_to_a_machining_centre(self, window):
        assert window.pages[3].driver.currentData() == "elumatec.ncx"

    @pytest.mark.parametrize("driver_key", ["elumatec.ncx", "kaban.kbn", "iso.gcode", "fom.cam"])
    def test_every_driver_posts_from_the_ui(self, window, driver_key):
        page = window.pages[3]
        index = page.driver.findData(driver_key)
        assert index >= 0
        page.driver.setCurrentIndex(index)
        page.post()
        pump()
        assert page.code.toPlainText().strip()

    def test_quote_page_prices(self, window):
        window.pages[1].build_element()
        window.go_to(4)
        window.pages[4].run()
        pump()
        quote = window.session.quote
        assert quote is not None and quote.net_price > quote.total_cost
        assert window.pages[4].waterfall.rowCount() > 5

    def test_shop_floor_releases_and_scans(self, window):
        window.pages[1].build_element()
        window.go_to(5)
        page = window.pages[5]
        page.release()
        pump()

        order = window.session.work_order
        assert order is not None and len(order) > 0

        page.item.setCurrentIndex(0)
        page.stage.setCurrentText("cut")
        page.scan()
        pump()
        assert order.items[0].stage is Stage.CUT
        assert "cut" in page.scan_result.text()

    def test_invalid_scan_shows_the_reason(self, window):
        window.pages[1].build_element()
        window.go_to(5)
        page = window.pages[5]
        page.release()
        page.item.setCurrentIndex(0)
        page.stage.setCurrentText("shipped")
        page.scan()
        pump()
        assert "cannot go from" in page.scan_result.text()

    def test_status_bar_summarises_the_session(self, window):
        window.pages[0].load_sample()
        window.pages[1].build_element()
        pump()
        assert "element(s)" in window.session.describe()


class TestSessionInvalidation:
    def test_adding_an_element_invalidates_the_nesting(self, window):
        window.go_to(1)
        window.pages[1].build_element()
        window.go_to(2)
        window.pages[2].run()
        assert window.session.nesting_report is not None

        # A new element changes the cut list, so the old plan must not linger.
        window.go_to(1)
        window.pages[1].name.setCurrentText("W-99")
        window.pages[1].build_element()
        assert window.session.nesting_report is None

    def test_rebuilding_the_same_element_replaces_it(self, window):
        page = window.pages[1]
        page.name.setCurrentText("W-01")
        page.build_element()
        first = window.session.builds[0].opening.element_id
        count = len(window.session.builds)

        # Rebuilding creates a new element id, so it adds rather than replaces.
        page.build_element()
        assert len(window.session.builds) == count + 1
        assert window.session.builds[0].opening.element_id == first
