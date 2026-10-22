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
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.logging_setup import get_logger
from .icons import app_icon
from .pages import PAGES, Page
from .session import Session
from ..design import BRAND
from .theme import DARK, LIGHT, METRICS, Palette, stylesheet

_log = get_logger("ui.window")

#: Sidebar grouping. Navigation follows the order work moves through the shop.
NAV_SECTIONS: list[tuple[str, list[int]]] = [
    ("סקירה", [0, 18, 1]),
    ("תכנון", [2, 19, 3, 4, 5]),
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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        from ..branding import active_brand

        brand = active_brand()
        self.logo = QLabel(brand.display_name)
        self.logo.setObjectName("SidebarLogo")
        self.logo.setWordWrap(True)
        self.version = QLabel(brand.tagline or f"v{__version__}")
        self.version.setObjectName("SidebarVersion")
        outer.addWidget(self.logo)
        outer.addWidget(self.version)

        # The navigation scrolls on its own. Eight sections and twenty pages
        # do not reliably fit a laptop's vertical pixels, and a sidebar that
        # silently clips its last button is worse than one with a scrollbar —
        # this stays correct as more pages are added, without retuning pixel
        # budgets by hand every time.
        scroll = QScrollArea()
        scroll.setObjectName("SidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav_body = QWidget()
        nav_body.setObjectName("SidebarNavBody")
        layout = QVBoxLayout(nav_body)
        layout.setContentsMargins(0, 0, 0, METRICS.space(3))
        layout.setSpacing(0)
        scroll.setWidget(nav_body)
        outer.addWidget(scroll, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[NavButton] = []
        self._titles: list[str] = []
        self._section_labels: list[QLabel] = []
        self._collapsed = False
        # Painted icons cannot read the stylesheet, so the sidebar keeps the two
        # colours it draws them in and re-renders them when the theme changes.
        self._icon_colour = DARK.text_muted
        # The selected item is a filled accent pill, so its glyph is white.
        self._icon_active = "#FFFFFF"
        self._layout = layout

        # A quiet brand mark, pinned below the scrolling navigation. The
        # operator's own company name sits at the top so it is clear whose
        # install this is; ProfileOS's own name sits at the bottom so the
        # product itself stays recognisable to somebody seeing it for the
        # first time — which is exactly who is standing in front of it during
        # a demonstration.
        # "Is my work saved?" answered without anybody having to ask.
        self.safety = QLabel("")
        self.safety.setObjectName("SidebarSafety")
        self.safety.setWordWrap(True)
        outer.addWidget(self.safety)

        footer = QLabel(f"ProfileOS · גרסה {__version__}")
        footer.setObjectName("SidebarFooter")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer = footer
        outer.addWidget(footer)

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
        self.footer.setVisible(not collapsed)
        self.safety.setVisible(not collapsed)
        for label in self._section_labels:
            label.setVisible(not collapsed)
        for button in self.buttons:
            button.set_collapsed(collapsed)


def _day_and_time(stamp: str) -> str:
    """An ISO stamp the way the shop writes it: 17/10/2026 14:05."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(stamp).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return stamp.replace("T", " ")[:16]


class MainWindow(QMainWindow):
    """The main application window."""

    def __init__(self, palette: Palette = DARK) -> None:
        super().__init__()
        self.colours = palette
        self.session = Session()
        #: Set by the application entry point. Off by default so that a window
        #: built by a test or a script never writes into the shop's backups.
        self.auto_backup = False

        from ..branding import active_brand

        self.setWindowTitle(active_brand().window_title())
        self.setWindowIcon(app_icon())
        self._size_to_screen()
        application = QApplication.instance()
        if application is not None:
            from .widgets import install_number_field_polish

            install_number_field_polish(application)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)
        # The theme switch sits where it can be found — a bright workshop
        # screen is exactly where nobody remembers a keyboard shortcut.
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("SidebarTheme")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setToolTip("החלפת ערכת צבעים (Ctrl+T)")
        self.theme_button.clicked.connect(self.toggle_theme)
        outer = self.sidebar.layout()
        outer.insertWidget(outer.count() - 1, self.theme_button)
        self._label_theme_button()

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

        # -- nothing designed is ever only in memory ------------------------- #
        from PySide6.QtCore import QTimer

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1500)
        self._autosave_timer.timeout.connect(self.autosave)
        # A program left open all week still backs up: once an hour, and only
        # when something changed since the last archive.
        self._backup_timer = QTimer(self)
        self._backup_timer.setInterval(60 * 60 * 1000)
        self._backup_timer.timeout.connect(self._hourly_backup)
        self._backup_timer.start()
        # Suppliers' folders and pages, checked in the background: folders
        # hourly, websites daily (each source knows when it is due).
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(30 * 60 * 1000)
        self._watch_timer.timeout.connect(self.watch_suppliers)
        self._watch_timer.start()
        self._watching = False
        self._save_state = ("ok", "")
        self.session.subscribe(self._on_session_change)
        self._update_safety()

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

        estimate_action = QAction(self)
        estimate_action.setShortcut(QKeySequence("Ctrl+E"))
        estimate_action.triggered.connect(self.quick_estimate)
        self.addAction(estimate_action)

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

    def quick_estimate(self) -> None:
        """Ctrl+E: a phone price in seconds; accepting it opens the real design."""
        from .dialogs import QuickEstimateDialog

        dialog = QuickEstimateDialog(self)
        if dialog.exec() and dialog.estimate is not None:
            page = self.go_to_page("Element")
            page.apply_preset(dialog.estimate.preset)

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
            self.toast(f"נפתח ⁦{paths[0].name}⁩; ⁦{len(paths) - 1}⁩ קבצים נוספים דולגו")

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
        store = default_store()
        if store.path_for(job.job_id).exists():
            # The same number is already a job here: most likely an older copy
            # mailed back. Replacing ours is never the quiet default.
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("כבר קיים פרויקט במספר הזה")
            box.setText(
                f"בתוכנה כבר יש פרויקט {job.job_id}. הקובץ שנפתח יכול להיות "
                "עותק ישן שלו.\n\nמה לעשות?"
            )
            as_new = box.addButton("הוסף כפרויקט חדש", QMessageBox.ButtonRole.AcceptRole)
            replace = box.addButton("החלף את הקיים", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("ביטול", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(as_new)
            box.exec()
            clicked = box.clickedButton()
            if clicked is as_new:
                job = job.model_copy(update={"job_id": store.next_id()})
            elif clicked is not replace:
                return
        store.save(job)
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
        self.sidebar.restyle_icons(palette.text_muted, "#FFFFFF")
        # Painted views hold their own colours, so they need telling directly.
        for page in self.pages:
            page.colours = palette
            for attribute in ("view", "clamp_view"):
                widget = getattr(page, attribute, None)
                if widget is not None and hasattr(widget, "palette_colours"):
                    widget.palette_colours = palette
                    widget.update()
            # The live 3D views sit on the page's own ground colour.
            for attribute in ("live", "_model_view"):
                model = getattr(page, attribute, None)
                if model is not None and hasattr(model, "set_background"):
                    model.set_background(
                        palette.surface_sunken,
                        "#FFFFFF" if palette.mode.value == "light" else palette.surface_raised,
                    )

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        """Take the day's backup on the way out, then close.

        Nobody remembers to back up; everybody closes the program. One archive
        a day, the last thirty kept, beside the data folder — so a dead hard
        disk costs at most a day.
        """
        # Whatever is waiting to be written is written before anything closes.
        if self.autosave_enabled and (
            self._autosave_timer.isActive() or self._save_state[0] == "pending"
        ):
            self.autosave()
        # A save that failed is said while the window can still be kept open —
        # a message on a window that is closing is a message nobody reads.
        if self.autosave_enabled and self._save_state[0] == "danger":
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("העבודה לא נשמרה")
            box.setText(
                "השמירה האחרונה נכשלה, והתכנון שעל המסך לא נשמר.\n"
                f"{self._save_state[1]}\n\nלהשאיר את התוכנה פתוחה כדי לשמור?"
            )
            stay = box.addButton("השאר פתוח", QMessageBox.ButtonRole.RejectRole)
            box.addButton("סגור בלי לשמור", QMessageBox.ButtonRole.DestructiveRole)
            box.setDefaultButton(stay)
            box.exec()
            if box.clickedButton() is stay:
                event.ignore()
                return
        archive = None
        if self.auto_backup:
            from ..core.backup import backup_if_due

            archive = backup_if_due()
        if archive is not None:
            self.statusBar().showMessage(f"גיבוי יומי נשמר: {archive.name}")
        super().closeEvent(event)

    def _hourly_backup(self) -> None:
        if not self.auto_backup:
            return
        from ..core.backup import backup_if_due

        backup_if_due()
        self._update_safety()

    #: Off for windows built by tests and scripts, like the backups: only the
    #: real program goes to the suppliers' websites on its own.
    watch_enabled = False

    def watch_suppliers(self) -> None:
        """Check the watched supplier sources that are due, off the screen's thread."""
        if not self.watch_enabled or self._watching:
            return
        # A price list accepted with a future date takes effect on that day,
        # also in a program that was left open overnight.
        from ..quoting.pricelists import load_price_lists

        load_price_lists()
        from ..catalogue.watch import check_all
        from .background import run_in_background

        self._watching = True

        def done(summary: Any, error: BaseException | None) -> None:
            self._watching = False
            if error is not None:
                # Not only the log: a shop that believes its suppliers are
                # watched when they are not is worse off than one that knows.
                from ..catalogue.watch import in_words

                _log.warning("Supplier check failed: %s", error)
                self.statusBar().showMessage(
                    "בדיקת קבצי הספקים נכשלה: " + in_words(error)
                    + " — נסו ״בדיקה עכשיו״ במסך קטלוג ← עדכוני ספקים", 30000,
                )
                return
            if summary.new:
                self.statusBar().showMessage(
                    f"נמצאו ⁦{len(summary.new)}⁩ קבצי קטלוג חדשים אצל הספקים — ראו קטלוג ← עדכוני ספקים",
                    15000,
                )
                home = next((p for p in self.pages if p.title == "Home"), None)
                if home is not None and home.isVisible():
                    home.refresh()

        run_in_background(lambda: check_all(only_due=True), done)

    #: Off for windows built by tests and scripts: only the real program goes
    #: to look for a newer version of itself.
    self_update_enabled = False

    def check_for_update(self) -> None:
        """Look for a newer version, download and check it, off the screen's thread."""
        if not self.self_update_enabled or getattr(self, "_updating", False):
            return
        from ..core import selfupdate
        from .background import run_in_background

        self._updating = True

        def done(state: Any, error: BaseException | None) -> None:
            self._updating = False
            if error is None and state is not None and state.ready:
                self.statusBar().showMessage(
                    f"גרסה חדשה ({state.ready}) הורדה ונבדקה — תותקן בהפעלה הבאה. "
                    "מערכת ← עדכונים ← ״התקנה והפעלה מחדש״ כדי להתקין עכשיו.", 30000,
                )
            system = next((p for p in self.pages if p.title == "System"), None)
            if system is not None and hasattr(system, "_refresh_program_update"):
                system._refresh_program_update()

        run_in_background(selfupdate.check_and_stage, done)

    def restart_for_update(self) -> None:
        """Close as on any close (saves, backup), then ask the launcher to
        put the update in place and start again."""
        from PySide6.QtWidgets import QApplication

        from ..core.selfupdate import RESTART_CODE

        application = QApplication.instance()
        application.setQuitOnLastWindowClosed(False)
        if self.close():
            application.exit(RESTART_CODE)
        else:
            application.setQuitOnLastWindowClosed(True)

    def announce_update(self) -> None:
        """Once, after the launcher acted: what is new, or why the old version is back."""
        from PySide6.QtWidgets import QMessageBox

        from ..core import selfupdate

        result = selfupdate.unannounced()
        if result is None:
            return
        if result.get("outcome") == "rolled_back":
            QMessageBox.warning(
                self, "העדכון בוטל",
                f"גרסה {result.get('version')} לא נפתחה, והתוכנה חזרה לגרסה הקודמת. "
                "הנתונים לא נפגעו. הגרסה הזאת לא תנוסה שוב; כשתתפרסם גרסה מתוקנת "
                "היא תותקן מעצמה.",
            )
            return
        system = next((p for p in self.pages if p.title == "System"), None)
        if system is not None and hasattr(system, "show_whats_new"):
            from_version = str(result.get("from", "") or "")
            parts = from_version.split(".")
            since = f"{parts[0]}-{parts[1]}-{parts[2]}" if len(parts) >= 3 and parts[0].isdigit() else ""
            self.statusBar().showMessage(f"התוכנה עודכנה לגרסה {result.get('version')}", 15000)
            system.show_whats_new(since)

    # -- data safety ---------------------------------------------------------- #
    #: Off for windows built by tests and scripts; the application entry point
    #: turns it on, so only the real program writes the shop's files on its own.
    autosave_enabled = False

    def _on_session_change(self, what: str) -> None:
        if what == "leaving":
            # About to switch job or replace the design: what is still
            # waiting to be written belongs to what is on screen now.
            if self.autosave_enabled and self._autosave_timer.isActive():
                self.autosave()
            return
        if what == "builds" and self.autosave_enabled:
            self._autosave_timer.start()
            self._save_state = ("pending", "")
            self._update_safety()

    def autosave(self) -> None:
        """Write the design where it belongs: the open job, or the draft.

        Runs a moment after every change and once more on close. A failure is
        shown in the sidebar and as a notice — never only in a log — because
        the one thing worse than losing work is believing it was saved.
        """
        from ..projects.draft import clear_draft, retire_draft, write_draft

        self._autosave_timer.stop()
        session = self.session
        try:
            if session.job is not None:
                saved = session.save_design_to_job()
                if retire_draft(saved.schedule) is not None:
                    self.toast("תכנון שלא תויק נשמר בצד — לא נמחק", "warning")
                self._save_state = ("ok", f"נשמר לפרויקט {session.job.job_id}")
            elif session.builds or session.unbuilt:
                write_draft(session.to_schedule())
                self._save_state = ("draft", "נשמר כטיוטה — פתחו פרויקט כדי לתייק")
            else:
                clear_draft()
                self._save_state = ("ok", "")
        except Exception as exc:  # noqa: BLE001 - shown, never swallowed
            _log.exception("Autosave failed")
            self._save_state = ("danger", str(exc))
            self.toast(f"השמירה האוטומטית נכשלה: {exc}", "danger")
        self._update_safety()

    def _update_safety(self) -> None:
        """The sidebar line that answers "is my work saved?" at a glance."""
        from datetime import date

        from ..core import storage

        label = getattr(self.sidebar, "safety", None)
        if label is None:
            return
        state, detail = self._save_state
        backup = self._last_backup_day()
        backup_text = (
            "גיבוי היום" if backup == date.today()
            else (f"גיבוי אחרון {backup:%d/%m}" if backup else "עוד לא נלקח גיבוי")
        )
        from ..core.backup import backup_status

        backup_state = backup_status()
        stale = backup is None or (date.today() - backup).days > 2
        # A failed save outranks everything: it is the one line that means
        # work is at risk right now.
        if state == "danger":
            text = "✕ השמירה נכשלה — העבודה עדיין על המסך"
        elif storage.RECOVERED:
            names = ", ".join(path.name for path, _damaged in storage.RECOVERED[:2])
            state, text = "warn", f"⚠ שוחזר מגרסה קודמת: {names}"
        elif backup_state["error"]:
            state, detail = "danger", backup_state["error"]
            text = "✕ הגיבוי האוטומטי נכשל — מערכת ← גיבוי ושחזור"
        elif backup_state["mirror_error"] and state == "ok":
            state, detail = "warn", backup_state["mirror_error"]
            text = "⚠ העותק השני של הגיבוי לא עודכן"
        elif backup is None and state == "ok" and not self._data_is_empty():
            state = "warn"
            text = "⚠ עוד לא נלקח אף גיבוי — מערכת ← גיבוי ושחזור"
        elif stale and backup is not None and state == "ok":
            state = "warn"
            text = f"⚠ הגיבוי האחרון מ-⁦{backup:%d/%m}⁩ — מערכת ← גיבוי ושחזור"
        elif state == "pending":
            text = "… שומר"
        elif state == "draft":
            text = "● נשמר כטיוטה · פתחו פרויקט כדי לתייק"
        else:
            text = f"✓ כל השינויים נשמרו · {backup_text}"
        label.setText(text)
        label.setToolTip(detail or text)
        label.setProperty("state", "ok" if state in ("ok", "pending") else
                          ("warn" if state in ("draft", "warn") else "danger"))
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _data_is_empty() -> bool:
        try:
            from ..core.config import get_settings

            root = get_settings().data_dir
            return not root.is_dir() or not any(root.iterdir())
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _last_backup_day() -> Any:
        from datetime import datetime

        try:
            from ..core.backup import default_backup_folder

            archives = sorted(default_backup_folder().glob("profileos-*.zip"))
        except Exception:  # noqa: BLE001
            return None
        if not archives:
            return None
        return datetime.fromtimestamp(archives[-1].stat().st_mtime).date()

    def offer_draft_recovery(self) -> bool:
        """At start-up: work designed last time and never filed comes back."""
        from PySide6.QtWidgets import QMessageBox

        from ..projects.draft import read_draft, set_draft_aside

        try:
            found = read_draft()
        except Exception as exc:  # noqa: BLE001
            self.toast(f"טיוטה שמורה לא נקראה: {exc}", "danger")
            return False
        if found is None:
            return False
        schedule, saved_at = found
        box = QMessageBox(self)
        box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("נמצאה עבודה שלא תויקה")
        when = _day_and_time(saved_at)
        box.setText(
            f"בפעם הקודמת תוכננו ⁦{len(schedule.openings)}⁩ פתחים שלא נשמרו "
            f"לשום פרויקט (נשמרו אוטומטית ב-⁦{when}⁩).\n\nלהחזיר אותם למסך?"
        )
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("החזר")
        box.button(QMessageBox.StandardButton.No).setText("לא עכשיו — שמור בצד")
        if box.exec() == QMessageBox.StandardButton.Yes:
            problems = self.session.load_schedule(schedule)
            for problem in problems[:3]:
                self.toast(problem, "danger")
            self.toast(f"הוחזרו ⁦{len(self.session.builds)}⁩ פתחים — פתחו פרויקט כדי לתייק")
            return True
        set_draft_aside()
        return False

    def toggle_theme(self) -> None:
        self.apply_palette(LIGHT if self.colours.mode.value == "dark" else DARK)
        light = self.colours.mode.value == "light"
        self._label_theme_button()
        self.toast("עברת לערכה הבהירה" if light else "עברת לערכה הכהה")
        # Remembered, so the next launch opens the way it was left.
        try:
            from ..core.config import get_settings, save_settings

            settings = get_settings()
            settings.theme = "light" if light else "dark"
            save_settings(settings)
        except Exception:  # noqa: BLE001 - the switch itself already happened
            pass

    def _label_theme_button(self) -> None:
        button = getattr(self, "theme_button", None)
        if button is not None:
            button.setText(
                "☾  ערכה כהה" if self.colours.mode.value == "light"
                else "☀  ערכה בהירה"
            )

    def _update_status(self) -> None:
        self.statusBar().showMessage(self.session.describe())


__all__ = ["MainWindow", "Sidebar", "NAV_SECTIONS"]
