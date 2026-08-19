"""The application shell: sidebar navigation and stacked pages."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.logging_setup import get_logger
from .pages import PAGES, Page
from .session import Session
from .theme import DARK, LIGHT, METRICS, Palette, stylesheet

_log = get_logger("ui.window")

#: Sidebar grouping. Navigation follows the order work moves through the shop.
NAV_SECTIONS: list[tuple[str, list[int]]] = [
    ("Design", [0, 1]),
    ("Production", [2, 3]),
    ("Commercial", [4]),
    ("Factory", [5]),
]


class Sidebar(QWidget):
    """Vertical navigation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(METRICS.sidebar_width)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, METRICS.space(3))
        layout.setSpacing(0)

        from ..branding import active_brand

        brand = active_brand()
        logo = QLabel(brand.display_name)
        logo.setObjectName("SidebarLogo")
        logo.setWordWrap(True)
        version = QLabel(brand.tagline or f"v{__version__}")
        version.setObjectName("SidebarVersion")
        layout.addWidget(logo)
        layout.addWidget(version)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        self._layout = layout

    def add_section(self, title: str) -> None:
        label = QLabel(title.upper())
        label.setObjectName("SidebarSection")
        self._layout.addWidget(label)

    def add_button(self, index: int, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.group.addButton(button, index)
        self.buttons.append(button)
        self._layout.addWidget(button)
        return button

    def finish(self) -> None:
        self._layout.addStretch(1)


class MainWindow(QMainWindow):
    """The main application window."""

    def __init__(self, palette: Palette = DARK) -> None:
        super().__init__()
        self.colours = palette
        self.session = Session()

        from ..branding import active_brand

        self.setWindowTitle(active_brand().window_title())
        self.resize(1560, 980)
        self.setMinimumSize(1120, 720)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages: list[Page] = []
        for section_title, indices in NAV_SECTIONS:
            self.sidebar.add_section(section_title)
            for index in indices:
                page_class = PAGES[index]
                page = page_class(self.session, self.colours)
                self.pages.append(page)
                self.stack.addWidget(page)
                self.sidebar.add_button(len(self.pages) - 1, page_class.title)
        self.sidebar.finish()

        self.sidebar.group.idClicked.connect(self.go_to)
        if self.sidebar.buttons:
            self.sidebar.buttons[0].setChecked(True)

        self.statusBar().showMessage("Ready")
        self.session.subscribe(lambda _what: self._update_status())
        self._install_shortcuts()
        self.apply_palette(palette)

    # -- navigation ---------------------------------------------------------- #
    def go_to(self, index: int) -> None:
        if not (0 <= index < len(self.pages)):
            return
        self.stack.setCurrentIndex(index)
        self.sidebar.buttons[index].setChecked(True)
        self.pages[index].refresh()
        self._update_status()

    def _install_shortcuts(self) -> None:
        """Ctrl+1..6 jump straight to a page; Ctrl+T toggles the theme."""
        for index in range(len(self.pages)):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda _checked=False, i=index: self.go_to(i))
            self.addAction(action)

        theme_action = QAction(self)
        theme_action.setShortcut(QKeySequence("Ctrl+T"))
        theme_action.triggered.connect(self.toggle_theme)
        self.addAction(theme_action)

    # -- appearance ----------------------------------------------------------- #
    def apply_palette(self, palette: Palette) -> None:
        self.colours = palette
        application = QApplication.instance()
        if application is not None:
            application.setStyleSheet(stylesheet(palette))
        # Painted views hold their own colours, so they need telling directly.
        for page in self.pages:
            page.colours = palette
            for attribute in ("view", "clamp_view"):
                widget = getattr(page, attribute, None)
                if widget is not None and hasattr(widget, "palette_colours"):
                    widget.palette_colours = palette
                    widget.update()

    def toggle_theme(self) -> None:
        self.apply_palette(LIGHT if self.colours.mode.value == "dark" else DARK)
        self.statusBar().showMessage(f"Switched to the {self.colours.mode.value} theme", 3000)

    def _update_status(self) -> None:
        self.statusBar().showMessage(self.session.describe())


__all__ = ["MainWindow", "Sidebar", "NAV_SECTIONS"]
