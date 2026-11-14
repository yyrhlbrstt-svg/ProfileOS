"""The command palette: everywhere in the software, one keystroke away.

Ctrl+K opens a small search box over the window. Typing filters pages and
actions; Enter runs the highlighted one. It is the fastest navigation in the
suite and it never has to be used — everything it reaches is also in the
sidebar — which is exactly what makes it safe to give to everyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .theme import METRICS


@dataclass(frozen=True)
class Command:
    """One thing the palette can do: a label to read, text to match, a call."""

    label: str
    keywords: str
    run: Callable[[], None]


class CommandPalette(QDialog):
    """A frameless search box floated over the main window."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self._window = window
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("Palette")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, METRICS.space(1))
        layout.setSpacing(0)
        outer.addWidget(panel)

        self.search = QLineEdit()
        self.search.setObjectName("PaletteInput")
        self.search.setPlaceholderText("לאן עוברים — או שאלו: כמה כסף יש במגירה?")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        # The answer to a question about the shop, read from its own books.
        self.answer_card = QFrame()
        self.answer_card.setObjectName("AnswerCard")
        card = QVBoxLayout(self.answer_card)
        card.setContentsMargins(METRICS.space(5), METRICS.space(3), METRICS.space(5), METRICS.space(3))
        card.setSpacing(METRICS.space(1))
        self.answer_title = QLabel("")
        self.answer_title.setObjectName("AnswerTitle")
        self.answer_title.setWordWrap(True)
        self.answer_detail = QLabel("")
        self.answer_detail.setObjectName("AnswerDetail")
        self.answer_detail.setWordWrap(True)
        self.answer_rows = QLabel("")
        self.answer_rows.setObjectName("AnswerRows")
        self.answer_rows.setWordWrap(True)
        self.answer_go = QPushButton("")
        self.answer_go.setObjectName("Primary")
        self.answer_go.clicked.connect(self._go_to_answer)
        go_row = QHBoxLayout()
        go_row.addWidget(self.answer_go)
        go_row.addStretch(1)
        for widget in (self.answer_title, self.answer_detail, self.answer_rows):
            card.addWidget(widget)
        card.addLayout(go_row)
        self.answer_card.setVisible(False)
        layout.addWidget(self.answer_card)
        self._answer = None
        self._context = None
        self._asking = QTimer(self)
        self._asking.setSingleShot(True)
        self._asking.setInterval(220)
        self._asking.timeout.connect(self._ask)

        self.results = QListWidget()
        self.results.setObjectName("PaletteList")
        self.results.itemActivated.connect(self._run_item)
        layout.addWidget(self.results, 1)

        from ..ask import EXAMPLES

        hint = QLabel("אפשר גם לשאול — למשל: "
                      + " · ".join(example.rstrip("?") for example in EXAMPLES[:3])
                      + "\nEnter — מעבר · Esc — סגירה · ↑↓ — ניווט")
        hint.setObjectName("PaletteHint")
        layout.addWidget(hint)

        self._commands = self._build_commands()
        self._filter("")
        self.resize(640, 460)

    # -- the catalogue ---------------------------------------------------- #
    def _build_commands(self) -> list[Command]:
        window = self._window
        commands: list[Command] = []
        for page in window.pages:
            title = page.hebrew or page.title
            commands.append(Command(
                label=title,
                keywords=f"{title} {page.title} {page.subtitle}".lower(),
                run=lambda t=page.title: window.go_to_page(t),
            ))
        commands.append(Command(
            label="הערכת מחיר מהירה (Ctrl+E)",
            keywords="estimate הערכה מחיר טלפון כמה עולה",
            run=window.quick_estimate,
        ))
        commands.append(Command(
            label="החלפת ערכת נושא (בהיר/כהה)",
            keywords="theme ערכת נושא בהיר כהה",
            run=window.toggle_theme,
        ))
        commands.append(Command(
            label="טעינת פרופיל לדוגמה",
            keywords="sample דוגמה פרופיל טען",
            run=self._load_sample,
        ))

        # The library is in the palette too, so a name typed here goes
        # straight to the thing rather than to the screen that holds it.
        from ..library import opening_library, profile_library

        for preset in opening_library():
            commands.append(Command(
                label=f"פתח: {preset.title}  ⁦{preset.width:.0f}×{preset.height:.0f}⁩",
                keywords=" ".join(preset.search_terms()).lower(),
                run=lambda p=preset: self._build_opening(p),
            ))
        commands.append(Command(
            label="חיפוש פתח לפי מידה…",
            keywords="פתח חלון מידה גודל הזזה בלגי דלת search size",
            run=self._find_opening,
        ))
        for profile in profile_library():
            commands.append(Command(
                label=f"פרופיל: {profile.hebrew}",
                keywords=" ".join(profile.search_terms()).lower(),
                run=lambda p=profile: self._load_profile(p),
            ))
        return commands

    def _load_sample(self) -> None:
        page = self._window.go_to_page("Profile")
        if hasattr(page, "load_sample"):
            page.load_sample()

    def _load_profile(self, profile: Any) -> None:
        page = self._window.go_to_page("Profile")
        if hasattr(page, "load"):
            page.load(profile.path)

    def _find_opening(self) -> None:
        page = self._window.go_to_page("Element")
        if hasattr(page, "find_opening"):
            page.find_opening()

    def _build_opening(self, preset: Any) -> None:
        page = self._window.go_to_page("Element")
        if hasattr(page, "apply_preset"):
            page.apply_preset(preset)

    # -- behaviour --------------------------------------------------------- #
    def _filter(self, text: str) -> None:
        # Every word has to appear somewhere, in any order: "הזזה 3" finds the
        # three-leaf slider whichever half was typed first.
        words = [word for word in text.strip().lower().split() if word]
        self.results.clear()
        for command in self._commands:
            haystack = f"{command.keywords} {command.label.lower()}"
            if all(word in haystack for word in words):
                item = QListWidgetItem(command.label)
                item.setData(Qt.ItemDataRole.UserRole, command)
                self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
        self._asking.start()

    def _ask(self) -> None:
        """A question about the shop gets its answer above the list."""
        from ..ask import answer

        text = self.search.text().strip()
        found = None
        if len(text) >= 3:
            try:
                if self._context is None:
                    from ..insights import gather_context

                    self._context = gather_context()
                found = answer(text, self._context)
            except Exception:  # noqa: BLE001 - the search goes on searching
                found = None
        self._answer = found
        self.answer_card.setVisible(found is not None)
        if found is None:
            return
        self.answer_title.setText(found.title)
        self.answer_detail.setText(found.detail)
        self.answer_detail.setVisible(bool(found.detail))
        self.answer_rows.setText("\n".join(f"• {row}" for row in found.rows))
        self.answer_rows.setVisible(bool(found.rows))
        self.answer_go.setText(found.go or "פתח")
        self.answer_go.setVisible(bool(found.page))

    def _go_to_answer(self) -> None:
        if self._answer is None or not self._answer.page:
            return
        page = self._answer.page
        self.accept()
        self._window.go_to_page(page)

    def _run_item(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        command.run()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # A page or action named as it is called: that one, whatever an
            # answer would say ("גבייה" is the collection page).
            typed = self.search.text().strip().lower()
            for row in range(self.results.count()):
                item = self.results.item(row)
                if item.text().strip().lower() == typed:
                    self._run_item(item)
                    return
            # A question asked: Enter goes where it is dealt with.
            self._asking.stop()
            self._ask()
            if self._answer is not None and self._answer.page:
                self._go_to_answer()
                return
            item = self.results.currentItem()
            if item is not None:
                self._run_item(item)
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.results.currentRow()
            step = 1 if key == Qt.Key.Key_Down else -1
            count = self.results.count()
            if count:
                self.results.setCurrentRow((row + step) % count)
            return
        super().keyPressEvent(event)

    def open_over(self) -> None:
        """Position over the top-centre of the main window and take focus."""
        geometry = self._window.geometry()
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + geometry.height() // 6
        self.move(x, y)
        self._context = None              # the books as they are now
        self.search.clear()
        self.search.setFocus()
        self.show()


__all__ = ["Command", "CommandPalette"]
