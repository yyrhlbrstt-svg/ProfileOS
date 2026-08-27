"""Application pages.

One page per stage of the workflow, in the order work actually moves:
profile -> element -> nesting -> machining -> quotation -> shop floor.

Pages share a :class:`~profileos.ui.session.Session`, so a profile analysed on
the first page is available to the last without any page importing another.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QInputDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .theme import METRICS, Palette
from .views import (
    BarChart,
    ClampView,
    ElevationView,
    NestingView,
    SectionView,
    SheetView,
)
from .widgets import Badge, Card, DataTable, FieldGrid, PageHeader, StatRow, page_layout

_log = get_logger("ui.pages")


#: Field names that turn up in the audit log, in the words the shop uses.
#: A log written in the code's vocabulary is a log only the author can read.
_AUDIT_FIELDS: dict[str, str] = {
    "values": "ערכי הסדרה",
    "stock_value": "שווי המלאי",
    "quote_total": "סכום ההצעה",
    "net_price": "מחיר לפני מע״מ",
    "payment_terms": "תנאי תשלום",
    "status": "סטטוס",
    "due_date": "מועד אספקה",
    "customer_id": "לקוח",
    "name": "שם",
}


def _audit_field(name: str) -> str:
    if not name:
        return "—"
    if name.startswith("revision "):
        return f"גרסה ⁦{name.split()[-1]}⁩"
    return _AUDIT_FIELDS.get(name, name)


def _audit_value(value: Any) -> str:
    """One recorded value, short enough for a table cell."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"⁦{value:,.2f}⁩"
    if isinstance(value, (int, bool)):
        return f"⁦{value}⁩"
    text = str(value)
    return text if len(text) <= 48 else text[:45] + "…"


