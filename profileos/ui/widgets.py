"""Reusable interface building blocks.

Small, composable pieces so every page is assembled from the same vocabulary:
a card, a stat tile, a labelled field, a header. This is what keeps six
different pages looking like one application.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import DARK, METRICS, MONO_FONTS, Palette, badge_style


class Card(QFrame):
    """A titled panel. The unit every page is built from."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*(METRICS.space(4),) * 4)
        self._layout.setSpacing(METRICS.space(3))

        self.title_label: QLabel | None = None
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("CardTitle")
            self._layout.addWidget(self.title_label)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: Any) -> None:
        self._layout.addLayout(layout)

    def set_title(self, title: str) -> None:
        if self.title_label is not None:
            self.title_label.setText(title.upper())

    @property
    def body(self) -> QVBoxLayout:
        return self._layout


class StatTile(QFrame):
    """A single headline number with a label and optional trend note."""

    def __init__(
        self, label: str, value: str = "-", note: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(METRICS.space(4), METRICS.space(3), METRICS.space(4), METRICS.space(3))
        layout.setSpacing(2)

        self.label = QLabel(label.upper())
        self.label.setObjectName("CardTitle")
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        self.note = QLabel(note)
        self.note.setObjectName("StatLabel")

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, note: str = "") -> None:
        self.value.setText(value)
        if note:
            self.note.setText(note)


