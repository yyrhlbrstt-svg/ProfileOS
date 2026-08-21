"""The finder: one search box for the things a fabricator looks for by name.

Two questions were being answered with a blank screen — *where is my profile*
and *where is the window I make every week*. Both are lookups, both are
answered the same way: a list you can search in Hebrew, a preview of what you
are about to get, and Enter.

The dialog is deliberately the same shape as the command palette. Somebody who
has learned one search box in this software has learned all of them.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import METRICS


class _Row(QWidget):
    """One result: what it is, what it will give you, where it came from."""

    def __init__(self, title: str, note: str, meta: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(METRICS.space(3), METRICS.space(2),
                                  METRICS.space(3), METRICS.space(2))
        layout.setSpacing(METRICS.space(3))

        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(title)
        name.setObjectName("FinderName")
        text.addWidget(name)
        if note:
            hint = QLabel(note)
            hint.setObjectName("FinderNote")
            text.addWidget(hint)
        layout.addLayout(text, 1)

        if meta:
            tag = QLabel(meta)
            tag.setObjectName("FinderMeta")
            tag.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(tag)


class FinderDialog(QDialog):
    """A searchable list of anything, chosen with one key.

    ``fetch`` is called on every keystroke and returns the rows to show, so
    the dialog never holds a stale copy of a library that a folder drop or an
    ingestion run has just changed.
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        title: str,
        placeholder: str,
        fetch: Callable[[str], Sequence[tuple[str, str, str, Any]]],
        empty_text: str = "לא נמצא כלום. נסה מילה אחרת.",
    ) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self._empty_text = empty_text
        self.chosen: Any = None

        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("Palette")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, METRICS.space(1))
        layout.setSpacing(0)
        outer.addWidget(panel)

        heading = QLabel(title)
        heading.setObjectName("FinderTitle")
        layout.addWidget(heading)

        self.search = QLineEdit()
        self.search.setObjectName("PaletteInput")
        self.search.setPlaceholderText(placeholder)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setObjectName("PaletteList")
        self.results.itemActivated.connect(self._choose)
        self.results.itemClicked.connect(self._choose)
        layout.addWidget(self.results, 1)

        self.hint = QLabel("Enter — בחירה · Esc — סגירה · ↑↓ — ניווט")
        self.hint.setObjectName("PaletteHint")
        layout.addWidget(self.hint)

        self.resize(560, 460)
        self._filter("")

    # -- list ---------------------------------------------------------------- #
    def _filter(self, text: str) -> None:
        self.results.clear()
        rows = list(self._fetch(text))
        for title, note, meta, payload in rows:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, payload)
            widget = _Row(title, note, meta)
            item.setSizeHint(widget.sizeHint())
            self.results.addItem(item)
            self.results.setItemWidget(item, widget)
        if rows:
            self.results.setCurrentRow(0)
            self.hint.setText("Enter — בחירה · Esc — סגירה · ↑↓ — ניווט")
        else:
            self.hint.setText(self._empty_text)

    def _choose(self, item: QListWidgetItem) -> None:
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.results.currentItem()
            if item is not None:
                self._choose(item)
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            count = self.results.count()
            if count:
                step = 1 if key == Qt.Key.Key_Down else -1
                self.results.setCurrentRow((self.results.currentRow() + step) % count)
            return
        super().keyPressEvent(event)

    def open_over(self) -> Any:
        """Show centred on the window and return what was picked, or ``None``."""
        parent = self.parentWidget()
        if parent is not None:
            geometry = parent.window().geometry()
            self.move(
                geometry.x() + (geometry.width() - self.width()) // 2,
                geometry.y() + geometry.height() // 8,
            )
        self.search.setFocus()
        return self.chosen if self.exec() else None


# --------------------------------------------------------------------------- #
# The two libraries, wrapped for the dialog
# --------------------------------------------------------------------------- #

def _profile_rows(text: str) -> list[tuple[str, str, str, Any]]:
    from ..library import search_profiles

    return [
        (profile.hebrew, profile.note or profile.path.name, profile.origin, profile)
        for profile in search_profiles(text)
    ]


def _opening_rows(text: str) -> list[tuple[str, str, str, Any]]:
    from ..library import search_openings

    return [
        (preset.hebrew, preset.note, preset.describe(), preset)
        for preset in search_openings(text)
    ]


def find_profile(parent: QWidget | None) -> Any:
    """Pick a cross-section from everything this installation can open."""
    dialog = FinderDialog(
        parent,
        title="ספריית פרופילים",
        placeholder="חפש פרופיל — זקף, משקוף, סרגל…",
        fetch=_profile_rows,
        empty_text="אין פרופיל בשם הזה. שים קבצי DXF בתיקיית הפרופילים שלך.",
    )
    return dialog.open_over()


def find_opening(parent: QWidget | None) -> Any:
    """Pick a ready-made opening: hozaza, tilt-turn, door, curtain wall."""
    dialog = FinderDialog(
        parent,
        title="ספריית פתחים",
        placeholder="חפש פתח — הזזה, בלגי, דלת, ממ״ד…",
        fetch=_opening_rows,
        empty_text="אין פתח בשם הזה. נסה: הזזה, ציר, דלת, מסך.",
    )
    return dialog.open_over()


__all__ = ["FinderDialog", "find_opening", "find_profile"]
