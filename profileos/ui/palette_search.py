"""The command palette: everywhere in the software, one keystroke away.

Ctrl+K opens a small search box over the window. Typing filters pages and
actions; Enter runs the highlighted one. It is the fastest navigation in the
suite and it never has to be used — everything it reaches is also in the
sidebar — which is exactly what makes it safe to give to everyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
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
        self.search.setPlaceholderText("לאן עוברים? הקלד כדי לחפש…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setObjectName("PaletteList")
        self.results.itemActivated.connect(self._run_item)
        layout.addWidget(self.results, 1)

        hint = QLabel("Enter — מעבר · Esc — סגירה · ↑↓ — ניווט")
        hint.setObjectName("PaletteHint")
        layout.addWidget(hint)

        self._commands = self._build_commands()
        self._filter("")
        self.resize(520, 380)

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

    def _run_item(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        command.run()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
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
        self.search.clear()
        self.search.setFocus()
        self.show()


__all__ = ["Command", "CommandPalette"]
