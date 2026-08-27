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
from ..design import BRAND
from .theme import DARK, LIGHT, METRICS, Palette, stylesheet

_log = get_logger("ui.window")

#: Sidebar grouping. Navigation follows the order work moves through the shop.
NAV_SECTIONS: list[tuple[str, list[int]]] = [
    ("סקירה", [0, 18, 1]),
    ("תכנון", [2, 3, 4, 5]),
    ("ייצור", [6, 7, 8]),
    ("מסחרי", [9, 10, 11]),
    ("מפעל", [12, 13]),
    ("אחרי המסירה", [14]),
    ("אינסטלציה", [15]),
    ("ספרייה", [16, 17]),
]


class NavButton(QPushButton):
    """One row of the sidebar: a glyph on the leading edge, then the name.

    The icon and the label are laid out explicitly rather than left to the
    button's own icon slot, because a right-to-left application needs the
    glyph on the right of the text and the pair pinned to the sidebar's right
    edge — and a stylesheet cannot say that about a composite the style draws
    itself. Colour is set here for the same reason: painted glyphs cannot read
    a stylesheet, so the button tells both halves what colour to be.
    """

    def __init__(self, text: str, page_title: str, colour: str, active: str) -> None:
        super().__init__()
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._page_title = page_title

        row = QHBoxLayout(self)
        row.setContentsMargins(METRICS.space(2), 0, METRICS.space(2), 0)
        row.setSpacing(METRICS.space(2))

        self.glyph = QLabel()
        self.glyph.setFixedSize(18, 18)
        self.glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = QLabel(text)
        self.label.setObjectName("NavLabel")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        row.addWidget(self.glyph)
        row.addWidget(self.label)
        row.addStretch(1)

        self.toggled.connect(lambda _checked: self._paint())
        self.restyle(colour, active)
        self._full_text = text

    def set_collapsed(self, collapsed: bool) -> None:
        """Show the glyph alone, with the name as the tooltip.

        On a narrow screen the sidebar is the cheapest hundred and sixty pixels
        in the window: the icons carry the navigation on their own once
        somebody has used the software for a day, and the tooltip carries it
        until then.
        """
        self.label.setVisible(not collapsed)
        self.setToolTip(self._full_text if collapsed else "")

    def restyle(self, colour: str, active: str) -> None:
        self._colour, self._active = colour, active
        self._paint()

    def _paint(self) -> None:
        from .icons import PAGE_ICONS, pixmap

        colour = self._active if self.isChecked() else self._colour
        name = PAGE_ICONS.get(self._page_title)
        if name:
            self.glyph.setPixmap(pixmap(name, colour))
        self.label.setStyleSheet(
            f"color: {colour}; font-weight: {600 if self.isChecked() else 500};"
        )


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
        self.logo = QLabel(brand.display_name)
        self.logo.setObjectName("SidebarLogo")
        self.logo.setWordWrap(True)
        self.version = QLabel(brand.tagline or f"v{__version__}")
        self.version.setObjectName("SidebarVersion")
        layout.addWidget(self.logo)
        layout.addWidget(self.version)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[NavButton] = []
        self._titles: list[str] = []
        self._section_labels: list[QLabel] = []
        self._collapsed = False
        # Painted icons cannot read the stylesheet, so the sidebar keeps the two
        # colours it draws them in and re-renders them when the theme changes.
        self._icon_colour = DARK.text_muted
        self._icon_active = BRAND.x300
        self._layout = layout

    def refresh_brand(self) -> None:
        """Re-read the operator, so the sidebar follows a change on the System page."""
        from ..branding import active_brand

        brand = active_brand()
        self.logo.setText(brand.display_name)
        self.version.setText(brand.tagline or f"v{__version__}")

    def add_section(self, title: str) -> None:
        label = QLabel(title)
        label.setObjectName("SidebarSection")
        self._layout.addWidget(label)
        self._section_labels.append(label)

    def add_button(self, index: int, text: str, page_title: str = "") -> QPushButton:
        button = NavButton(text, page_title, self._icon_colour, self._icon_active)
        self.group.addButton(button, index)
        self.buttons.append(button)
        self._layout.addWidget(button)
        self._titles.append(page_title)
        return button

    def restyle_icons(self, colour: str, active: str) -> None:
        """Re-render the glyphs after a theme change — they are painted, not themed."""
        self._icon_colour, self._icon_active = colour, active
        for button in self.buttons:
            button.restyle(colour, active)

    def finish(self) -> None:
        self._layout.addStretch(1)

    def set_collapsed(self, collapsed: bool) -> None:
        """Narrow the sidebar to its glyphs, or give the names back."""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setFixedWidth(56 if collapsed else METRICS.sidebar_width)
        self.logo.setVisible(not collapsed)
        self.version.setVisible(not collapsed)
        for label in self._section_labels:
            label.setVisible(not collapsed)
        for button in self.buttons:
            button.set_collapsed(collapsed)


