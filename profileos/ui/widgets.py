"""Reusable interface building blocks.

Small, composable pieces so every page is assembled from the same vocabulary:
a card, a stat tile, a labelled field, a header. This is what keeps six
different pages looking like one application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDoubleSpinBox,
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
        self, label: str, value: str = "—", note: str = "", parent: QWidget | None = None
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
        # A label of digits alone has no direction of its own and Qt lays it
        # out left-to-right, at the far edge from its Hebrew title. Pinned to
        # the right absolutely, so the figure sits under the words naming it.
        right = (
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
            | Qt.AlignmentFlag.AlignVCenter
        )
        for part in (self.label, self.value, self.note):
            part.setAlignment(right)
        self._mark_empty(value)

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, note: str = "") -> None:
        self.value.setText(value)
        if note:
            self.note.setText(note)
        self._mark_empty(value)

    def _mark_empty(self, value: str) -> None:
        empty = value.strip() in ("", "-", "—")
        if bool(self.value.property("empty")) != empty:
            self.value.setProperty("empty", empty)
            self.value.style().unpolish(self.value)
            self.value.style().polish(self.value)


class _NumberFieldPolish(QObject):
    """Sit every number field's text on the right, away from its arrows.

    In a right-to-left window Qt puts a spin box's step buttons on the left
    edge, and left-aligned digits run straight into them. Right-aligned is
    also simply where a Hebrew reader looks for the value. Done once, as the
    widget is polished, so it covers every field on every page and dialog —
    including the ones built after the window opens.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Polish and isinstance(watched, QAbstractSpinBox):
            watched.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignAbsolute
                | Qt.AlignmentFlag.AlignVCenter
            )
        return False