class StatRow(QWidget):
    """A row of :class:`StatTile` widgets, keyed by name."""

    def __init__(self, definitions: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(METRICS.space(3))

        self.tiles: dict[str, StatTile] = {}
        for key, label in definitions:
            tile = StatTile(label)
            self.tiles[key] = tile
            layout.addWidget(tile)

    def set(self, key: str, value: str, note: str = "") -> None:
        if key in self.tiles:
            self.tiles[key].set(value, note)

    def update_many(self, values: dict[str, tuple[str, str]]) -> None:
        for key, (value, note) in values.items():
            self.set(key, value, note)


class PageHeader(QWidget):
    """The title bar at the top of every page."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(METRICS.space(6), METRICS.space(4), METRICS.space(6), METRICS.space(4))
        layout.setSpacing(METRICS.space(4))

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("PageSubtitle")
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        layout.addLayout(text)
        layout.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(METRICS.space(2))
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def set_subtitle(self, text: str) -> None:
        self.subtitle.setText(text)


class Badge(QLabel):
    """A small coloured status chip."""

    def __init__(self, text: str, kind: str, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self._palette = palette
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.setStyleSheet(badge_style(self._palette, kind))


class FieldGrid(QWidget):
    """A two-column grid of labelled input controls."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(METRICS.space(3))
        self.grid.setVerticalSpacing(METRICS.space(2))
        self.grid.setColumnStretch(1, 1)
        self._row = 0

    def add(self, label: str, widget: QWidget) -> QWidget:
        caption = QLabel(label)
        caption.setObjectName("FieldLabel")
        self.grid.addWidget(caption, self._row, 0, Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(widget, self._row, 1)
        self._row += 1
        return widget

    def add_span(self, widget: QWidget) -> QWidget:
        self.grid.addWidget(widget, self._row, 0, 1, 2)
        self._row += 1
        return widget


class DataTable(QTableWidget):
    """A read-only table with sensible defaults for numeric engineering data.

    ``empty_text`` is painted in the body while the table has no rows, so an
    empty table explains itself instead of presenting a blank rectangle.
    """

    def __init__(
        self,
        headers: Sequence[str],
        parent: QWidget | None = None,
        *,
        empty_text: str = "אין נתונים עדיין",
    ) -> None:
        super().__init__(0, len(headers), parent)
        self._empty_text = empty_text
        self.setHorizontalHeaderLabels(list(headers))
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setWordWrap(False)

        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setHighlightSections(False)
        self.verticalHeader().setDefaultSectionSize(METRICS.row_height)

    def set_rows(
        self,
        rows: Iterable[Sequence[Any]],
        *,
        numeric_columns: Sequence[int] = (),
        colours: dict[tuple[int, int], str] | None = None,
    ) -> None:
        """Replace the contents.

        ``numeric_columns`` are right-aligned and rendered with tabular figures,
        so a column of dimensions lines up on the decimal point.
        """
        rows = list(rows)
        self.setRowCount(len(rows))
        mono = QFont(MONO_FONTS[0])

        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                if column_index in numeric_columns:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setFont(mono)
                if colours and (row_index, column_index) in colours:
                    from PySide6.QtGui import QColor

                    item.setForeground(QColor(colours[(row_index, column_index)]))
                self.setItem(row_index, column_index, item)

    def clear_rows(self) -> None:
        self.setRowCount(0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        if self.rowCount() or not self._empty_text:
            return
        from PySide6.QtGui import QPainter

        painter = QPainter(self.viewport())
        painter.setPen(QColor(DARK.text_faint))
        font = painter.font()
        font.setPointSize(METRICS.font_size)
        painter.setFont(font)
        painter.drawText(
            self.viewport().rect(),
            Qt.AlignmentFlag.AlignCenter,
            self._empty_text,
        )
        painter.end()


def divider(parent: QWidget | None = None) -> QFrame:
    """A one-pixel horizontal rule."""
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


def page_layout(widget: QWidget) -> QVBoxLayout:
    """Standard page margins and spacing."""
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(METRICS.space(6), METRICS.space(4), METRICS.space(6), METRICS.space(6))
    layout.setSpacing(METRICS.space(4))
    return layout


__all__ = [
    "Card",
    "StatTile",
    "StatRow",
    "PageHeader",
    "Badge",
    "FieldGrid",
    "DataTable",
    "divider",
    "page_layout",
]

class EmptyState(QWidget):
    """What a screen shows before it has anything: what this is, and the way in.

    Every page starts empty on a fresh morning, and a blank card teaches
    nobody. The empty state names the thing the page will show, says how it
    comes to exist, and carries the button that starts it — so the first click
    of the day is on the screen itself, not in a menu somebody has to know.
    """

    def __init__(
        self,
        glyph: str,
        title: str,
        body: str,
        *,
        action: str = "",
        on_action: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(METRICS.space(6), METRICS.space(10),
                                  METRICS.space(6), METRICS.space(10))
        layout.setSpacing(METRICS.space(2))
        layout.addStretch(1)

        # The glyph is drawn, not typed: a large thin-stroke mark in the faint
        # ink, so the empty state reads as designed rather than as a missing
        # image. Callers pass a short string of drawing characters ("▢", "⌀").
        mark = QLabel(glyph)
        mark.setObjectName("EmptyGlyph")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = mark.font()
        font.setPointSize(34)
        font.setWeight(QFont.Weight.Thin)
        mark.setFont(font)
        layout.addWidget(mark)

        heading = QLabel(title)
        heading.setObjectName("EmptyTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        text = QLabel(body)
        text.setObjectName("EmptyBody")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setWordWrap(True)
        layout.addWidget(text)

        if action:
            row = QHBoxLayout()
            row.addStretch(1)
            self.button = QPushButton(action)
            self.button.setObjectName("Primary")
            self.button.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_action is not None:
                self.button.clicked.connect(on_action)
            row.addWidget(self.button)
            row.addStretch(1)
            layout.addSpacing(METRICS.space(3))
            layout.addLayout(row)
        layout.addStretch(2)


class Skeleton(QWidget):
    """A shimmering placeholder for content that is being computed.

    Rows of soft bars in the raised tone, with a slow sweep of slightly
    lighter ink. Shown while an engine call runs, so a working screen never
    looks like a frozen one.
    """

    def __init__(self, rows: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)
        self.setMinimumHeight(rows * (METRICS.row_height + METRICS.space(2)))

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.02) % 1.4
        self.update()

    def paintEvent(self, event: Any) -> None:
        from PySide6.QtGui import QLinearGradient, QPainter

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base = QColor(DARK.surface_raised)
        sheen = QColor(DARK.border_strong)
        width = self.width()
        row_height = METRICS.row_height - METRICS.space(1)
        for index in range(self._rows):
            y = index * (METRICS.row_height + METRICS.space(2))
            bar_width = width * (0.92 - 0.14 * (index % 3))
            gradient = QLinearGradient(0, 0, width, 0)
            centre = self._phase - 0.2
            gradient.setColorAt(max(0.0, min(1.0, centre - 0.18)), base)
            gradient.setColorAt(max(0.0, min(1.0, centre)), sheen)
            gradient.setColorAt(max(0.0, min(1.0, centre + 0.18)), base)
            painter.setBrush(gradient)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(0, y, bar_width, row_height),
                                    METRICS.radius_small, METRICS.radius_small)
        painter.end()