class MainWindow(QMainWindow):
    """The main application window."""

    def __init__(self, palette: Palette = DARK) -> None:
        super().__init__()
        self.colours = palette
        self.session = Session()

        from ..branding import active_brand

        self.setWindowTitle(active_brand().window_title())
        self._size_to_screen()

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
                self.sidebar.add_button(
                    len(self.pages) - 1,
                    page_class.hebrew or page_class.title,
                    page_class.title,
                )
        self.sidebar.finish()

        self.sidebar.group.idClicked.connect(self.go_to)
        if self.sidebar.buttons:
            self.sidebar.buttons[0].setChecked(True)

        # A drawing dragged onto the window is the shortest path there is from
        # the supplier's email to a measured section.
        self.setAcceptDrops(True)

        self.statusBar().showMessage("מוכן")
        self.session.subscribe(lambda _what: self._update_status())
        self._install_shortcuts()
        self.apply_palette(palette)

    # -- navigation ---------------------------------------------------------- #

    def refresh_brand(self) -> None:
        """Called when the System page changes which fabricator this is."""
        self.sidebar.refresh_brand()

    def set_session_owner(self, session: Any) -> None:
        """Record who signed in, and show it in the title and the status bar."""
        self.access_session = session
        self.setWindowTitle(f"{self.windowTitle()} — {session.username}")
        self.statusBar().showMessage(f"מחובר: {session.describe()}", 8000)

    def go_to(self, index: int) -> None:
        if not (0 <= index < len(self.pages)):
            return
        self.stack.setCurrentIndex(index)
        self.sidebar.buttons[index].setChecked(True)
        self.pages[index].refresh()
        self._fade_in(self.pages[index])
        self._update_status()

    def _fade_in(self, page: Any) -> None:
        """A quiet 150 ms entrance for the page that just appeared.

        The effect is removed when the animation ends: a persistent opacity
        effect forces every later paint through an offscreen buffer, which
        blurs text on fractional-DPI displays.
        """
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", page)
        animation.setDuration(METRICS.motion_fast_ms)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: page.setGraphicsEffect(None))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def page(self, title: str) -> Page:
        """Find a page by its title.

        Pages get inserted as the suite grows, so anything that reaches for one
        by position breaks the moment a page lands before it. Titles do not
        move.
        """
        for page in self.pages:
            if page.title == title:
                return page
        raise KeyError(f"No page titled {title!r}; have {[p.title for p in self.pages]}")

    def go_to_page(self, title: str) -> Page:
        page = self.page(title)
        self.go_to(self.pages.index(page))
        return page

    def _install_shortcuts(self) -> None:
        """Ctrl+1..9 jump to a page, Ctrl+T toggles the theme, Ctrl+K searches."""
        for index in range(min(len(self.pages), 9)):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{index + 1}"))
            action.triggered.connect(lambda _checked=False, i=index: self.go_to(i))
            self.addAction(action)

        theme_action = QAction(self)
        theme_action.setShortcut(QKeySequence("Ctrl+T"))
        theme_action.triggered.connect(self.toggle_theme)
        self.addAction(theme_action)

        palette_action = QAction(self)
        palette_action.setShortcut(QKeySequence("Ctrl+K"))
        palette_action.triggered.connect(self.open_palette)
        self.addAction(palette_action)

        save_action = QAction(self)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self.save_job)
        self.addAction(save_action)

    def save_job(self) -> None:
        """Ctrl+S from anywhere: write the work into the open job file."""
        self.page("Projects").save_current()

    def open_palette(self) -> None:
        """The Ctrl+K search box: every page and common action, one keystroke."""
        from .palette_search import CommandPalette

        if getattr(self, "_palette_dialog", None) is None:
            self._palette_dialog = CommandPalette(self)
        self._palette_dialog.open_over()

    # -- notifications -------------------------------------------------------- #
    def toast(self, message: str, kind: str = "info") -> None:
        """A short notice at the top of the window; also logged to the status bar."""
        from .widgets import show_toast

        show_toast(self, message, kind)
        self.statusBar().showMessage(message, 6000)

    # -- dropped files --------------------------------------------------------- #
    #: What a drop is allowed to open, and which page opens it.
    DROP_SUFFIXES = {".dxf": "Profile", ".dwg": "Profile", ".json": "Projects"}

    def _droppable(self, event: Any) -> list[Any]:
        from pathlib import Path

        data = event.mimeData()
        if not data.hasUrls():
            return []
        paths = [Path(url.toLocalFile()) for url in data.urls() if url.isLocalFile()]
        return [p for p in paths if p.suffix.lower() in self.DROP_SUFFIXES]

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if self._droppable(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if self._droppable(event):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        """Open what was dropped on the page that knows what to do with it."""
        paths = self._droppable(event)
        if not paths:
            return
        event.acceptProposedAction()
        self.open_path(paths[0])
        if len(paths) > 1:
            self.toast(f"נפתח {paths[0].name}; {len(paths) - 1} קבצים נוספים דולגו")

    def open_path(self, path: Any) -> None:
        """Open one file: a drawing goes to the profile page, a job to the store."""
        from pathlib import Path

        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".dxf", ".dwg"):
            page = self.go_to_page("Profile")
            page.load(path)
            return
        if suffix == ".json":
            self._open_job_file(path)

    def _open_job_file(self, path: Any) -> None:
        """A job file mailed in: read it, file it, and open it."""
        from ..projects import JobFile, default_store

        try:
            job = JobFile.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a stray JSON is not an error worth a dialog
            self.toast(f"{path.name} אינו קובץ פרויקט", "danger")
            return
        default_store().save(job)
        page = self.go_to_page("Projects")
        self.session.set_job(job)
        if job.schedule is not None:
            self.session.load_schedule(job.schedule)
        page.refresh()
        self.toast(f"נפתח פרויקט {job.job_id} — {job.name}", "success")

    def _size_to_screen(self) -> None:
        """Open at the size this screen can actually show.

        A fixed 1560x980 is a good window on the office monitor and a window
        with its bottom edge under the taskbar on the shop laptop. The
        available geometry already excludes the taskbar, so filling most of it
        — and never claiming more than it — is the one rule that holds on both.
        The minimum is set low enough that the window can be halved on a small
        screen without Qt refusing to shrink it.
        """
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless
            self.resize(1280, 820)
            self.setMinimumSize(900, 600)
            return
        available = screen.availableGeometry()
        width = min(1560, available.width())
        height = min(980, available.height())
        self.setMinimumSize(min(900, width), min(600, height))
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def apply_responsive_layout(self) -> None:
        """Fold the sidebar to its glyphs once the names stop earning space.

        Two thresholds rather than one, so a window dragged slowly across the
        boundary settles instead of flickering between the two layouts.
        """
        if self.width() < 1080:
            self.sidebar.set_collapsed(True)
        elif self.width() > 1160:
            self.sidebar.set_collapsed(False)

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        # Qt holds back resize events until a window is shown, so the first
        # layout decision has to be taken here rather than waiting for one.
        super().showEvent(event)
        self.apply_responsive_layout()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.apply_responsive_layout()
        from .widgets import reposition_toasts

        reposition_toasts(self)

    # -- appearance ----------------------------------------------------------- #
    def apply_palette(self, palette: Palette) -> None:
        self.colours = palette
        application = QApplication.instance()
        if application is not None:
            sheet = stylesheet(palette)
            # setStyleSheet on the application re-polishes every widget alive in
            # the process, so setting the same sheet again is not free — it is
            # the cost of a full restyle for no change at all.
            if application.property("profileos_stylesheet") != sheet:
                application.setStyleSheet(sheet)
                application.setProperty("profileos_stylesheet", sheet)
        self.sidebar.restyle_icons(palette.text_muted, palette.accent)
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
        self.toast(
            "עברת לערכה הבהירה" if self.colours.mode.value == "light"
            else "עברת לערכה הכהה"
        )

    def _update_status(self) -> None:
        self.statusBar().showMessage(self.session.describe())


__all__ = ["MainWindow", "Sidebar", "NAV_SECTIONS"]