def install_number_field_polish(application: QApplication) -> None:
    """Install :class:`_NumberFieldPolish` once per application."""
    if getattr(application, "_number_field_polish", None) is None:
        polish = _NumberFieldPolish(application)
        application.installEventFilter(polish)
        application._number_field_polish = polish


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

    def value(self, key: str) -> str:
        """What a tile currently reads — so code and tests can ask."""
        tile = self.tiles.get(key)
        return tile.value.text() if tile is not None else ""


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
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
            | Qt.AlignmentFlag.AlignVCenter
        )
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
                    # Absolute: in a right-to-left table a plain AlignRight is
                    # mirrored to the left edge, away from the column's title.
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setFont(mono)
                if colours and (row_index, column_index) in colours:
                    from PySide6.QtGui import QColor

                    item.setForeground(QColor(colours[(row_index, column_index)]))
                self.setItem(row_index, column_index, item)

    def selectRow(self, row: int) -> None:  # noqa: N802 - Qt's name
        """Select one whole row — also before the table was ever shown.

        Qt's own ``selectRow`` does nothing on a table not yet laid out in a
        right-to-left window, so a selection made while a page or a dialog is
        being built (the job just saved, the newest version) silently
        vanished. The selection model does not care about the layout.
        """
        if not 0 <= row < self.rowCount():
            return
        from PySide6.QtCore import QItemSelectionModel

        flags = (QItemSelectionModel.SelectionFlag.ClearAndSelect
                 | QItemSelectionModel.SelectionFlag.Rows)
        self.selectionModel().setCurrentIndex(self.model().index(row, 0), flags)

    def stretch(self, column: int) -> None:
        """Give one column the slack and size the rest to their contents.

        Stretching every column equally is right for a table of figures and
        wrong for one where a single column carries the sentence: the sentence
        gets an equal share and is truncated, while six columns of dates sit
        half empty beside it.
        """
        header = self.horizontalHeader()
        for index in range(self.columnCount()):
            header.setSectionResizeMode(
                index,
                QHeaderView.ResizeMode.Stretch if index == column
                else QHeaderView.ResizeMode.ResizeToContents,
            )

    def clear_rows(self) -> None:
        self.setRowCount(0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        if self.rowCount() or not self._empty_text:
            return
        from PySide6.QtGui import QPainter

        from PySide6.QtGui import QPalette

        painter = QPainter(self.viewport())
        # The theme's own muted text — a fixed dark-theme grey was nearly
        # invisible on white — at the body size, not larger than the rows.
        ground = self.viewport().palette().color(QPalette.ColorRole.Base)
        from .theme import LIGHT

        painter.setPen(QColor(LIGHT.text_muted if ground.lightness() > 128 else DARK.text_muted))
        font = painter.font()
        font.setPixelSize(METRICS.font_size)
        painter.setFont(font)
        painter.drawText(
            self.viewport().rect().adjusted(16, 8, -16, -8),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            self._empty_text,
        )
        painter.end()


class MillimetreBox(QDoubleSpinBox):
    """A dimension in millimetres: shown as "1800", not "1800.00", while a
    half millimetre that was measured is kept and shown ("1199.5").
    Rounding the value itself would quietly change a stored opening."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDecimals(2)  # what a QDoubleSpinBox kept before: nothing lost

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt's name
        return f"{value:.2f}".rstrip("0").rstrip(".")


def documents_dir() -> Path:
    """Where a saved or chosen file starts: the person's Documents.

    A file dialog left to itself opens in the program's own folder — which an
    update replaces and the backup does not cover. A job card saved there
    would be gone with the next version.
    """
    from PySide6.QtCore import QStandardPaths

    # Windows' own answer — it follows OneDrive's redirection of Documents.
    found = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    if found and Path(found).is_dir():
        return Path(found)
    home = Path.home()
    documents = home / "Documents"
    return documents if documents.is_dir() else home


def suggested_path(name: str) -> str:
    """A file name to save, placed in :func:`documents_dir`."""
    return str(documents_dir() / name)


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


class Toast(QLabel):
    """A short-lived notice that appears where the eye already is.

    The status bar is honest but easy to miss; a toast slides in at the top of
    the window, stays long enough to read, and removes itself. It never takes
    focus and never blocks a click.
    """

    LIFETIME_MS = 2800

    def __init__(self, window: QWidget, message: str, kind: str = "info") -> None:
        super().__init__(message, window)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.adjustSize()
        self._window = window
        self.show()
        self.raise_()
        QTimer.singleShot(self.LIFETIME_MS, self._dismiss)

    def _dismiss(self) -> None:
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(METRICS.motion_base_ms)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.finished.connect(self._remove)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _remove(self) -> None:
        toasts = getattr(self._window, "_toasts", None)
        if toasts and self in toasts:
            toasts.remove(self)
            reposition_toasts(self._window)
        self.deleteLater()


def show_toast(window: QWidget, message: str, kind: str = "info") -> None:
    """Show a toast on ``window``, stacking below any that are already up."""
    toasts: list[Toast] = getattr(window, "_toasts", None) or []
    # Two is the most anyone reads; older notices have had their moment.
    while len(toasts) >= 2:
        stale = toasts.pop(0)
        stale.hide()
        stale.deleteLater()
    toast = Toast(window, message, kind)
    toasts.append(toast)
    window._toasts = toasts
    reposition_toasts(window)


def reposition_toasts(window: QWidget) -> None:
    """Stack toasts up from the bottom edge.

    The header and the stat tiles below it are the numbers a shop points at
    while a page is talking to it — a toast anchored at the top used to land
    squarely on top of them for its whole lifetime. The bottom edge has
    nothing to say over.
    """
    # Above the status bar, not over it — and over nothing it would repeat.
    bar = window.statusBar() if hasattr(window, "statusBar") else None
    bottom = bar.sizeHint().height() if bar is not None and not bar.isHidden() else 0
    y = window.height() - bottom - METRICS.space(4)
    for toast in getattr(window, "_toasts", []):
        toast.adjustSize()
        y -= toast.height()
        toast.move((window.width() - toast.width()) // 2, y)
        toast.raise_()
        y -= METRICS.space(1)