class Page(QWidget):
    """Base page: a header, a body, and access to the shared session.

    ``title`` is the stable identifier code navigates by; ``hebrew`` is what
    the person sees. The two are separate on purpose — the working language of
    the interface is Hebrew, and renaming an identifier every time wording
    improves would break every lookup.
    """

    title = "Page"
    hebrew = ""
    subtitle = ""
    #: Pages built from a stack of cards set this: on a laptop the stack is
    #: taller than the screen, and squeezing every card until its table shows
    #: one row is worse than scrolling. Pages built around a splitter leave it
    #: off — a splitter already decides how to share the height it is given.
    scrollable = False

    def __init__(self, session: Any, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.colours = palette

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = PageHeader(self.hebrew or self.title, self.subtitle)
        outer.addWidget(self.header)

        body = QWidget()
        self.body = page_layout(body)
        if self.scrollable:
            area = QScrollArea()
            area.setWidget(body)
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            outer.addWidget(area, 1)
        else:
            outer.addWidget(body, 1)

        self.build()

    def build(self) -> None:
        """Override to populate :attr:`body`."""

    def refresh(self) -> None:
        """Called when the page becomes visible."""

    # -- error handling ------------------------------------------------------ #
    def report(self, exc: Exception, context: str = "") -> None:
        """Show an engine error to the user rather than losing it to a log."""
        _log.error("%s: %s", context or "Operation failed", exc, exc_info=True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(context or "הפעולה נכשלה")
        box.setText(str(exc))
        if not isinstance(exc, ProfileOSError):
            box.setDetailedText(traceback.format_exc())
        box.exec()

    def status(self, message: str) -> None:
        window = self.window()
        if hasattr(window, "toast"):
            window.toast(message)
        elif hasattr(window, "statusBar"):
            window.statusBar().showMessage(message, 6000)


class HomePage(Page):
    """Where the day starts: what has been done, and what the next step is.

    The competitors open on a form. Opening on the state of the work — with
    the one next action a click away — is what makes the software feel like it
    is working *with* the fabricator rather than waiting for them.
    """

    title = "Home"
    hebrew = "דף הבית"
    scrollable = True
    subtitle = ""

    #: The production chain, in the order work moves: (page title, Hebrew
    #: name, how to tell from the session that the step has been done).
    STEPS: tuple[tuple[str, str, str], ...] = (
        ("Profile", "פרופיל", "section_properties"),
        ("Element", "פתחים", "builds"),
        ("Nesting", "חיתוך", "nesting_report"),
        ("Glass", "זכוכית", "glass_report"),
        ("Machining", "CNC", "post_results"),
        ("Quotation", "הצעת מחיר", "quote"),
        ("Shop floor", "ייצור", "work_order"),
    )

    def build(self) -> None:
        from ..branding import active_brand

        palette_button = QPushButton("Ctrl+K — מעבר מהיר")
        palette_button.setObjectName("Ghost")
        palette_button.clicked.connect(self._open_palette)
        self.header.add_action(palette_button)

        self.greeting = QLabel()
        self.greeting.setObjectName("HomeGreeting")
        self.body.addWidget(self.greeting)

        self.next_label = QLabel()
        self.next_label.setObjectName("HomeNext")
        self.next_label.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(self.next_label)

        # -- the pipeline ------------------------------------------------- #
        pipeline = Card("קו הייצור")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(1))
        self._step_buttons: list[QPushButton] = []
        for position, (page_title, hebrew, _attr) in enumerate(self.STEPS):
            if position:
                arrow = QLabel("←")
                arrow.setObjectName("PipeArrow")
                row.addWidget(arrow)
            button = QPushButton(hebrew)
            button.setObjectName("PipeStep")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, t=page_title: self._go(t)
            )
            self._step_buttons.append(button)
            row.addWidget(button, 1)
        pipeline.add_layout(row)
        self.body.addWidget(pipeline)

        # -- the numbers -------------------------------------------------- #
        self.stats = StatRow([
            ("elements", "פתחים"), ("area", "שטח"),
            ("quote", "הצעת מחיר"), ("floor", "בייצור"),
        ])
        self.body.addWidget(self.stats)

        # -- quick actions ------------------------------------------------ #
        actions = Card("פעולות מהירות")
        buttons = QHBoxLayout()
        buttons.setSpacing(METRICS.space(2))
        for label, handler in (
            ("פרויקט חדש", lambda: self._go("Projects")),
            ("חפש פתח", self._find_opening),
            ("חפש פרופיל", self._find_profile),
            ("הצעת מחיר", lambda: self._go("Quotation")),
            ("רצפת הייצור", lambda: self._go("Shop floor")),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, h=handler: h())
            buttons.addWidget(button)
        buttons.addStretch(1)
        actions.add_layout(buttons)
        self.body.addWidget(actions)

        # What this installation can actually do this morning. It sits above
        # the order book because a shop that does not know it cannot cut yet
        # finds out at the saw.
        setup = Card("מצב ההקמה")
        self.readiness_headline = QLabel("")
        self.readiness_headline.setObjectName("StatLabel")
        self.readiness_headline.setWordWrap(True)
        setup.add(self.readiness_headline)
        self.readiness_table = DataTable(
            ["", "מה", "מצב", "מה זה חוסם", "איך סוגרים"],
            empty_text="הכול מוכן",
        )
        # The home page shows the next few things to do, not the whole audit:
        # a twelve-row checklist on the front page is a checklist nobody reads.
        # The full list is on the System page and on the command line.
        self.readiness_table.setFixedHeight(METRICS.row_height * 5)
        setup.add(self.readiness_table)
        self.readiness_more = QLabel("")
        self.readiness_more.setObjectName("Hint")
        setup.add(self.readiness_more)
        self.body.addWidget(setup)

        # What somebody has to do today. It sits above the order book because
        # a follow-up nobody sees on the morning it is due is a follow-up that
        # never happens, and a quotation nobody chases is a job lost to a habit
        # rather than to a price.
        today = Card("להיום")
        self.today_headline = QLabel("")
        self.today_headline.setObjectName("StatLabel")
        self.today_headline.setWordWrap(True)
        today.add(self.today_headline)
        self.today_table = DataTable(
            ["", "מתי", "על מה", "מה לעשות", "אחראי"],
            empty_text="אין משימות להיום",
        )
        self.today_table.stretch(3)
        self.today_table.setFixedHeight(METRICS.row_height * 5)
        today.add(self.today_table)
        open_tasks = QPushButton("כל המשימות")
        open_tasks.setObjectName("Ghost")
        open_tasks.clicked.connect(lambda: self._go("Projects"))
        today.add(open_tasks)
        self.body.addWidget(today)

        recent = Card("פרויקטים אחרונים")
        self.recent_table = DataTable(
            ["מספר", "שם", "לקוח", "סטטוס", "עודכן"],
            empty_text="עדיין אין פרויקטים — פתח אחד בעמוד ״פרויקטים״",
        )
        self.recent_table.setMaximumHeight(METRICS.row_height * 5)
        self.recent_table.itemDoubleClicked.connect(self._open_recent)
        recent.add(self.recent_table)
        hint = QLabel("לחיצה כפולה על שורה פותחת את הפרויקט לעבודה.")
        hint.setObjectName("Hint")
        recent.add(hint)
        self.body.addWidget(recent)

        note = QLabel(
            "כל צעד בקו נשען על הקודם לו: הפרופיל שנמדד הוא זה שנבנה ממנו "
            "הפתח, רשימת החיתוך שלו היא זו שמגיעה למסור, והמחיר מחושב מאותם "
            "נתונים בדיוק. שינוי במקום אחד מתגלגל לכל השאר."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.body.addStretch(1)

        brand = active_brand()
        self._brand_name = brand.display_name

    # -- behaviour -------------------------------------------------------- #
    def _go(self, page_title: str) -> None:
        window = self.window()
        if hasattr(window, "go_to_page"):
            window.go_to_page(page_title)

    def _open_recent(self, item: Any) -> None:
        """Double-clicking a recent job opens it, on the page that owns opening."""
        row = item.row()
        if row >= len(self._recent):
            return
        job = self._recent[row]
        window = self.window()
        if not hasattr(window, "go_to_page"):
            return
        page = window.go_to_page("Projects")
        for index, listed in enumerate(page._jobs):
            if listed.job_id == job.job_id:
                page.jobs_table.setCurrentCell(index, 0)
                page.open_job()
                return

    def _load_sample(self) -> None:
        window = self.window()
        if hasattr(window, "go_to_page"):
            page = window.go_to_page("Profile")
            if hasattr(page, "load_sample"):
                page.load_sample()

    def show_today(self) -> None:
        """What is due, worst-late first, and what nobody is chasing."""
        from datetime import date as _date

        from ..projects.followups import default_tasks

        try:
            book = default_tasks()
            tasks = book.due_by()
        except Exception as exc:  # noqa: BLE001
            self.today_table.set_rows([])
            self.today_headline.setText(str(exc))
            return

        tasks.sort(key=lambda task: task.due)
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        for task in tasks[:5]:
            colours[(len(rows), 0)] = (
                self.colours.danger if task.is_overdue()
                else self.colours.warning
            )
            rows.append([
                "●", task.due.strftime("%d/%m"),
                task.subject_name or task.about or "—",
                task.what, task.assigned_to or "—",
            ])
        self.today_table.set_rows(rows, colours=colours)

        summary = book.summary()
        line = (
            f"פתוחות ⁦{summary['open']}⁩ · להיום ⁦{summary['due_today']}⁩"
            + (
                f" · באיחור ⁦{summary['overdue']}⁩" if summary["overdue"] else ""
            )
        )
        try:
            from ..projects import default_store

            forgotten = book.unchased_quotes(list(default_store().all()))
        except Exception:  # noqa: BLE001
            forgotten = []
        if forgotten:
            line += (
                f" · ⁦{len(forgotten)}⁩ הצעות פתוחות שאיש אינו עוקב אחריהן"
            )
        self.today_headline.setText(line)

    def show_readiness(self) -> None:
        """The setup list, worst first — the things that block real work."""
        from ..readiness import State, readiness

        try:
            report = readiness()
        except Exception as exc:  # noqa: BLE001 - never lose the home page
            _log.warning("Readiness report failed: %s", exc)
            self.readiness_table.set_rows([])
            self.readiness_headline.setText("")
            return

        order = {State.ATTENTION: 0, State.EMPTY: 1, State.PARTIAL: 2, State.READY: 3}
        tone = {
            State.ATTENTION: self.colours.danger,
            State.EMPTY: self.colours.warning,
            State.PARTIAL: self.colours.info,
            State.READY: self.colours.success,
        }
        outstanding = sorted(
            (check for check in report if not check.state.is_ready),
            key=lambda check: (0 if check.critical else 1, order[check.state]),
        )

        shown = outstanding[:4]
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        for index, check in enumerate(shown):
            rows.append([
                "●", check.hebrew, check.state.hebrew,
                check.blocks or "—", check.fix or "—",
            ])
            colours[(index, 0)] = tone[check.state]
        self.readiness_table.set_rows(rows, colours=colours)
        self.readiness_more.setText(
            f"ועוד ⁦{len(outstanding) - len(shown)}⁩ פריטים — הרשימה המלאה בעמוד ״מערכת״"
            if len(outstanding) > len(shown) else ""
        )

        self.readiness_headline.setText(report.verdict())
        self.readiness_headline.setStyleSheet(
            f"color: {self.colours.success if report.may_cut else self.colours.warning};"
        )

    def _find_opening(self) -> None:
        """Straight from the front door to a built window."""
        window = self.window()
        if hasattr(window, "go_to_page"):
            page = window.go_to_page("Element")
            if hasattr(page, "find_opening"):
                page.find_opening()

    def _find_profile(self) -> None:
        window = self.window()
        if hasattr(window, "go_to_page"):
            page = window.go_to_page("Profile")
            if hasattr(page, "find_profile"):
                page.find_profile()

    def _open_palette(self) -> None:
        window = self.window()
        if hasattr(window, "open_palette"):
            window.open_palette()

    def refresh(self) -> None:
        from datetime import datetime

        now = datetime.now()
        hebrew_days = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        hebrew_months = [
            "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
            "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
        ]
        part = "בוקר טוב" if 5 <= now.hour < 12 else (
            "צהריים טובים" if 12 <= now.hour < 17 else "ערב טוב"
        )
        self.greeting.setText(f"{part}, {self._brand_name}")
        self.show_readiness()
        self.show_today()
        when = (
            f"יום {hebrew_days[now.weekday()]}, "
            f"{now.day} ב{hebrew_months[now.month - 1]} {now.year}"
        )
        # And the Hebrew date, because half the shop's deadlines are set in it.
        try:
            from ..hebrew_calendar import describe, holiday_on

            when += f"  ·  {describe(now.date())}"
            festival = holiday_on(now.date())
            if festival is not None:
                when += f"  ·  {festival.describe()}"
        except Exception:  # noqa: BLE001 - a date is not worth a broken page
            pass
        job = self.session.job
        self.header.set_subtitle(
            f"{when}  ·  פרויקט פתוח: {job.job_id} — {job.name}"
            if job is not None else when
        )

        # Step states: everything the session holds is "done"; the first gap
        # is the active step, and everything past it just waits its turn.
        active_seen = False
        active_hebrew = ""
        for button, (page_title, hebrew, attribute) in zip(self._step_buttons, self.STEPS):
            done = bool(getattr(self.session, attribute, None))
            if done:
                state = "done"
                button.setText(f"✓ {hebrew}")
            elif not active_seen:
                state, active_seen, active_hebrew = "active", True, hebrew
                button.setText(hebrew)
            else:
                state = "pending"
                button.setText(hebrew)
            button.setProperty("state", state)
            button.style().unpolish(button)
            button.style().polish(button)

        if not active_seen:
            self.next_label.setText("כל שלבי הקו הושלמו — העבודה בדרך למשלוח.")
        else:
            self.next_label.setText(f"הצעד הבא: <b>{active_hebrew}</b>")

        self._recent = []
        try:
            from ..projects import default_store

            self._recent = default_store().all()[:5]
        except Exception:  # noqa: BLE001 - the dashboard must open regardless
            _log.exception("Could not read the job store for the dashboard")
        self.recent_table.set_rows([
            [job.job_id, job.name, job.customer_name or "—",
             job.status.hebrew, job.updated]
            for job in self._recent
        ])

        area = self.session.total_area
        self.stats.update_many({
            "elements": (str(len(self.session.builds)) if self.session.builds else "—",
                         "מתוכננים בפרויקט"),
            "area": (f"⁦{area:.1f} m²⁩" if area else "—", ""),
            "quote": (
                f"⁦{self.session.quote.net_price:,.0f} ₪⁩"
                if self.session.quote else "—",
                "לפני מע\"מ" if self.session.quote else "טרם חושבה",
            ),
            "floor": (
                str(len(self.session.work_order)) if self.session.work_order else "—",
                "פריטים בעבודה" if self.session.work_order else "טרם שוחרר",
            ),
        })


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #

class ProjectsPage(Page):
    """The order book: every job the shop has taken on, and where it stands.

    This is the page the other packages open on, and the one this suite was
    missing: without it the work lives in memory and dies with the window.
    A job here is a file on disk — openable next year, mailable to an
    engineer, readable in a text editor.
    """

    title = "Projects"
    hebrew = "פרויקטים"
    subtitle = "תיקי עבודה, לקוחות ומצב ההזמנות"

    def build(self) -> None:
        new_job = QPushButton("פרויקט חדש")
        new_job.setObjectName("Primary")
        new_job.clicked.connect(self.new_job)
        self.header.add_action(new_job)

        save = QPushButton("שמירת העבודה")
        save.clicked.connect(self.save_current)
        self.header.add_action(save)

        self.stats = StatRow([
            ("open", "פרויקטים פתוחים"), ("quoted", "בהצעה"),
            ("production", "בייצור"), ("backlog", "צבר הזמנות"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._jobs_tab(), "פרויקטים")
        tabs.addTab(self._customers_tab(), "לקוחות")
        tabs.addTab(self._costing_tab(), "רווחיות")
        tabs.addTab(self._files_tab(), "מסמכים")
        tabs.addTab(self._tasks_tab(), "מעקב ומשימות")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

    # -- follow-ups -------------------------------------------------------- #
    def _tasks_tab(self) -> QWidget:
        """What somebody has to do today, and what nobody is chasing.

        A shop that never sets these concludes its prices are too high, when
        what it has is a habit of not ringing back.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        bar = QHBoxLayout()
        bar.setSpacing(METRICS.space(2))

        chase = QPushButton("קבע מעקב להצעה שנשלחה")
        chase.setObjectName("Primary")
        chase.clicked.connect(self.schedule_chase)
        bar.addWidget(chase)

        close = QPushButton("סגור משימה נבחרת")
        close.clicked.connect(self.close_task)
        bar.addWidget(close)

        self.task_note = QLineEdit()
        self.task_note.setPlaceholderText("מה קרה בשיחה — נשמר עם סגירת המשימה")
        bar.addWidget(self.task_note, 1)
        layout.addLayout(bar)

        self.task_headline = QLabel("")
        self.task_headline.setObjectName("StatLabel")
        self.task_headline.setWordWrap(True)
        layout.addWidget(self.task_headline)

        self.task_table = DataTable(
            ["", "מתי", "סוג", "על מה", "מה לעשות", "אחראי", "מצב"],
            empty_text="אין משימות פתוחות",
        )
        self.task_table.stretch(4)
        layout.addWidget(self.task_table, 1)

        self.unchased_table = DataTable(
            ["תיק", "לקוח", "סכום", "נשלחה"],
            empty_text="כל ההצעות הפתוחות במעקב",
        )
        unchased = Card("הצעות שאיש אינו עוקב אחריהן")
        unchased.add(self.unchased_table, 1)
        layout.addWidget(unchased, 1)

        self._tasks = None
        self.show_tasks()
        return page

    def _task_book(self) -> Any:
        if self._tasks is None:
            from ..projects.followups import default_tasks

            self._tasks = default_tasks()
        return self._tasks

    def schedule_chase(self) -> None:
        from datetime import date as _date

        job = getattr(self.session, "job", None)
        if job is None:
            self.report(
                ProfileOSError("פתח תיק עבודה קודם"), "אין למה לקבוע מעקב"
            )
            return
        try:
            made = self._task_book().chase_quote(
                job, sent_on=_date.today()
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לקבוע מעקב")
            return
        self.status(
            f"נקבעו ⁦{len(made)}⁩ תזכורות מעקב" if made
            else "כבר קיים מעקב פתוח להצעה הזאת"
        )
        self.show_tasks()

    def close_task(self) -> None:
        from ..projects.followups import Outcome

        row = self.task_table.currentRow()
        if row < 0 or row >= len(self._task_rows):
            self.report(
                ProfileOSError("בחר משימה בטבלה"), "לא נבחרה משימה"
            )
            return
        try:
            self._task_book().close(
                self._task_rows[row], Outcome.DONE,
                result=self.task_note.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לסגור את המשימה")
            return
        self.task_note.clear()
        self.show_tasks()

    def show_tasks(self) -> None:
        from datetime import date as _date

        book = self._task_book()
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        self._task_rows: list[str] = []

        for task in book.open_tasks:
            late = task.is_overdue()
            colours[(len(rows), 0)] = (
                self.colours.danger if late
                else self.colours.warning if task.due <= _date.today()
                else self.colours.success
            )
            rows.append([
                "●",
                task.due.strftime("%d/%m/%Y"),
                task.kind.hebrew,
                task.subject_name or task.about or "—",
                task.what,
                task.assigned_to or "—",
                f"⁦{task.days_late()}⁩ ימי איחור" if late else "פתוחה",
            ])
            self._task_rows.append(task.task_id)
        self.task_table.set_rows(rows, colours=colours)

        summary = book.summary()
        self.task_headline.setText(
            f"פתוחות ⁦{summary['open']}⁩ · להיום ⁦{summary['due_today']}⁩ · "
            f"באיחור ⁦{summary['overdue']}⁩"
            + (
                f" · ⁦{summary['closed_silently']}⁩ נסגרו בלי לרשום מה קרה"
                if summary["closed_silently"] else ""
            )
        )

        try:
            jobs = list(self._store().all())
        except Exception:  # noqa: BLE001
            jobs = []
        self.unchased_table.set_rows([
            [
                job.job_id, job.customer_name or "—",
                f"⁦{job.quote_total:,.0f}⁩ ₪" if job.quote_total else "—",
                job.quoted_on[:10] if job.quoted_on else "—",
            ]
            for job in book.unchased_quotes(jobs)
        ])

    # -- storage ----------------------------------------------------------- #
    def _store(self) -> Any:
        """The job folder, read fresh each time.

        Resolving it per call rather than caching it means a data directory
        changed in the settings takes effect without a restart — and it keeps
        the page testable against a temporary folder.
        """
        from ..projects import default_store

        return default_store()

    def _book(self) -> Any:
        from ..projects import default_customers

        return default_customers()

    # -- jobs -------------------------------------------------------------- #
    def _jobs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.search = QLineEdit()
        self.search.setPlaceholderText("חיפוש לפי שם, לקוח, מספר או אתר…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.refresh())
        layout.addWidget(self.search)

        self.jobs_table = DataTable(
            ["מספר", "שם", "לקוח", "סטטוס", "יחידות", "שווי", "עודכן"],
            empty_text="אין עדיין פרויקטים — לחץ ״פרויקט חדש״ כדי לפתוח את הראשון",
        )
        self.jobs_table.itemSelectionChanged.connect(self._show_job)
        layout.addWidget(self.jobs_table, 1)

        detail = Card("הפרויקט הנבחר")
        self.job_summary = QLabel("לא נבחר פרויקט")
        self.job_summary.setObjectName("FieldValue")
        self.job_summary.setWordWrap(True)
        detail.add(self.job_summary)

        row = QHBoxLayout()
        row.setSpacing(METRICS.space(2))
        self.open_button = QPushButton("פתיחה לעבודה")
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(self.open_job)
        row.addWidget(self.open_button)

        self.status_combo = QComboBox()
        row.addWidget(self.status_combo)
        advance = QPushButton("עדכון סטטוס")
        advance.clicked.connect(self.advance_status)
        row.addWidget(advance)

        pack = QPushButton("תיק עבודה להדפסה")
        pack.clicked.connect(self.export_dossier)
        row.addWidget(pack)
        row.addStretch(1)
        detail.add_layout(row)
        layout.addWidget(detail)

        note = QLabel(
            "הפרויקט נשמר כקובץ אחד לכל עבודה בתיקיית הנתונים — אפשר לגבות "
            "אותו, לשלוח אותו במייל ולפתוח אותו גם בעוד חמש שנים. הפתחים "
            "נבנים מחדש בכל פתיחה, כך שתיקון בכללי המערכת מגיע גם לעבודות ישנות."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _selected_job(self) -> Any:
        rows = self.jobs_table.selectionModel()
        if rows is None or not rows.selectedRows():
            return None
        index = rows.selectedRows()[0].row()
        if index >= len(self._jobs):
            return None
        return self._jobs[index]

    def _show_job(self) -> None:
        from ..projects import TRANSITIONS

        job = self._selected_job()
        self.show_costing(job)
        self.show_attachments(job)
        self.status_combo.clear()
        if job is None:
            self.job_summary.setText("לא נבחר פרויקט")
            return

        lines = [f"{job.job_id} · {job.name}"]
        if job.customer_name:
            lines.append(f"לקוח: {job.customer_name}")
        if job.site_address:
            lines.append(f"אתר: {job.site_address}")
        if job.reference:
            lines.append(f"אסמכתה: {job.reference}")
        lines.append(
            f"סטטוס: {job.status.hebrew} · {job.unit_count} יחידות "
            f"({job.opening_count} פתחים) · "
            f"⁦{job.total_area:.1f} m²⁩"
        )
        if job.quote_total:
            lines.append(f"הצעה אחרונה: ⁦{job.quote_total:,.0f} ₪⁩ ({job.quoted_on})")
        self.job_summary.setText("  ·  ".join(lines[:1]) + "\n" + "\n".join(lines[1:]))

        for status in sorted(TRANSITIONS[job.status], key=lambda s: s.value):
            self.status_combo.addItem(status.hebrew, status.value)
        self.status_combo.setEnabled(self.status_combo.count() > 0)

    def new_job(self) -> None:
        from ..systems import DIRECTORY
        from .dialogs import NewJobDialog

        systems = [("generic", "כללי")]
        systems += [
            (entry.id, f"{entry.hebrew or entry.display}")
            for entry in sorted(DIRECTORY, key=lambda e: e.display)
        ]
        customers = self._book().all()
        dialog = NewJobDialog(customers, systems, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        customer = next(
            (c for c in customers if c.customer_id == values["customer_id"]), None
        )
        try:
            job = self._store().create(
                values["name"],
                customer=customer,
                reference=values["reference"],
                site_address=values["site_address"],
                system_id=values["system_id"],
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לפתוח את הפרויקט")
            return

        self.session.set_job(job)
        self.refresh()
        self.status(f"נפתח פרויקט {job.job_id} — {job.name}")

    def open_job(self) -> None:
        job = self._selected_job()
        if job is None:
            self.report(ProfileOSError("בחר פרויקט מהרשימה"), "לא נבחר פרויקט")
            return

        problems: list[str] = []
        if job.schedule is not None:
            problems = self.session.load_schedule(job.schedule)
        else:
            self.session.clear_builds()
        self.session.set_job(job)
        for problem in problems[:3]:
            self.status(problem)
        self.status(
            f"נפתח {job.job_id} — {len(self.session.builds)} פתחים נטענו"
            if self.session.builds
            else f"נפתח {job.job_id} — עדיין אין בו פתחים"
        )
        self.refresh()

    def save_current(self) -> None:
        """Write what is on screen into the open job."""
        job = self.session.job
        if job is None:
            self.report(
                ProfileOSError("אין פרויקט פתוח. פתח פרויקט חדש או בחר קיים."),
                "אין לאן לשמור",
            )
            return

        job.schedule = self.session.to_schedule(
            name=job.name, system_id=job.system_id
        )
        if self.session.quote is not None:
            job.record_quote(
                float(self.session.quote.net_price),
                getattr(self.session.quote, "currency", "ILS"),
            )
        try:
            self._store().save(job)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "השמירה נכשלה")
            return
        self.refresh()
        self.status(f"נשמרו {job.opening_count} פתחים ב{job.job_id}")

    def advance_status(self) -> None:
        from ..projects import JobStatus

        job = self._selected_job()
        if job is None or not self.status_combo.count():
            self.report(ProfileOSError("בחר פרויקט וסטטוס"), "אין מה לעדכן")
            return
        target = JobStatus(self.status_combo.currentData())
        try:
            job.advance(target)
            self._store().save(job)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לעדכן את הסטטוס")
            return
        if self.session.job is not None and self.session.job.job_id == job.job_id:
            self.session.set_job(job)
        self.refresh()
        self.status(f"{job.job_id} עודכן ל{target.hebrew}")

    def export_dossier(self) -> None:
        """Write the whole job as one printable page.

        The elements are rebuilt from the job's own schedule rather than taken
        from the screen, so the pack describes the job as it is saved — printing
        one job while another is open cannot mix the two.
        """
        from ..projects import write_dossier

        job = self._selected_job()
        if job is None:
            self.report(ProfileOSError("בחר פרויקט מהרשימה"), "אין מה להדפיס")
            return

        builds = list(self.session.builds)
        if self.session.job is None or self.session.job.job_id != job.job_id:
            builds = []
            if job.schedule is not None:
                from ..elements.builder import ElementBuilder

                for opening in job.schedule.openings:
                    try:
                        builder = ElementBuilder.for_system(
                            opening.system_id or job.system_id
                        )
                    except Exception:  # noqa: BLE001 - generic rules will do
                        builder = ElementBuilder()
                    try:
                        builds.append(builder.build(opening))
                    except Exception:  # noqa: BLE001 - reported by omission
                        _log.warning("Dossier skipped %s", opening.element_id)

        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת תיק העבודה", f"{job.job_id}.html", "דפי HTML (*.html)"
        )
        if not path:
            return
        try:
            written = write_dossier(job, builds, path)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להפיק את תיק העבודה")
            return
        self.status(f"תיק העבודה נשמר: {written}")

    # -- customers ---------------------------------------------------------- #
    def _costing_tab(self) -> QWidget:
        """Is this job making money — asked while it can still be answered."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.costing_headline = QLabel("")
        self.costing_headline.setObjectName("StatLabel")
        self.costing_headline.setWordWrap(True)
        layout.addWidget(self.costing_headline)

        self.costing_table = DataTable(
            ["", "ערך"],
            empty_text="בחר פרויקט ברשימה כדי לראות את הרווחיות שלו",
        )
        self.costing_table.horizontalHeader().setVisible(False)
        layout.addWidget(self.costing_table, 1)

        self.costing_notes = QLabel("")
        self.costing_notes.setObjectName("Hint")
        self.costing_notes.setWordWrap(True)
        layout.addWidget(self.costing_notes)

        note = QLabel(
            "הרווח כאן נקרא מארבעה צדדים — מה שהוצע, מה שהוזמן מספקים, מה "
            "שחויב וכמה חזרנו לאתר. מה שאין עליו נתון נאמר במפורש ולא נכנס "
            "לחשבון, כי רווח שמניח אפס לעבודה שלא נרשמה גרוע מאין רווח."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def show_costing(self, job: Any) -> None:
        """Fill the profitability panel for one job."""
        from ..projects.costing import cost_job

        if job is None:
            self.costing_table.set_rows([])
            self.costing_headline.setText("")
            return
        try:
            from ..service import default_register

            service = default_register()
        except Exception:  # noqa: BLE001 - costing works without the register
            service = None
        costing = cost_job(job, service=service)
        self.costing_table.set_rows(
            [[label, value] for label, value in costing.summary_rows()]
        )
        self.costing_headline.setText(f"{job.job_id} · {job.name} — {costing.verdict()}")
        self.costing_headline.setStyleSheet(
            f"color: {self.colours.danger if costing.is_losing else self.colours.text};"
        )
        self.costing_notes.setText(" · ".join(costing.warnings))

    def _files_tab(self) -> QWidget:
        """The photographs and papers that settle arguments later."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(2))
        self.attachment_kind = QComboBox()
        from ..projects.attachments import AttachmentKind

        for kind in AttachmentKind:
            self.attachment_kind.addItem(kind.hebrew, kind.value)
        add = QPushButton("צרף קובץ…")
        add.clicked.connect(self.add_attachment)
        controls.addWidget(add)
        controls.addWidget(self.attachment_kind)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.files_table = DataTable(
            ["סוג", "תיאור", "פתח", "נוסף", "מי", "גודל"],
            empty_text="אין מסמכים בתיק — צרף צילום מדידה או הצעה חתומה",
        )
        layout.addWidget(self.files_table, 1)

        self.files_note = QLabel("")
        self.files_note.setObjectName("Hint")
        self.files_note.setWordWrap(True)
        layout.addWidget(self.files_note)

        note = QLabel(
            "הקבצים נשמרים בתיקיית העבודה עצמה — אפשר לפתוח אותם, לשלוח אותם "
            "ולגבות אותם בלי התוכנה. לכל קובץ נשמרת חתימת ביקורת, כך שאם מסמך "
            "חתום הוחלף אחרי שהוגש — רואים את זה."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def add_attachment(self) -> None:
        from ..projects.attachments import AttachmentKind, attachments_for

        job = self._selected_job()
        if job is None:
            self.report(ProfileOSError("בחר פרויקט מהרשימה"), "לא נבחר פרויקט")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "צירוף מסמך לתיק", "",
            "מסמכים וצילומים (*.jpg *.jpeg *.png *.heic *.pdf *.dxf *.dwg);;כל הקבצים (*)",
        )
        if not path:
            return
        try:
            attachments_for(job.job_id).add(
                Path(path),
                kind=AttachmentKind(self.attachment_kind.currentData()),
                caption=Path(path).stem,
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לצרף את הקובץ")
            return
        self.show_attachments(job)
        self.status(f"צורף {Path(path).name}")

    def show_attachments(self, job: Any) -> None:
        from ..projects.attachments import attachments_for

        if job is None:
            self.files_table.set_rows([])
            self.files_note.setText("")
            return
        store = attachments_for(job.job_id)
        self.files_table.set_rows([
            [
                item.kind.hebrew, item.caption or item.name, item.element or "—",
                item.added_at[:10], item.added_by or "—",
                f"{item.size / 1024:,.0f} KB",
            ]
            for item in store
        ])
        summary = store.summary()
        parts = [
            f"⁦{summary['count']}⁩ מסמכים",
            f"⁦{summary['photos']}⁩ צילומים",
            f"⁦{summary['megabytes']:.1f}⁩ MB",
        ]
        changed = store.changed()
        if changed:
            parts.append(
                f"⁦{len(changed)}⁩ קבצים שונו מאז שצורפו — בדוק אותם"
            )
        if summary["missing"]:
            parts.append(f"⁦{summary['missing']}⁩ קבצים חסרים מהתיקייה")
        self.files_note.setText(" · ".join(parts))
        self.files_note.setStyleSheet(
            f"color: {self.colours.warning};" if changed or summary["missing"] else ""
        )

    def _customers_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        add = QPushButton("לקוח חדש")
        add.clicked.connect(self.new_customer)
        layout.addWidget(add)

        self.customers_table = DataTable(
            ["קוד", "שם", "איש קשר", "טלפון", "דוא\"ל", "עיר", "מספר עוסק"],
            empty_text="עדיין אין לקוחות — לחץ ״לקוח חדש״",
        )
        layout.addWidget(self.customers_table, 1)

        note = QLabel(
            "הלקוחות משותפים לכל הפרויקטים: פרטי הלקוח נכנסים להצעת המחיר "
            "ולמסמכי המשלוח בלי להקליד אותם מחדש בכל עבודה."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def new_customer(self) -> None:
        from .dialogs import NewCustomerDialog

        dialog = NewCustomerDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            customer = self._book().add(**values)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להוסיף את הלקוח")
            return
        self.refresh()
        self.status(f"נוסף לקוח {customer.name}")

    # -- refresh ------------------------------------------------------------- #
    def refresh(self) -> None:
        store = self._store()
        self._jobs = store.all()
        needle = self.search.text().strip().lower()
        if needle:
            self._jobs = [
                job for job in self._jobs
                if needle in " ".join((
                    job.job_id, job.name, job.customer_name,
                    job.site_address, job.reference,
                )).lower()
            ]

        colours: dict[tuple[int, int], str] = {}
        tint = {
            "enquiry": self.colours.text_muted,
            "quoted": self.colours.info,
            "won": self.colours.accent,
            "in_production": self.colours.warning,
            "installed": self.colours.success,
            "lost": self.colours.danger,
        }
        rows = []
        for index, job in enumerate(self._jobs):
            rows.append([
                job.job_id, job.name, job.customer_name or "—",
                job.status.hebrew, str(job.unit_count),
                # The shekel sits after the figure in Hebrew, and the whole
                # amount is isolated so the bidi run does not reorder it.
                f"⁦{job.quote_total:,.0f} ₪⁩" if job.quote_total else "—",
                job.updated,
            ])
            colours[(index, 3)] = tint.get(job.status.value, self.colours.text)
        self.jobs_table._empty_text = (
            f"לא נמצא פרויקט התואם ״{needle}״" if needle
            else "אין עדיין פרויקטים — לחץ ״פרויקט חדש״ כדי לפתוח את הראשון"
        )
        self.jobs_table.set_rows(rows, numeric_columns=(4, 5), colours=colours)
        # A list with a row nobody has clicked still has a most-recent job, and
        # showing its detail beats an empty panel that says nothing was chosen.
        if rows and not self.jobs_table.selectionModel().hasSelection():
            self.jobs_table.setCurrentCell(0, 0)

        pipeline = store.pipeline()
        open_count = sum(
            count for status, count in pipeline.items()
            if status not in ("installed", "lost")
        )
        backlog = store.backlog_value()
        self.stats.update_many({
            "open": (str(open_count) if open_count else "—", "בעבודה כרגע"),
            "quoted": (str(pipeline["quoted"]) if pipeline["quoted"] else "—",
                       "ממתינות לתשובה"),
            "production": (
                str(pipeline["in_production"]) if pipeline["in_production"] else "—",
                "על רצפת הייצור",
            ),
            "backlog": (
                f"⁦{backlog:,.0f} ₪⁩" if backlog else "—",
                "הוזמן וטרם הותקן",
            ),
        })

        self.customers_table.set_rows([
            [c.customer_id, c.name, c.contact or "—", c.phone or "—",
             c.email or "—", c.city or "—", c.tax_id or "—"]
            for c in self._book().all()
        ])

        job = self.session.job
        self.header.set_subtitle(
            f"פתוח: {job.job_id} — {job.name}" if job is not None
            else "תיקי עבודה, לקוחות ומצב ההזמנות"
        )


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #

class ProfilePage(Page):
    """Import a DXF cross-section and analyse it."""

    title = "Profile"
    hebrew = "פרופיל"
    subtitle = "ייבוא חתך מקטלוג היצרן וחישוב תכונות הנדסיות"

    def build(self) -> None:
        # The library first, the file dialog second. A fabricator looking for
        # a mullion knows what a mullion is called; they do not know which
        # folder the supplier's DXF was saved into three months ago.
        self.find_button = QPushButton("חפש פרופיל")
        self.find_button.setObjectName("Primary")
        self.find_button.clicked.connect(self.find_profile)
        self.header.add_action(self.find_button)

        self.open_button = QPushButton("פתח שרטוט...")
        self.open_button.clicked.connect(self.open_dxf)
        self.header.add_action(self.open_button)

        self.stats = StatRow(
            [("area", "שטח מ״מ²"), ("ix", "Iₓ מ״מ⁴"), ("iy", "I_y מ״מ⁴"),
             ("j", "J מ״מ⁴"), ("mass", "משקל ק״ג/מ׳")]
        )
        self.body.addWidget(self.stats)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.view = SectionView(self.colours)
        splitter.addWidget(self.view)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(METRICS.space(3))

        properties_card = Card("חתך")
        tabs = QTabWidget()
        self.properties = DataTable(["סימן", "ערך", "יחידה"])
        tabs.addTab(self.properties, "תכונות")

        features_panel = QWidget()
        features_layout = QVBoxLayout(features_panel)
        features_layout.setContentsMargins(0, 0, 0, 0)
        features_layout.setSpacing(METRICS.space(2))
        self.feature_summary = DataTable(["", ""])
        self.feature_summary.horizontalHeader().setVisible(False)
        self.feature_summary.setMaximumHeight(METRICS.row_height * 6 + 4)
        features_layout.addWidget(self.feature_summary)
        self.features = DataTable(["מאפיין", "פתח", "עומק", "חתירה"])
        features_layout.addWidget(self.features, 1)
        self.feature_notes = QLabel("")
        self.feature_notes.setWordWrap(True)
        self.feature_notes.setObjectName("Muted")
        features_layout.addWidget(self.feature_notes)
        tabs.addTab(features_panel, "מאפיינים")

        properties_card.add(tabs, 1)
        side_layout.addWidget(properties_card, 1)

        check_card = Card("בדיקת עומס רוח")
        fields = FieldGrid()
        self.span = QDoubleSpinBox(); self.span.setRange(100, 20000); self.span.setValue(3000); self.span.setSuffix(" mm")
        self.pressure = QDoubleSpinBox(); self.pressure.setRange(0.1, 10.0); self.pressure.setValue(1.2); self.pressure.setSingleStep(0.1); self.pressure.setSuffix(" kN/m²")
        self.tributary = QDoubleSpinBox(); self.tributary.setRange(100, 10000); self.tributary.setValue(1500); self.tributary.setSuffix(" mm")
        fields.add("מפתח", self.span)
        fields.add("לחץ רוח", self.pressure)
        fields.add("רוחב תורם", self.tributary)
        check_card.add(fields)

        run = QPushButton("בדוק")
        run.clicked.connect(self.run_check)
        check_card.add(run)
        self.checks = DataTable(["בדיקה", "דרישה", "כושר", "ניצולת"])
        check_card.add(self.checks, 1)
        self.max_span = QLabel("—")
        self.max_span.setObjectName("StatLabel")
        check_card.add(self.max_span)
        side_layout.addWidget(check_card, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.body.addWidget(splitter, 1)

    def find_profile(self) -> None:
        """Search the library — the shop's own drawings and the examples."""
        from .finder import find_profile

        chosen = find_profile(self)
        if chosen is not None:
            self.load(chosen.path)

    def load_sample(self) -> None:
        from ..library import sample_profiles

        samples = sample_profiles()
        if not samples:
            self.report(ProfileOSError("שרטוטי הדוגמה לא נמצאו בהתקנה"), "אין דוגמה")
            return
        self.load(samples[0].path)

    def open_dxf(self) -> None:
        """A DWG is accepted too; it is converted on the way in."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "פתיחת שרטוט פרופיל",
            "",
            "שרטוטים (*.dxf *.dwg);;DXF (*.dxf);;DWG (*.dwg);;כל הקבצים (*)",
        )
        if path:
            self.load(Path(path))

    def load(self, path: Path) -> None:
        from ..structural import analyse_dxf

        try:
            properties, section = analyse_dxf(str(path), profile_id=path.stem)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.report(exc, "לא ניתן לנתח את השרטוט")
            return

        # The same drawing, also as a catalogue profile, so a wall detail can be
        # cut against the real outline rather than a schematic box. A failure
        # here costs the detail its outline, never the analysis on screen.
        profile = None
        try:
            from ..geometry import profile_from_dxf

            profile, _ = profile_from_dxf(str(path), path.stem, "ui")
        except Exception:  # noqa: BLE001 - the section itself is what matters
            _log.warning("Could not derive a profile definition from %s", path)

        self.session.set_section(properties, section, path, profile)
        self.view.set_section(section.polygon, properties, section.validation)
        self.properties.set_rows(properties.summary_rows(), numeric_columns=(1,))

        def fmt(value: float | None, digits: int = 0) -> str:
            return "—" if value is None else f"{value:,.{digits}f}"

        self.stats.update_many({
            "area": (fmt(properties.area, 1), path.stem),
            "ix": (fmt(properties.ixx), ""),
            "iy": (fmt(properties.iyy), ""),
            "j": (fmt(properties.j), properties.torsion_method or ""),
            "mass": (fmt(properties.mass_per_metre, 3), properties.material_id or ""),
        })
        self.header.set_subtitle(
            f"{path.name} · \u2066{section.width:.0f} × {section.height:.0f}\u2069 מ״מ · "
            f"{section.topology.chamber_count} תאים"
        )
        self.show_features(section, properties.material_id)
        self.status(f"נותח {path.name}")
        self.run_check()

    def show_features(self, section: Any, material: str | None) -> None:
        """Fill the Features tab with what was read off the drawing.

        A failure here must not cost the operator the structural analysis they
        actually asked for, so it is reported in the panel rather than raised.
        """
        from ..geometry.features import features_for_section

        try:
            report = features_for_section(section, material=material)
        except Exception as exc:  # noqa: BLE001 - the section is still usable
            _log.warning("Feature recognition failed: %s", exc)
            self.feature_summary.set_rows([["זיהוי מאפיינים", "לא זמין"]])
            self.features.set_rows([])
            self.feature_notes.setText(str(exc))
            return

        self.session.set_features(report)
        self.feature_summary.set_rows(
            [[label, value] for label, value in report.summary_rows()]
        )
        rows = []
        colours: dict[tuple[int, int], str] = {}
        for index, feature in enumerate(report.features):
            pocket = feature.pocket
            rows.append([
                feature.kind.label(self.session.language),
                f"{pocket.mouth:.2f}",
                f"{pocket.depth:.2f}",
                f"{pocket.undercut:.2f}" if pocket.undercut > 0.4 else "—",
            ])
            if feature.kind.value != "pocket":
                colours[(index, 0)] = self.colours.accent
        self.features.set_rows(rows, numeric_columns=(1, 2, 3), colours=colours)

        notes = [strip.evidence and
                 f"פס פוליאמיד \u2066{strip.width:.1f}\u2069 מ״מ"
                 for strip in report.strips]
        notes.extend(report.warnings)
        self.feature_notes.setText("  ".join(note for note in notes if note))

    def run_check(self) -> None:
        if self.session.section_properties is None:
            return
        from ..models.materials import get_material
        from ..structural import LoadCase, check_member, maximum_span, wind_line_load

        properties = self.session.section_properties
        material = get_material(properties.material_id)
        load = LoadCase(
            lateral_line_load=wind_line_load(self.pressure.value(), self.tributary.value())
        )
        try:
            check = check_member(properties, material, span=self.span.value(), load=load)
            limit = maximum_span(
                properties, material,
                pressure_kn_m2=self.pressure.value(),
                tributary_width_mm=self.tributary.value(),
            )
        except ProfileOSError as exc:
            self.report(exc, "הבדיקה נכשלה")
            return

        colours: dict[tuple[int, int], str] = {}
        rows = []
        check_names = {
            "bending": "כפיפה", "shear": "גזירה", "deflection": "כפף",
            "web crippling": "מעיכת דופן", "buckling": "קריסה",
        }
        for index, entry in enumerate(check.results):
            name = next(
                (hebrew for key, hebrew in check_names.items() if key in entry.name.lower()),
                entry.name,
            )
            rows.append([
                name, f"{entry.demand:.4g}", f"{entry.capacity:.4g}",
                f"{entry.utilisation * 100:.1f}%",
            ])
            colours[(index, 3)] = self.colours.success if entry.passes else self.colours.danger
        self.checks.set_rows(rows, numeric_columns=(1, 2, 3), colours=colours)

        verdict = "עומד" if check.passes else "נכשל"
        self.max_span.setText(
            f"הפרופיל {verdict} במפתח \u2066{self.span.value():.0f}\u2069 מ״מ. "
            f"המפתח המרבי לעומסים אלה: \u2066{limit:,.0f}\u2069 מ״מ."
        )


# --------------------------------------------------------------------------- #
# Element
# --------------------------------------------------------------------------- #

class ElementPage(Page):
    """Design a window, door or curtain-wall element."""

    title = "Element"
    hebrew = "פתח"
    subtitle = "תכנון פתח וגזירת רשימת חיתוך, זכוכית ופרזול"

    def build(self) -> None:
        # Searching the library is the first action on this screen, because
        # nobody's day starts by typing twelve numbers for a window they have
        # made four hundred times. The form stays exactly where it was for the
        # one that is genuinely new.
        find_button = QPushButton("חפש פתח")
        find_button.setObjectName("Primary")
        find_button.clicked.connect(self.find_opening)
        self.header.add_action(find_button)

        build_button = QPushButton("בנה פתח")
        build_button.clicked.connect(self.build_element)
        self.header.add_action(build_button)

        template_button = QPushButton("תבניות")
        template_button.clicked.connect(self.use_template)
        self.header.add_action(template_button)

        save_template = QPushButton("שמור כתבנית")
        save_template.clicked.connect(self.save_as_template)
        self.header.add_action(save_template)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        form_card = Card("פתח")
        fields = FieldGrid()
        self.name = QComboBox(); self.name.setEditable(True); self.name.addItems(["W-04", "D-01", "CW-01"])
        self.width = QDoubleSpinBox(); self.width.setRange(200, 12000); self.width.setValue(2400); self.width.setSuffix(" mm")
        self.height = QDoubleSpinBox(); self.height.setRange(200, 6000); self.height.setValue(1800); self.height.setSuffix(" mm")
        self.quantity = QSpinBox(); self.quantity.setRange(1, 999); self.quantity.setValue(4)
        self.kind = QComboBox()
        for kind_id, kind_he in [("window", "חלון"), ("door", "דלת"), ("curtain_wall", "קיר מסך"),
                                 ("shopfront", "חזית מסחרית"), ("sliding_unit", "מערכת הזזה")]:
            self.kind.addItem(kind_he, kind_id)
        self.columns = QSpinBox(); self.columns.setRange(1, 12); self.columns.setValue(3)
        self.rows = QSpinBox(); self.rows.setRange(1, 12); self.rows.setValue(1)
        self.sash_column = QSpinBox(); self.sash_column.setRange(0, 11); self.sash_column.setValue(1)
        self.sash_row = QSpinBox(); self.sash_row.setRange(0, 11); self.sash_row.setValue(0)
        self.sash_type = QComboBox()
        from ..elements.model import OpeningType as _OT

        for sash_kind in ("fixed", "casement", "tilt_turn", "top_hung", "sliding", "door"):
            self.sash_type.addItem(_OT(sash_kind).label("he"), sash_kind)
        self.sash_type.setCurrentIndex(2)
        self.sill_height = QDoubleSpinBox(); self.sill_height.setRange(0, 100000)
        self.sill_height.setValue(900); self.sill_height.setSuffix(" mm")

        # Where it is fitted. Two small fields, and they are what turns a pile
        # of finished units into a loading list somebody can work from.
        self.location = QLineEdit()
        self.location.setPlaceholderText("סלון, חדר שינה, מרפסת…")
        self.floor = QSpinBox()
        self.floor.setRange(-3, 60)
        self.floor.setSuffix(" קומה")

        # The series is a field like any other, because "which system is this
        # in" is a question the shop answers per element, not per install.
        # Generic keeps the family stand-ins and says so on the cut list.
        self.system = QComboBox()
        self.system.addItem("רגיל — ערכים טיפוסיים", "generic")
        from ..systems import DIRECTORY as _DIRECTORY

        for entry in sorted(_DIRECTORY, key=lambda e: (e.manufacturer, e.series)):
            self.system.addItem(entry.display, entry.id)

        # What is fitted to the window. On an Israeli flat this is not an
        # extra — a bedroom window without a shutter is unfinished — so it is
        # on the form beside the size rather than on a screen of its own.
        self.shutter = QComboBox()
        self.shutter.addItem("ללא תריס", "")
        from ..accessories import SLATS, Drive as _Drive

        for _slat in SLATS:
            for _drive in (_Drive.MOTOR, _Drive.STRAP):
                self.shutter.addItem(
                    f"{_slat.hebrew} · {_drive.hebrew}", f"{_slat.slat_id}|{_drive.value}"
                )

        self.screen = QComboBox()
        self.screen.addItem("ללא רשת", "")
        from ..accessories import MeshKind as _Mesh, ScreenKind as _ScreenKind

        for _kind in _ScreenKind:
            for _mesh in (_Mesh.FIBREGLASS, _Mesh.ALUMINIUM):
                self.screen.addItem(
                    f"{_kind.hebrew} · {_mesh.hebrew}", f"{_kind.value}|{_mesh.value}"
                )

        self.sill = QComboBox()
        from ..accessories import SillKind as _SillKind

        for _sill in _SillKind:
            self.sill.addItem(_sill.hebrew, _sill.value)

        self.glass = QComboBox()

        from ..glazing import STANDARD_BUILDUPS

        hebrew_kinds = {"mono": "מונוליטית", "dgu": "בידודית", "tgu": "טריפל", "lam": "טריפלקס"}
        for key, unit in STANDARD_BUILDUPS.items():
            kind = next((name for prefix, name in hebrew_kinds.items() if key.startswith(prefix)), "")
            self.glass.addItem(f"{kind} \u2066{unit.describe()}\u2069 · U \u2066{unit.u_value():.2f}\u2069", key)
        self.glass.setCurrentIndex(min(1, self.glass.count() - 1))

        for label, widget in [
            ("שם", self.name), ("סוג", self.kind), ("רוחב", self.width),
            ("גובה", self.height), ("כמות", self.quantity), ("עמודות", self.columns),
            ("שורות", self.rows), ("עמודת כנף", self.sash_column), ("שורת כנף", self.sash_row),
            ("סוג פתיחה", self.sash_type), ("גובה סף", self.sill_height),
            ("סדרה", self.system), ("זכוכית", self.glass),
            ("תריס", self.shutter), ("רשת", self.screen), ("אדן", self.sill),
            ("מיקום", self.location), ("קומה", self.floor),
        ]:
            fields.add(label, widget)
        form_card.add(fields)
        form_card.body.addStretch(1)
        form_card.setMaximumWidth(METRICS.panel_width)
        splitter.addWidget(form_card)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(METRICS.space(3))

        self.stats = StatRow([
            ("pieces", "חלקים"), ("glass", "זכוכית מ״ר"), ("mass", "זכוכית ק״ג"),
            ("gasket", "אטמים מ׳"), ("hardware", "פרזול"),
        ])
        right_layout.addWidget(self.stats)

        inner = QSplitter(Qt.Orientation.Vertical)
        self.view = ElevationView(self.colours)
        inner.addWidget(self.view)

        lower = QWidget()
        lower_layout = QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(METRICS.space(2))

        # The verdict sits above the tabs rather than inside one, because a
        # blocker the operator has to click to find is a blocker they will miss.
        self.verdict = QLabel("")
        self.verdict.setObjectName("StatLabel")
        self.verdict.setWordWrap(True)
        lower_layout.addWidget(self.verdict)

        tabs = QTabWidget()
        self.cuts = DataTable(["תפקיד", "פרופיל", "אורך", "כמות", "זוויות"])
        self.panes = DataTable(["סימון", "מידה", "מפרט", "שטח מ״ר", "משקל ק״ג", "U", "בטיחות"])
        self.hardware = DataTable(["קוד", "פריט", "כמות", "יחידה"])
        self.hardware_check = QLabel("")
        self.hardware_check.setObjectName("Hint")
        self.hardware_check.setWordWrap(True)
        self.feasibility = DataTable(["", "היכן", "מה", "נמדד", "גבול"])
        self.warnings = QPlainTextEdit(); self.warnings.setReadOnly(True)

        # Accessories get a panel rather than a column, because the number the
        # builder is waiting for — the hole to leave in the wall — belongs
        # beside them and nowhere else.
        fitted = QWidget()
        fitted_layout = QVBoxLayout(fitted)
        fitted_layout.setContentsMargins(0, 0, 0, 0)
        fitted_layout.setSpacing(METRICS.space(2))
        self.opening_note = QLabel("")
        self.opening_note.setObjectName("StatLabel")
        self.opening_note.setWordWrap(True)
        fitted_layout.addWidget(self.opening_note)
        self.accessories = DataTable(
            ["אביזר", "מידה", "כמות", "משקל ק״ג", "פרטים"],
            empty_text="לא נבחרו אביזרים — בחר תריס, רשת או אדן בטופס",
        )
        fitted_layout.addWidget(self.accessories, 1)

        # Performance is where a specification is won or lost, and it is the
        # panel a customer's engineer reads first.
        performance = QWidget()
        performance_layout = QVBoxLayout(performance)
        performance_layout.setContentsMargins(0, 0, 0, 0)
        performance_layout.setSpacing(METRICS.space(2))
        self.performance_headline = QLabel("")
        self.performance_headline.setObjectName("StatLabel")
        self.performance_headline.setWordWrap(True)
        performance_layout.addWidget(self.performance_headline)
        # Side by side rather than stacked: the figures are a narrow column and
        # the findings are sentences, and stacking gave each of them half of a
        # panel that was already short.
        performance_split = QSplitter(Qt.Orientation.Horizontal)
        self.performance = DataTable(
            ["", "ערך"],
            empty_text="בנה פתח כדי לראות ⁦U_w⁩, ⁦R_w⁩ ולחץ רוח",
        )
        self.performance.horizontalHeader().setVisible(False)
        performance_split.addWidget(self.performance)
        self.compliance = DataTable(["", "נושא", "תקן", "ממצא"])
        performance_split.addWidget(self.compliance)
        performance_split.setStretchFactor(0, 0)
        performance_split.setStretchFactor(1, 1)
        performance_split.setSizes([METRICS.panel_width, METRICS.panel_width * 2])
        performance_layout.addWidget(performance_split, 1)

        tabs.addTab(self.cuts, "רשימת חיתוך")
        tabs.addTab(self.panes, "זכוכית")
        # The hardware tab carries the load check under the list, because the
        # question "will this sash still close in five years" is answered by
        # the rating and nothing else on this screen.
        hardware_panel = QWidget()
        hardware_layout = QVBoxLayout(hardware_panel)
        hardware_layout.setContentsMargins(0, 0, 0, 0)
        hardware_layout.setSpacing(METRICS.space(2))
        hardware_layout.addWidget(self.hardware, 1)
        hardware_layout.addWidget(self.hardware_check)
        tabs.addTab(hardware_panel, "פרזול")
        tabs.addTab(fitted, "אביזרים")
        tabs.addTab(performance, "ביצועים")
        tabs.addTab(self.feasibility, "ישימות")
        tabs.addTab(self.warnings, "אזהרות")
        lower_layout.addWidget(tabs, 1)
        inner.addWidget(lower)
        inner.setStretchFactor(0, 3)
        inner.setStretchFactor(1, 2)
        # On a laptop the drawing will happily take the whole panel and leave
        # the cut list two rows deep. Give the tables a fixed share to start
        # from; the splitter is still there for anybody who wants the drawing
        # larger.
        inner.setSizes([360, 300])
        right_layout.addWidget(inner, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # Give the two panes their proportions outright. Left to itself a
        # splitter halves the space, and a form capped at its panel width
        # leaves the remainder as a hole in the middle of the screen.
        splitter.setSizes([METRICS.panel_width, METRICS.panel_width * 3])
        self.body.addWidget(splitter, 1)

    #: Mark prefixes, so a picked preset arrives named the way the shop names
    #: things rather than as "sliding_3".
    MARK_PREFIX = {
        "window": "W", "door": "D", "curtain_wall": "CW",
        "shopfront": "SF", "sliding_unit": "S",
    }

    def find_opening(self) -> None:
        """Pick a ready-made opening and build it, in two clicks."""
        from .finder import find_opening

        preset = find_opening(self)
        if preset is not None:
            self.apply_preset(preset)

    # -- templates -------------------------------------------------------- #
    def save_as_template(self) -> None:
        """Keep this configuration so the next one of these is two clicks.

        Half the errors in a quotation are in the half that was typed rather
        than chosen, and a shop that fits fifty of the same window a year
        types it fifty times.
        """
        from ..projects.templates import default_templates, template_from_opening

        build = self.session.builds[-1] if self.session.builds else None
        opening = getattr(build, "opening", None)
        if opening is None:
            self.report(
                ProfileOSError("בנה פתח קודם, ואז אפשר לשמור אותו כתבנית"),
                "אין מה לשמור",
            )
            return

        name, ok = QInputDialog.getText(
            self, "שמירת תבנית", "שם התבנית, כפי שהיו קוראים לה בבית המלאכה:",
            text=str(getattr(opening, "name", "") or ""),
        )
        if not ok or not name.strip():
            return

        job = getattr(self.session, "job", None)
        try:
            book = default_templates()
            template = book.add(template_from_opening(
                opening, name=name.strip(),
                from_job=str(getattr(job, "job_id", "") or ""),
            ))
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "שמירת התבנית נכשלה")
            return
        self.status(f"נשמרה תבנית: {template.describe()}")

    def use_template(self) -> None:
        """Pick a saved configuration and make it at the size on the form."""
        from ..projects.templates import default_templates

        try:
            book = default_templates()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לקרוא את התבניות")
            return

        templates = book.popular()
        if not templates:
            self.report(
                ProfileOSError(
                    "עדיין אין תבניות. בנה פתח ולחץ ״שמור כתבנית״."
                ),
                "אין תבניות",
            )
            return

        labels = [template.describe() for template in templates]
        chosen, ok = QInputDialog.getItem(
            self, "תבניות", "איזו תבנית?", labels, 0, False
        )
        if not ok:
            return
        template = templates[labels.index(chosen)]

        try:
            opening = book.use(
                template.template_id,
                self.width.value() or template.typical_width or 1000.0,
                self.height.value() or template.typical_height or 1000.0,
                name=self.next_mark(template.kind),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להשתמש בתבנית")
            return

        self.name.setCurrentText(opening.name)
        index = self.kind.findData(template.kind)
        if index >= 0:
            self.kind.setCurrentIndex(index)
        self.width.setValue(opening.width)
        self.height.setValue(opening.height)
        self.columns.setValue(len(template.mullion_fractions) + 1)
        self.rows.setValue(len(template.transom_fractions) + 1)
        self.build_element()

        if template.price_is_stale and template.last_price_per_m2:
            self.status(
                f"שים לב: {template.price_line()}"
            )

    def next_mark(self, kind: str) -> str:
        """The next free mark for this kind of opening in this job."""
        prefix = self.MARK_PREFIX.get(kind, "E")
        used = {build.opening.name for build in self.session.builds}
        for number in range(1, 1000):
            mark = f"{prefix}-{number:02d}"
            if mark not in used:
                return mark
        return f"{prefix}-999"

    def apply_preset(self, preset: Any) -> None:
        """Fill the form from a library opening, then make it.

        Everything the preset sets is a field the operator can still change —
        it is a starting point, not a lock. Building straight away is the
        point of the exercise: the screen answers with a drawing, a cut list
        and a verdict before anybody has typed anything.
        """
        self.name.setCurrentText(self.next_mark(preset.kind))
        index = self.kind.findData(preset.kind)
        if index >= 0:
            self.kind.setCurrentIndex(index)
        self.width.setValue(preset.width)
        self.height.setValue(preset.height)
        self.quantity.setValue(preset.quantity)
        self.columns.setValue(preset.columns)
        self.rows.setValue(preset.rows)
        self.sash_column.setValue(preset.sash_column)
        self.sash_row.setValue(preset.sash_row)
        sash_index = self.sash_type.findData(preset.sash_type)
        if sash_index >= 0:
            self.sash_type.setCurrentIndex(sash_index)
        self.sill_height.setValue(preset.sill)
        system_index = self.system.findData(preset.system_id)
        self.system.setCurrentIndex(system_index if system_index >= 0 else 0)
        glass_index = self.glass.findData(preset.glass)
        if glass_index >= 0:
            self.glass.setCurrentIndex(glass_index)
        self._apply_fittings(preset.fittings)
        self.build_element()
        self.status(f"{preset.title} · {preset.describe()}")

    def _fittings(self) -> dict:
        """The fit-out the form is asking for, as a job file keeps it."""
        spec: dict[str, Any] = {}
        shutter = self.shutter.currentData()
        if shutter:
            slat_id, drive = shutter.split("|", 1)
            spec["shutter"] = {"slat_id": slat_id, "drive": drive, "box": "built_in"}
        screen = self.screen.currentData()
        if screen:
            kind, mesh = screen.split("|", 1)
            spec["screen"] = {"kind": kind, "mesh": mesh}
        sill = self.sill.currentData()
        if sill and sill != "none":
            spec["sill"] = sill
        return spec

    def _apply_fittings(self, spec: dict) -> None:
        """Set the form's fit-out fields from a saved specification."""
        shutter = spec.get("shutter")
        self.shutter.setCurrentIndex(
            max(0, self.shutter.findData(
                f"{shutter['slat_id']}|{shutter.get('drive', 'motor')}"
            )) if shutter else 0
        )
        screen = spec.get("screen")
        self.screen.setCurrentIndex(
            max(0, self.screen.findData(
                f"{screen['kind']}|{screen.get('mesh', 'fibreglass')}"
            )) if screen else 0
        )
        sill = spec.get("sill") or "none"
        self.sill.setCurrentIndex(max(0, self.sill.findData(sill)))

    def show_accessories(self, build: Any) -> None:
        """What is fitted, what it weighs, and the hole the builder leaves."""
        from ..accessories import accessories_for

        try:
            fitted = accessories_for(build.opening)
        except Exception as exc:  # noqa: BLE001 - never lose a build over a fitting
            _log.warning("Accessory sizing failed: %s", exc)
            self.accessories.set_rows([])
            self.opening_note.setText(str(exc))
            return

        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        for index, accessory in enumerate(fitted):
            detail = "; ".join(accessory.notes) or accessory.metadata.get("kind", "")
            rows.append([
                accessory.hebrew,
                f"{accessory.width:.0f} × {accessory.height:.0f}",
                accessory.quantity,
                f"{accessory.mass * accessory.quantity:.1f}",
                detail,
            ])
            if accessory.warnings:
                colours[(index, 0)] = self.colours.warning
        self.accessories.set_rows(rows, numeric_columns=(2, 3), colours=colours)

        if not len(fitted):
            self.opening_note.setText("")
            self.opening_note.setStyleSheet("")
            return

        width, height = fitted.structural_opening(
            build.opening.width, build.opening.height
        )
        text = (
            f"פתח בנייה נדרש: \u2066{width:.0f} × {height:.0f}\u2069 מ״מ "
            f"(החלון \u2066{build.opening.width:.0f} × {build.opening.height:.0f}\u2069)"
        )
        if fitted.warnings:
            text += " · " + " · ".join(fitted.warnings)
            self.opening_note.setStyleSheet(f"color: {self.colours.warning};")
        else:
            self.opening_note.setStyleSheet(f"color: {self.colours.success};")
        self.opening_note.setText(text)

    def show_hardware_check(self, build: Any) -> None:
        """Whether the shop's own hardware can carry the leaves just drawn.

        Silent when the library is empty: a shop that has not entered a load
        chart yet should not be nagged on every build, but the moment they
        have one, every oversized sash is caught here rather than in a
        warranty call two winters later.
        """
        from ..hardware import default_library

        try:
            library = default_library()
        except Exception:  # noqa: BLE001
            self.hardware_check.setText("")
            return
        if not len(library):
            self.hardware_check.setText(
                "ספריית הפרזול ריקה — הזינו טבלת עומסים של ספק כדי שהתוכנה "
                "תבדוק שהצירים נושאים את הכנף"
            )
            self.hardware_check.setStyleSheet(f"color: {self.colours.text_muted};")
            return

        from ..elements import ElementBuilder

        rects = ElementBuilder().cell_rects(build.opening, build.rules)
        problems: list[str] = []
        heaviest = 0.0
        for cell in build.opening.all_cells():
            sash = getattr(cell, "sash", None)
            if sash is None:
                continue
            rect = rects.get(cell.key) if isinstance(rects, dict) else None
            width = getattr(rect, "width", build.opening.width)
            height = getattr(rect, "height", build.opening.height)
            glass_mass = max(
                (panel.mass / max(panel.area, 1e-6) for panel in build.glass),
                default=25.0,
            )
            selection = library.select_for(
                opening_type=str(sash.opening_type),
                width=width, height=height, glass_mass_per_m2=glass_mass,
            )
            heaviest = max(heaviest, selection.sash_mass)
            problems.extend(selection.unmet)
            problems.extend(selection.warnings)

        if not heaviest:
            self.hardware_check.setText("")
            return
        if problems:
            self.hardware_check.setText(
                f"כנף כבדה ביותר ⁦{heaviest:.0f}⁩ ק״ג · " + " · ".join(problems[:3])
            )
            self.hardware_check.setStyleSheet(f"color: {self.colours.danger};")
        else:
            self.hardware_check.setText(
                f"כנף כבדה ביותר ⁦{heaviest:.0f}⁩ ק״ג — הפרזול בספרייה נושא אותה"
            )
            self.hardware_check.setStyleSheet(f"color: {self.colours.success};")

    def show_performance(self, build: Any) -> None:
        """U-value, sound reduction, wind pressure and what they are judged by."""
        from ..compliance import Site, Verdict, check_compliance

        try:
            report = check_compliance(build, Site())
        except Exception as exc:  # noqa: BLE001 - the build stands without it
            _log.warning("Compliance check failed: %s", exc)
            self.performance.set_rows([])
            self.compliance.set_rows([])
            self.performance_headline.setText(str(exc))
            return

        rows: list[list[Any]] = []
        for section in (report.thermal, report.acoustic, report.wind, report.classes):
            if section is not None:
                rows.extend([label, value] for label, value in section.summary_rows())
        self.performance.set_rows(rows)

        tone = {
            Verdict.FAIL: self.colours.danger,
            Verdict.CHECK: self.colours.warning,
            Verdict.PASS: self.colours.success,
        }
        finding_rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        for index, finding in enumerate(report.findings):
            finding_rows.append([
                finding.verdict.hebrew, finding.subject,
                finding.citation or "—", finding.text,
            ])
            colours[(index, 0)] = tone[finding.verdict]
        self.compliance.set_rows(finding_rows, colours=colours)

        headline = report.verdict()
        if report.thermal and report.acoustic:
            headline = (
                f"{report.thermal.describe()} · {report.acoustic.describe()} · {headline}"
            )
        self.performance_headline.setText(headline)
        self.performance_headline.setStyleSheet(
            f"color: {self.colours.danger if report.failures else self.colours.text};"
        )

    def _builder(self, opening: Any) -> Any:
        """The rule set for the chosen series, or the generic one.

        An unclassified series is refused with the reason rather than quietly
        cut on a stand-in: a bar cut to a guess is a bar in the skip.
        """
        from ..elements import ElementBuilder

        if opening.system_id in ("", "generic"):
            return ElementBuilder()
        from ..systems import UnclassifiedSystem

        try:
            return ElementBuilder.for_system(opening.system_id)
        except UnclassifiedSystem as exc:
            raise ProfileOSError(
                "הסדרה עדיין לא סווגה, אז אין לפי מה לגזור. "
                "סווג אותה בעמוד ״קטלוג״ ואז חזור לכאן."
            ) from exc

    def build_element(self) -> None:
        from ..elements import Cell, ElementBuilder, ElementKind, Opening, OpeningType, Sash

        try:
            opening = Opening(
                name=self.name.currentText() or "Element",
                kind=ElementKind(self.kind.currentData()),
                width=self.width.value(), height=self.height.value(),
                quantity=self.quantity.value(),
                glass_spec_id=self.glass.currentData(),
                system_id=self.system.currentData() or "generic",
            )
            opening.metadata["accessories"] = self._fittings()
            if self.location.text().strip() or self.floor.value():
                opening.metadata["place"] = {
                    "location": self.location.text().strip(),
                    "floor": self.floor.value(),
                    # Fitted in the order they were designed, unless somebody
                    # says otherwise: it is the order the shop thought in.
                    "sequence": len(self.session.builds),
                }
            opening.divide_evenly(self.columns.value(), self.rows.value())

            sash_type = OpeningType(self.sash_type.currentData())
            if sash_type is not OpeningType.FIXED:
                column = min(self.sash_column.value(), opening.column_count - 1)
                row = min(self.sash_row.value(), opening.row_count - 1)
                opening.set_cell(Cell(column=column, row=row, sash=Sash(opening_type=sash_type)))

            build = self._builder(opening).build(opening, sill_height=self.sill_height.value())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לבנות את הפתח")
            return

        self.session.add_build(build)
        self.view.set_build(build)

        summary = build.summary()
        self.stats.update_many({
            "pieces": (str(summary["pieces"]), f"×{opening.quantity}"),
            "glass": (f"{summary['glass_area_m2']:.2f}", f"{summary['glass_panes']} שמשות"),
            "mass": (f"{summary['glass_mass_kg']:.0f}", ""),
            "gasket": (f"{summary['gasket_m']:.1f}", ""),
            "hardware": (str(summary["hardware_items"]), ""),
        })

        self.cuts.set_rows(
            [[c.role, c.profile_id, f"{c.length:.1f}", c.quantity,
              f"{c.angle_left:g}/{c.angle_right:g}"]
             for c in sorted(build.cuts, key=lambda c: (c.role, -c.length))],
            numeric_columns=(2, 3, 4),
        )

        colours: dict[tuple[int, int], str] = {}
        pane_rows = []
        for index, panel in enumerate(build.glass):
            safety = "תקין" if panel.compliant else ("נדרשת בטיחותית" if panel.safety_required else "—")
            if not panel.compliant:
                colours[(index, 6)] = self.colours.danger
            pane_rows.append([
                panel.mark, f"{panel.width:.0f} × {panel.height:.0f}",
                panel.build_up.describe(), f"{panel.area:.3f}", f"{panel.mass:.1f}",
                f"{panel.build_up.u_value():.2f}", safety,
            ])
        self.panes.set_rows(pane_rows, numeric_columns=(3, 4, 5), colours=colours)

        self.hardware.set_rows(
            [[h.code, h.name, h.quantity, h.unit] for h in build.hardware], numeric_columns=(2,)
        )
        self.warnings.setPlainText(
            "\n".join(f"• {w}" for w in build.warnings) or "אין אזהרות."
        )
        self.show_feasibility(build)
        self.show_accessories(build)
        self.show_performance(build)
        self.show_hardware_check(build)
        self.header.set_subtitle(
            f"{opening.name}: \u2066{opening.width:.0f} × {opening.height:.0f}\u2069 מ״מ · "
            f"רשת \u2066{opening.column_count}×{opening.row_count}\u2069 · "
            f"{len(self.session.builds)} פתחים בפרויקט"
        )
        self.status(f"נבנה {opening.name}")

    @staticmethod
    def _finding_text(finding: Any) -> str:
        """The Hebrew wording where there is one, the English otherwise.

        Findings carry two prepared strings rather than a key, because most of
        them quote a measurement that has already been formatted.
        """
        return finding.hebrew or finding.english

    def show_feasibility(self, build: Any) -> None:
        """Say straight away whether what was just drawn can be made."""
        from ..elements.feasibility import Severity, check_element

        try:
            report = check_element(
                build,
                sill_height=self.sill_height.value() if hasattr(self, "sill_height") else 0.0,
            )
        except Exception as exc:  # noqa: BLE001 - never lose the build over a check
            _log.warning("Feasibility check failed: %s", exc)
            self.verdict.setText("")
            self.feasibility.set_rows([])
            return

        self.session.set_feasibility(report)
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        tone = {
            Severity.BLOCKER: self.colours.danger,
            Severity.WARNING: self.colours.warning,
            Severity.NOTE: self.colours.text_muted,
        }
        for index, finding in enumerate(report.sorted()):
            rows.append([
                finding.severity.label(self.session.language),
                finding.subject,
                self._finding_text(finding),
                "" if finding.measured is None else f"{finding.measured:.1f}",
                "" if finding.limit is None else f"{finding.limit.value:.1f} {finding.limit.unit}",
            ])
            colours[(index, 0)] = tone[finding.severity]
        self.feasibility.set_rows(rows, numeric_columns=(3, 4), colours=colours)

        if not report.blockers and not report.warnings:
            notes = len(report.findings)
            tail = f" ({notes} הערות)" if notes else ""
            self.verdict.setText(f"ניתן לייצור כמתוכנן{tail}")
            self.verdict.setStyleSheet(f"color: {self.colours.success};")
        elif report.can_be_made:
            self.verdict.setText(f"ניתן לייצור, עם {len(report.warnings)} נקודות לבדיקה")
            self.verdict.setStyleSheet(f"color: {self.colours.warning};")
        else:
            first = report.blockers[0]
            self.verdict.setText(f"לא ניתן לייצור: {self._finding_text(first)}")
            self.verdict.setStyleSheet(f"color: {self.colours.danger};")


# --------------------------------------------------------------------------- #
# Drawings
# --------------------------------------------------------------------------- #

class DrawingsPage(Page):
    """Shop drawings: elevations and the wall sections that go with them.

    The drawing engine has always been here; this is the screen that drives it.
    A set is assembled from the elements as they will actually be built, so the
    sheet in the site folder and the bar on the saw come from one calculation —
    which is the whole argument for drawing inside the software that cuts.
    """

    title = "Drawings"
    hebrew = "שרטוטים"
    subtitle = "חזיתות, חתכי קיר וסט מוכן להפצה"

    #: The junctions a set may include, in the order they are drawn.
    DETAILS: tuple[tuple[str, str], ...] = (
        ("head", "משקוף עליון"),
        ("jamb", "מזוזה"),
        ("sill", "סף תחתון"),
        ("mullion", "אומנה"),
        ("transom", "משקוף רוחב"),
    )

    def build(self) -> None:
        from PySide6.QtSvgWidgets import QSvgWidget

        produce = QPushButton("הפקת סט")
        produce.setObjectName("Primary")
        produce.clicked.connect(self.produce)
        self.header.add_action(produce)

        export = QPushButton("ייצוא")
        export.clicked.connect(self.export_package)
        self.header.add_action(export)

        self.stats = StatRow([
            ("sheets", "גיליונות"), ("details", "חתכים"),
            ("scale", "קנה מידה"), ("revision", "מהדורה"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("הגדרות הסט")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))

        self.wall = QComboBox()
        self.wall.addItem("בטון, בידוד, חיפוי אבן", "stone")
        self.wall.addItem("קיר בלוקים בטיח", "block")

        self.size = QComboBox()
        for label in ("A4", "A3", "A2", "A1"):
            self.size.addItem(label, label)
        self.size.setCurrentText("A3")

        self.elevation_scale = QComboBox()
        for scale in (10, 20, 25, 50):
            self.elevation_scale.addItem(f"1:{scale}", scale)
        self.elevation_scale.setCurrentText("1:20")

        self.detail_scale = QComboBox()
        for scale in (2, 5, 10):
            self.detail_scale.addItem(f"1:{scale}", scale)
        self.detail_scale.setCurrentText("1:5")

        for label, widget in (
            ("קיר", self.wall), ("גיליון", self.size),
            ("חזיתות", self.elevation_scale), ("חתכים", self.detail_scale),
        ):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)

        # Which junctions to cut. A set with no details is a set of elevations,
        # which is a legitimate thing to issue, so none of these is compulsory.
        details_row = QHBoxLayout()
        details_row.setSpacing(METRICS.space(3))
        caption = QLabel("חתכים לכלול")
        caption.setObjectName("FieldLabel")
        details_row.addWidget(caption)
        self.detail_boxes: dict[str, QCheckBox] = {}
        for key, label in self.DETAILS:
            box = QCheckBox(label)
            box.setChecked(key in ("head", "jamb", "sill", "mullion"))
            self.detail_boxes[key] = box
            details_row.addWidget(box)
        details_row.addStretch(1)
        controls.add_layout(details_row)
        self.body.addWidget(controls)

        # -- the sheet itself ---------------------------------------------- #
        picker = QHBoxLayout()
        picker.setSpacing(METRICS.space(2))
        caption = QLabel("גיליון")
        caption.setObjectName("FieldLabel")
        picker.addWidget(caption)
        self.sheet_picker = QComboBox()
        self.sheet_picker.currentIndexChanged.connect(self._show_sheet)
        picker.addWidget(self.sheet_picker, 1)
        self.body.addLayout(picker)

        self.canvas = QSvgWidget()
        self.canvas.setMinimumHeight(420)
        self.body.addWidget(self.canvas, 1)

        self.stamp = QLabel()
        self.stamp.setObjectName("Hint")
        self.stamp.setWordWrap(True)
        self.body.addWidget(self.stamp)

        note = QLabel(
            "החתכים נחתכים במפגש שבין האלומיניום לבניין: משקוף עליון, מזוזה, "
            "סף ואומנה. אם נטען פרופיל אמיתי מהקטלוג, החתך מצויר לפי המתאר "
            "שלו; אחרת הוא סכמטי ואומר זאת על הגיליון."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self._package = None

    # -- production --------------------------------------------------------- #
    def _details(self) -> list[Any]:
        from ..drawing.section import Detail

        return [
            Detail(key) for key, _label in self.DETAILS
            if self.detail_boxes[key].isChecked()
        ]

    def produce(self) -> None:
        from datetime import date

        from ..branding import active_brand
        from ..drawing import PackageInfo, Revision, SheetSize, build_package
        from ..drawing.section import RENDERED_BLOCK, STONE_CLAD_CONCRETE

        if not self.session.builds:
            self.report(
                ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח ואז חזור לכאן."),
                "אין מה לשרטט",
            )
            return

        job = self.session.job
        brand = active_brand()
        name = job.name if job is not None else "פרויקט"
        info = PackageInfo(
            project=name,
            client=job.customer_name if job is not None else "",
            company=brand.display_name,
            company_line=brand.tagline or "",
            number_prefix=f"{(job.job_id if job is not None else 'A')}-",
            drawn_by=brand.display_name,
            size=SheetSize(self.size.currentData()),
            language=self.session.language,
            wall=RENDERED_BLOCK if self.wall.currentData() == "block" else STONE_CLAD_CONCRETE,
            revisions=[Revision("A", date.today(), "הופק לאישור", "")],
        )
        try:
            self._package = build_package(
                self.session.builds,
                info,
                elevation_scale=self.elevation_scale.currentData(),
                detail_scale=self.detail_scale.currentData(),
                profile=self.session.profile,
                details=self._details(),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להפיק את הסט")
            return

        package = self._package
        self.sheet_picker.blockSignals(True)
        self.sheet_picker.clear()
        for sheet in package.sheets:
            block = sheet.title_block
            self.sheet_picker.addItem(f"{block.number} — {block.title}")
        self.sheet_picker.blockSignals(False)
        if package.sheets:
            self.sheet_picker.setCurrentIndex(0)
            self._show_sheet()

        self.stats.update_many({
            "sheets": (str(len(package.sheets)), "בסט"),
            "details": (str(len(self._details())), "מפגשים נחתכו"),
            "scale": (f"1:{self.elevation_scale.currentData()}", "חזיתות"),
            "revision": (info.revision, "לאישור"),
        })
        self.stamp.setText("  ·  ".join(package.stamps) if package.stamps else "")
        self.status(f"הופקו {len(package.sheets)} גיליונות")

    def _show_sheet(self) -> None:
        """Render the chosen sheet into the preview."""
        if self._package is None:
            return
        index = self.sheet_picker.currentIndex()
        if not (0 <= index < len(self._package.sheets)):
            return
        try:
            svg = self._package.sheets[index].to_svg()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להציג את הגיליון")
            return
        self.canvas.load(svg.encode("utf-8"))

    def export_package(self) -> None:
        if self._package is None:
            self.report(ProfileOSError("הפק את הסט קודם"), "אין מה לייצא")
            return
        folder = QFileDialog.getExistingDirectory(self, "לאן לשמור את סט השרטוטים?")
        if not folder:
            return
        try:
            written = self._package.write(folder, formats=("pdf", "dxf", "svg"))
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הייצוא נכשל")
            return
        self.status(f"נשמרו {len(written)} קבצים אל {folder}")

    def refresh(self) -> None:
        count = len(self.session.builds)
        job = self.session.job
        self.header.set_subtitle(
            f"{job.job_id} — {count} פתחים מוכנים לשרטוט" if job is not None
            else (f"{count} פתחים מוכנים לשרטוט" if count
                  else "תכנן פתחים כדי להפיק סט שרטוטים")
        )


# --------------------------------------------------------------------------- #
# Nesting
# --------------------------------------------------------------------------- #

class NestingPage(Page):
    """Optimise the project's cut list onto stock bars."""

    title = "Nesting"
    hebrew = "אופטימיזציית חיתוך"
    subtitle = "שיבוץ רשימת החיתוך על מוטות המלאי"

    def build(self) -> None:
        run = QPushButton("הרץ אופטימיזציה")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        self.stats = StatRow([
            ("bars", "מוטות"), ("pieces", "חלקים"), ("yield", "ניצולת"),
            ("waste", "פחת"), ("remnants", "שאריות"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("פרמטרים")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))
        self.kerf = QDoubleSpinBox(); self.kerf.setRange(0, 20); self.kerf.setValue(3.5); self.kerf.setSuffix(" mm")
        self.stock = QComboBox(); self.stock.setEditable(True); self.stock.addItems(["6000", "6500", "6000,6500", "7000"])
        self.strategy = QComboBox(); self.strategy.addItems(["auto", "milp", "ffd", "bfd"])
        self.profile = QComboBox()
        for label, widget in [("עובי להב", self.kerf), ("אורכי מלאי", self.stock),
                              ("אסטרטגיה", self.strategy), ("פרופיל", self.profile)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.view = NestingView(self.colours)
        scroll.setWidget(self.view)
        splitter.addWidget(scroll)

        self.summary = DataTable(["פרופיל", "מוטות", "חלקים", "ניצולת", "פחת", "אסטרטגיה", "אופטימלי"])
        splitter.addWidget(self.summary)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        self.profile.currentTextChanged.connect(self._show_profile)

    def refresh(self) -> None:
        self.header.set_subtitle(
            f"{len(self.session.builds)} פתחים ממתינים"
            if self.session.builds else "תכנן פתח קודם — בעמוד ״פתח״"
        )

    def run(self) -> None:
        from ..elements import collect_cut_items
        from ..models.orders import Project
        from ..nesting import nest_project

        if not self.session.builds:
            self.report(ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח בעמוד ״פתח״ ואז חזור לכאן."), "אין מה לשבץ")
            return

        from ..core.config import get_settings

        settings = get_settings()
        settings.nesting.kerf_mm = self.kerf.value()
        try:
            settings.nesting.stock_lengths_mm = [
                float(v) for v in self.stock.currentText().split(",") if v.strip()
            ]
        except ValueError:
            self.report(ProfileOSError("אורכי המלאי חייבים להיות מספרים, מופרדים בפסיק"), "קלט שגוי")
            return

        project = Project(name="פרויקט", items=collect_cut_items(self.session.builds))
        try:
            report = nest_project(project, strategy=self.strategy.currentText())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "האופטימיזציה נכשלה")
            return

        self.session.set_nesting(project, report)

        remnants = sum(len(r.reusable_remnants(300.0)) for r in report.results.values())
        self.stats.update_many({
            "bars": (str(report.total_bars), f"\u2066{report.total_stock_length / 1000:.1f} m\u2069"),
            "pieces": (str(sum(r.total_pieces for r in report.results.values())), ""),
            "yield": (f"{report.overall_yield_pct:.2f}%", "יעד 97.5%"),
            "waste": (f"{100 - report.overall_yield_pct:.2f}%", ""),
            "remnants": (str(remnants), "לשימוש חוזר"),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, (profile_id, result) in enumerate(sorted(report.results.items())):
            rows.append([
                profile_id, result.bar_count, result.total_pieces,
                f"{result.yield_pct:.2f}%", f"{result.waste_pct:.2f}%",
                result.strategy, "כן" if result.optimal else "—",
            ])
            colours[(index, 3)] = (
                self.colours.success if result.yield_pct >= 95 else self.colours.warning
            )
        self.summary.set_rows(rows, numeric_columns=(1, 2, 3, 4), colours=colours)

        self.profile.blockSignals(True)
        self.profile.clear()
        self.profile.addItems(sorted(report.results))
        self.profile.blockSignals(False)
        if report.results:
            self._show_profile(self.profile.currentText())

        self.status(
            f"{report.total_bars} מוטות בניצולת {report.overall_yield_pct:.2f}% "
            f"תוך \u2066{report.solve_time_s:.2f}\u2069 שניות"
        )

    def _show_profile(self, profile_id: str) -> None:
        report = self.session.nesting_report
        if report is None or profile_id not in report.results:
            return
        self.view.set_result(report.results[profile_id])


# --------------------------------------------------------------------------- #
# Machining
# --------------------------------------------------------------------------- #

class MachiningPage(Page):
    """Plan clamps and post machine code."""

    title = "Machining"
    hebrew = "עיבוד CNC"
    subtitle = "תכנון הקיבוע והפקת קוד למכונה"

    def build(self) -> None:
        post = QPushButton("הפק תוכנית")
        post.setObjectName("Primary")
        post.clicked.connect(self.post)
        self.header.add_action(post)

        save = QPushButton("שמור לקובץ...")
        save.clicked.connect(self.save)
        self.header.add_action(save)

        controls = Card("עבודה")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(4))
        self.driver = QComboBox()

        from ..cnc import available_drivers

        for entry in available_drivers():
            self.driver.addItem(entry.get("display_name") or entry["key"], entry["key"])
        # Default to a machining centre rather than whichever driver sorts
        # first: this page is about machining, and a saw driver emits only a
        # cut list, which looks like the post has silently dropped the work.
        default = self.driver.findData("elumatec.ncx")
        if default >= 0:
            self.driver.setCurrentIndex(default)
        self.length = QDoubleSpinBox(); self.length.setRange(200, 8000); self.length.setValue(2450); self.length.setSuffix(" mm")
        self.angle_left = QDoubleSpinBox(); self.angle_left.setRange(15, 165); self.angle_left.setValue(45); self.angle_left.setSuffix("°")
        self.angle_right = QDoubleSpinBox(); self.angle_right.setRange(15, 165); self.angle_right.setValue(45); self.angle_right.setSuffix("°")
        self.clearance = QDoubleSpinBox(); self.clearance.setRange(0, 100); self.clearance.setValue(15); self.clearance.setSuffix(" mm")
        for label, widget in [("דרייבר", self.driver), ("אורך מוט", self.length),
                              ("זווית שמאל", self.angle_left), ("זווית ימין", self.angle_right),
                              ("מרווח מלחציים", self.clearance)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        setup_card = Card("קיבוע — עיבודים מעל המוט, מלחציים מתחתיו")
        self.clamp_view = ClampView(self.colours)
        setup_card.add(self.clamp_view, 1)
        self.clamp_status = QLabel("—")
        self.clamp_status.setObjectName("StatLabel")
        setup_card.add(self.clamp_status)
        self.body.addWidget(setup_card, 1)

        code_card = Card("קוד מכונה")
        self.code = QPlainTextEdit(); self.code.setReadOnly(True)
        self.code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code_card.add(self.code, 1)
        self.body.addWidget(code_card, 1)

        self._results: list[Any] = []

    def _build_job(self) -> Any:
        from ..cnc import MachiningJob, PieceProgram, expand_macros
        from ..models.machines import Clamp, MachineDefinition, Tool, ToolLibrary, ToolType
        from ..models.profile import Face, MachiningMacro

        tools = ToolLibrary(id="ui", name="Magazine", tools=[
            Tool(number=3, name="D5 drill", tool_type=ToolType.DRILL, diameter=5.0, flute_length=40.0),
            Tool(number=5, name="D8 end mill", tool_type=ToolType.END_MILL, diameter=8.0, flute_length=35.0),
            Tool(number=7, name="D6 slot mill", tool_type=ToolType.SLOT_MILL, diameter=6.0, flute_length=30.0),
            Tool(number=9, name="D12 end mill", tool_type=ToolType.END_MILL, diameter=12.0, flute_length=50.0),
        ])
        machine = MachineDefinition(
            id="ui-machine", name="Machining centre", vendor="Generic", model="5-axis",
            post_processor=self.driver.currentData(), axis_count=5,
            machinable_faces=set(Face), clamp_clearance=self.clearance.value(),
            clamps=[Clamp(id=f"C{i}", position=p, width=120.0)
                    for i, p in enumerate([400.0, 1200.0, 2000.0], 1)],
        )
        length = self.length.value()
        macros = [
            MachiningMacro(macro_id="lock.euro_cylinder", face=Face.FRONT,
                           position_x=length * 0.5, position_y=30.0, depth=25.0, tool_id=5),
            MachiningMacro(macro_id="hinge.standard", face=Face.FRONT,
                           position_x=length * 0.16, position_y=30.0, depth=12.0, tool_id=9),
            MachiningMacro(macro_id="drainage.slots", face=Face.BOTTOM, position_x=length * 0.12,
                           position_y=0.0, depth=8.0, tool_id=7,
                           parameters={"count": 2, "spacing": length * 0.35}),
            MachiningMacro(macro_id="notch.akm", face=Face.TOP, position_x=0.0, position_y=0.0,
                           depth=18.0, tool_id=9, from_right_end=True, parameters={"length": 25.0}),
        ]
        piece = PieceProgram(
            piece_id="PC-101", profile_id="MB70-MULLION", length=length,
            angle_left=self.angle_left.value(), angle_right=self.angle_right.value(),
            operations=expand_macros(macros, bar_length=length), mark="mullion",
        )
        return MachiningJob(machine=machine, name="Project", pieces=[piece], tool_library=tools)

    def post(self) -> None:
        from ..cnc import detect_collisions, get_driver

        try:
            job = self._build_job()
            piece = job.pieces[0]
            before = detect_collisions(
                list(piece.operations), job.machine.active_clamps(),
                clearance=self.clearance.value(),
            )
            job.plan_all_clamps()
            plan = piece.clamp_plan
            self.clamp_view.set_piece(piece, plan.unresolved)
            parts: list[str] = []
            if plan.moves:
                parts.append(
                    f"{len(plan.moves)} מלחציים הוזזו "
                    f"(\u2066{plan.total_travel:.0f}\u2069 מ״מ)"
                )
            if plan.disabled:
                parts.append(f"{len(plan.disabled)} מלחציים נוטרלו")
            if plan.unresolved:
                parts.append(f"{len(plan.unresolved)} התנגשויות לא נפתרו")
            outcome = "; ".join(parts) if parts else "אין הפרעות מלחציים; המלחציים נותרו במקומם."
            self.clamp_status.setText(
                f"זוהו {len(before)} הפרעות לפני התכנון. {outcome}"
                + ("" if plan.ok else "  לא נפתר — אין להריץ את התוכנית הזו.")
            )

            results = get_driver(self.driver.currentData()).post(job)
        except Exception as exc:  # noqa: BLE001
            self.code.setPlainText("")
            self.report(exc, "ההפקה נכשלה")
            return

        self._results = results
        self.session.set_machining(job, results)
        preview = results[0]
        self.code.setPlainText(preview.content[:40000])
        self.header.set_subtitle(
            f"{preview.filename} · \u2066{preview.size:,}\u2069 בתים · {len(job.all_operations())} עיבודים"
        )
        self.status(f"הופקו {len(results)} קבצים עם {self.driver.currentData()}")

    def save(self) -> None:
        if not self._results:
            self.report(ProfileOSError("הפק תוכנית קודם"), "אין מה לשמור")
            return
        directory = QFileDialog.getExistingDirectory(self, "שמירת קוד מכונה אל")
        if not directory:
            return
        for result in self._results:
            result.write(directory)
        self.status(f"נשמרו {len(self._results)} קבצים אל {directory}")


# --------------------------------------------------------------------------- #
# Quotation
# --------------------------------------------------------------------------- #

class QuotePage(Page):
    """Price the project, then negotiate the quotation without losing either.

    The page edits a :class:`~profileos.quoting.editor.QuoteDraft`: change the
    system, the glass, the finish or the margin and everything reprices; edit a
    line's unit price in the table and the pin survives every reprice, flagged
    when the arithmetic has moved under it. The two documents — the customer
    copy and the internal cost sheet — are written from the same draft.
    """

    title = "Quotation"
    hebrew = "הצעת מחיר"
    subtitle = "תמחור, משא ומתן על השורות והפקת המסמכים"

    def build(self) -> None:
        from PySide6.QtWidgets import QTableWidgetItem

        self._item_type = QTableWidgetItem

        run = QPushButton("תמחר את הפרויקט")
        run.setObjectName("Primary")
        run.clicked.connect(self.start_draft)
        self.header.add_action(run)

        save = QPushButton("הפק מסמכים...")
        save.clicked.connect(self.save_documents)
        self.header.add_action(save)

        self.stats = StatRow([
            ("net", "לפני מע״מ"), ("vat", "מע״מ"), ("gross", "סה״כ לתשלום"),
            ("margin", "רווח אחרי עריכות"), ("kg", "אלומיניום"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("מפרט ותנאים — כל שינוי מתמחר הכל מחדש")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(4))
        self.system = QComboBox()
        from ..systems import DIRECTORY

        self.system.addItem("generic", "generic")
        for entry in sorted(DIRECTORY, key=lambda e: e.id):
            self.system.addItem(entry.display, entry.id)
        self.glass = QComboBox()
        from ..glazing.glass import STANDARD_BUILDUPS

        for build_up in STANDARD_BUILDUPS.values():
            self.glass.addItem(build_up.describe(), build_up.id)
        self.glass.setCurrentIndex(1)
        self.finish = QComboBox()
        from ..quoting.editor import FINISHES

        for finish in FINISHES.values():
            self.finish.addItem(f"{finish.hebrew} · {finish.name}", finish.id)
        self.finish.setCurrentIndex(1)
        self.margin = QDoubleSpinBox(); self.margin.setRange(0, 90); self.margin.setValue(25); self.margin.setSuffix(" %")
        for label, widget in [("סדרה", self.system), ("זכוכית", self.glass),
                              ("גמר", self.finish), ("רווח", self.margin)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        for widget in (self.system, self.glass, self.finish):
            widget.currentIndexChanged.connect(self.apply_spec)
        self.margin.valueChanged.connect(self.apply_spec)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        lines_card = Card("שורות ללקוח — לחיצה כפולה על מחיר ליחידה מקבעת אותו")
        self.lines = DataTable(["פריט", "תיאור", "כמות", "מחיר ליחידה", "סה״כ", ""])
        self.lines.setEditTriggers(
            self.lines.EditTrigger.DoubleClicked | self.lines.EditTrigger.EditKeyPressed
        )
        self.lines.itemChanged.connect(self._line_edited)
        lines_card.add(self.lines, 1)
        undo = QPushButton("בטל עריכה אחרונה")
        undo.clicked.connect(self.undo_edit)
        lines_card.add(undo)
        splitter.addWidget(lines_card)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(METRICS.space(3))

        options_card = Card("חלופות")
        self.options = DataTable(["חלופה", "מפרט", "לפני מע״מ", "±"])
        options_card.add(self.options, 1)
        add_option = QPushButton("הוסף חלופה תרמית")
        add_option.clicked.connect(self.add_option)
        options_card.add(add_option)
        side_layout.addWidget(options_card, 1)

        internal_card = Card("פנימי — לעולם לא מודפס בעותק הלקוח")
        self.waterfall = DataTable(["רכיב", "סכום"])
        internal_card.add(self.waterfall, 1)
        self.notes = QPlainTextEdit(); self.notes.setReadOnly(True); self.notes.setMaximumHeight(100)
        internal_card.add(self.notes)
        side_layout.addWidget(internal_card, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.body.addWidget(splitter, 1)

        self.draft: Any = None
        self._loading = False

    # -- the draft ----------------------------------------------------------- #
    def start_draft(self) -> None:
        from ..quoting.editor import QuoteDraft

        if not self.session.builds:
            self.report(ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח בעמוד ״פתח״ ואז חזור לכאן."), "אין מה לתמחר")
            return
        try:
            self.draft = QuoteDraft.start(
                [build.opening for build in self.session.builds],
                project_name="פרויקט",
                system_id=self.system.currentData() or "generic",
                glass_id=self.glass.currentData(),
                finish_id=self.finish.currentData(),
                fallback_rates={
                    "profile": 5.5, "glass_m2": 60.0, "hardware": 12.0, "gasket_m": 1.1,
                },
            )
            self.draft.set_margin(self.margin.value())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "התמחור נכשל")
            return
        self.session.set_quote(self.draft.variant().bom, self.draft.quotation)
        self.refresh_draft()
        self.status(f"תומחר {self.draft.quotation.quote_id}")

    def apply_spec(self, *_args: Any) -> None:
        if self.draft is None or self._loading:
            return
        try:
            variant = self.draft.variant()
            if self.system.currentData() != variant.system_id:
                self.draft.set_system(self.system.currentData())
            if self.glass.currentData() != variant.glass_id:
                self.draft.set_glass(self.glass.currentData())
            if self.finish.currentData() != variant.finish_id:
                self.draft.set_finish(self.finish.currentData())
            if abs(self.margin.value() - variant.policy.margin_pct) > 1e-9:
                self.draft.set_margin(self.margin.value())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לתמחר מחדש")
            return
        self.refresh_draft()

    def add_option(self) -> None:
        if self.draft is None:
            return
        self.draft.add_variant("מפרט תרמי", glass_id="dgu-6-16-6", finish_id="anodized")
        self.refresh_draft()

    def undo_edit(self) -> None:
        if self.draft is None:
            return
        entry = self.draft.undo()
        self.status(f"בוטל: {entry.what}" if entry else "אין מה לבטל")
        self.refresh_draft()

    # -- line edits ----------------------------------------------------------- #
    def _line_edited(self, item: Any) -> None:
        if self.draft is None or self._loading or item.column() != 3:
            return
        code = self.lines.item(item.row(), 0)
        if code is None:
            return
        try:
            value = float(str(item.text()).replace(",", ""))
            self.draft.set_line_price(code.text(), value, by="desktop")
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לקבוע את המחיר")
        self.refresh_draft()

    # -- rendering -------------------------------------------------------------- #
    def refresh_draft(self) -> None:
        if self.draft is None:
            return
        self._loading = True
        try:
            draft = self.draft
            totals = draft.totals()
            sheet = draft.internal_sheet()
            variant = draft.variant()

            rows = []
            colours: dict[tuple[int, int], str] = {}
            openings = {opening.element_id: opening for opening in draft.openings}
            kind_hebrew = {"window": "חלון", "door": "דלת", "curtain_wall": "קיר מסך",
                           "shopfront": "חזית מסחרית", "sliding_unit": "מערכת הזזה"}
            for index, line in enumerate(draft.customer_lines()):
                opening = openings.get(line["code"])
                description = line["description"]
                if opening is not None:
                    kind = kind_hebrew.get(opening.kind.value, opening.kind.value)
                    description = (
                        f"{opening.name or opening.element_id} — {kind} "
                        f"\u2066{opening.width:.0f} × {opening.height:.0f}\u2069 מ״מ"
                    )
                rows.append([
                    line["code"], description, f"{line['quantity']:g}",
                    f"{line['unit_price']:,.2f}", f"{line['total']:,.2f}",
                    "נערך ידנית" if line["edited"] else "",
                ])
                if line["edited"]:
                    colours[(index, 5)] = self.colours.warning
            self.lines.set_rows(rows, numeric_columns=(2, 3, 4), colours=colours)
            # Re-enable editing: set_rows makes items, so flags come from the
            # table's triggers rather than per item.

            option_rows = []
            for row in draft.compare():
                option_rows.append([
                    row["name"], f"{row['glass']} · {row['finish']}",
                    f"{row['net']:,.0f}",
                    "—" if abs(row["difference"]) < 0.005 else f"{row['difference']:+,.0f}",
                ])
            self.options.set_rows(option_rows, numeric_columns=(2, 3))

            waterfall_hebrew = {
                "Materials": "חומרים", "Labour": "עבודה", "Fixed charges": "עלויות קבועות",
                "Total cost": "עלות כוללת", "Net price": "מחיר לפני מע״מ",
                "Gross price": "מחיר כולל מע״מ",
            }

            def hebrew_row(label: str) -> str:
                for english, hebrew in waterfall_hebrew.items():
                    if label.startswith(english):
                        return label.replace(english, hebrew)
                return (label.replace("Contingency", "בצ״מ")
                        .replace("Overhead", "תקורה")
                        .replace("Delivery", "הובלה והתקנה")
                        .replace("Margin", "רווח")
                        .replace("Tax", "מע״מ"))

            self.waterfall.set_rows(
                [[hebrew_row(label), f"{value:,.2f}"] for label, value in sheet["breakdown"]],
                numeric_columns=(1,),
            )
            warnings = list(sheet["warnings"])
            for override in sheet["overrides"]:
                if override["stale"]:
                    warnings.append(
                        f"{override['element']}: השורה קובעה ידנית והחישוב זז מאז — יש להסתכל שוב."
                    )
            self.notes.setPlainText("\n".join(f"• {w}" for w in warnings) or "אין אזהרות.")

            self.stats.update_many({
                "net": (f"{totals['net']:,.0f}", variant.policy.currency),
                "vat": (f"{totals['vat']:,.0f}", f"{variant.policy.tax_pct:g}%"),
                "gross": (f"{totals['gross']:,.0f}", ""),
                "margin": (f"{sheet['margin_after_edits']:,.0f}", "after hand edits"),
                "kg": (f"{variant.aluminium_kg:,.0f}", variant.finish.name),
            })
            self.header.set_subtitle(
                f"{draft.quotation.quote_id} · בתוקף עד "
                f"\u2066{draft.quotation.valid_until.strftime('%d/%m/%Y')}\u2069"
            )
        finally:
            self._loading = False

    def save_documents(self) -> None:
        if self.draft is None:
            self.report(ProfileOSError("תמחר את הפרויקט קודם"), "אין מה להפיק")
            return
        from ..quoting.document import render_quotation

        directory = QFileDialog.getExistingDirectory(self, "לאן לשמור את מסמכי ההצעה?")
        if not directory:
            return
        base = Path(directory) / self.draft.quotation.quote_id
        customer = base.with_suffix(".customer.html")
        internal = base.with_suffix(".internal.html")
        customer.write_text(render_quotation(self.draft, language=self.session.language),
                            encoding="utf-8")
        internal.write_text(
            render_quotation(self.draft, language=self.session.language, internal=True),
            encoding="utf-8",
        )
        self.status(f"נשמרו {customer.name} ו-{internal.name}")
        self._record_in_job()

    def _record_in_job(self) -> None:
        """Write the quoted figure onto the open job, and move it along.

        A quotation that has been issued is a fact about the job, not only
        about this window: the order book should show the number without the
        operator being asked to copy it across. The status only advances from
        enquiry — a job already won is not dragged backwards by reprinting its
        quotation.
        """
        job = self.session.job
        if job is None or self.draft is None:
            return
        from ..projects import JobStatus, default_store

        totals = self.draft.totals()
        job.record_quote(float(totals["net"]), self.draft.quotation.currency)
        if job.status is JobStatus.ENQUIRY:
            job.advance(JobStatus.QUOTED, "הצעה הופקה מהמערכת")
        try:
            default_store().save(job)
        except Exception:  # noqa: BLE001 - the documents are already written
            _log.exception("Could not record the quotation on job %s", job.job_id)
            return
        self.status(f"{job.job_id} עודכן: {job.status.hebrew}")


# --------------------------------------------------------------------------- #
# Shop floor
# --------------------------------------------------------------------------- #

class ShopFloorPage(Page):
    """Release the project to production and track it."""

    title = "Shop floor"
    hebrew = "רצפת ייצור"
    subtitle = "שחרור פקודת עבודה ומעקב ייצור"

    def build(self) -> None:
        release = QPushButton("שחרר פקודת עבודה")
        release.setObjectName("Primary")
        release.clicked.connect(self.release)
        self.header.add_action(release)

        card = QPushButton("ייצא כרטיס עבודה...")
        card.clicked.connect(self.export_card)
        self.header.add_action(card)

        labels = QPushButton("הדפס מדבקות...")
        labels.clicked.connect(self.export_labels)
        self.header.add_action(labels)

        self.stats = StatRow([
            ("items", "פריטים"), ("progress", "התקדמות"), ("stage", "צוואר בקבוק"),
            ("rework", "לתיקון"), ("scrap", "פסולים"),
        ])
        self.body.addWidget(self.stats)

        scan_card = Card("רישום סריקה")
        row = QHBoxLayout(); row.setSpacing(METRICS.space(3))
        self.item = QComboBox()
        self.stage = QComboBox()

        from ..mes import Stage

        for stage in Stage:
            if stage.value != "planned":
                self.stage.addItem(stage.label("he"), stage.value)
        self.operator = QComboBox(); self.operator.setEditable(True)
        self.operator.addItems(["דנה", "יוסי", "מאיה"])
        scan = QPushButton("סרוק")
        scan.clicked.connect(self.scan)
        for label, widget in [("פריט", self.item), ("שלב", self.stage), ("מפעיל", self.operator)]:
            caption = QLabel(label); caption.setObjectName("FieldLabel")
            row.addWidget(caption); row.addWidget(widget)
        row.addWidget(scan)
        row.addStretch(1)
        scan_card.add_layout(row)
        self.scan_result = QLabel("—")
        self.scan_result.setObjectName("StatLabel")
        scan_card.add(self.scan_result)
        self.body.addWidget(scan_card)

        tabs = QTabWidget()
        items_page = QWidget()
        items_layout = QVBoxLayout(items_page)
        items_layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        self.items = DataTable(["מזהה", "סוג", "תיאור", "שלב", "התקדמות"])
        items_layout.addWidget(self.items, 1)
        tabs.addTab(items_page, "פריטי ייצור")
        tabs.addTab(self._hours_tab(), "שעות עבודה")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

    # -- hours ----------------------------------------------------------------- #
    def _hours_tab(self) -> QWidget:
        """Book real hours against a job, so the margin is measured not guessed.

        Every quote in this trade is priced on an estimate of how long the work
        takes. Without hours booked against the job, that estimate is never
        corrected, and a shop can lose money on the same kind of job for years
        while its quotation screen keeps saying the margin is fine.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        entry_card = Card("רישום שעות")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(3))

        self.hours_person = QComboBox()
        self.hours_person.setEditable(True)
        self.hours_person.addItems(["דנה", "יוסי", "מאיה"])

        self.hours_job = QLineEdit()
        self.hours_job.setPlaceholderText("מספר תיק")

        self.hours_operation = QComboBox()
        self.hours_operation.setEditable(True)
        self.hours_operation.addItems([
            "חיתוך", "עיבוד CNC", "הרכבה", "זיגוג", "אריזה",
            "התקנה באתר", "מדידה", "תיקון",
        ])

        self.hours_start = QLineEdit("07:00")
        self.hours_start.setMaximumWidth(METRICS.space(14))
        self.hours_end = QLineEdit("16:00")
        self.hours_end.setMaximumWidth(METRICS.space(14))

        self.hours_rework = QCheckBox("תיקון חוזר")

        self.hours_rate = QDoubleSpinBox()
        self.hours_rate.setRange(0.0, 999.0)
        self.hours_rate.setDecimals(2)
        self.hours_rate.setSuffix(" ₪/ש׳")

        for label, widget in [
            ("עובד", self.hours_person), ("תיק", self.hours_job),
            ("פעולה", self.hours_operation), ("משעה", self.hours_start),
            ("עד", self.hours_end), ("עלות", self.hours_rate),
        ]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addWidget(self.hours_rework)

        book = QPushButton("רשום")
        book.setObjectName("Primary")
        book.clicked.connect(self.book_hours)
        row.addWidget(book)
        row.addStretch(1)
        entry_card.add_layout(row)

        self.hours_result = QLabel("—")
        self.hours_result.setObjectName("StatLabel")
        entry_card.add(self.hours_result)
        layout.addWidget(entry_card)

        self.hours_table = DataTable(
            ["תאריך", "עובד", "תיק", "פעולה", "שעות", "עלות", "תיקון"],
            empty_text="אין רישומי שעות — בלי שעות, הרווחיות היא הערכה בלבד",
        )
        layout.addWidget(self.hours_table, 1)

        self.hours_summary = QLabel("")
        self.hours_summary.setObjectName("Hint")
        self.hours_summary.setWordWrap(True)
        layout.addWidget(self.hours_summary)

        self._timebook = None
        self.show_hours()
        return page

    def _book(self):
        if self._timebook is None:
            from ..erp.timesheets import default_timebook

            self._timebook = default_timebook()
        return self._timebook

    def book_hours(self) -> None:
        from ..erp.timesheets import minutes_between

        try:
            minutes = minutes_between(
                self.hours_start.text(), self.hours_end.text()
            )
            entry = self._book().book(
                person=self.hours_person.currentText(),
                job_id=self.hours_job.text().strip(),
                minutes=minutes,
                operation=self.hours_operation.currentText(),
                rework=self.hours_rework.isChecked(),
                rate=self.hours_rate.value(),
            )
        except Exception as exc:  # noqa: BLE001
            self.hours_result.setText(str(exc))
            self.hours_result.setStyleSheet(f"color: {self.colours.danger};")
            return

        self.hours_result.setText(entry.describe())
        self.hours_result.setStyleSheet(f"color: {self.colours.success};")
        self.show_hours()

    def show_hours(self) -> None:
        book = self._book()
        rows: list[list[Any]] = []
        for entry in list(book)[:200]:
            rows.append([
                entry.on.isoformat(), entry.person, entry.job_id or "—",
                entry.operation or entry.note or "—",
                f"⁦{entry.hours:.2f}⁩",
                f"⁦{entry.cost:,.2f}⁩ ₪" if entry.rate else "—",
                "כן" if entry.rework else "",
            ])
        self.hours_table.set_rows(rows)

        if not len(book):
            self.hours_summary.setText("")
            return
        total = round(sum(entry.hours for entry in book), 2)
        rework = book.rework_share()
        by_person = book.by_person()
        leaders = ", ".join(
            f"{name} ⁦{hours:.1f}⁩"
            for name, hours in sorted(
                by_person.items(), key=lambda pair: pair[1], reverse=True
            )[:4]
        )
        self.hours_summary.setText(
            f"סה״כ ⁦{total:.1f}⁩ שעות · תיקונים חוזרים ⁦{rework:.0f}%⁩ "
            f"מהזמן · {leaders}"
        )

    def release(self) -> None:
        from ..mes import work_order_from_builds

        if not self.session.builds:
            self.report(ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח בעמוד ״פתח״ ואז חזור לכאן."), "אין מה לשחרר")
            return
        order = work_order_from_builds(self.session.builds, project_id="PRJ", name="פרויקט")
        self.session.set_work_order(order)

        self.item.clear()
        for entry in order.items:
            self.item.addItem(f"{entry.item_id} — {entry.description[:40]}", entry.item_id)
        self._refresh_items()
        self.status(f"שוחררו {len(order)} פריטים כ-{order.work_order_id}")

    def scan(self) -> None:
        from ..mes import Stage

        order = self.session.work_order
        if order is None:
            self.report(ProfileOSError("שחרר פקודת עבודה קודם"), "אין מה לסרוק")
            return

        item_id = self.item.currentData()
        ok, message = order.scan(
            item_id or "", Stage(self.stage.currentData()),
            operator=self.operator.currentText(), station="UI",
        )
        colour = self.colours.success if ok else self.colours.danger
        self.scan_result.setText(message)
        self.scan_result.setStyleSheet(f"color: {colour};")
        self._refresh_items()

    def _refresh_items(self) -> None:
        order = self.session.work_order
        if order is None:
            return
        kind_hebrew = {
            "profile_piece": "פרופיל", "glass_pane": "זכוכית",
            "element": "פתח", "hardware_kit": "ערכת פרזול",
        }
        self.items.set_rows(
            [[i.item_id, kind_hebrew.get(i.kind.value, i.kind.value), i.description,
              i.stage.label("he"), f"{i.progress() * 100:.0f}%"] for i in order.items],
            numeric_columns=(4,),
        )
        summary = order.summary()
        bottleneck = order.bottleneck()
        self.stats.update_many({
            "items": (str(summary["items"]), order.work_order_id),
            "progress": (f"{summary['progress_pct']:.0f}%", ""),
            "stage": (bottleneck[0].label("he") if bottleneck else "—",
                      f"{bottleneck[1]} פריטים" if bottleneck else ""),
            "rework": (str(summary["rework"]), ""),
            "scrap": (str(summary["scrapped"]), ""),
        })

    def export_card(self) -> None:
        from ..mes import write_job_card

        order = self.session.work_order
        if order is None:
            self.report(ProfileOSError("שחרר פקודת עבודה קודם"), "אין מה לייצא")
            return
        path, _ = QFileDialog.getSaveFileName(self, "שמירת כרטיס עבודה", "job-card.html", "HTML (*.html)")
        if not path:
            return
        write_job_card(order, path, self.session.builds)
        self.status(f"נשמר {path}")

    def export_labels(self) -> None:
        """One label per physical piece, laid out on real label stock.

        Two hundred cut bars leave the saw in a morning, all silver and most
        within a few millimetres of each other. This is what stops the
        afternoon going on working out which four belong to which window.
        """
        from ..mes.labels import STOCKS, labels_for_order, write_labels

        order = self.session.work_order
        if order is None:
            self.report(
                ProfileOSError("שחרר פקודת עבודה קודם"), "אין מה להדפיס"
            )
            return

        pieces = labels_for_order(order)
        if not pieces:
            self.report(
                ProfileOSError("אין פריטים בפקודת העבודה"), "אין מה להדפיס"
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת גיליון מדבקות", "labels.html", "HTML (*.html)"
        )
        if not path:
            return
        try:
            run = write_labels(pieces, path, stock=STOCKS["a4-24"])
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הדפסת המדבקות נכשלה")
            return
        self.status(f"{run.describe()} · {path}")




# --------------------------------------------------------------------------- #
# Glass
# --------------------------------------------------------------------------- #

class GlassPage(Page):
    """Nest the project's glass onto stock sheets."""

    title = "Glass"
    hebrew = "זכוכית"
    subtitle = "שיבוץ הזיגוג על לוחות גלם"

    def build(self) -> None:
        run = QPushButton("שבץ זכוכית")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        export = QPushButton("ייצא מפות חיתוך")
        export.clicked.connect(self.export_maps)
        self.header.add_action(export)

        order = QPushButton("הזמנה לספק זכוכית...")
        order.clicked.connect(self.export_glass_order)
        self.header.add_action(order)

        self.stats = StatRow([
            ("sheets", "לוחות"), ("panes", "שמשות"), ("yield", "ניצולת"),
            ("offcuts", "שאריות"), ("stages", "שלבי חיתוך"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("מכונה ומלאי")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))

        self.kerf = QDoubleSpinBox()
        self.kerf.setRange(0, 20)
        self.kerf.setValue(0.0)
        self.kerf.setSuffix(" mm")
        self.kerf.setToolTip(
            "גלגלת חריטה לזכוכית לא מסירה חומר — חורטים ושוברים — לכן בזכוכית "
            "הערך אפס. מסור לפאנל מרוכב מסיר את עובי הלהב, בדרך כלל 4–5 מ״מ."
        )

        self.trim = QDoubleSpinBox()
        self.trim.setRange(0, 200)
        self.trim.setValue(20.0)
        self.trim.setSuffix(" mm")
        self.trim.setToolTip(
            "מוסר מכל ארבע הצלעות לפני שיבוץ. לוח גלם מגיע עם שוליים פגומים, "
            "ובזכוכית עם ציפוי — עם רצועת ציפוי מוסרת בקצה."
        )

        self.stages = QComboBox()
        self.stages.addItems(["ללא הגבלה", "2", "3"])
        self.stages.setToolTip(
            "כמה פעמים קו החיתוך רשאי לפנות. קו אוטומטי מסתדר עם שניים: "
            "חיתוכי רוחב לפסים, ואז חיתוכי אורך לשמשות."
        )

        self.stock = QComboBox()
        self.stock.setEditable(True)
        self.stock.addItems([
            "כל הסטנדרטיים", "3210x2250", "3210x2550", "6000x3210",
            "3210x2250,6000x3210",
        ])
        self.stock.setToolTip(
            "מידות לוח כרוחב×גובה. בחירת הגלם חשובה בדרך כלל יותר מהשיבוץ: "
            "שמשה של 2334 מ״מ לא תצא מלוח 2250."
        )

        for label, widget in [("עובי להב", self.kerf), ("שולי קצה", self.trim),
                              ("שלבים", self.stages), ("גלם", self.stock)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Vertical)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.view = SheetView(self.colours)
        scroll.setWidget(self.view)
        splitter.addWidget(scroll)

        self.summary = DataTable(
            ["מפרט", "שמשות", "לוחות", "ניצולת", "שאריות", "שלבים", "הוכחה"]
        )
        splitter.addWidget(self.summary)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        self.material = QComboBox()
        self.material.currentTextChanged.connect(self._show_material)
        picker = QHBoxLayout()
        caption = QLabel("מפרט מוצג")
        caption.setObjectName("FieldLabel")
        picker.addWidget(caption)
        picker.addWidget(self.material, 1)
        self.body.addLayout(picker)

    def refresh(self) -> None:
        panes = sum(
            sum(pane.quantity for pane in build.glass) * build.opening.quantity
            for build in self.session.builds
        )
        self.header.set_subtitle(
            f"{panes} שמשות מתוך {len(self.session.builds)} פתחים"
            if self.session.builds
            else "תכנן פתח קודם — בעמוד ״פתח״"
        )

    def _spec(self) -> Any:
        from ..nesting import SheetSpec

        stages = self.stages.currentText()
        return SheetSpec(
            kerf=self.kerf.value(),
            edge_trim=self.trim.value(),
            stages=None if stages == "ללא הגבלה" else int(stages),
        )

    def _stock(self) -> list[Any]:
        from ..nesting import SheetStock
        from ..nesting.sheet import STANDARD_GLASS_STOCK

        text = self.stock.currentText().strip()
        if not text or text == "כל הסטנדרטיים":
            return list(STANDARD_GLASS_STOCK)
        sheets: list[Any] = []
        for token in text.split(","):
            token = token.strip()
            width, _, height = token.lower().partition("x")
            sheets.append(SheetStock(float(width), float(height), label=token))
        return sheets

    def run(self) -> None:
        from ..nesting import nest_project_glass, sheet_parts_from_builds

        if not self.session.builds:
            self.report(
                ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח בעמוד ״פתח״ ואז חזור לכאן."), "אין מה לשבץ"
            )
            return

        try:
            stock = self._stock()
        except ValueError:
            self.report(
                ProfileOSError("מידות לוח נכתבות כרוחב×גובה, למשל 3210x2250"),
                "קלט שגוי",
            )
            return

        parts = sheet_parts_from_builds(self.session.builds)
        if not parts:
            self.report(
                ProfileOSError("לפתחים האלה אין זכוכית"), "אין מה לשבץ"
            )
            return

        try:
            report = nest_project_glass(parts, stock=stock, spec=self._spec())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "שיבוץ הזכוכית נכשל")
            return

        self.session.set_glass(report)

        offcuts = sum(len(r.reusable_offcuts()) for r in report.results.values())
        stages = max(
            (r.stages_used or 0 for r in report.results.values()), default=0
        )
        self.stats.update_many({
            "sheets": (str(report.sheet_count), f"{report.total_stock_area / 1e6:.1f} m²"),
            "panes": (str(sum(r.total_pieces for r in report.results.values())), ""),
            "yield": (f"{report.yield_pct:.2f}%", f"\u2066{report.total_placed_area / 1e6:.1f} m²\u2069 זיגוג"),
            "offcuts": (str(offcuts), "חוזרות למדף"),
            "stages": (str(stages) if stages else "—", "פניות קו"),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, (material, result) in enumerate(sorted(report.results.items())):
            if result.optimal:
                proof = "אופטימלי"
            elif result.metadata.get("optimal_within_stage_limit"):
                proof = "אופטימלי עד 3 שלבים"
            else:
                proof = f"חסם {result.lower_bound}"
            rows.append([
                material, result.total_pieces, result.sheet_count,
                f"{result.yield_pct:.2f}%", len(result.reusable_offcuts()),
                result.stages_used or "—", proof,
            ])
            colours[(index, 3)] = (
                self.colours.success if result.yield_pct >= 80 else self.colours.warning
            )
        self.summary.set_rows(rows, numeric_columns=(1, 2, 3, 4, 5), colours=colours)

        self.material.blockSignals(True)
        self.material.clear()
        self.material.addItems(sorted(report.results))
        self.material.blockSignals(False)
        if report.results:
            self._show_material(self.material.currentText())

        for warning in report.warnings:
            self.status(warning)
        self.status(
            f"{report.sheet_count} לוחות בניצולת {report.yield_pct:.2f}%"
        )

    def _show_material(self, material: str) -> None:
        report = self.session.glass_report
        if report is None or material not in report.results:
            return
        self.view.set_result(report.results[material])

    def export_maps(self) -> None:
        from ..nesting import render_layout_svg

        report = self.session.glass_report
        if report is None:
            self.report(
                ProfileOSError("שבץ את הזכוכית קודם"), "אין מה לייצא"
            )
            return
        folder = QFileDialog.getExistingDirectory(self, "לאן לשמור את מפות החיתוך?")
        if not folder:
            return
        target = Path(folder)
        written = 0
        for material, result in report.results.items():
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in material)
            for layout in result.layouts:
                path = target / f"{safe}-sheet{layout.sheet_index + 1:02d}.svg"
                path.write_text(render_layout_svg(layout), encoding="utf-8")
                written += 1
        self.status(f"נשמרו {written} מפות חיתוך אל {target}")


    def export_glass_order(self) -> None:
        """The order that goes to the glazier — the one item that cannot be recut.

        Built from the same panes the machining came from rather than retyped
        from a cutting list, which is how a pane four millimetres out gets
        paid for twice.
        """
        from ..glazing.order import (
            order_from_builds, render_glass_order, write_glass_order,
        )

        builds = self.session.builds
        if not builds:
            self.report(
                ProfileOSError(
                    "אין פתחים בעבודה. תכנן פתחים בעמוד ״פתח״ ואז חזור לכאן."
                ),
                "אין מה להזמין",
            )
            return

        job = getattr(self.session, "job", None)
        order = order_from_builds(
            builds,
            job_id=str(getattr(job, "job_id", "") or ""),
            job_name=str(getattr(job, "name", "") or ""),
        )

        problems = order.problems()
        if problems:
            box = QMessageBox(self)
            box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            box.setWindowTitle("הזמנת זכוכית")
            box.setText(
                "ההזמנה לא עברה בדיקה:\n\n"
                + "\n".join(f"• {problem}" for problem in problems[:8])
                + "\n\nלשמור בכל זאת? המסמך יישא באנר ״לא לשליחה״."
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return

        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת הזמנת זכוכית", "glass-order.html", "HTML (*.html)"
        )
        if not path:
            return
        write_glass_order(order, path)
        self.status(f"{order.describe()} · {path}")


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #

class PlumbingPage(Page):
    """Installation: the plumbing side of the same building.

    A fabricator who also runs the plumbing — and in Israel that is most of
    them — should not need a second program, a second licence and a second
    place to lose the job. The five stages of a plumbing design sit on one
    screen in the order the office works through them: count the fixtures,
    size the supply, size the waste, keep the hot water hot, then price it.
    """

    title = "Plumbing"
    hebrew = "אינסטלציה"
    subtitle = "כלים סניטריים, אספקה, דלוחין, מים חמים וכתב כמויות"

    def build(self) -> None:
        design = QPushButton("חשב הכול")
        design.setObjectName("Primary")
        design.clicked.connect(self.run_design)
        self.header.add_action(design)

        export = QPushButton("ייצוא כתב כמויות")
        export.clicked.connect(self.export_takeoff)
        self.header.add_action(export)

        self.stats = StatRow([
            ("fixtures", "כלים"), ("demand", "ספיקה"),
            ("dfu", "יחידות ניקוז"), ("stack", "מפל"), ("loss", "איבוד חום"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._fixtures_tab(), "כלים סניטריים")
        tabs.addTab(self._supply_tab(), "אספקה")
        tabs.addTab(self._drainage_tab(), "דלוחין ואוורור")
        tabs.addTab(self._hot_tab(), "מים חמים")
        tabs.addTab(self._takeoff_tab(), "כתב כמויות")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

        self._takeoff = None
        self._schedule = None

    # -- fixtures ----------------------------------------------------------- #
    def _fixtures_tab(self) -> QWidget:
        from ..plumbing import FIXTURES, TYPICAL_DWELLING

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))
        self.dwellings = QSpinBox()
        self.dwellings.setRange(1, 500)
        self.dwellings.setValue(8)
        self.dwellings.setSuffix(" דירות")
        self.dwellings.valueChanged.connect(self._apply_dwellings)

        self.supply_kind = QComboBox()
        self.supply_kind.addItem("מיכל הדחה", "tank")
        self.supply_kind.addItem("מדיח לחץ", "valve")

        for label, widget in (("כפל דירה טיפוסית", self.dwellings),
                              ("סוג הדחה", self.supply_kind)):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)

        # One spin box per fixture: a schedule is a count, and typing counts is
        # faster than any dialog that adds them one at a time.
        grid = FieldGrid()
        self.counts: dict[str, QSpinBox] = {}
        for item in FIXTURES:
            box = QSpinBox()
            box.setRange(0, 9999)
            box.setValue(TYPICAL_DWELLING.get(item.id, 0) * 8)
            box.valueChanged.connect(self._recount)
            self.counts[item.id] = box
            grid.add(item.hebrew, box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid)
        layout.addWidget(scroll, 1)

        self.demand_label = QLabel()
        self.demand_label.setObjectName("Hint")
        self.demand_label.setWordWrap(True)
        layout.addWidget(self.demand_label)
        return page

    def _apply_dwellings(self) -> None:
        """Refill the counts from the typical dwelling, times the number given."""
        from ..plumbing import TYPICAL_DWELLING

        count = self.dwellings.value()
        for fixture_id, box in self.counts.items():
            box.blockSignals(True)
            box.setValue(TYPICAL_DWELLING.get(fixture_id, 0) * count)
            box.blockSignals(False)
        self._recount()

    def _build_schedule(self) -> Any:
        from ..plumbing import FixtureSchedule, SupplyKind

        counts = {key: box.value() for key, box in self.counts.items() if box.value()}
        return FixtureSchedule.of(
            counts,
            kind=SupplyKind(self.supply_kind.currentData()),
            name="אינסטלציה",
        )

    def _recount(self) -> None:
        """The demand follows the counts as they are typed, not on a button."""
        schedule = self._build_schedule()
        self._schedule = schedule
        if not schedule.lines:
            self.demand_label.setText("הזן כמויות כדי לראות את הספיקה הנדרשת.")
            return
        self.demand_label.setText(
            f"⁦{schedule.cold_lu:g}⁩ יחידות עומס קרים ו־⁦{schedule.hot_lu:g}⁩ חמות · "
            f"ספיקה בו-זמנית ⁦{schedule.cold_demand():.2f}⁩ ו־⁦{schedule.hot_demand():.2f}⁩ "
            f"ל'/שנ' · קו משותף ⁦{schedule.total_demand():.2f}⁩ ל'/שנ' · "
            f"⁦{schedule.dfu:g}⁩ יחידות ניקוז"
        )
        self.stats.set("fixtures", str(schedule.fixture_count), "מחוברים")
        self.stats.set("demand", f"⁦{schedule.total_demand():.2f}⁩", "ל'/שנ' בו-זמנית")
        self.stats.set("dfu", f"{schedule.dfu:g}", "יחידות ניקוז")

    # -- supply -------------------------------------------------------------- #
    def _supply_tab(self) -> QWidget:
        from ..plumbing import BUILTIN_CATALOGUES

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(4))
        self.material = QComboBox()
        for key, catalogue in sorted(BUILTIN_CATALOGUES.items()):
            self.material.addItem(catalogue.name, key)

        self.run_length = QDoubleSpinBox()
        self.run_length.setRange(1.0, 2000.0)
        self.run_length.setValue(45.0)
        self.run_length.setSuffix(" מ'")

        self.height_gain = QDoubleSpinBox()
        self.height_gain.setRange(-100.0, 300.0)
        self.height_gain.setValue(12.0)
        self.height_gain.setSuffix(" מ'")

        self.available = QDoubleSpinBox()
        self.available.setRange(50.0, 1600.0)
        self.available.setValue(350.0)
        self.available.setSuffix(" קפ\"א")

        for label, widget in (
            ("חומר", self.material), ("אורך קו", self.run_length),
            ("הפרש גובה", self.height_gain), ("לחץ זמין", self.available),
        ):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            controls.addWidget(caption)
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.supply_table = DataTable(
            ["קו", "ספיקה", "קוטר", "מהירות", "איבוד/מ'", "איבוד כולל", "מצב"],
            empty_text="לחץ ״חשב הכול״ כדי לתכנן את קווי האספקה",
        )
        layout.addWidget(self.supply_table, 1)

        note = QLabel(
            "כל קו נבדק מול שלושה גבולות: מהירות מרבית, איבוד לחץ למטר, והלחץ "
            "שבאמת זמין בקצה. קוטר שנפסל נרשם עם הסיבה — מהירות גבוהה מדי היא "
            "רעש ובלאי, ואיבוד גבוה מדי הוא ברז שלא נותן מים בקומה העליונה."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # -- drainage ------------------------------------------------------------- #
    def _drainage_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(4))
        self.floors = QSpinBox()
        self.floors.setRange(1, 60)
        self.floors.setValue(4)
        self.floors.setSuffix(" קומות")

        self.fall = QComboBox()
        for value, label in ((0.01, "1% (1:100)"), (0.02, "2% (1:50)"), (0.04, "4% (1:25)")):
            self.fall.addItem(label, value)
        self.fall.setCurrentIndex(1)

        self.vent_length = QDoubleSpinBox()
        self.vent_length.setRange(1.0, 300.0)
        self.vent_length.setValue(25.0)
        self.vent_length.setSuffix(" מ'")

        for label, widget in (("קומות", self.floors), ("שיפוע ענף", self.fall),
                              ("אורך אוורור", self.vent_length)):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            controls.addWidget(caption)
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.drainage_table = DataTable(
            ["חלק", "קוטר", "נימוק"],
            empty_text="לחץ ״חשב הכול״ כדי לתכנן את מערכת הדלוחין",
        )
        layout.addWidget(self.drainage_table, 1)

        self.drainage_notes = QLabel()
        self.drainage_notes.setObjectName("Hint")
        self.drainage_notes.setWordWrap(True)
        layout.addWidget(self.drainage_notes)

        note = QLabel(
            "שלושה כללים גוברים על הטבלאות: קו ניקוז לא קטן בכיוון הזרימה, ענף "
            "אינו קטן מהמחסום הגדול ביותר שמתחבר אליו, ואסלה מתחברת ל־100 מ\"מ. "
            "הקיבולות הן ערכי הטבלאות המקובלים; הרשות המאשרת היא הקובעת."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # -- hot water -------------------------------------------------------------- #
    def _hot_tab(self) -> QWidget:
        from ..plumbing import INSULATION

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(4))
        self.loop_length = QDoubleSpinBox()
        self.loop_length.setRange(1.0, 2000.0)
        self.loop_length.setValue(120.0)
        self.loop_length.setSuffix(" מ'")

        self.insulation = QDoubleSpinBox()
        self.insulation.setRange(0.0, 100.0)
        self.insulation.setValue(25.0)
        self.insulation.setSuffix(" מ\"מ")

        self.insulation_kind = QComboBox()
        for key, (label, _lambda) in INSULATION.items():
            self.insulation_kind.addItem(label, key)

        self.dead_leg = QDoubleSpinBox()
        self.dead_leg.setRange(0.0, 60.0)
        self.dead_leg.setValue(5.0)
        self.dead_leg.setSuffix(" מ'")

        for label, widget in (
            ("אורך לולאה", self.loop_length), ("עובי בידוד", self.insulation),
            ("סוג בידוד", self.insulation_kind), ("זנב ארוך ביותר", self.dead_leg),
        ):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            controls.addWidget(caption)
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.hot_grid = FieldGrid()
        self._hot_values: dict[str, QLabel] = {}
        for label in ("איבוד למטר", "איבוד כולל", "ספיקת מחזור", "קו חוזר",
                      "עומד משאבה", "הספק משאבה", "צריכה שנתית", "המתנה בזנב"):
            value = QLabel("—")
            value.setObjectName("FieldValue")
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
            self._hot_values[label] = self.hot_grid.add(label, value)
        layout.addWidget(self.hot_grid)

        self.hot_notes = QLabel()
        self.hot_notes.setObjectName("Hint")
        self.hot_notes.setWordWrap(True)
        layout.addWidget(self.hot_notes)

        note = QLabel(
            "ת\"י 1205 מחייב בידוד קווי מים חמים, וקו שאינו מבודד מאבד פי שלושה "
            "עד ארבעה. הזנב שאינו במחזור הוא מה שמכתיב כמה שניות ממתינים בברז — "
            "מחזור לא מתקן זנב ארוך."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    # -- take-off ----------------------------------------------------------------- #
    def _takeoff_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(4))
        self.waste = QDoubleSpinBox()
        self.waste.setRange(0.0, 40.0)
        self.waste.setValue(10.0)
        self.waste.setSuffix("%")
        caption = QLabel("פחת")
        caption.setObjectName("FieldLabel")
        controls.addWidget(caption)
        controls.addWidget(self.waste)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.takeoff_table = DataTable(
            ["סוג", "תיאור", "כמות", "יחידה", "הערה"],
            empty_text="לחץ ״חשב הכול״ כדי להפיק כתב כמויות",
        )
        layout.addWidget(self.takeoff_table, 1)

        note = QLabel(
            "הצנרת נספרת באורכי מלאי ולא במטרים: 46 מ' של 28 מ\"מ נחושת הם 11 "
            "מוטות של 5 מ' ושארית. הפחת נרשם כשורה נפרדת כדי שאפשר יהיה להתווכח "
            "איתו, ולא מוסתר בתוך הכמות."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # -- the calculation ------------------------------------------------------------ #
    def run_design(self) -> None:
        """Run all five stages from the counts on screen."""
        from ..plumbing import (
            DeadLeg,
            PipeRun,
            ServiceType,
            design_circulation,
            design_drainage,
            get_catalogue,
            size_pipe,
            take_off,
        )

        schedule = self._build_schedule()
        if not schedule.lines:
            self.report(
                ProfileOSError("הזן לפחות כלי סניטרי אחד"), "אין מה לחשב"
            )
            return
        self._schedule = schedule
        self._recount()

        catalogue = get_catalogue(self.material.currentData())
        material = self.material.currentData().split("-")[0]
        length = self.run_length.value()
        available = self.available.value() * 1000.0

        # -- supply ------------------------------------------------------- #
        runs: list[Any] = []
        rows: list[list[str]] = []
        colours: dict[tuple[int, int], str] = {}
        services = (
            ("קו ראשי משותף", schedule.total_demand(), ServiceType.COLD_WATER, 0.0),
            ("קו מים קרים", schedule.cold_demand(), ServiceType.COLD_WATER, 0.0),
            ("קו מים חמים", schedule.hot_demand(), ServiceType.HOT_WATER, 25.0),
        )
        for index, (name, flow, service, insulation) in enumerate(services):
            if flow <= 0:
                continue
            try:
                sized = size_pipe(
                    flow, length, catalogue, service=service,
                    fittings={"elbow_90_long": 12, "tee_through": 6,
                              "gate_valve_open": 2, "water_meter": 1},
                    height_gain_m=self.height_gain.value(),
                    available_pressure=available,
                )
            except Exception as exc:  # noqa: BLE001
                self.report(exc, "לא ניתן לתכנן את קו האספקה")
                return
            designation = sized.size.designation if sized.size else "—"
            rows.append([
                name, f"{flow:.2f}", designation,
                f"{sized.velocity:.2f}" if sized.size else "—",
                f"{sized.loss_per_metre:.0f}" if sized.size else "—",
                f"{sized.total_loss / 1000.0:.1f}" if sized.size else "—",
                "תקין" if sized.ok else "אין קוטר מתאים",
            ])
            colours[(index, 6)] = (
                self.colours.success if sized.ok else self.colours.danger
            )
            if sized.size is not None:
                runs.append(PipeRun(
                    service, designation, length, material,
                    insulation_mm=insulation,
                    fittings={"elbow_90_long": 12, "tee_through": 6},
                    valves=2, name=name,
                ))
        self.supply_table.set_rows(rows, numeric_columns=(1, 3, 4, 5), colours=colours)

        # -- drainage ------------------------------------------------------ #
        try:
            drainage = design_drainage(
                schedule,
                floors=self.floors.value(),
                fall=self.fall.currentData(),
                vent_length_m=self.vent_length.value(),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לתכנן את מערכת הדלוחין")
            return
        self.drainage_table.set_rows([list(row) for row in drainage.rows()])
        self.drainage_notes.setText("  ·  ".join(drainage.notes()))
        if drainage.stack.size_mm:
            self.stats.set("stack", f"⌀{drainage.stack.size_mm:.0f}", "מ\"מ")
            runs.append(PipeRun(
                ServiceType.DRAINAGE, f"{drainage.stack.size_mm:.0f} mm",
                self.floors.value() * 3.0, "pvc",
                fittings={"elbow_45": self.floors.value() * 2}, name="מפל",
            ))

        # -- hot water ------------------------------------------------------ #
        flow_bore = 28.0
        for run in runs:
            if run.service is ServiceType.HOT_WATER:
                size = catalogue.by_designation(run.designation)
                if size is not None:
                    flow_bore = size.outer_diameter
                break
        try:
            circulation = design_circulation(
                self.loop_length.value(), flow_bore, catalogue,
                insulation_mm=self.insulation.value(),
                material=self.insulation_kind.currentData(),
                dead_legs=[DeadLeg("הזנב הארוך ביותר", self.dead_leg.value(), 16.0)],
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לתכנן את מחזור המים החמים")
            return

        leg = circulation.dead_legs[0] if circulation.dead_legs else None
        for label, value in (
            ("איבוד למטר", f"⁦{circulation.loss_per_metre:.1f}⁩ ואט/מ'"),
            ("איבוד כולל", f"⁦{circulation.total_watts:.0f}⁩ ואט"),
            ("ספיקת מחזור", f"⁦{circulation.flow_lps:.3f}⁩ ל'/שנ'"),
            ("קו חוזר", getattr(circulation.return_size, "designation", "—")),
            ("עומד משאבה", f"⁦{circulation.pump_head_kpa:.1f}⁩ קפ\"א"),
            ("הספק משאבה", f"⁦{circulation.pump_watts:.1f}⁩ ואט"),
            ("צריכה שנתית", f"⁦{circulation.annual_kwh:,.0f}⁩ קוט\"ש"),
            ("המתנה בזנב", f"⁦{leg.wait_seconds:.0f}⁩ שנ׳" if leg else "—"),
        ):
            self._hot_values[label].setText(value)
        self.hot_notes.setText("  ·  ".join(circulation.notes))
        self.stats.set("loss", f"⁦{circulation.total_watts:.0f}⁩", "ואט בלולאה")
        if circulation.return_size is not None:
            runs.append(PipeRun(
                ServiceType.HOT_WATER, circulation.return_size.designation,
                self.loop_length.value(), material,
                insulation_mm=self.insulation.value(), name="קו חוזר",
            ))

        # -- take-off -------------------------------------------------------- #
        self._takeoff = take_off(runs, schedule=schedule, waste_pct=self.waste.value())
        self.takeoff_table.set_rows(
            [list(row) for row in self._takeoff.rows()], numeric_columns=(2,)
        )
        self.status(
            f"תוכננו {len(rows)} קווי אספקה, מפל ⌀{drainage.stack.size_mm or 0:.0f} "
            f"ולולאת מחזור של ⁦{circulation.total_watts:.0f}⁩ ואט"
        )

    def export_takeoff(self) -> None:
        """Write the merchant's list as a CSV anybody can open."""
        import csv

        if self._takeoff is None:
            self.report(ProfileOSError("חשב קודם"), "אין מה לייצא")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת כתב הכמויות", "plumbing-takeoff.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["סוג", "תיאור", "כמות", "יחידה", "הערה"])
                writer.writerows(self._takeoff.rows())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הייצוא נכשל")
            return
        self.status(f"כתב הכמויות נשמר: {path}")

    def refresh(self) -> None:
        self._recount()


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class ServicePage(Page):
    """The calls that come back, and what they say about the work.

    Every other screen in this suite is about making the window. This is the
    only one about the window a year later, which is where a shop's reputation
    is actually decided — and where, until now, the record was a notebook.
    """

    title = "Service"
    hebrew = "שירות"
    subtitle = "קריאות שירות, אחריות ומה שחוזר מהשטח"

    def build(self) -> None:
        new_call = QPushButton("קריאה חדשה")
        new_call.setObjectName("Primary")
        new_call.clicked.connect(self.new_call)
        self.header.add_action(new_call)

        close_call = QPushButton("סגירת קריאה")
        close_call.clicked.connect(self.close_call)
        self.header.add_action(close_call)

        self.stats = StatRow([
            ("open", "קריאות פתוחות"), ("overdue", "באיחור"),
            ("ours", "שעות על חשבוננו"), ("charged", "נגבה"),
            ("ontime", "בזמן היעד"),
        ])
        self.body.addWidget(self.stats)

        splitter = QSplitter(Qt.Orientation.Vertical)

        calls = Card("קריאות")
        self.calls_table = DataTable(
            ["מספר", "לקוח", "פתח", "תקלה", "נפתחה", "יעד", "מצב", "אחריות", "סיבה"],
            empty_text="אין קריאות שירות — לחץ ״קריאה חדשה״ כדי לרשום אחת",
        )
        calls.add(self.calls_table, 1)
        splitter.addWidget(calls)

        lower = QWidget()
        lower_layout = QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(METRICS.space(3))

        patterns = Card("מה חוזר")
        self.patterns_table = DataTable(
            ["תופעה וסיבה", "מקרים"],
            empty_text="עדיין אין מספיק קריאות מאובחנות כדי לראות דפוס",
        )
        patterns.add(self.patterns_table, 1)
        pattern_note = QLabel(
            "תקלה שחוזרת שלוש פעמים מאותה סיבה היא הודעה לבית המלאכה, "
            "לא לטכנאי. הסיבה נרשמת בסגירת הקריאה — בלעדיה אי אפשר לראות דפוס."
        )
        pattern_note.setObjectName("Hint")
        pattern_note.setWordWrap(True)
        patterns.add(pattern_note)
        lower_layout.addWidget(patterns, 1)

        warranty = Card("אחריות")
        self.warranty_table = DataTable(["רכיב", "חודשים"])
        warranty.add(self.warranty_table, 1)
        lower_layout.addWidget(warranty, 1)

        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([320, 240])
        self.body.addWidget(splitter, 1)

        self._calls: list[Any] = []

    def _register(self) -> Any:
        from ..service import default_register

        return default_register()

    def new_call(self) -> None:
        from .dialogs import NewServiceCallDialog

        dialog = NewServiceCallDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from ..service import ServiceCall, Symptom

        values = dialog.values()
        try:
            call = ServiceCall(
                job_id=values["job_id"],
                customer_name=values["customer"],
                element_name=values["element"],
                symptom=Symptom(values["symptom"]),
                description=values["description"],
                delivered=values["delivered"],
                phone=values["phone"],
            )
            self._register().add(call)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לרשום את הקריאה")
            return
        self.refresh()
        self.status(f"נרשמה קריאה {call.call_id} — {call.symptom.hebrew}")

    def close_call(self) -> None:
        """Close the selected call with the cause, which is the whole point."""
        from datetime import date as _date

        from .dialogs import CloseServiceCallDialog

        call = self._selected()
        if call is None:
            self.report(ProfileOSError("בחר קריאה מהרשימה"), "לא נבחרה קריאה")
            return
        dialog = CloseServiceCallDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from ..service import Cause

        values = dialog.values()
        call.close(
            _date.today(),
            Cause(values["cause"]),
            minutes=values["minutes"],
            engineer=values["engineer"],
            note=values["note"],
            charged=values["charged"],
        )
        self._register().update(call)
        self.refresh()
        self.status(f"נסגרה {call.call_id} — {call.cause.hebrew}")

    def _selected(self) -> Any:
        model = self.calls_table.selectionModel()
        if model is None or not model.selectedRows():
            return None
        index = model.selectedRows()[0].row()
        return self._calls[index] if index < len(self._calls) else None

    def refresh(self) -> None:
        from datetime import date as _date

        from ..service import WARRANTY_HEBREW, WARRANTY_MONTHS

        register = self._register()
        self._calls = register.all()

        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        today = _date.today()
        for index, call in enumerate(self._calls):
            covered = call.under_warranty
            warranty_text = (
                "לא ידוע" if covered is None else ("באחריות" if covered else "מחוץ לאחריות")
            )
            rows.append([
                call.call_id, call.customer_name, call.element_name or "—",
                call.symptom.hebrew,
                call.opened.strftime("%d/%m/%Y"),
                call.due_by().strftime("%d/%m/%Y"),
                call.state.hebrew, warranty_text, call.cause.hebrew,
            ])
            if call.is_overdue(today):
                colours[(index, 5)] = self.colours.danger
            elif call.state.is_open:
                colours[(index, 6)] = self.colours.warning
            if covered is False:
                colours[(index, 7)] = self.colours.text_muted
        self.calls_table.set_rows(rows, colours=colours)
        if rows and self.calls_table.currentRow() < 0:
            self.calls_table.setCurrentCell(0, 0)

        self.patterns_table.set_rows(
            [[label, count] for label, count in register.recurring(minimum=2)],
            numeric_columns=(1,),
        )
        self.warranty_table.set_rows(
            [[WARRANTY_HEBREW.get(key, key), months]
             for key, months in WARRANTY_MONTHS.items()],
            numeric_columns=(1,),
        )

        quality = register.cost_of_quality()
        performance = register.response_performance()
        self.stats.update_many({
            "open": (str(len(register.open_calls())), ""),
            "overdue": (str(len(register.overdue(today))), ""),
            "ours": (f"{quality['hours_our_fault']:.1f}", "חזרות לאתר"),
            "charged": (f"{quality['recovered']:,.0f}", "₪"),
            "ontime": (f"{performance['within_target']:.0f}%", f"{performance['closed']} נסגרו"),
        })


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #

class CollectionPage(Page):
    """Cheques: money that has been promised but is not money yet.

    A customer who hands over five post-dated cheques has paid in a sense no
    ledger recognises. This screen is the drawer they are kept in, and the
    only place that says which of them can be banked this morning.
    """

    title = "Collection"
    hebrew = "גבייה"
    subtitle = "צ׳קים דחויים, מה נפרע ומה חזר"

    def build(self) -> None:
        add = QPushButton("צ׳ק חדש")
        add.setObjectName("Primary")
        add.clicked.connect(self.new_cheque)
        self.header.add_action(add)

        deposit = QPushButton("הפקדה")
        deposit.clicked.connect(self.deposit)
        self.header.add_action(deposit)

        bounce = QPushButton("סימון כחוזר")
        bounce.clicked.connect(self.bounce)
        self.header.add_action(bounce)

        self.stats = StatRow([
            ("in_hand", "במגירה ₪"), ("bankable", "להפקדה היום ₪"),
            ("bounced", "חזרו ₪"), ("days", "ימי אשראי ממוצעים"),
        ])
        self.body.addWidget(self.stats)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        cheques = Card("צ׳קים")
        self.cheques_table = DataTable(
            ["מספר", "לקוח", "סכום", "לפירעון", "בנק", "עבודה", "מצב"],
            empty_text="אין צ׳קים בספר — לחץ ״צ׳ק חדש״",
        )
        cheques.add(self.cheques_table, 1)
        splitter.addWidget(cheques)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(METRICS.space(3))

        flow = Card("תזרים צפוי")
        self.flow_table = DataTable(
            ["שבוע", "צפוי ₪"],
            empty_text="אין צ׳קים דחויים",
        )
        flow.add(self.flow_table, 1)
        side_layout.addWidget(flow, 1)

        risk = Card("לקוחות בסיכון")
        self.risk_table = DataTable(
            ["לקוח", "חזרו", "שיעור"],
            empty_text="אף צ׳ק לא חזר",
        )
        risk.add(self.risk_table, 1)
        note = QLabel(
            "צ׳ק אינו הכנסה עד שהוא נפרע. הוא לא נרשם בספרים כאן בכוונה — "
            "רגע שרושמים אותו כתקבול, מפסיקים לשים לב שלקוח משלם בנייר."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        risk.add(note)
        side_layout.addWidget(risk, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([METRICS.panel_width * 2, METRICS.panel_width])
        self.body.addWidget(splitter, 1)

        self._book = None
        self._cheques: list[Any] = []

    def book(self) -> Any:
        """The drawer, kept for the life of the window."""
        from ..erp.collection import ChequeBook

        if self._book is None:
            self._book = ChequeBook()
        return self._book

    def new_cheque(self) -> None:
        from .dialogs import NewChequeDialog

        dialog = NewChequeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from ..erp.collection import Cheque

        values = dialog.values()
        try:
            cheque = Cheque(
                customer=values["customer"],
                amount=values["amount"],
                due=values["due"],
                bank=values["bank"],
                number=values["number"],
                job_id=values["job_id"],
            )
            self.book().add(cheque)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לרשום את הצ׳ק")
            return
        self.refresh()
        self.status(f"נרשם צ׳ק ⁦{cheque.amount:,.0f}⁩ ₪ מ{cheque.customer}")

    def _selected(self) -> Any:
        model = self.cheques_table.selectionModel()
        if model is None or not model.selectedRows():
            return None
        index = model.selectedRows()[0].row()
        return self._cheques[index] if index < len(self._cheques) else None

    def deposit(self) -> None:
        cheque = self._selected()
        if cheque is None:
            self.report(ProfileOSError("בחר צ׳ק מהרשימה"), "לא נבחר צ׳ק")
            return
        try:
            cheque.deposit()
        except Exception as exc:  # noqa: BLE001 - a post-dated cheque says why
            self.report(exc, "אי אפשר להפקיד")
            return
        self.refresh()
        self.status(f"הופקד ⁦{cheque.amount:,.0f}⁩ ₪")

    def bounce(self) -> None:
        cheque = self._selected()
        if cheque is None:
            self.report(ProfileOSError("בחר צ׳ק מהרשימה"), "לא נבחר צ׳ק")
            return
        cheque.bounce(reason="חזר")
        self.refresh()
        self.status(f"סומן כחוזר: ⁦{cheque.amount:,.0f}⁩ ₪", )

    def refresh(self) -> None:
        book = self.book()
        self._cheques = list(book)

        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        bankable = {cheque.cheque_id for cheque in book.bankable()}
        for index, cheque in enumerate(self._cheques):
            rows.append([
                cheque.number or cheque.cheque_id, cheque.customer,
                f"{cheque.amount:,.0f}", cheque.due.strftime("%d/%m/%Y"),
                cheque.bank or "—", cheque.job_id or "—", cheque.state.hebrew,
            ])
            from ..erp.collection import ChequeState

            if cheque.state is ChequeState.BOUNCED:
                colours[(index, 6)] = self.colours.danger
            elif cheque.cheque_id in bankable:
                colours[(index, 3)] = self.colours.success
            elif cheque.state is ChequeState.CLEARED:
                colours[(index, 6)] = self.colours.success
        self.cheques_table.set_rows(rows, numeric_columns=(2,), colours=colours)

        self.flow_table.set_rows(
            [[week.strftime("%d/%m/%Y"), f"{amount:,.0f}"]
             for week, amount in book.cash_flow()],
            numeric_columns=(1,),
        )
        self.risk_table.set_rows(
            [[customer, count, f"{rate:.0f}%"]
             for customer, count, rate in book.risky_customers(minimum=1)],
            numeric_columns=(1,),
        )

        summary = book.summary()
        self.stats.update_many({
            "in_hand": (f"{summary['in_hand']:,.0f}", f"{summary['count']} צ׳קים"),
            "bankable": (f"{summary['bankable_today']:,.0f}", ""),
            "bounced": (f"{summary['bounced']:,.0f}", ""),
            "days": (f"{summary['average_days_out']:.0f}", "ימים"),
        })


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #

class DeliveryPage(Page):
    """Loading the lorry in the order the units come off it.

    The last thing that happens in the workshop is the one nobody plans:
    finished units loaded in the order they were glazed, arriving at a site
    that wants the ground floor first. This screen is the fix, and it costs
    one look at it.
    """

    title = "Delivery"
    hebrew = "הובלה והרכבה"
    subtitle = "רשימת העמסה בסדר ההרכבה, ותכנון ימי ההרכבה"

    def build(self) -> None:
        plan = QPushButton("תכנן")
        plan.setObjectName("Primary")
        plan.clicked.connect(self.replan)
        self.header.add_action(plan)

        handover = QPushButton("תיק מסירה...")
        handover.clicked.connect(self.export_handover)
        self.header.add_action(handover)

        self.stats = StatRow([
            ("loads", "הובלות"), ("pieces", "יחידות"), ("mass", "משקל ק״ג"),
            ("days", "ימי הרכבה"), ("finish", "סיום צפוי"),
        ])
        self.body.addWidget(self.stats)

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(3))

        self.vehicle = QComboBox()
        from ..delivery import VEHICLES

        for lorry in VEHICLES:
            self.vehicle.addItem(
                f"{lorry.hebrew} · ⁦{lorry.payload_kg:,.0f}⁩ ק״ג", lorry.name
            )
        self.vehicle.setCurrentIndex(2)

        self.condition = QComboBox()
        from ..delivery import Access as _Access, SiteCondition as _Condition

        for condition in _Condition:
            self.condition.addItem(condition.hebrew, condition.value)

        self.access = QComboBox()
        for access in _Access:
            self.access.addItem(access.hebrew, access.value)

        self.crew_size = QSpinBox()
        self.crew_size.setRange(1, 8)
        self.crew_size.setValue(2)
        self.crew_size.setSuffix(" אנשים")

        for label, widget in (
            ("רכב", self.vehicle), ("תנאי האתר", self.condition),
            ("גישה", self.access), ("צוות", self.crew_size),
        ):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            controls.addWidget(caption)
            controls.addWidget(widget)
        controls.addStretch(1)
        self.body.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        loading = Card("רשימת העמסה")
        self.loads_table = DataTable(
            ["הובלה", "סימון", "מיקום", "קומה", "מידה", "כמות", "ק״ג", "נשיאה", "אביזרים"],
            empty_text="תכנן פתחים ולחץ ״תכנן״ — הרשימה נבנית מהעבודה שעל השולחן",
        )
        loading.add(self.loads_table, 1)
        splitter.addWidget(loading)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(METRICS.space(3))

        schedule = Card("ימי הרכבה")
        self.days_table = DataTable(
            ["יום", "תאריך", "יחידות", "שעות"],
            empty_text="אין תכנון עדיין",
        )
        schedule.add(self.days_table, 1)
        side_layout.addWidget(schedule, 1)

        notes = Card("לשים לב")
        # A plain text edit does not inherit the application's direction, so
        # a Hebrew line with a number in it comes out shuffled unless both the
        # widget and its document are told which way the text runs.
        self.delivery_notes = QPlainTextEdit()
        self.delivery_notes.setReadOnly(True)
        self.delivery_notes.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        option = self.delivery_notes.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirection.RightToLeft)
        option.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.delivery_notes.document().setDefaultTextOption(option)
        notes.add(self.delivery_notes, 1)
        side_layout.addWidget(notes, 1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([METRICS.panel_width * 2, METRICS.panel_width])
        self.body.addWidget(splitter, 1)

    def refresh(self) -> None:
        self.replan()

    def replan(self) -> None:
        """Rebuild the load list and the fitting days from the open work."""
        from ..delivery import (
            Access, Crew, SiteCondition, pack, plan_installation, units_from_builds,
        )

        builds = self.session.builds
        if not builds:
            self.loads_table.set_rows([])
            self.days_table.set_rows([])
            self.delivery_notes.setPlainText(
                "אין פתחים בעבודה. תכנן פתחים בעמוד ״פתח״ ואז חזור לכאן."
            )
            self.stats.update_many({
                key: ("—", "") for key in
                ("loads", "pieces", "mass", "days", "finish")
            })
            return

        units = units_from_builds(builds)
        packing = pack(units, vehicle_name=self.vehicle.currentData())
        plan = plan_installation(
            units,
            crew=Crew(name="צוות א׳", people=self.crew_size.value()),
            condition=SiteCondition(self.condition.currentData()),
            access=Access(self.access.currentData()),
        )

        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        index = 0
        for load in packing.loads:
            for unit in load.units:
                rows.append([
                    load.number, unit.mark, unit.location or "—", unit.floor,
                    f"{unit.width:.0f}×{unit.height:.0f}", unit.quantity,
                    f"{unit.total_mass:.1f}", unit.handling.hebrew,
                    ", ".join(unit.accessories) or "—",
                ])
                if unit.handling.people >= 4:
                    colours[(index, 7)] = self.colours.warning
                index += 1
        self.loads_table.set_rows(rows, numeric_columns=(0, 3, 5, 6), colours=colours)

        self.days_table.set_rows(
            [
                [number, day.strftime("%d/%m/%Y"), len(tasks),
                 f"{sum(task.minutes for task in tasks) / 60.0:.1f}"]
                for number, (day, tasks) in enumerate(plan.days, start=1)
            ],
            numeric_columns=(0, 2, 3),
        )

        notes = [load.describe() for load in packing.loads]
        notes.extend(packing.warnings)
        notes.extend(plan.warnings)
        self.delivery_notes.setPlainText("\n".join(f"• {note}" for note in notes))

        summary = packing.summary()
        self.stats.update_many({
            "loads": (str(summary["loads"]), "נדרש מנוף" if summary["crane"] else ""),
            "pieces": (str(summary["pieces"]), ""),
            "mass": (f"{summary['mass_kg']:,.0f}", ""),
            "days": (str(len(plan.days)), f"{plan.person_hours:.0f} שעות-אדם"),
            "finish": (
                plan.finish.strftime("%d/%m") if plan.finish else "—",
                plan.finish.strftime("%Y") if plan.finish else "",
            ),
        })


    def export_handover(self) -> None:
        """The folder the customer keeps, and the warranty that starts with it.

        Written at handover rather than reconstructed three years later from
        whatever anybody remembers, which is how a claim in year four is
        currently settled.
        """
        from datetime import date as _date

        from ..delivery.handover import pack_from_job, write_handover

        builds = self.session.builds
        if not builds:
            self.report(
                ProfileOSError(
                    "אין פתחים בעבודה. תכנן פתחים בעמוד ״פתח״ ואז חזור לכאן."
                ),
                "אין מה למסור",
            )
            return

        job = getattr(self.session, "job", None)
        from ..branding import active_brand

        brand = active_brand()
        contact = " · ".join(
            part for part in (brand.display_name, getattr(brand, "phone", ""))
            if part
        )
        pack = pack_from_job(
            job if job is not None else _EmptyJob(),
            builds=builds,
            handed_over_on=_date.today(),
            service_contact=contact,
        )

        problems = pack.problems()
        if problems:
            self.status(" · ".join(problems[:3]))

        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת תיק מסירה", "handover.html", "HTML (*.html)"
        )
        if not path:
            return
        write_handover(pack, path)
        self.status(f"{pack.describe()} · {path}")


class _EmptyJob:
    """Stands in when work is on the table but no job file is open."""

    job_id = ""
    name = ""
    customer_name = ""
    site_address = ""


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #

class CataloguePage(Page):
    """Build an owned profile library from what suppliers publish."""

    title = "Catalogue"
    hebrew = "קטלוג"
    subtitle = "קריאת שרטוטי יצרן וטבלאות לספרייה שבבעלותך"

    def build(self) -> None:
        self._system_ids: list[str] = []

        run = QPushButton("קליטה")
        run.setObjectName("Primary")
        run.clicked.connect(self.run)
        self.header.add_action(run)

        export = QPushButton("שמירה כתוסף")
        export.clicked.connect(self.export_plugin)
        self.header.add_action(export)

        self.stats = StatRow([
            ("articles", "פריטים"), ("geometry", "עם גאומטריה"),
            ("verified", "מאומתים"), ("conflicts", "סתירות"),
            ("unmatched", "ללא התאמה"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._systems_tab(), "מערכות")
        tabs.addTab(self._ingest_tab(), "קליטת קטלוג")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

    def _ingest_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        sources = Card("מקורות")
        grid = FieldGrid()

        self.drawings_label = QLabel("לא נבחרה תיקייה")
        self.drawings_label.setObjectName("FieldValue")
        pick_drawings = QPushButton("בחירת תיקיית DXF…")
        pick_drawings.clicked.connect(self._pick_drawings)
        drawings_row = QHBoxLayout()
        drawings_row.addWidget(pick_drawings)
        drawings_row.addWidget(self.drawings_label, 1)

        self.table_label = QLabel("לא נבחרה טבלה")
        self.table_label.setObjectName("FieldValue")
        pick_table = QPushButton("בחירת קטלוג…")
        pick_table.clicked.connect(self._pick_table)
        table_row = QHBoxLayout()
        table_row.addWidget(pick_table)
        table_row.addWidget(self.table_label, 1)

        self.series = QComboBox()
        self.series.setEditable(True)
        self.series.addItems(["", "MB-70", "4300", "CW-50"])

        sources.add_layout(drawings_row)
        sources.add_layout(table_row)
        series_row = QHBoxLayout()
        caption = QLabel("סדרת מערכת")
        caption.setObjectName("FieldLabel")
        series_row.addWidget(caption)
        series_row.addWidget(self.series)
        series_row.addStretch(1)
        sources.add_layout(series_row)
        sources.add(grid)
        layout.addWidget(sources)

        note = QLabel(
            "כל שרטוט נמדד על ידי מנועי הגאומטריה והחוזק ומושווה מול הטבלה "
            "שהיצרן פרסם. הסכמה היא ראיה; אי-הסכמה מדווחת ולא מוכרעת, "
            "והפריט נשאר מחוץ לספרייה עד שמישהו מחליט איזה נתון נכון."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.entries = DataTable(
            ["פריט", "שם", "סטטוס", "נבדקו", "סתירות", "שרטוט"],
            empty_text="בחר מקורות ולחץ ״קליטה״ כדי לבנות את הספרייה",
        )
        splitter.addWidget(self.entries)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setObjectName("Mono")
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self.entries.itemSelectionChanged.connect(self._show_detail)

        self._drawings: Path | None = None
        self._table: Path | None = None
        return page

    # -- systems ------------------------------------------------------------- #
    def _systems_tab(self) -> QWidget:
        """The series this shop works with, and what each may be used for.

        This is the screen that turns a directory into a library. A series
        starts unclassified — the software will not guess whether קליל 7000 is
        casement or sliding, because the wrong guess picks the wrong hardware.
        Classifying it here takes seconds, is recorded with who decided, and
        survives a restart. Cutting still waits for the supplier's own figures,
        which arrive through ingestion on the tab beside this one.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        controls = QHBoxLayout()
        controls.setSpacing(METRICS.space(3))
        self.family_combo = QComboBox()
        from ..systems import SystemFamily

        for family in SystemFamily:
            self.family_combo.addItem(family.label("he"), family.value)

        self.decided_by = QLineEdit()
        self.decided_by.setPlaceholderText("מי מחליט ולפי מה — למשל: דאדי, קטלוג קליל 2026")

        classify = QPushButton("סווג את הסדרה שנבחרה")
        classify.setObjectName("Primary")
        classify.clicked.connect(self.classify_system)

        for label, widget in (("משפחה", self.family_combo), ("מקור", self.decided_by)):
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            controls.addWidget(caption)
            controls.addWidget(widget, 1 if widget is self.decided_by else 0)
        controls.addWidget(classify)

        # And the button that actually opens the saw: eleven numbers off the
        # supplier's catalogue, entered once.
        confirm = QPushButton("הזן נתוני יצרן…")
        confirm.clicked.connect(self.confirm_system)
        controls.addWidget(confirm)
        layout.addLayout(controls)

        self.systems_table = DataTable(
            ["סדרה", "יצרן", "שם", "משפחה", "להצעה", "לחיתוך", "מקור"],
            empty_text="ספריית המערכות ריקה",
        )
        layout.addWidget(self.systems_table, 1)

        self.systems_status = QLabel()
        self.systems_status.setObjectName("Hint")
        self.systems_status.setWordWrap(True)
        layout.addWidget(self.systems_status)

        note = QLabel(
            "סדרה שסווגה אפשר לתמחר לפיה — על ערכי משפחה טיפוסיים, לא על נתוני "
            "היצרן. חיתוך נפתח רק אחרי שהוזנו נתוני היצרן: ⁦11⁩ מספרים "
            "מהקטלוג, פעם אחת לסדרה, בכפתור ״הזן נתוני יצרן״. "
            "עד אז כל דף חיתוך נושא ״לא לייצור״, וזה בכוונה — "
            "מוט שנחתך לפי ניחוש הוא מוט שנזרק."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _selected_system(self) -> str:
        rows = self.systems_table.selectionModel()
        if rows is None or not rows.selectedRows():
            return ""
        index = rows.selectedRows()[0].row()
        return self._system_ids[index] if index < len(self._system_ids) else ""

    def confirm_system(self) -> None:
        """Enter the supplier's own figures, which is what unlocks cutting."""
        from .dialogs import ConfirmSystemDialog

        entry_id = self._selected_system()
        if not entry_id:
            self.report(ProfileOSError("בחר סדרה מהרשימה"), "לא נבחרה סדרה")
            return

        from ..systems import DIRECTORY, Confirmation, default_confirmations

        entry = DIRECTORY.get(entry_id)
        book = default_confirmations()
        dialog = ConfirmSystemDialog(
            entry.display if entry else entry_id,
            existing=book.get(entry_id),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        confirmation = Confirmation(
            entry_id=entry_id,
            source=values["source"],
            entered_by=values["entered_by"],
            values=values["figures"],
            profiles=values["profiles"],
        )
        try:
            book.record(confirmation)
        except Exception as exc:  # noqa: BLE001 - the reason is the answer
            self.report(exc, "הנתונים לא התקבלו")
            return

        self.refresh()
        self.status(f"{entry.display if entry else entry_id} — ניתן לחיתוך")

    def classify_system(self) -> None:
        from ..systems import DIRECTORY, SystemFamily, default_decisions

        entry_id = self._selected_system()
        if not entry_id:
            self.report(ProfileOSError("בחר סדרה מהרשימה"), "לא נבחרה סדרה")
            return
        source = self.decided_by.text().strip()
        family = SystemFamily(self.family_combo.currentData())
        try:
            default_decisions().record(entry_id, family.value, source=source)
            DIRECTORY.classify(entry_id, family, source=source)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לסווג את הסדרה")
            return
        self._refresh_systems()
        self.status(f"{entry_id} סווגה כ{family.label('he')}")

    def _refresh_systems(self) -> None:
        from ..systems import DIRECTORY

        entries = sorted(DIRECTORY, key=lambda e: (e.manufacturer, e.series))
        self._system_ids = [entry.id for entry in entries]

        colours: dict[tuple[int, int], str] = {}
        rows = []
        quotable = cuttable = 0
        for index, entry in enumerate(entries):
            readiness = DIRECTORY.readiness(entry.id)
            quotable += bool(readiness.may_quote)
            cuttable += bool(readiness.may_cut)
            rows.append([
                entry.series, entry.manufacturer,
                entry.hebrew or entry.display,
                entry.family.label("he") if entry.family else "לא סווגה",
                "כן" if readiness.may_quote else "לא",
                "כן" if readiness.may_cut else "לא",
                entry.source or "—",
            ])
            colours[(index, 4)] = (
                self.colours.success if readiness.may_quote else self.colours.text_faint
            )
            colours[(index, 5)] = (
                self.colours.success if readiness.may_cut else self.colours.warning
            )
        self.systems_table.set_rows(rows, colours=colours)
        self.systems_status.setText(
            f"⁦{len(entries)}⁩ סדרות · ⁦{quotable}⁩ ניתנות לתמחור · "
            f"⁦{cuttable}⁩ ניתנות לחיתוך"
            + ("" if cuttable else " — אף סדרה עדיין ללא נתוני היצרן עצמו")
        )

    def _pick_drawings(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "תיקיית DXF של היצרן")
        if folder:
            self._drawings = Path(folder)
            self.drawings_label.setText(str(self._drawings))

    def _pick_table(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "קטלוג יצרן", "", "קטלוגים (*.pdf *.csv *.tsv *.txt)"
        )
        if path:
            self._table = Path(path)
            self.table_label.setText(self._table.name)

    def run(self) -> None:
        from ..catalogue import ingest

        if self._drawings is None and self._table is None:
            self.report(
                ProfileOSError("בחר תיקיית שרטוטים, טבלת קטלוג, או שניהם"),
                "אין מה לקלוט",
            )
            return

        try:
            report = ingest(
                table=self._table,
                drawings=self._drawings,
                system_series=self.series.currentText() or "unknown",
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הקליטה נכשלה")
            return

        self.session.set_catalogue(report)
        stats = report.summary()
        self.stats.update_many({
            "articles": (str(stats["entries"]), ""),
            "geometry": (str(stats["with_geometry"]), "נמדדו מ-DXF"),
            "verified": (str(stats["verified"]), "הטבלה מסכימה"),
            "conflicts": (str(stats["conflicts"]), "הטבלה חולקת"),
            "unmatched": (
                str(stats["unmatched_drawings"] + stats["unmatched_rows"]),
                "בלי מקבילה",
            ),
        })

        colours: dict[tuple[int, int], str] = {}
        rows = []
        tint = {
            "verified": self.colours.success,
            "conflict": self.colours.danger,
            "unverified": self.colours.warning,
        }
        status_hebrew = {
            "verified": "מאומת", "conflict": "סתירה", "unverified": "לא מאומת",
        }
        for index, entry in enumerate(report.entries):
            summary = entry.summary()
            rows.append([
                entry.profile_id, (entry.name or "")[:40],
                status_hebrew.get(entry.status, entry.status),
                summary["checked"], summary["conflicts"] or "—",
                Path(entry.dxf_path).name if entry.dxf_path else "—",
            ])
            if entry.status in tint:
                colours[(index, 2)] = tint[entry.status]
        self.entries.set_rows(rows, numeric_columns=(3, 4), colours=colours)

        for message in report.errors[:5]:
            self.status(message)
        self.status(
            f"{stats['entries']} פריטים, {stats['verified']} מאומתים, "
            f"{stats['conflicts']} בסתירה"
        )

    def _show_detail(self) -> None:
        report = self.session.catalogue_report
        rows = self.entries.selectionModel()
        if report is None or rows is None or not rows.selectedRows():
            return
        index = rows.selectedRows()[0].row()
        if index >= len(report.entries):
            return
        entry = report.entries[index]

        lines = [f"{entry.profile_id}  —  {entry.name or 'ללא שם'}", ""]
        if entry.dxf_path:
            lines.append(f"שרטוט : {entry.dxf_path}")
        if entry.pdf_page:
            lines.append(f"עמוד  : {entry.pdf_page}")
        status_hebrew = {
            "verified": "מאומת", "conflict": "סתירה", "unverified": "לא מאומת",
        }
        lines.append(f"סטטוס : {status_hebrew.get(entry.status, entry.status)}")
        lines.append("")
        if entry.checks:
            lines.append("מפורסם מול נמדד")
            for check in entry.checks:
                mark = {"agree": "תקין", "disagree": "סתירה", "unchecked": "  -"}[
                    str(check.status)
                ]
                lines.append(f"  {mark}  {check.describe()}")
        else:
            lines.append("לא הושוו נתונים: רק מקור אחד סיפק מספרים")
        if entry.warnings:
            lines.append("")
            lines.append("אזהרות")
            lines.extend(f"  - {warning}" for warning in entry.warnings)
        self.detail.setPlainText("\n".join(lines))

    def refresh(self) -> None:
        self._refresh_systems()

    def export_plugin(self) -> None:
        from ..catalogue import to_plugin

        report = self.session.catalogue_report
        if report is None:
            self.report(ProfileOSError("קלוט קטלוג קודם"), "אין מה לשמור")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת ספריית פרופילים", "profile-library.json", "JSON (*.json)"
        )
        if not path:
            return
        target = Path(path)
        payload = to_plugin(
            report,
            plugin_id=target.stem,
            name=f"{self.series.currentText() or 'unknown'} profile library",
        )
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.status(
            f"נשמרו {len(payload['profiles'])} פרופילים אל {target}; "
            f"{len(payload['excluded_for_conflict'])} הושהו בגלל סתירה"
        )


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #

class SystemPage(Page):
    """Updates, licence, brand, plugins and the capability comparison."""

    title = "System"
    hebrew = "מערכת"
    subtitle = "עדכונים, רישוי, תוספים והיקף התוכנה"

    def build(self) -> None:
        check = QPushButton("בדיקת עדכונים")
        check.setObjectName("Primary")
        check.clicked.connect(self.check_updates)
        self.header.add_action(check)

        tabs = QTabWidget()
        tabs.addTab(self._readiness_tab(), "מצב ההקמה")
        tabs.addTab(self._updates_tab(), "עדכונים")
        tabs.addTab(self._licence_tab(), "רישיון")
        tabs.addTab(self._brand_tab(), "מפעיל")
        tabs.addTab(self._plugins_tab(), "תוספים")
        tabs.addTab(self._backup_tab(), "גיבוי ושחזור")
        tabs.addTab(self._audit_tab(), "יומן שינויים")

        # The comparison verifies every capability claim, which means importing
        # every engine in the suite — OR-Tools, sectionproperties, FastAPI and
        # the rest. Doing that while the window is still being built costs the
        # user twenty seconds before they see anything, to populate a tab most
        # sessions never open. It is filled the first time it is looked at.
        self._compare_page = QWidget()
        self._compare_layout = QVBoxLayout(self._compare_page)
        self._compare_layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        self._compare_layout.setSpacing(METRICS.space(3))
        self._compare_built = False
        self._compare_index = tabs.addTab(self._compare_page, "השוואה")
        tabs.currentChanged.connect(self._tab_changed)

        self.tabs = tabs
        self.body.addWidget(tabs, 1)

    def _tab_changed(self, index: int) -> None:
        if index == self._compare_index:
            self._build_comparison()

    # -- updates ------------------------------------------------------------- #
    def refresh(self) -> None:
        """The setup list is re-read every time the page is opened.

        It is the one panel whose answer changes because of work done on
        other screens, so a cached copy of it would be wrong exactly when it
        matters.
        """
        self.show_setup()

    def _readiness_tab(self) -> QWidget:
        """The full setup list — the front page shows only the next few."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.setup_headline = QLabel("")
        self.setup_headline.setObjectName("StatLabel")
        self.setup_headline.setWordWrap(True)
        layout.addWidget(self.setup_headline)

        self.setup_table = DataTable(
            ["", "מה", "מצב", "מה יש כרגע", "מה זה חוסם", "איך סוגרים"],
            empty_text="אין בדיקות",
        )
        layout.addWidget(self.setup_table, 1)

        note = QLabel(
            "אף תוכנה בענף אינה שלמה עד שהעובדות של המפעל בתוכה: אילו סדרות "
            "קונים, מה הקיזוזים של הספק, מה הצַבָּע גובה, איזו מכונה על הרצפה. "
            "ההבדל כאן הוא שזה נאמר במפורש במקום שמסך ייראה גמור ויתמחר על "
            "ערכי ברירת מחדל."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def show_setup(self) -> None:
        from ..readiness import State, readiness

        try:
            report = readiness()
        except Exception as exc:  # noqa: BLE001
            self.setup_table.set_rows([])
            self.setup_headline.setText(str(exc))
            return

        tone = {
            State.ATTENTION: self.colours.danger,
            State.EMPTY: self.colours.warning,
            State.PARTIAL: self.colours.info,
            State.READY: self.colours.success,
        }
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        for index, check in enumerate(report):
            rows.append([
                "●", check.hebrew, check.state.hebrew, check.detail,
                check.blocks or "—", check.fix or "—",
            ])
            colours[(index, 0)] = tone[check.state]
        self.setup_table.set_rows(rows, colours=colours)
        self.setup_headline.setText(report.verdict())
        self.setup_headline.setStyleSheet(
            f"color: {self.colours.success if report.may_cut else self.colours.warning};"
        )

    def _updates_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        note = QLabel(
            "עדכוני תוכן הם מניפסטים חתומים. כל חבילה מורדת, החתימה שלה "
            "נבדקת והתוכן מאומת לפני שמשהו מותקן; החבילות מותקנות באופן "
            "אטומי ומוחזרות במלואן אם חלק כלשהו נכשל. מערכות פרופיל, כללים "
            "ומחירונים נכנסים לתוקף בלי הפעלה מחדש."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        source_row = QHBoxLayout()
        source_row.setSpacing(METRICS.space(3))
        self.update_source = QLineEdit()
        self.update_source.setPlaceholderText("https://updates.example.com/ או תיקייה")
        self.update_key_label = QLabel("לא נבחר מפתח")
        self.update_key_label.setObjectName("FieldValue")
        pick_key = QPushButton("מפתח מנפיק…")
        pick_key.clicked.connect(self._pick_update_key)
        self.update_channel = QComboBox()
        self.update_channel.addItems(["stable", "beta", "canary"])

        for label, widget in [("מקור", self.update_source)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            source_row.addWidget(caption)
            source_row.addWidget(widget, 1)
        source_row.addWidget(pick_key)
        source_row.addWidget(self.update_key_label)
        channel_caption = QLabel("ערוץ")
        channel_caption.setObjectName("FieldLabel")
        source_row.addWidget(channel_caption)
        source_row.addWidget(self.update_channel)
        layout.addLayout(source_row)

        key_note = QLabel(
            "בלי המפתח הציבורי של המנפיק אי אפשר להתקין דבר: מניפסט לא חתום "
            "נדחה במקום להיות מהימן — וזו כל מטרת המנגנון."
        )
        key_note.setObjectName("Hint")
        key_note.setWordWrap(True)
        layout.addWidget(key_note)

        self.update_table = DataTable(
            ["חבילה", "גרסה", "סוג", "גודל", "תיאור"],
            empty_text="לחץ ״בדיקת עדכונים״ כדי לראות חבילות זמינות",
        )
        layout.addWidget(self.update_table, 1)

        self.apply_button = QPushButton("החלת עדכונים")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply_updates)
        layout.addWidget(self.apply_button)

        installed_title = QLabel("תוכן מותקן")
        installed_title.setObjectName("CardTitle")
        layout.addWidget(installed_title)
        self.installed_table = DataTable(
            ["חבילה", "גרסה", "סוג", "הותקן"],
            empty_text="עדיין לא הותקן תוכן",
        )
        layout.addWidget(self.installed_table, 1)

        self._update_key: Path | None = None
        self._update_plan: Any = None
        self._refresh_installed()
        return page

    def _pick_update_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "מפתח ציבורי של המנפיק", "", "מפתחות PEM (*.pem *.pub);;כל הקבצים (*)"
        )
        if path:
            self._update_key = Path(path)
            self.update_key_label.setText(self._update_key.name)

    def _engine(self, *, need_key: bool) -> Any:
        """Build an update engine, or explain what is missing.

        Reading the installed state does not need a key; fetching a manifest
        does, and refusing to build the engine without one is better than
        building it with a throwaway key that would reject every signature
        with a misleading error.
        """
        from ..core.config import get_settings
        from ..core.hotreload import PluginLoader
        from ..security.keys import SigningKey, VerifyKey
        from ..updates import DirectorySource, UpdateChannel, UpdateEngine, HttpSource

        settings = get_settings()
        settings.ensure_directories()

        if need_key:
            if self._update_key is None or not self._update_key.is_file():
                raise ProfileOSError(
                    "בחר את המפתח הציבורי של המנפיק לפני בדיקת עדכונים"
                )
            verify_key = VerifyKey.from_pem(self._update_key.read_bytes())
        elif self._update_key is not None and self._update_key.is_file():
            verify_key = VerifyKey.from_pem(self._update_key.read_bytes())
        else:
            # Only ever used for the read-only installed listing below.
            verify_key = SigningKey.generate().public_key()

        raw = self.update_source.text().strip()
        if raw.startswith(("http://", "https://")):
            source: Any = HttpSource(raw)
        else:
            source = DirectorySource(raw or settings.data_dir)

        return UpdateEngine(
            source,
            verify_key,
            settings,
            channel=UpdateChannel(self.update_channel.currentText()),
            loader=PluginLoader(settings, strict=False),
        )

    def _refresh_installed(self) -> None:
        try:
            engine = self._engine(need_key=False)
            installed = engine.installed()
        except Exception as exc:  # noqa: BLE001
            self.installed_table.set_rows([["שגיאה", "", "", str(exc)]])
            return
        self.installed_table.set_rows(
            [
                [p.package_id, p.version, p.kind, p.installed_at[:19]]
                for p in installed
            ]
        )

    def check_updates(self) -> None:
        try:
            engine = self._engine(need_key=True)
            plan = engine.check()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "בדיקת העדכונים נכשלה")
            return

        self._update_plan = plan
        self.update_table.set_rows(
            [
                [
                    package.package_id,
                    package.version,
                    package.kind.value,
                    f"{package.size / 1024:.1f} kB",
                    package.description or "",
                ]
                for package in plan.packages
            ],
            numeric_columns=(3,),
        )
        self.apply_button.setEnabled(plan.has_updates)
        if plan.skipped:
            for package_id, reason in plan.skipped[:3]:
                self.status(f"דולג {package_id}: {reason}")
        self.status(
            f"{len(plan.packages)} עדכונים, ⁦{plan.total_size / 1024:.0f} kB⁩"
            if plan.has_updates
            else "הכול מעודכן"
        )

    def apply_updates(self) -> None:
        if self._update_plan is None or not self._update_plan.has_updates:
            self.report(ProfileOSError("בדוק עדכונים קודם"), "אין מה להחיל")
            return
        try:
            engine = self._engine(need_key=True)
            result = engine.apply(self._update_plan)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "העדכון נכשל והוחזר לאחור")
            return

        if result.ok:
            self.status(
                f"הוחלו {len(result.applied)} עדכונים תוך "
                f"⁦{result.duration_s:.2f} ש׳⁩, {result.reloaded} נטענו חיים"
            )
        else:
            detail = "; ".join(f"{pid}: {why}" for pid, why in result.failed)
            self.report(
                ProfileOSError(
                    f"העדכון נכשל והוחזר לאחור — {detail}"
                    if result.rolled_back
                    else f"העדכון נכשל חלקית — {detail}"
                ),
                "העדכון נכשל",
            )
        for warning in result.warnings[:3]:
            self.status(warning)
        self._update_plan = None
        self.apply_button.setEnabled(False)
        self.update_table.clear_rows()
        self._refresh_installed()

    # -- licence -------------------------------------------------------------- #
    def _licence_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        grid = FieldGrid()
        try:
            from ..security.hwid import current_fingerprint

            fingerprint = current_fingerprint()
            for label, text in [
                ("טביעת אצבע של המחשב", fingerprint.short),
                ("מאפיינים שנרשמו", str(len(fingerprint.traits))),
                (
                    "מאפיינים",
                    ", ".join(trait.name for trait in fingerprint.traits) or "אין",
                ),
            ]:
                value = QLabel(text)
                value.setObjectName("FieldValue")
                value.setWordWrap(True)
                grid.add(label, value)
        except Exception as exc:  # noqa: BLE001
            value = QLabel(f"לא זמין: {exc}")
            value.setObjectName("FieldValue")
            grid.add("טביעת אצבע של המחשב", value)
        layout.addWidget(grid)

        note = QLabel(
            "הרישיון נחתם לטביעת האצבע של המחשב הזה ב-AES-256-GCM ומאומת "
            "כולו במנותק מהרשת. טביעת האצבע היא סט משוקלל של מאפיינים ולא "
            "מספר סידורי אחד, כך שהחלפת דיסק לא מבטלת את הרישיון, ורק "
            "תקצירי מאפיינים נשמרים — קובץ הרישיון לעולם לא נושא כתובת MAC "
            "או מספר סידורי גלויים. אחרי פקיעה התוכנה עוברת לקריאה בלבד "
            "לתקופת החסד במקום לנעול את המפעל באמצע עבודה."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    # -- brand ---------------------------------------------------------------- #
    #: Field order on the operator tab, and where each value comes from.
    _BRAND_FIELDS: tuple[tuple[str, str], ...] = (
        ("שם", "display_name"),
        ("שם משפטי", "legal_name"),
        ("כתובת", "address_line"),
        ("עיר", "city"),
        ("מיקוד", "postcode"),
        ("מדינה", "country"),
        ("טלפון", "phone"),
        ("פקס", "fax"),
        ("דוא\"ל", "email"),
        ("אתר", "website"),
    )

    def _brand_tab(self) -> QWidget:
        from ..branding import BRANDS, BUILTIN_BRANDS, configured_brand_id

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        chooser = QHBoxLayout()
        caption = QLabel("מפעיל")
        caption.setObjectName("FieldLabel")
        self.brand_picker = QComboBox()
        known = dict(BUILTIN_BRANDS)
        for key in BRANDS.keys():
            entry = BRANDS.get_or_none(key)
            if entry is not None:
                known[key] = entry
        for key, entry in sorted(known.items()):
            self.brand_picker.addItem(entry.display_name, key)
        index = self.brand_picker.findData(configured_brand_id())
        if index >= 0:
            self.brand_picker.setCurrentIndex(index)
        self.brand_picker.currentIndexChanged.connect(self._choose_brand)
        chooser.addWidget(caption)
        chooser.addWidget(self.brand_picker)
        chooser.addStretch(1)
        layout.addLayout(chooser)

        # Built once, then updated in place. Tearing the tab down and building
        # it again leaves the old labels on screen until Qt gets round to
        # deleting them, and the two sets of text overlap.
        grid = FieldGrid()
        self._brand_values: dict[str, QLabel] = {}
        for label, _ in self._BRAND_FIELDS:
            value = QLabel()
            value.setObjectName("FieldValue")
            value.setWordWrap(True)
            # Hebrew values are laid out right-to-left inside the label, which
            # is correct, but left to itself the label then parks the text at
            # the far edge of a wide column, half a screen from its caption.
            # Pinning the label's own alignment keeps value beside label while
            # the text inside it still reads in its own direction.
            # AlignLeft alone is the *leading* edge, which Qt resolves to the
            # right for a Hebrew paragraph; AlignAbsolute makes it mean left.
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
            self._brand_values[label] = grid.add(label, value)
        layout.addWidget(grid)

        self.brand_note = QLabel()
        self.brand_note.setObjectName("Hint")
        self.brand_note.setWordWrap(True)
        layout.addWidget(self.brand_note)

        note = QLabel(
            "הפרטים האלה מופיעים על הצעות מחיר, כרטיסי עבודה ובכותרת של כל "
            "תוכנית מכונה. מוסיפים מפעיל נוסף, או מתקנים פרטים, באמצעות "
            "תוסף מותג."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        self._show_brand()
        return page

    def _show_brand(self) -> None:
        from ..branding import active_brand

        brand = active_brand()
        for label, attribute in self._BRAND_FIELDS:
            text = getattr(brand, attribute, None)
            self._brand_values[label].setText(str(text) if text else "לא הוגדר")
        self.brand_note.setText(brand.notes or "")
        self.brand_note.setVisible(bool(brand.notes))

    def _choose_brand(self) -> None:
        from ..branding import set_active_brand

        brand_id = self.brand_picker.currentData()
        if not brand_id:
            return
        try:
            brand = set_active_brand(brand_id, persist=True)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להחליף את המפעיל")
            return
        self._show_brand()
        window = self.window()
        if hasattr(window, "refresh_brand"):
            window.refresh_brand()
        self.status(
            f"המפעיל הוגדר ל{brand.display_name}; מעכשיו הוא יופיע על מסמכים "
            "ובכותרות תוכניות המכונה"
        )

    # -- plugins --------------------------------------------------------------- #
    def _plugins_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        table = DataTable(["רישום", "רשומות", "תוכן"])
        try:
            from ..core.registry import registry_report

            rows = []
            for name, entries in sorted(registry_report().items()):
                keys = sorted(entry.get("key", "?") for entry in entries)
                shown = ", ".join(keys[:6])
                if len(keys) > 6:
                    shown += f", … ועוד {len(keys) - 6}"
                rows.append([name, len(entries), shown])
        except Exception as exc:  # noqa: BLE001
            rows = [["שגיאה", 0, str(exc)]]
        table.set_rows(rows, numeric_columns=(1,))
        layout.addWidget(table, 1)

        note = QLabel(
            "קוד המקור של תוסף נבדק מול מדיניות AST לפני שהוא מורץ: בלי "
            "ייבוא מחוץ לרשימה המותרת, בלי גישה לקבצים או לרשת, בלי הרצה "
            "דינמית. תוספי נתונים מאומתים מול הסכימה שלהם במקום."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # -- backup ---------------------------------------------------------------- #
    def _backup_tab(self) -> QWidget:
        """Write a copy of the shop, and read one back.

        The one screen in the suite whose absence costs more than every other
        screen combined, so it is a button and a list rather than a document
        somebody is supposed to have read.
        """
        self._backup_folder: Path | None = None
        self._backup_paths: list[Path] = []

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        bar = QHBoxLayout()
        bar.setSpacing(METRICS.space(2))
        write = QPushButton("גבה עכשיו")
        write.setObjectName("Primary")
        write.clicked.connect(self.write_backup)
        bar.addWidget(write)

        restore = QPushButton("שחזר מגיבוי…")
        restore.clicked.connect(self.restore_backup)
        bar.addWidget(restore)

        reveal = QPushButton("בחר תיקיית גיבוי…")
        reveal.clicked.connect(self.choose_backup_folder)
        bar.addWidget(reveal)
        bar.addStretch(1)
        layout.addLayout(bar)

        self.backup_folder_label = QLabel("")
        self.backup_folder_label.setObjectName("Hint")
        self.backup_folder_label.setWordWrap(True)
        layout.addWidget(self.backup_folder_label)

        self.backup_table = DataTable(
            ["נכתב", "מפעיל", "קבצים", "גודל", "מה בפנים"],
            empty_text="אין עדיין אף גיבוי — הכול קיים בעותק אחד",
        )
        layout.addWidget(self.backup_table, 1)

        note = QLabel(
            "גיבוי הוא קובץ zip מתוארך אחד עם כל מה שהתוכנה יודעת: תיקי "
            "עבודה, לקוחות, הסדרות שאושרו, קריאות שירות, פנקס הצ׳קים. "
            "שחזור לעולם אינו דורס: התיקייה הנוכחית מוזזת הצידה ומיקומה "
            "נאמר, כך ששחזור של הקובץ הלא נכון מתבטל בהזזת תיקייה אחת "
            "חזרה. העתיקו את הקובץ לדיסק חיצוני — גיבוי שיושב על אותו "
            "מחשב אינו גיבוי."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.show_backups()
        return page

    def _backup_where(self) -> Path:
        from ..core.backup import default_backup_folder

        return self._backup_folder or default_backup_folder()

    def show_backups(self) -> None:
        from ..core.backup import list_backups

        folder = self._backup_where()
        self.backup_folder_label.setText(f"תיקיית הגיבוי: {folder}")
        if not folder.is_dir():
            self.backup_table.set_rows([])
            return

        rows: list[list[Any]] = []
        self._backup_paths = []
        labels = {
            "jobs": "תיקים", "customers": "לקוחות",
            "system_confirmations": "סדרות", "service_calls": "קריאות",
            "hardware": "פרזול", "price_list": "מחירון", "files": "מסמכים",
        }
        for path, manifest in list_backups(folder):
            self._backup_paths.append(path)
            inside = " · ".join(
                f"{labels.get(key, key)} ⁦{count}⁩"
                for key, count in sorted(manifest.contents.items())
                if count
            )
            rows.append([
                manifest.created[:16].replace("T", " "),
                manifest.brand or "—",
                f"⁦{manifest.files}⁩",
                f"⁦{manifest.bytes / 1_048_576:.1f}⁩ MB",
                inside or "—",
            ])
        self.backup_table.set_rows(rows)

    def choose_backup_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "תיקיית גיבוי", str(self._backup_where())
        )
        if not chosen:
            return
        self._backup_folder = Path(chosen)
        self.show_backups()

    def write_backup(self) -> None:
        from ..core.backup import prune, read_manifest, write_backup

        try:
            archive = write_backup(self._backup_where())
            manifest = read_manifest(archive)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הגיבוי נכשל")
            return

        removed = prune(archive.parent, keep=14)
        message = f"נכתב גיבוי: {manifest.describe()}"
        if removed:
            message += f" · נמחקו ⁦{len(removed)}⁩ גיבויים ישנים"
        self.status(message)
        self.show_backups()
        self.show_setup()

    def restore_backup(self) -> None:
        """Show what a restore would replace, and only then do it."""
        from ..core.backup import plan_restore, restore

        start = str(self._backup_where())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "בחרו קובץ גיבוי", start, "גיבויים (*.zip)"
        )
        if not chosen:
            return

        try:
            plan = plan_restore(Path(chosen))
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "אי אפשר לקרוא את הגיבוי")
            return

        lines = [plan.describe(), ""]
        lines += [
            f"{label}: כרגע ⁦{now}⁩ · בגיבוי ⁦{inside}⁩"
            for label, now, inside in plan.comparison()
        ]
        if plan.warnings:
            lines += [""] + [f"⚠ {warning}" for warning in plan.warnings]
        lines += ["", "התיקייה הנוכחית תוזז הצידה ולא תימחק. להמשיך?"]

        box = QMessageBox(self)
        box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        box.setWindowTitle("שחזור מגיבוי")
        box.setText("\n".join(lines))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        try:
            root, aside = restore(Path(chosen))
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "השחזור נכשל")
            return

        self.status(
            f"שוחזר אל {root}"
            + (f" · הנתונים הקודמים ב-{aside}" if aside else "")
            + " — סגרו ופתחו את התוכנה"
        )
        self.show_backups()

    # -- audit ------------------------------------------------------------------ #
    def _audit_tab(self) -> QWidget:
        """What changed, by whom, and whether the record is still intact."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        bar = QHBoxLayout()
        bar.setSpacing(METRICS.space(2))
        check = QPushButton("בדוק שלמות")
        check.setObjectName("Primary")
        check.clicked.connect(self.verify_audit)
        bar.addWidget(check)

        self.audit_filter = QLineEdit()
        self.audit_filter.setPlaceholderText("סינון: שם עובד, תיק או סדרה")
        self.audit_filter.textChanged.connect(self.show_audit)
        bar.addWidget(self.audit_filter, 1)
        layout.addLayout(bar)

        self.audit_verdict = QLabel("")
        self.audit_verdict.setObjectName("StatLabel")
        self.audit_verdict.setWordWrap(True)
        layout.addWidget(self.audit_verdict)

        self.audit_table = DataTable(
            ["מתי", "מי", "מה", "על מה", "שדה", "לפני", "אחרי"],
            empty_text="עדיין לא נרשם שינוי",
        )
        layout.addWidget(self.audit_table, 1)

        note = QLabel(
            "כל רשומה נושאת את טביעת האצבע של הרשומה שלפניה, כך שמחיקה של "
            "שורה מהאמצע או עריכה של מספר בדיעבד שוברות את השרשרת ו״בדוק "
            "שלמות״ אומר בדיוק היכן. זה תיעוד ולא הרשאות: הוא אומר מה קרה, "
            "הוא לא מונע דבר."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.show_audit()
        return page

    def show_audit(self) -> None:
        from ..core.audit import audit

        needle = self.audit_filter.text().strip().casefold()
        try:
            entries = audit().recent(400)
        except Exception as exc:  # noqa: BLE001
            self.audit_table.set_rows([])
            self.audit_verdict.setText(str(exc))
            return

        rows: list[list[Any]] = []
        for entry in entries:
            haystack = " ".join([
                entry.person, entry.subject, entry.field_name, entry.note
            ]).casefold()
            if needle and needle not in haystack:
                continue
            rows.append([
                entry.at[:16].replace("T", " "),
                entry.person,
                entry.action.hebrew,
                entry.subject,
                _audit_field(entry.field_name),
                _audit_value(entry.before),
                _audit_value(entry.after),
            ])
        self.audit_table.set_rows(rows[:200])

    def verify_audit(self) -> None:
        from ..core.audit import audit

        try:
            result = audit().verify()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לבדוק את היומן")
            return
        self.audit_verdict.setText(result.describe())
        self.audit_verdict.setStyleSheet(
            f"color: {self.colours.success if result.ok else self.colours.danger};"
        )
        self.show_audit()

    # -- comparison ------------------------------------------------------------ #
    def _build_comparison(self) -> None:
        if self._compare_built:
            return
        self._compare_built = True

        from .. import compare as cmp

        layout = self._compare_layout

        failures = cmp.verify_claims()
        stats = cmp.summary()
        header = QLabel(
            f"{stats['profileos_implemented']} מתוך {stats['capabilities']} "
            f"יכולות ממומשות, {stats['not_documented_elsewhere']} שאף אחת "
            f"מ-{stats['packages_compared']} התוכנות שהושוו לא מתעדת, "
            f"{stats['profileos_gaps']} שאין בחבילה הזאת."
        )
        header.setObjectName("Hint")
        header.setWordWrap(True)
        layout.addWidget(header)
        if failures:
            broken = QLabel(
                f"{len(failures)} הצהרות יכולת כבר לא מפנות לקוד."
            )
            broken.setObjectName("Hint")
            broken.setWordWrap(True)
            layout.addWidget(broken)

        headers = ["יכולת", "ProfileOS"] + [p.heading for p in cmp.PACKAGES]
        table = DataTable(headers)
        marks = {
            cmp.Support.FULL: "כן",
            cmp.Support.PARTIAL: "חלקי",
            cmp.Support.NOT_DOCUMENTED: "לא",
            cmp.Support.UNKNOWN: "?",
        }
        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, capability in enumerate(cmp.CAPABILITIES):
            name = capability.name_he + (" *" if capability.differentiator else "")
            own = cmp.profileos_support(capability)
            rows.append(
                [name, marks[own]]
                + [marks[p.level(capability.id)] for p in cmp.PACKAGES]
            )
            colours[(index, 1)] = (
                self.colours.success
                if own is cmp.Support.FULL
                else self.colours.warning
            )
        table.set_rows(rows, colours=colours)
        # Capability names carry the meaning; the support columns are three
        # characters wide. Stretching all of them equally truncates the only
        # column a reader actually has to read.
        table.stretch(0)
        layout.addWidget(table, 1)

        legend = QLabel(
            "כן = מתועד, חלקי = חלקי או מודול בתשלום, לא = לא נמצא בחומר "
            "הפומבי של היצרן, ? = לא נבדק. שום דבר כאן לא נבחן מול התקנה "
            "של מתחרה, ו\"לא\" לעולם אינו \"לא קיים\".\n\n"
            + "\n".join(f"• {limit}" for limit in cmp.STANDING_LIMITATIONS_HE)
        )
        legend.setObjectName("Hint")
        legend.setWordWrap(True)
        layout.addWidget(legend)





# --------------------------------------------------------------------------- #
# Site measurement
# --------------------------------------------------------------------------- #

class SurveyPage(Page):
    """The one screen where the software meets a building.

    Everything upstream is arithmetic on numbers somebody typed. This is where
    the expensive mistakes are made, so it asks for the measurements the trade
    actually takes — three widths, three heights, both diagonals — rather than
    one width and one height, which is what makes a non-parallel opening look
    fine until the frame is welded.
    """

    title = "Survey"
    hebrew = "מדידה באתר"
    subtitle = "שלושה רוחבים, שלושה גבהים, שני אלכסונים — ומידת המסגרת שיוצאת מהם"

    #: Which attribute each editable column writes to.
    _COLUMNS: tuple[tuple[str, str], ...] = (
        ("width_head", "רוחב עליון"),
        ("width_middle", "רוחב אמצע"),
        ("width_sill", "רוחב תחתון"),
        ("height_left", "גובה שמאל"),
        ("height_middle", "גובה אמצע"),
        ("height_right", "גובה ימין"),
        ("diagonal_a", "אלכסון א"),
        ("diagonal_b", "אלכסון ב"),
    )

    def build(self) -> None:
        take = QPushButton("פתח גיליון מדידה")
        take.setObjectName("Primary")
        take.clicked.connect(self.open_sheet)
        self.header.add_action(take)

        export = QPushButton("ייצא גיליון ריק...")
        export.clicked.connect(self.export_sheet)
        self.header.add_action(export)

        self.stats = StatRow([
            ("openings", "פתחים"), ("measured", "נמדדו"),
            ("makeable", "מוכנים לייצור"), ("square", "מחוץ לזווית"),
            ("clearance", "מרווח התקנה"),
        ])
        self.body.addWidget(self.stats)

        entry = Card("רישום מדידה")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(3))

        self.survey_opening = QComboBox()
        row.addWidget(self._caption("פתח"))
        row.addWidget(self.survey_opening)

        self._boxes: dict[str, QDoubleSpinBox] = {}
        for key, label in self._COLUMNS:
            box = QDoubleSpinBox()
            box.setRange(0.0, 20_000.0)
            box.setDecimals(1)
            box.setSingleStep(1.0)
            box.setMaximumWidth(METRICS.space(22))
            box.setSpecialValueText("—")
            self._boxes[key] = box
            row.addWidget(self._caption(label))
            row.addWidget(box)
        row.addStretch(1)
        entry.add_layout(row)

        second = QHBoxLayout()
        second.setSpacing(METRICS.space(3))
        self.survey_by = QComboBox()
        self.survey_by.setEditable(True)
        self.survey_by.addItems(["יוסי", "דנה", "מאיה"])
        second.addWidget(self._caption("מדד"))
        second.addWidget(self.survey_by)

        self.survey_clearance = QDoubleSpinBox()
        self.survey_clearance.setRange(0.0, 60.0)
        self.survey_clearance.setDecimals(1)
        self.survey_clearance.setSpecialValueText("—")
        self.survey_clearance.setSuffix(" מ״מ לצד")
        second.addWidget(self._caption("מרווח התקנה"))
        second.addWidget(self.survey_clearance)

        self.floor_finished = QCheckBox("הרצפה גמורה")
        second.addWidget(self.floor_finished)

        record = QPushButton("רשום מדידה")
        record.setObjectName("Primary")
        record.clicked.connect(self.record_measurement)
        second.addWidget(record)
        second.addStretch(1)
        entry.add_layout(second)

        self.survey_result = QLabel("—")
        self.survey_result.setObjectName("StatLabel")
        self.survey_result.setWordWrap(True)
        entry.add(self.survey_result)
        self.body.addWidget(entry)

        self.survey_table = DataTable(
            ["סימון", "חדר", "רוחב", "גובה", "אלכסונים", "מסגרת", "מדד", "מצב"],
            empty_text="פתח גיליון מדידה מתוך תיק עבודה עם רשימת פתחים",
        )
        self.body.addWidget(self.survey_table, 1)

        self.survey_problems = QLabel("")
        self.survey_problems.setObjectName("Hint")
        self.survey_problems.setWordWrap(True)
        self.body.addWidget(self.survey_problems)

        note = QLabel(
            "המסגרת נגזרת מהמידה הקטנה ביותר פחות מרווח ההתקנה משני הצדדים. "
            "מרווח ההתקנה הוא נתון של הסדרה — בלעדיו לא תוצג מידת מסגרת, "
            "כי ניחוש כאן הוא החלון שנכנס לפח."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self._survey = None

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # -- the sheet -------------------------------------------------------------- #
    def open_sheet(self) -> None:
        """Open a blank sheet with a line per opening the job already has."""
        from ..delivery.survey import survey_for_job

        job = getattr(self.session, "job", None)
        if job is None or getattr(job, "schedule", None) is None:
            self.report(
                ProfileOSError(
                    "פתח תיק עבודה עם רשימת פתחים בעמוד ״פרויקטים״ ואז חזור לכאן"
                ),
                "אין ממה לפתוח גיליון",
            )
            return

        clearance = self.survey_clearance.value() or None
        self._survey = survey_for_job(job, clearance_per_side=clearance)
        self.survey_opening.clear()
        for entry in self._survey:
            self.survey_opening.addItem(entry.reference or "—", entry.reference)
        self.show_survey()
        self.status(self._survey.describe())

    def record_measurement(self) -> None:
        from datetime import date as _date

        if self._survey is None:
            self.report(ProfileOSError("פתח גיליון מדידה קודם"), "אין מה לרשום")
            return

        reference = self.survey_opening.currentData()
        try:
            entry = self._survey.opening(reference or "")
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא נמצא הפתח")
            return

        for key, _label in self._COLUMNS:
            value = self._boxes[key].value()
            setattr(entry, key, value if value > 0 else None)
        entry.measured_by = self.survey_by.currentText()
        entry.measured_on = _date.today()
        entry.floor_is_finished = self.floor_finished.isChecked()
        clearance = self.survey_clearance.value()
        if clearance > 0:
            entry.clearance_per_side = clearance

        problems = entry.problems()
        self.survey_result.setText(
            entry.describe() + (" · " + " · ".join(problems) if problems else "")
        )
        self.survey_result.setStyleSheet(
            f"color: {self.colours.danger if problems else self.colours.success};"
        )
        self.show_survey()

    def show_survey(self) -> None:
        from ..delivery.survey import SQUARE_TOLERANCE_MM

        survey = self._survey
        if survey is None:
            return

        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        racked = 0
        for index, entry in enumerate(survey):
            frame = entry.frame_size()
            square = entry.out_of_square
            if square is not None and square > SQUARE_TOLERANCE_MM:
                racked += 1

            if not entry.is_measured:
                state, tone = "לא נמדד", self.colours.warning
            elif entry.may_be_made:
                state, tone = "מוכן", self.colours.success
            else:
                state, tone = "לבדיקה", self.colours.danger
            colours[(index, 7)] = tone

            rows.append([
                entry.reference or "—", entry.room or "—",
                f"⁦{entry.smallest_width:g}⁩" if entry.smallest_width else "—",
                f"⁦{entry.smallest_height:g}⁩" if entry.smallest_height else "—",
                f"⁦{square:g}⁩" if square is not None else "—",
                f"⁦{frame[0]:g}×{frame[1]:g}⁩" if frame else "—",
                entry.measured_by or "—",
                state,
            ])
        self.survey_table.set_rows(rows, colours=colours)

        clearances = {
            entry.clearance_per_side for entry in survey
            if entry.clearance_per_side
        }
        self.stats.update_many({
            "openings": (f"⁦{len(survey)}⁩", survey.job_name or survey.job_id),
            "measured": (
                f"⁦{len(survey.measured)}⁩",
                f"⁦{survey.progress:.0f}%⁩ מהגיליון",
            ),
            "makeable": (f"⁦{len(survey.makeable)}⁩", ""),
            "square": (
                f"⁦{racked}⁩",
                f"מעל ⁦{SQUARE_TOLERANCE_MM:g}⁩ מ״מ" if racked else "הכול בזווית",
            ),
            "clearance": (
                f"⁦{sorted(clearances)[0]:g}⁩ מ״מ" if len(clearances) == 1
                else ("מעורב" if clearances else "—"),
                "לצד" if clearances else "חסר נתון סדרה",
            ),
        })

        problems = survey.problems()
        self.survey_problems.setText(
            " · ".join(problems[:6])
            + (f" · ועוד ⁦{len(problems) - 6}⁩" if len(problems) > 6 else "")
        )

    def export_sheet(self) -> None:
        """Write the blank sheet somebody carries to the site."""
        import csv

        from ..delivery.survey import Survey

        if self._survey is None:
            self.report(ProfileOSError("פתח גיליון מדידה קודם"), "אין מה לייצא")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "שמירת גיליון מדידה", "measurements.csv", "CSV (*.csv)"
        )
        if not path:
            return
        # utf-8-sig so Excel in a Hebrew office opens it without a dialogue.
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(Survey.SHEET_HEADERS)
            writer.writerows(self._survey.sheet_rows())
        self.status(f"נשמר {path}")


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

class ReportsPage(Page):
    """The page the owner opens on a Sunday morning.

    Every other screen answers a question about one job. This one answers
    questions about the shop, and it is deliberately blunt about how much it
    knows: a percentage over four quotations says so beside itself rather than
    printing the same confident figure as one over four hundred.
    """

    title = "Reports"
    hebrew = "דוחות"
    subtitle = "מכירות, אחוזי סגירה, רווחיות לקוח וצבר העבודה"

    def build(self) -> None:
        refresh = QPushButton("רענן")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self.refresh)
        self.header.add_action(refresh)

        chooser = QHBoxLayout()
        chooser.setSpacing(METRICS.space(2))
        caption = QLabel("שנה")
        caption.setObjectName("FieldLabel")
        self.year_picker = QComboBox()
        from datetime import date as _date

        this_year = _date.today().year
        for offset in range(0, 6):
            self.year_picker.addItem(f"⁦{this_year - offset}⁩", this_year - offset)
        self.year_picker.currentIndexChanged.connect(self.refresh)
        chooser.addWidget(caption)
        chooser.addWidget(self.year_picker)
        chooser.addStretch(1)
        self.body.addLayout(chooser)

        self.stats = StatRow([
            ("won", "הוזמן"), ("rate", "אחוז סגירה"), ("average", "הזמנה ממוצעת"),
            ("backlog", "צבר פתוח"), ("late", "באיחור"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._sales_tab(), "מכירות")
        tabs.addTab(self._customers_tab(), "לקוחות")
        tabs.addTab(self._pipeline_tab(), "צבר ואיחורים")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

        self._loaded = False

    # -- tabs ------------------------------------------------------------------ #
    def _sales_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        chart_card = Card("הצעות מול הזמנות, לפי חודש")
        self.months_chart = BarChart(self.colours)
        chart_card.add(self.months_chart, 1)
        layout.addWidget(chart_card, 1)

        legend = QLabel(
            "העמודה החיוורת היא מה שהוצע, המלאה היא מה שנסגר מתוכו. "
            "תיק נספר בחודש שבו נשלחה ההצעה, לא בחודש שבו נסגר — כי זו "
            "השאלה שהחודש נמדד בה."
        )
        legend.setObjectName("Hint")
        legend.setWordWrap(True)
        layout.addWidget(legend)

        self.sales_table = DataTable(
            ["חודש", "הצעות", "שווי", "הוזמנו", "שווי", "אחוז סגירה"],
            empty_text="אין תיקי עבודה בשנה הזאת",
        )
        layout.addWidget(self.sales_table, 1)

        self.sales_note = QLabel("")
        self.sales_note.setObjectName("Hint")
        self.sales_note.setWordWrap(True)
        layout.addWidget(self.sales_note)
        return page

    def _customers_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.customers_table = DataTable(
            ["לקוח", "תיקים", "נסגרו", "לא נסגרו", "שווי", "אחוז סגירה", "רווחיות"],
            empty_text="אין עדיין לקוחות",
        )
        layout.addWidget(self.customers_table, 1)

        self.customers_note = QLabel(
            "רווחיות ״—״ פירושה שלא נרשמו שעות או חומרים מול התיקים של "
            "הלקוח הזה — וזה לא אותו דבר כמו רווחיות אפס."
        )
        self.customers_note.setObjectName("Hint")
        self.customers_note.setWordWrap(True)
        layout.addWidget(self.customers_note)
        return page

    def _pipeline_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        split = QHBoxLayout()
        split.setSpacing(METRICS.space(3))

        stage_card = Card("לפי שלב")
        self.stage_table = DataTable(
            ["שלב", "תיקים", "שווי"], empty_text="אין עבודה פתוחה"
        )
        stage_card.add(self.stage_table, 1)
        split.addWidget(stage_card, 1)

        late_card = Card("איחורים ומועדים קרובים")
        self.late_table = DataTable(
            ["תיק", "שם", "מצב"], empty_text="אין תיק שעבר את מועדו"
        )
        late_card.add(self.late_table, 1)
        split.addWidget(late_card, 1)
        layout.addLayout(split, 1)

        self.pipeline_note = QLabel("")
        self.pipeline_note.setObjectName("Hint")
        self.pipeline_note.setWordWrap(True)
        layout.addWidget(self.pipeline_note)
        return page

    # -- filling it ------------------------------------------------------------ #
    def refresh(self) -> None:
        from datetime import date as _date

        from .. import reports
        from ..projects import default_store

        try:
            jobs = list(default_store().all())
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לקרוא את תיקי העבודה")
            return

        chosen = self.year_picker.currentData() or _date.today().year
        report = reports.sales(jobs, reports.year(chosen))
        live = reports.pipeline(jobs)
        months = reports.by_month(jobs, chosen)

        rate = report.win_rate()
        average = report.average_order()
        self.stats.update_many({
            "won": (
                f"⁦{report.won_value:,.0f}⁩ ₪",
                f"⁦{report.won_count}⁩ תיקים",
            ),
            "rate": (
                f"⁦{rate.value:.0f}%⁩",
                f"מתוך ⁦{report.decided}⁩ שהוכרעו" if report.decided else "טרם הוכרע",
            ),
            "average": (f"⁦{average.value:,.0f}⁩ ₪", ""),
            "backlog": (
                f"⁦{live.open_value:,.0f}⁩ ₪",
                f"⁦{sum(live.counts.values())}⁩ תיקים",
            ),
            "late": (
                f"⁦{len(live.overdue)}⁩",
                "תיקים שעברו את מועדם" if live.overdue else "הכול בזמן",
            ),
        })

        self.months_chart.set_series(
            [row["label"][:3] for row in months],
            [row["quoted_value"] for row in months],
            [row["won_value"] for row in months],
        )
        self.sales_table.set_rows(
            [
                [
                    row["label"], f"⁦{row['quoted']}⁩",
                    f"⁦{row['quoted_value']:,.0f}⁩",
                    f"⁦{row['won']}⁩", f"⁦{row['won_value']:,.0f}⁩",
                    f"⁦{row['win_rate_pct']:.0f}%⁩" if row["quoted"] else "—",
                ]
                for row in months
            ],
        )
        self.sales_note.setText(
            " · ".join(report.warnings())
            or f"{report.describe()} · {report.value_win_rate().label}: "
               f"{report.value_win_rate().format()}"
        )

        customers = reports.by_customer(jobs, costs=self._booked_costs(jobs))
        colours: dict[tuple[int, int], str] = {}
        rows: list[list[Any]] = []
        for index, line in enumerate(customers):
            margin = "—" if line.margin is None else f"⁦{line.margin:.1f}%⁩"
            rows.append([
                line.name or "—", f"⁦{line.jobs}⁩", f"⁦{line.won}⁩",
                f"⁦{line.lost}⁩", f"⁦{line.value:,.0f}⁩ ₪",
                f"⁦{line.win_rate:.0f}%⁩", margin,
            ])
            if line.margin is not None:
                colours[(index, 6)] = (
                    self.colours.success if line.margin >= 20
                    else self.colours.warning if line.margin >= 8
                    else self.colours.danger
                )
        self.customers_table.set_rows(rows, colours=colours)

        self.stage_table.set_rows([list(row) for row in live.rows()])
        late_rows: list[list[Any]] = []
        late_colours: dict[tuple[int, int], str] = {}
        for job_id, name, days in live.overdue[:20]:
            late_colours[(len(late_rows), 2)] = self.colours.danger
            late_rows.append([job_id, name, f"⁦{days}⁩ ימי איחור"])
        for job_id, name, days in live.due_this_week[:20]:
            late_colours[(len(late_rows), 2)] = self.colours.warning
            late_rows.append([job_id, name, f"בעוד ⁦{days}⁩ ימים"])
        self.late_table.set_rows(late_rows, colours=late_colours)
        self.pipeline_note.setText(
            live.describe()
            + (
                f" · ⁦{live.undated}⁩ תיקים פתוחים בלי מועד אספקה"
                if live.undated else ""
            )
        )
        self._loaded = True

    def _booked_costs(self, jobs: list[Any]) -> dict[str, float]:
        """What each job actually cost, where hours were booked against it."""
        try:
            from ..erp.timesheets import default_timebook

            book = default_timebook()
        except Exception:  # noqa: BLE001
            return {}
        return {
            job.job_id: book.cost_of_job(job.job_id)
            for job in jobs
            if book.cost_of_job(job.job_id)
        }


# --------------------------------------------------------------------------- #
# 3D views
# --------------------------------------------------------------------------- #

class ViewPage(Page):
    """See the element the way the customer will."""

    title = "3D view"
    hebrew = "תלת-ממד"
    subtitle = "מבטי הדמיה ומבטים טכניים של הפתחים"

    def build(self) -> None:
        from PySide6.QtSvgWidgets import QSvgWidget

        render = QPushButton("הצג")
        render.setObjectName("Primary")
        render.clicked.connect(self.render_scene)
        self.header.add_action(render)

        export = QPushButton("ייצא")
        export.clicked.connect(self.export_scene)
        self.header.add_action(export)

        self.stats = StatRow([
            ("parts", "חלקים"), ("triangles", "משולשים"),
            ("metal", "מתכת"), ("size", "מידה"), ("panes", "שמשות"),
        ])
        self.body.addWidget(self.stats)

        controls = Card("מבט")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(4))

        self.element = QComboBox()
        self.view = QComboBox()
        self.view.addItem("הדמיה", "presentation"); self.view.addItem("חזית", "elevation")
        self.finish = QComboBox()
        self.finish.addItem("טבעי", "natural"); self.finish.addItem("ברונזה", "bronze")
        self.glass = QComboBox()
        self.glass.addItem("עם זכוכית", "with glass"); self.glass.addItem("מסגרות בלבד", "frames only")

        for label, widget in [("פתח", self.element), ("מבט", self.view),
                              ("גמר", self.finish), ("זיגוג", self.glass)]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget)
        row.addStretch(1)
        controls.add_layout(row)
        self.body.addWidget(controls)

        self.canvas = QSvgWidget()
        self.canvas.setMinimumHeight(420)
        self.body.addWidget(self.canvas, 1)

        note = QLabel(
            "המודל נבנה מאותם חתכי פרופיל ולפי אותם כללי מערכת שמייצרים את "
            "רשימת החיתוך — מה שמצויר כאן הוא מה שהמפעל יבנה. הייצוא כותב SVG "
            "להדפסה, glTF לכל כלי תלת-ממד, וצפיין אינטראקטיבי עצמאי."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        self.body.addWidget(note)

        for widget in (self.element, self.view, self.finish, self.glass):
            widget.currentTextChanged.connect(self._redraw)
        self._scene = None

    def refresh(self) -> None:
        ids = [build.opening.element_id for build in self.session.builds]
        current = self.element.currentText()
        self.element.blockSignals(True)
        self.element.clear()
        self.element.addItems(ids)
        if current in ids:
            self.element.setCurrentText(current)
        self.element.blockSignals(False)
        self.header.set_subtitle(
            f"{len(ids)} פתחים מתוכננים" if ids else "תכנן פתח קודם"
        )

    def _build_for(self, element_id: str):
        """The build the picker names — or the first one, if it names nothing.

        The picker is filled by :meth:`refresh`, which runs when the page is
        navigated to. Reaching the page another way — a keyboard shortcut, a
        restored layout — leaves it empty while the session plainly has
        elements in it, and erroring at the user in that state is a bug, not a
        message worth showing.
        """
        for build in self.session.builds:
            if build.opening.element_id == element_id:
                return build
        if self.session.builds and not element_id:
            self.refresh()
            return self.session.builds[0]
        return None

    def render_scene(self) -> None:
        from ..viz3d import ViewStyle, build_element_scene

        build = self._build_for(self.element.currentText())
        if build is None:
            self.report(
                ProfileOSError("עדיין לא תוכננו פתחים. תכנן פתח בעמוד ״פתח״ ואז חזור לכאן."), "אין מה להציג"
            )
            return
        try:
            self._scene = build_element_scene(
                build,
                style=ViewStyle(show_glass=self.glass.currentData() == "with glass"),
            )
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לבנות את המודל")
            return

        scene = self._scene
        size = scene.size
        panes = sum(1 for mesh in scene.meshes if mesh.material == "glass")
        self.stats.update_many({
            "parts": (str(len(scene.meshes)), "גופים"),
            "triangles": (f"{scene.triangle_count:,}", ""),
            "metal": (f"{scene.aluminium_volume() * 2.70e-6:.1f} ק\"ג", "לפי 2.70 גרם/סמ\"ק"),
            "size": (f"{size[0]:.0f}×{size[1]:.0f}", f"עומק {size[2]:.0f} מ\"מ"),
            "panes": (str(panes), ""),
        })
        self._redraw()

    def _redraw(self) -> None:
        if self._scene is None:
            return
        from ..viz3d import (
            BRONZE_MATERIALS,
            DEFAULT_MATERIALS,
            RenderOptions,
            elevation_camera,
            presentation_camera,
            render_svg,
        )

        options = RenderOptions(
            width=1200, height=760,
            materials=dict(
                BRONZE_MATERIALS if self.finish.currentData() == "bronze"
                else DEFAULT_MATERIALS
            ),
            background=self.colours.surface_sunken,
        )
        camera = (
            elevation_camera(self._scene)
            if self.view.currentData() == "elevation"
            else presentation_camera(self._scene)
        )
        try:
            svg = render_svg(self._scene, camera, options)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן להציג את המבט")
            return
        self.canvas.load(svg.encode("utf-8"))

    def export_scene(self) -> None:
        from ..viz3d import RenderOptions, render_viewer, render_views, write_gltf

        if self._scene is None:
            self.report(ProfileOSError("הצג את הפתח קודם"), "אין מה לייצא")
            return
        folder = QFileDialog.getExistingDirectory(self, "לאן לייצא את המבטים?")
        if not folder:
            return
        target = Path(folder)
        stem = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in self.element.currentText()
        ) or "element"
        written = 0
        for name, svg in render_views(self._scene, RenderOptions()).items():
            (target / f"{stem}-{name}.svg").write_text(svg, encoding="utf-8")
            written += 1
        (target / f"{stem}.html").write_text(
            render_viewer(self._scene), encoding="utf-8"
        )
        write_gltf(self._scene, target / f"{stem}.gltf")
        write_gltf(self._scene, target / f"{stem}.glb")
        self.status(f"נשמרו {written + 3} קבצים אל {target}")


# --------------------------------------------------------------------------- #
# ERP
# --------------------------------------------------------------------------- #

class AccountsPage(Page):
    """Stock, purchasing, the ledger and the shop's capacity."""

    title = "Accounts"
    hebrew = "הנהלת חשבונות"
    subtitle = "מלאי, רכש, ספר חשבונות ועומס המפעל"

    def build(self) -> None:
        audit = QPushButton("ביקורת")
        audit.setObjectName("Primary")
        audit.clicked.connect(self.run_audit)
        self.header.add_action(audit)

        self.stats = StatRow([
            ("stock", "מלאי"), ("debtors", "חייבים"), ("creditors", "זכאים"),
            ("result", "תוצאה"), ("entries", "פקודות יומן"),
        ])
        self.body.addWidget(self.stats)

        tabs = QTabWidget()
        tabs.addTab(self._stock_tab(), "מלאי")
        tabs.addTab(self._purchasing_tab(), "רכש")
        tabs.addTab(self._ledger_tab(), "ספר חשבונות")
        tabs.addTab(self._planning_tab(), "קיבולת")
        tabs.addTab(self._currency_tab(), "מטבע חוץ")
        self.body.addWidget(tabs, 1)
        self.tabs = tabs

    # -- currency -------------------------------------------------------------- #
    def _currency_tab(self) -> QWidget:
        """Rates the shop typed, with the date and where each came from.

        Profiles, hardware and machinery are bought in euros and dollars and
        sold in shekels. A quotation held for ninety days on a rate somebody
        remembered is a quotation whose margin is decided by the currency
        market. Nothing here is fetched from anywhere: a rate exists because
        somebody entered it and named its source, or it does not exist.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        from ..erp.currency import CURRENCIES, HOME, STALE_DAYS

        card = Card("רישום שער")
        row = QHBoxLayout()
        row.setSpacing(METRICS.space(3))

        self.fx_currency = QComboBox()
        for code, hebrew in CURRENCIES.items():
            if code != HOME:
                self.fx_currency.addItem(f"{hebrew} ({code})", code)

        self.fx_rate = QDoubleSpinBox()
        self.fx_rate.setRange(0.0001, 999.0)
        self.fx_rate.setDecimals(4)
        self.fx_rate.setSuffix(" ₪")

        self.fx_source = QLineEdit()
        self.fx_source.setPlaceholderText("בנק ישראל / חשבונית הספק / הבנק")

        for label, widget in [
            ("מטבע", self.fx_currency), ("שקלים ליחידה", self.fx_rate),
            ("מקור", self.fx_source),
        ]:
            caption = QLabel(label)
            caption.setObjectName("FieldLabel")
            row.addWidget(caption)
            row.addWidget(widget, 1 if widget is self.fx_source else 0)

        record = QPushButton("רשום שער")
        record.setObjectName("Primary")
        record.clicked.connect(self.record_rate)
        row.addWidget(record)
        card.add_layout(row)

        self.fx_result = QLabel("—")
        self.fx_result.setObjectName("StatLabel")
        card.add(self.fx_result)
        layout.addWidget(card)

        self.fx_table = DataTable(
            ["מטבע", "שער", "תאריך", "גיל", "מקור", "מצב"],
            empty_text="לא נרשם אף שער — אי אפשר לתמחר רכש במטבע זר",
        )
        layout.addWidget(self.fx_table, 1)

        note = QLabel(
            "שער בלי מקור ובלי תאריך אינו שער אלא זיכרון. שער בן יותר "
            f"מ-⁦{STALE_DAYS}⁩ ימים מסומן ישן, וכל תמחור שנשען עליו נושא "
            "אזהרה עד שהוא מתעדכן."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        self._ratebook = None
        self.show_rates()
        return page

    def _rates(self):
        if self._ratebook is None:
            from ..erp.currency import default_rates

            self._ratebook = default_rates()
        return self._ratebook

    def record_rate(self) -> None:
        from datetime import date as _date

        from ..erp.currency import Rate

        try:
            rate = self._rates().record(Rate(
                currency=self.fx_currency.currentData(),
                per_unit=self.fx_rate.value(),
                on=_date.today(),
                source=self.fx_source.text().strip(),
            ))
        except Exception as exc:  # noqa: BLE001
            self.fx_result.setText(str(exc))
            self.fx_result.setStyleSheet(f"color: {self.colours.danger};")
            return

        self.fx_result.setText(rate.describe())
        self.fx_result.setStyleSheet(f"color: {self.colours.success};")
        self.show_rates()

    def show_rates(self) -> None:
        from ..erp.currency import CURRENCIES

        book = self._rates()
        rows: list[list[Any]] = []
        colours: dict[tuple[int, int], str] = {}
        index = 0
        for code in book.currencies():
            for rate in book.history(code)[:6]:
                if rate.is_stale():
                    state, tone = "ישן", self.colours.warning
                elif not rate.is_sourced:
                    state, tone = "בלי מקור", self.colours.warning
                else:
                    state, tone = "תקף", self.colours.success
                rows.append([
                    f"{CURRENCIES.get(code, code)} ({code})",
                    f"⁦{rate.per_unit:.4f}⁩ ₪",
                    rate.on.strftime("%d/%m/%Y"),
                    f"⁦{rate.age()}⁩ ימים",
                    rate.source or "—",
                    state,
                ])
                colours[(index, 5)] = tone
                index += 1
        self.fx_table.set_rows(rows, colours=colours)

    # -- shared company ------------------------------------------------------- #
    def _company(self):
        if self.session.company is None:
            from ..erp import company_for_brand

            self.session.set_company(company_for_brand())
        return self.session.company

    # -- stock ---------------------------------------------------------------- #
    def _stock_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.stock_table = DataTable(
            ["פריט", "שם", "במלאי", "משוריין", "בהזמנה",
             "צפוי", "עלות ליחידה", "שווי", "להזמנה"],
            empty_text="לחץ ״ביקורת״ כדי לרענן את תמונת המלאי",
        )
        layout.addWidget(self.stock_table, 1)

        note = QLabel(
            "השווי עוקב אחרי התנועה הפיזית: FIFO צורך את המשלוח הישן ביותר "
            "קודם, כך שהעלות של מוט תלויה מאיזה משלוח הוא הגיע. ניפוק מעבר "
            "למה שנמצא על המדף נדחה — יתרה שלילית היא קבלת סחורה חסרה, "
            "לא כמות."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _refresh_stock(self) -> None:
        from ..erp import format_money

        company = self._company()
        colours: dict[tuple[int, int], str] = {}
        rows = []
        for index, row in enumerate(company.stock.valuation_report()):
            rows.append([
                row["code"], row["name"], f"{row['on_hand']:.1f}",
                f"{row['allocated']:.1f}", f"{row['on_order']:.1f}",
                f"{row['projected']:.1f}",
                format_money(int(row["unit_cost"]), company.currency),
                format_money(row["value"], company.currency),
                "כן" if row["below_reorder"] else "",
            ])
            if row["below_reorder"]:
                colours[(index, 8)] = self.colours.warning
        self.stock_table.set_rows(rows, numeric_columns=(2, 3, 4, 5, 6, 7), colours=colours)

    # -- purchasing ------------------------------------------------------------ #
    def _purchasing_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        plan = QPushButton("תכנון רכש לפתחים שתוכננו")
        plan.clicked.connect(self.plan_purchases)
        layout.addWidget(plan)

        self.requirements_table = DataTable(
            ["פריט", "נדרש", "פנוי", "בהזמנה", "לרכישה", "יחידה"],
            empty_text="לחץ ״תכנון רכש״ אחרי שתכננת פתחים",
        )
        layout.addWidget(self.requirements_table, 1)

        self.orders_table = DataTable(
            ["הזמנה", "ספק", "שורות", "נטו", "מועד אספקה"],
            empty_text="הזמנות רכש יופיעו כאן אחרי התכנון",
        )
        layout.addWidget(self.orders_table, 1)

        note = QLabel(
            "הדרישה נטו היא מה שצריך לקנות: הברוטו, פחות מה שפנוי על המדף, "
            "פחות מה שכבר בהזמנה. ההזמנות מקובצות אחת לכל ספק ומעוגלות "
            "כלפי מעלה לאורך המלאי, כי ספק לא מוכר 11.4 מטר ממוט של 6 מטר."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def plan_purchases(self) -> None:
        from ..erp import StockItem, money

        if not self.session.builds:
            self.report(
                ProfileOSError("עדיין לא תוכננו פתחים"), "אין מה לתכנן"
            )
            return

        company = self._company()
        demand: dict[str, float] = {}
        for build in self.session.builds:
            quantity = build.opening.quantity
            for cut in build.cuts:
                demand[cut.profile_id] = demand.get(cut.profile_id, 0.0) + (
                    cut.total_length * quantity / 1000.0
                )

        prices: dict[str, float] = {}
        for code, needed in demand.items():
            if code not in company.stock.items:
                company.add_item(
                    StockItem(code, code, supplier_id="unassigned",
                              reorder_quantity=6.0, lead_time_days=12)
                )
            prices[code] = float(money(42.0))

        try:
            rows, orders = company.plan_purchases(demand, prices)
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "לא ניתן לתכנן את הרכש")
            return

        self.requirements_table.set_rows(
            [
                [r.item, f"{r.gross:.1f}", f"{r.free:.1f}", f"{r.on_order:.1f}",
                 f"{r.net:.1f}", r.unit]
                for r in rows
            ],
            numeric_columns=(1, 2, 3, 4),
        )
        from ..erp import format_money

        self.orders_table.set_rows(
            [
                [o.order_id, o.supplier_id, str(len(o.lines)),
                 format_money(o.net, company.currency),
                 o.promised.isoformat() if o.promised else ""]
                for o in orders
            ],
            numeric_columns=(2, 3),
        )
        self.status(
            f"{sum(1 for r in rows if r.must_order)} פריטים לרכישה "
            f"ב־{len(orders)} הזמנות"
        )
        self._refresh_stock()

    # -- ledger ----------------------------------------------------------------- #
    def _ledger_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.trial_table = DataTable(["קוד", "חשבון", "סוג", "חובה", "זכות"])
        layout.addWidget(self.trial_table, 1)

        self.position_grid = FieldGrid()
        layout.addWidget(self.position_grid)
        self._position_values: dict[str, QLabel] = {}
        for label in ("הכנסות", "הוצאות", "תוצאה", "נכסים", "התחייבויות",
                      "הון", "הפרש מאזן"):
            value = QLabel("—")
            value.setObjectName("FieldValue")
            value.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
            self._position_values[label] = self.position_grid.add(label, value)

        note = QLabel(
            "הנהלת חשבונות כפולה, באגורות. פקודת יומן שאינה מתאזנת לאפס "
            "נדחית ברגע הרישום, כך שמאזן הבוחן לא יכול שלא להתאזן — "
            "והביקורת מוכיחה זאת במקום להניח, ובודקת את חשבונות המלאי "
            "מול ספר המלאי."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _refresh_ledger(self) -> None:
        from ..erp import format_money

        company = self._company()
        type_hebrew = {
            "asset": "נכס", "liability": "התחייבות", "equity": "הון",
            "income": "הכנסה", "expense": "הוצאה",
        }
        self.trial_table.set_rows(
            [
                [row.account.code, row.account.name,
                 type_hebrew.get(str(row.account.type), str(row.account.type)),
                 format_money(row.debits, company.currency) if row.debits else "",
                 format_money(row.credits, company.currency) if row.credits else ""]
                for row in company.ledger.trial_balance()
            ],
            numeric_columns=(3, 4),
        )
        profit = company.ledger.profit_and_loss()
        sheet = company.ledger.balance_sheet()
        for label, value in [
            ("הכנסות", profit["income"]), ("הוצאות", profit["expense"]),
            ("תוצאה", profit["result"]), ("נכסים", sheet["assets"]),
            ("התחייבויות", sheet["liabilities"]), ("הון", sheet["equity"]),
            ("הפרש מאזן", sheet["difference"]),
        ]:
            self._position_values[label].setText(format_money(value, company.currency))

    # -- capacity ---------------------------------------------------------------- #
    def _planning_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, METRICS.space(3), 0, 0)
        layout.setSpacing(METRICS.space(3))

        run = QPushButton("שיבוץ הפתחים שתוכננו")
        run.clicked.connect(self.run_schedule)
        layout.addWidget(run)

        self.schedule_table = DataTable(
            ["פעולה", "עמדת עבודה", "התחלה", "סיום", "שעות"],
            empty_text="לחץ ״שיבוץ״ אחרי שתכננת פתחים",
        )
        layout.addWidget(self.schedule_table, 1)
        self.load_table = DataTable(
            ["קוד", "עמדת עבודה", "שעות", "זמין", "ניצולת"],
            empty_text="עומס עמדות העבודה יופיע כאן אחרי השיבוץ",
        )
        layout.addWidget(self.load_table, 1)

        note = QLabel(
            "קיבולת סופית: מסור שכבר משוריין לשלוש עבודות דוחה את הרביעית, "
            "וזה מה שקורה ברצפת הייצור בין אם התוכנית אומרת זאת ובין אם לא. "
            "שבוע העבודה ראשון–חמישי עם שישי חצי יום, וזמן האספקה של הזכוכית "
            "רץ במקביל לעבודת המפעל ולא אחריה."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def run_schedule(self) -> None:
        from ..erp import DEFAULT_WORK_CENTRES, Scheduler, demand_from_builds

        if not self.session.builds:
            self.report(
                ProfileOSError("עדיין לא תוכננו פתחים"), "אין מה לשבץ"
            )
            return
        demand = demand_from_builds(self.session.builds, "JOB")
        plan = Scheduler().schedule([demand])
        operation_hebrew = {
            "cutting": "חיתוך", "machining": "עיבוד", "glazing_order": "הזמנת זכוכית",
            "assembly": "הרכבה", "glazing": "זיגוג", "packing": "אריזה",
        }
        self.schedule_table.set_rows(
            [
                [operation_hebrew.get(str(op.operation), str(op.operation)),
                 op.work_centre, op.start.isoformat(),
                 op.finish.isoformat(), f"{op.hours:.2f}" if op.hours else "—"]
                for op in sorted(plan.operations, key=lambda o: (o.start, o.operation))
            ],
            numeric_columns=(4,),
        )
        colours: dict[tuple[int, int], str] = {}
        rows = plan.utilisation(DEFAULT_WORK_CENTRES)
        for index, row in enumerate(rows):
            colours[(index, 4)] = (
                self.colours.danger if row["utilisation_pct"] > 90
                else self.colours.warning if row["utilisation_pct"] > 70
                else self.colours.success
            )
        self.load_table.set_rows(
            [
                [r["code"], r["name"], f"{r['hours']:.1f}",
                 f"{r['available']:.1f}", f"{r['utilisation_pct']:.0f}%"]
                for r in rows
            ],
            numeric_columns=(2, 3, 4), colours=colours,
        )
        finish = plan.completion[demand.job_id]
        bottleneck = plan.bottleneck(DEFAULT_WORK_CENTRES)
        hebrew_days = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
        self.status(
            f"סיום {finish.isoformat()} (יום {hebrew_days[finish.weekday()]})"
            + (f" · צוואר בקבוק: {bottleneck['name']}" if bottleneck else "")
        )

    # -- audit -------------------------------------------------------------------- #
    def run_audit(self) -> None:
        from ..erp import format_money

        company = self._company()
        try:
            report = company.audit()
        except Exception as exc:  # noqa: BLE001
            self.report(exc, "הספרים והמדפים לא מסכימים")
            return

        summary = company.summary()
        self.stats.update_many({
            "stock": (format_money(summary["stock_value"], company.currency), ""),
            "debtors": (format_money(summary["debtors"], company.currency), "חייבים לנו"),
            "creditors": (format_money(summary["creditors"], company.currency), "אנחנו חייבים"),
            "result": (format_money(summary["result"], company.currency), "בתקופה הנוכחית"),
            "entries": (str(report["entries"]), f"{report['movements']} תנועות"),
        })
        self._refresh_stock()
        self._refresh_ledger()
        self.status(
            "הספר מאוזן, המלאי הותאם מול היסטוריית התנועות, "
            "וחשבונות המלאי מסכימים עם ספר המלאי."
        )

    def refresh(self) -> None:
        self._refresh_stock()
        self._refresh_ledger()


PAGES: list[type[Page]] = [
    HomePage, ProjectsPage, ProfilePage, ElementPage, ViewPage, DrawingsPage,
    NestingPage, GlassPage,
    MachiningPage, QuotePage, AccountsPage, CollectionPage,
    ShopFloorPage, DeliveryPage, ServicePage, PlumbingPage,
    CataloguePage,
    SystemPage,
    ReportsPage,
    SurveyPage,
]

__all__ = [
    "Page", "HomePage", "ProjectsPage", "ProfilePage", "ElementPage", "ViewPage",
    "DrawingsPage", "NestingPage", "GlassPage", "MachiningPage", "QuotePage", "AccountsPage",
    "ShopFloorPage", "DeliveryPage", "ServicePage", "CollectionPage", "PlumbingPage",
    "CataloguePage", "SystemPage", "ReportsPage", "SurveyPage", "PAGES",
]
