"""Custom-painted views: sections, elevations, nesting diagrams, clamp layouts.

These are drawn directly with ``QPainter`` rather than assembled from widgets.
A profile section is thousands of coordinates that must pan and zoom smoothly;
painting it is both faster and gives exact control over line weights and the
grid, which is what makes technical drawing look technical rather than
approximate.

All four views share :class:`CanvasView`, which owns the world-to-screen
transform, the pan/zoom interaction and the grid. Subclasses implement
:meth:`CanvasView.paint_world` in world (millimetre) coordinates and let the
base class handle everything else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import DARK, MONO_FONTS, Palette


def ltr(text: str) -> str:
    """Isolate a string as left-to-right for painting on a canvas.

    The application runs right-to-left, and Qt applies the bidi algorithm to
    every painted string — which turns "754 × 1734" into "1734 × 754" the
    moment it is drawn in an RTL context. Dimensions, marks and units on a
    drawing are technical notation and always read left-to-right, so each one
    is wrapped in an isolate before it reaches the painter.
    """
    return f"\u2066{text}\u2069"

def _colour(value: str, alpha: int | None = None) -> QColor:
    colour = QColor(value)
    if alpha is not None:
        colour.setAlpha(alpha)
    return colour


class CanvasView(QWidget):
    """A pan/zoom drawing canvas in world (millimetre) coordinates.

    The transform is a uniform scale plus a translation, with **Y flipped**:
    engineering drawings have Y increasing upward, Qt has it increasing
    downward, and doing the flip once here means no subclass ever has to
    remember it.
    """

    #: Emitted with the cursor position in world coordinates.
    cursor_moved = Signal(float, float)

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self.setObjectName("Canvas")
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._scale = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._panning = False
        self._last_mouse = QPoint()
        self._world_bounds: tuple[float, float, float, float] | None = None
        self._fitted = False

        self.show_grid = True
        self.show_axes = True
        self.margin = 28.0

    # -- transform ---------------------------------------------------------- #
    def to_screen(self, x: float, y: float) -> QPointF:
        """World millimetres -> widget pixels."""
        return QPointF(
            x * self._scale + self._offset.x(),
            -y * self._scale + self._offset.y(),
        )

    def to_world(self, point: QPointF) -> tuple[float, float]:
        """Widget pixels -> world millimetres."""
        if self._scale == 0:
            return (0.0, 0.0)
        return (
            (point.x() - self._offset.x()) / self._scale,
            -(point.y() - self._offset.y()) / self._scale,
        )

    def set_world_bounds(self, bounds: tuple[float, float, float, float] | None) -> None:
        """Declare the drawing extent and refit on the next paint."""
        self._world_bounds = bounds
        self._fitted = False
        self.update()

    def fit(self) -> None:
        """Scale and centre so the world bounds fill the widget."""
        if not self._world_bounds:
            return
        min_x, min_y, max_x, max_y = self._world_bounds
        width = max(max_x - min_x, 1e-6)
        height = max(max_y - min_y, 1e-6)

        available_w = max(self.width() - 2 * self.margin, 10.0)
        available_h = max(self.height() - 2 * self.margin, 10.0)
        self._scale = min(available_w / width, available_h / height)

        centre_x = (min_x + max_x) / 2.0
        centre_y = (min_y + max_y) / 2.0
        self._offset = QPointF(
            self.width() / 2.0 - centre_x * self._scale,
            self.height() / 2.0 + centre_y * self._scale,
        )
        self._fitted = True
        self.update()

    @property
    def scale(self) -> float:
        return self._scale

    # -- interaction --------------------------------------------------------- #
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom about the cursor, so the point under it stays put."""
        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            return
        factor = 1.15**steps
        cursor = event.position()
        world_before = self.to_world(cursor)

        self._scale = max(1e-4, min(self._scale * factor, 1e5))
        # Re-anchor so the world point under the cursor maps back to it.
        self._offset = QPointF(
            cursor.x() - world_before[0] * self._scale,
            cursor.y() + world_before[1] * self._scale,
        )
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._panning = True
            self._last_mouse = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        position = event.position()
        if self._panning:
            delta = position.toPoint() - self._last_mouse
            self._offset += QPointF(delta.x(), delta.y())
            self._last_mouse = position.toPoint()
            self.update()
        world = self.to_world(position)
        self.cursor_moved.emit(world[0], world[1])

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._panning = False
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.fit()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if not self._fitted:
            self.fit()

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, event: Any) -> None:
        if not self._fitted:
            self.fit()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.rect(), _colour(self.palette_colours.surface_sunken))

        if self.show_grid:
            self._paint_grid(painter)
        if self.show_axes:
            self._paint_axes(painter)

        self.paint_world(painter)
        self._paint_scale_bar(painter)
        painter.end()

    def paint_world(self, painter: QPainter) -> None:
        """Override to draw the content. Coordinates are world millimetres."""

    def _grid_step(self) -> float:
        """Pick a 1-2-5 grid step giving roughly 60 px spacing."""
        target = 60.0 / max(self._scale, 1e-9)
        magnitude = 10.0 ** math.floor(math.log10(max(target, 1e-9)))
        for multiple in (1.0, 2.0, 5.0, 10.0):
            if magnitude * multiple >= target:
                return magnitude * multiple
        return magnitude * 10.0

    def _paint_grid(self, painter: QPainter) -> None:
        step = self._grid_step()
        if step * self._scale < 6.0:
            return  # too dense to be useful

        left, top = self.to_world(QPointF(0, 0))
        right, bottom = self.to_world(QPointF(self.width(), self.height()))
        pen = QPen(_colour(self.palette_colours.draw_grid), 1)
        painter.setPen(pen)

        start_x = math.floor(min(left, right) / step) * step
        end_x = max(left, right)
        x = start_x
        while x <= end_x:
            screen = self.to_screen(x, 0).x()
            painter.drawLine(QPointF(screen, 0), QPointF(screen, self.height()))
            x += step

        start_y = math.floor(min(top, bottom) / step) * step
        end_y = max(top, bottom)
        y = start_y
        while y <= end_y:
            screen = self.to_screen(0, y).y()
            painter.drawLine(QPointF(0, screen), QPointF(self.width(), screen))
            y += step

    def _paint_axes(self, painter: QPainter) -> None:
        pen = QPen(_colour(self.palette_colours.draw_axis), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        origin = self.to_screen(0, 0)
        if 0 <= origin.x() <= self.width():
            painter.drawLine(QPointF(origin.x(), 0), QPointF(origin.x(), self.height()))
        if 0 <= origin.y() <= self.height():
            painter.drawLine(QPointF(0, origin.y()), QPointF(self.width(), origin.y()))

    def _paint_scale_bar(self, painter: QPainter) -> None:
        """A bar showing what one grid step is worth, bottom-left."""
        step = self._grid_step()
        pixels = step * self._scale
        if pixels < 20 or pixels > self.width() * 0.6:
            return

        y = self.height() - 22
        x = 16
        pen = QPen(_colour(self.palette_colours.text_faint), 1.4)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, y), QPointF(x + pixels, y))
        painter.drawLine(QPointF(x, y - 4), QPointF(x, y + 4))
        painter.drawLine(QPointF(x + pixels, y - 4), QPointF(x + pixels, y + 4))

        label = f"{step:g} mm" if step < 1000 else f"{step / 1000:g} m"
        font = QFont(MONO_FONTS[0], 9)
        painter.setFont(font)
        painter.drawText(QPointF(x + pixels + 8, y + 4), ltr(label))

    # -- helpers for subclasses ---------------------------------------------- #
    def world_path(self, rings: Sequence[Sequence[tuple[float, float]]]) -> QPainterPath:
        """Build a screen-space path from world-space rings (even-odd filled)."""
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for ring in rings:
            if len(ring) < 2:
                continue
            path.moveTo(self.to_screen(*ring[0]))
            for point in ring[1:]:
                path.lineTo(self.to_screen(*point))
            path.closeSubpath()
        return path

    def draw_label(
        self, painter: QPainter, x: float, y: float, text: str,
        *, colour: str | None = None, size: int = 10, centre: bool = True,
    ) -> None:
        """Draw a text label at a world position."""
        painter.setFont(QFont(MONO_FONTS[0], size))
        painter.setPen(QPen(_colour(colour or self.palette_colours.text_muted)))
        point = self.to_screen(x, y)
        if centre:
            metrics = QFontMetricsF(painter.font())
            point -= QPointF(metrics.horizontalAdvance(text) / 2.0, -metrics.height() / 4.0)
        painter.drawText(point, ltr(text))


# --------------------------------------------------------------------------- #
# Section view
# --------------------------------------------------------------------------- #

class SectionView(CanvasView):
    """Draws a profile cross-section with its material, voids and centroid."""

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(palette, parent)
        self._rings: list[list[tuple[float, float]]] = []
        self._centroid: tuple[float, float] | None = None
        self._shear_centre: tuple[float, float] | None = None
        self._thin_spots: list[tuple[float, float, float]] = []
        self._principal_angle: float | None = None
        self.show_centroid = True
        self.show_thin_spots = True
        self.show_principal_axes = True

    def set_section(self, polygon: Any, properties: Any = None, validation: Any = None) -> None:
        """Load a Shapely polygon and, optionally, its computed properties."""
        from ..geometry.shapely_bridge import polygon_rings_coordinates

        rings: list[list[tuple[float, float]]] = []
        for shell, holes in polygon_rings_coordinates(polygon):
            rings.append(list(shell))
            rings.extend(list(hole) for hole in holes)
        self._rings = rings

        if properties is not None:
            self._centroid = (properties.centroid_x, properties.centroid_y)
            self._principal_angle = properties.principal_angle
            if properties.shear_centre_x is not None:
                self._shear_centre = (properties.shear_centre_x, properties.shear_centre_y)
            else:
                self._shear_centre = None
        if validation is not None and validation.thickness is not None:
            self._thin_spots = list(validation.thickness.thin_spots)

        bounds = polygon.bounds
        self.set_world_bounds((bounds[0], bounds[1], bounds[2], bounds[3]))

    def clear(self) -> None:
        self._rings = []
        self._centroid = None
        self._shear_centre = None
        self._thin_spots = []
        self.set_world_bounds(None)

    def paint_world(self, painter: QPainter) -> None:
        if not self._rings:
            self._paint_placeholder(painter, "Open a DXF to see the cross-section")
            return

        palette = self.palette_colours
        path = self.world_path(self._rings)

        painter.setBrush(QBrush(_colour(palette.draw_material, 150)))
        painter.setPen(QPen(_colour(palette.draw_material_edge), 1.6))
        painter.drawPath(path)

        if self.show_principal_axes and self._centroid and self._principal_angle is not None:
            self._paint_principal_axes(painter)
        if self.show_centroid and self._centroid:
            self._paint_marker(painter, *self._centroid, palette.draw_highlight, "C")
        if self._shear_centre:
            self._paint_marker(painter, *self._shear_centre, palette.info, "S")
        if self.show_thin_spots and self._thin_spots:
            self._paint_thin_spots(painter)

    def _paint_principal_axes(self, painter: QPainter) -> None:
        assert self._centroid is not None and self._principal_angle is not None
        cx, cy = self._centroid
        extent = 0.0
        if self._world_bounds:
            min_x, min_y, max_x, max_y = self._world_bounds
            extent = max(max_x - min_x, max_y - min_y) * 0.6

        angle = math.radians(self._principal_angle)
        for offset, colour, label in (
            (0.0, self.palette_colours.draw_highlight, "1"),
            (math.pi / 2, self.palette_colours.text_faint, "2"),
        ):
            dx = math.cos(angle + offset) * extent
            dy = math.sin(angle + offset) * extent
            painter.setPen(QPen(_colour(colour, 160), 1.0, Qt.PenStyle.DashDotLine))
            painter.drawLine(self.to_screen(cx - dx, cy - dy), self.to_screen(cx + dx, cy + dy))
            self.draw_label(painter, cx + dx, cy + dy, label, colour=colour, size=9)

    def _paint_marker(
        self, painter: QPainter, x: float, y: float, colour: str, label: str
    ) -> None:
        point = self.to_screen(x, y)
        painter.setPen(QPen(_colour(colour), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(point, 6, 6)
        painter.drawLine(point + QPointF(-9, 0), point + QPointF(9, 0))
        painter.drawLine(point + QPointF(0, -9), point + QPointF(0, 9))
        painter.setFont(QFont(MONO_FONTS[0], 9, QFont.Weight.Bold))
        painter.drawText(point + QPointF(11, -6), ltr(label))

    def _paint_thin_spots(self, painter: QPainter) -> None:
        painter.setPen(QPen(_colour(self.palette_colours.danger), 1.4))
        painter.setBrush(QBrush(_colour(self.palette_colours.danger, 90)))
        for x, y, _thickness in self._thin_spots:
            painter.drawEllipse(self.to_screen(x, y), 4, 4)

    def _paint_placeholder(self, painter: QPainter, message: str) -> None:
        painter.setPen(QPen(_colour(self.palette_colours.text_faint)))
        painter.setFont(QFont("Heebo", 12))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, message)


# --------------------------------------------------------------------------- #
# Elevation view
# --------------------------------------------------------------------------- #

class ElevationView(CanvasView):
    """Draws an element elevation: frame, divisions, sashes and glass."""

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(palette, parent)
        self._build: Any = None
        self.show_dimensions = True
        self.show_grid = False

    def set_build(self, build: Any) -> None:
        """Load an :class:`~profileos.elements.ElementBuild`."""
        self._build = build
        if build is None:
            self.set_world_bounds(None)
            return
        opening = build.opening
        self.set_world_bounds((0.0, 0.0, opening.width, opening.height))

    def paint_world(self, painter: QPainter) -> None:
        if self._build is None:
            painter.setPen(QPen(_colour(self.palette_colours.text_faint)))
            painter.setFont(QFont("Heebo", 12))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "תכנן פתח כדי לראות את החזית"
            )
            return

        from ..elements import ElementBuilder

        palette = self.palette_colours
        build = self._build
        opening = build.opening
        rules = build.rules
        rects = ElementBuilder().cell_rects(opening, rules)

        # Outer frame.
        outer = QRectF(self.to_screen(0, opening.height), self.to_screen(opening.width, 0))
        painter.setBrush(QBrush(_colour(palette.draw_frame, 130)))
        painter.setPen(QPen(_colour(palette.draw_material_edge), 1.8))
        painter.drawRect(outer)

        for cell in opening.all_cells():
            rect = rects.get(cell.key)
            if rect is None or rect.width <= 0 or rect.height <= 0:
                continue
            self._paint_cell(painter, build, cell, rect, rules)

        if self.show_dimensions:
            self._paint_dimensions(painter, opening)

    def _paint_cell(self, painter: QPainter, build: Any, cell: Any, rect: Any, rules: Any) -> None:
        palette = self.palette_colours
        area = QRectF(
            self.to_screen(rect.x, rect.y + rect.height),
            self.to_screen(rect.x + rect.width, rect.y),
        )

        if cell.panel:
            painter.setBrush(QBrush(_colour(palette.text_faint, 110)))
            painter.setPen(QPen(_colour(palette.border_strong), 1.2))
            painter.drawRect(area)
            self.draw_label(
                painter, rect.x + rect.width / 2, rect.y + rect.height / 2, "PANEL", size=9
            )
            return

        if cell.sash is not None:
            # The sash rail, drawn as an inset frame inside the cell.
            painter.setBrush(QBrush(_colour(palette.draw_sash, 120)))
            painter.setPen(QPen(_colour(palette.draw_material_edge), 1.4))
            painter.drawRect(area)
            inset = rules.sash.sash_face_width
            glass_rect = QRectF(
                self.to_screen(rect.x + inset, rect.y + rect.height - inset),
                self.to_screen(rect.x + rect.width - inset, rect.y + inset),
            )
        else:
            glass_rect = area

        painter.setBrush(QBrush(_colour(palette.draw_glass, 90)))
        painter.setPen(QPen(_colour(palette.draw_glass), 1.2))
        painter.drawRect(glass_rect)

        if cell.sash is not None:
            self._paint_opening_symbol(painter, glass_rect, cell.sash)

        panel = next((p for p in build.glass if p.cell_key == cell.key), None)
        if panel is not None:
            centre_x = rect.x + rect.width / 2
            self.draw_label(
                painter, centre_x, rect.y + rect.height / 2,
                f"{panel.width:.0f} x {panel.height:.0f}", size=9,
                colour=palette.text if panel.compliant else palette.danger,
            )
            if not panel.compliant:
                self.draw_label(
                    painter, centre_x, rect.y + rect.height / 2 - rect.height * 0.1,
                    "SAFETY GLASS REQUIRED", size=8, colour=palette.danger,
                )

    def _paint_opening_symbol(self, painter: QPainter, rect: QRectF, sash: Any) -> None:
        """Draw the standard triangle showing which way a sash opens.

        The apex sits on the hinge side, which is the convention on elevation
        drawings: the operator reads hinge position straight off the symbol.
        """
        from ..elements import HingeSide, OpeningType

        painter.setPen(QPen(_colour(self.palette_colours.draw_material_edge, 170), 1.2,
                            Qt.PenStyle.DashLine))

        if sash.opening_type in (OpeningType.SLIDING, OpeningType.LIFT_SLIDE):
            middle = rect.center().y()
            painter.drawLine(QPointF(rect.left() + 8, middle), QPointF(rect.right() - 8, middle))
            for x in (rect.left() + 14, rect.right() - 14):
                painter.drawLine(QPointF(x, middle - 6), QPointF(x, middle + 6))
            return

        path = QPainterPath()
        if sash.hinge_side is HingeSide.LEFT:
            path.moveTo(rect.topRight()); path.lineTo(rect.left(), rect.center().y())
            path.lineTo(rect.bottomRight())
        elif sash.hinge_side is HingeSide.RIGHT:
            path.moveTo(rect.topLeft()); path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.bottomLeft())
        elif sash.hinge_side is HingeSide.TOP:
            path.moveTo(rect.bottomLeft()); path.lineTo(rect.center().x(), rect.top())
            path.lineTo(rect.bottomRight())
        else:
            path.moveTo(rect.topLeft()); path.lineTo(rect.center().x(), rect.bottom())
            path.lineTo(rect.topRight())
        painter.drawPath(path)

        # A tilt-and-turn also tilts, so it carries the bottom-hung symbol too.
        if sash.opening_type is OpeningType.TILT_TURN:
            tilt = QPainterPath()
            tilt.moveTo(rect.topLeft())
            tilt.lineTo(rect.center().x(), rect.bottom())
            tilt.lineTo(rect.topRight())
            painter.setPen(QPen(_colour(self.palette_colours.info, 140), 1.0, Qt.PenStyle.DotLine))
            painter.drawPath(tilt)

    def _paint_dimensions(self, painter: QPainter, opening: Any) -> None:
        palette = self.palette_colours
        painter.setPen(QPen(_colour(palette.draw_dimension), 1.0))

        # Overall width, below the element.
        y = -opening.height * 0.06
        left, right = self.to_screen(0, y), self.to_screen(opening.width, y)
        painter.drawLine(left, right)
        for point in (left, right):
            painter.drawLine(point + QPointF(0, -5), point + QPointF(0, 5))
        self.draw_label(painter, opening.width / 2, y * 1.7, f"{opening.width:.0f}", size=10)

        # Overall height, to the left.
        x = -opening.width * 0.06
        bottom, top = self.to_screen(x, 0), self.to_screen(x, opening.height)
        painter.drawLine(bottom, top)
        for point in (bottom, top):
            painter.drawLine(point + QPointF(-5, 0), point + QPointF(5, 0))
        self.draw_label(painter, x * 1.7, opening.height / 2, f"{opening.height:.0f}", size=10)


# --------------------------------------------------------------------------- #
# Nesting view
# --------------------------------------------------------------------------- #

@dataclass
class _BarGeometry:
    """Layout constants for the nesting diagram, in pixels."""

    bar_height: int = 34
    gap: int = 14
    left_margin: int = 96
    right_margin: int = 24
    top_margin: int = 16


class NestingView(QWidget):
    """Draws the bar layouts from a nesting result.

    Not a :class:`CanvasView`: a cutting plan is a list of bars read top to
    bottom, so it scrolls vertically and scales horizontally to the widget
    rather than panning freely.
    """

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self.setObjectName("Canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

        self._result: Any = None
        self._geometry = _BarGeometry()
        self._hover_bar: int | None = None
        self.min_remnant = 300.0

    def set_result(self, result: Any, min_remnant: float = 300.0) -> None:
        self._result = result
        self.min_remnant = min_remnant
        if result is not None:
            geometry = self._geometry
            height = (
                geometry.top_margin
                + len(result.layouts) * (geometry.bar_height + geometry.gap)
                + geometry.top_margin
            )
            self.setMinimumHeight(max(240, height))
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        geometry = self._geometry
        index = int(
            (event.position().y() - geometry.top_margin) // (geometry.bar_height + geometry.gap)
        )
        self._hover_bar = index if self._result and 0 <= index < len(self._result.layouts) else None
        self.update()

    def leaveEvent(self, event: Any) -> None:
        self._hover_bar = None
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette_colours
        painter.fillRect(self.rect(), _colour(palette.surface_sunken))

        if self._result is None or not self._result.layouts:
            painter.setPen(QPen(_colour(palette.text_faint)))
            painter.setFont(QFont("Heebo", 12))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "הרץ אופטימיזציה כדי לראות את תוכנית החיתוך"
            )
            painter.end()
            return

        geometry = self._geometry
        result = self._result
        longest = max(layout.stock_length for layout in result.layouts)
        usable_width = max(self.width() - geometry.left_margin - geometry.right_margin, 40)
        scale = usable_width / longest

        for index, layout in enumerate(result.layouts):
            y = geometry.top_margin + index * (geometry.bar_height + geometry.gap)
            self._paint_bar(painter, layout, index, y, scale, geometry)

        painter.end()

    def _paint_bar(
        self, painter: QPainter, layout: Any, index: int, y: float, scale: float,
        geometry: _BarGeometry,
    ) -> None:
        palette = self.palette_colours
        x0 = geometry.left_margin
        height = geometry.bar_height
        bar_width = layout.stock_length * scale

        if index == self._hover_bar:
            painter.fillRect(
                QRectF(0, y - 3, self.width(), height + 6), _colour(palette.surface_raised)
            )

        # The stock bar.
        painter.setBrush(QBrush(_colour(palette.bar_stock)))
        painter.setPen(QPen(_colour(palette.border_strong), 1))
        painter.drawRect(QRectF(x0, y, bar_width, height))

        # Label on the left.
        painter.setFont(QFont(MONO_FONTS[0], 9))
        painter.setPen(QPen(_colour(palette.text_muted)))
        kind = "שארית" if layout.is_remnant else "מוט"
        painter.drawText(QRectF(4, y, geometry.left_margin - 12, height),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                         ltr(f"{kind} {index + 1}\n{layout.stock_length:.0f}"))

        # Pieces.
        cursor = x0 + layout.trim_start * scale
        for position, placement in enumerate(layout.placements):
            width = placement.effective_length * scale
            colour = palette.bar_piece if position % 2 == 0 else palette.bar_piece_alt
            rect = QRectF(cursor, y + 2, max(width - 1.0, 1.0), height - 4)

            painter.setBrush(QBrush(_colour(colour, 210)))
            painter.setPen(QPen(_colour(colour), 1))
            painter.drawRect(rect)

            # Mitre angles shown as a slanted top edge, so the operator can see
            # at a glance which pieces are square and which are cut.
            self._paint_mitre(painter, rect, placement)

            if width > 46:
                painter.setFont(QFont(MONO_FONTS[0], 8))
                painter.setPen(QPen(_colour(palette.text_inverse if palette.mode.value == "light" else "#0f1216")))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                                 ltr(f"{placement.demand_key.length:.0f}"))
            cursor += width

        # Remnant.
        remnant = layout.remnant_length
        if remnant > 0.5:
            reusable = remnant >= self.min_remnant
            colour = palette.bar_remnant_good if reusable else palette.bar_remnant_scrap
            rect = QRectF(cursor, y + 2, max(remnant * scale - 1.0, 1.0), height - 4)
            painter.setBrush(QBrush(_colour(colour, 90)))
            painter.setPen(QPen(_colour(colour), 1, Qt.PenStyle.DashLine))
            painter.drawRect(rect)
            if remnant * scale > 52:
                painter.setFont(QFont(MONO_FONTS[0], 8))
                painter.setPen(QPen(_colour(colour)))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, ltr(f"{remnant:.0f}"))

        # Yield readout on the right.
        painter.setFont(QFont(MONO_FONTS[0], 9))
        painter.setPen(QPen(_colour(palette.text_faint)))
        painter.drawText(
            QRectF(x0 + bar_width + 4, y, geometry.right_margin + 40, height),
            Qt.AlignmentFlag.AlignVCenter,
            ltr(f"{layout.piece_count}"),
        )

    def _paint_mitre(self, painter: QPainter, rect: QRectF, placement: Any) -> None:
        """Shade the wedge a mitre cut removes, at each end of the piece."""
        palette = self.palette_colours
        for angle, at_left in (
            (placement.demand_key.angle_left, True),
            (placement.demand_key.angle_right, False),
        ):
            if abs(angle - 90.0) < 1e-6:
                continue
            inset = min(rect.height() * 0.9, rect.width() * 0.4)
            path = QPainterPath()
            if at_left:
                path.moveTo(rect.left(), rect.top())
                path.lineTo(rect.left() + inset, rect.top())
                path.lineTo(rect.left(), rect.bottom())
            else:
                path.moveTo(rect.right(), rect.top())
                path.lineTo(rect.right() - inset, rect.top())
                path.lineTo(rect.right(), rect.bottom())
            path.closeSubpath()
            painter.setBrush(QBrush(_colour(palette.surface_sunken, 190)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)


# --------------------------------------------------------------------------- #
# Clamp / machining view
# --------------------------------------------------------------------------- #

class ClampView(QWidget):
    """Shows a bar with its clamps and machining operations along its length.

    This is the view that makes a clamp collision obvious: operations are drawn
    above the bar, clamps below, and any overlap is highlighted in the danger
    colour with the interference span marked.
    """

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self.setObjectName("Canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(220)

        self._piece: Any = None
        self._collisions: list[Any] = []

    def set_piece(self, piece: Any, collisions: Sequence[Any] = ()) -> None:
        self._piece = piece
        self._collisions = list(collisions)
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette_colours
        painter.fillRect(self.rect(), _colour(palette.surface_sunken))

        if self._piece is None:
            painter.setPen(QPen(_colour(palette.text_faint)))
            painter.setFont(QFont("Heebo", 12))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "בנה תוכנית עיבוד כדי לראות את הקיבוע"
            )
            painter.end()
            return

        piece = self._piece
        left, right = 60.0, self.width() - 40.0
        scale = (right - left) / max(piece.length, 1e-6)
        bar_y = self.height() / 2.0 - 16
        bar_height = 32.0

        # The bar itself.
        painter.setBrush(QBrush(_colour(palette.draw_material, 150)))
        painter.setPen(QPen(_colour(palette.draw_material_edge), 1.5))
        painter.drawRect(QRectF(left, bar_y, piece.length * scale, bar_height))

        painter.setFont(QFont(MONO_FONTS[0], 9))
        painter.setPen(QPen(_colour(palette.text_faint)))
        painter.drawText(QPointF(left, bar_y - 46), ltr(f"{piece.label}  {piece.length:.0f} mm"))

        colliding = {c.operation.op_id for c in self._collisions}

        # Operations above the bar.
        for index, operation in enumerate(piece.operations):
            lo, hi = operation.extent_x()
            x = left + lo * scale
            width = max((hi - lo) * scale, 3.0)
            # Stagger rows so overlapping features stay readable.
            y = bar_y - 14 - (index % 3) * 11
            danger = operation.op_id in colliding
            colour = palette.danger if danger else palette.accent
            painter.setBrush(QBrush(_colour(colour, 200)))
            painter.setPen(QPen(_colour(colour), 1))
            painter.drawRect(QRectF(x, y, width, 7))
            painter.setPen(QPen(_colour(colour, 120), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(x + width / 2, y + 7), QPointF(x + width / 2, bar_y))

        # Clamps below the bar.
        plan = getattr(piece, "clamp_plan", None)
        clamps = plan.active_clamps() if plan is not None else []
        colliding_clamps = {c.clamp.id for c in self._collisions}
        for clamp in clamps:
            x = left + clamp.start * scale
            width = max(clamp.width * scale, 4.0)
            danger = clamp.id in colliding_clamps
            colour = palette.danger if danger else palette.success
            painter.setBrush(QBrush(_colour(colour, 130)))
            painter.setPen(QPen(_colour(colour), 1.4))
            painter.drawRect(QRectF(x, bar_y + bar_height + 6, width, 22))
            painter.setFont(QFont(MONO_FONTS[0], 8))
            painter.setPen(QPen(_colour(palette.text_muted)))
            painter.drawText(
                QRectF(x - 12, bar_y + bar_height + 30, width + 24, 14),
                Qt.AlignmentFlag.AlignCenter, ltr(clamp.id),
            )

        # Interference spans.
        for collision in self._collisions:
            lo, hi = collision.overlap
            x = left + lo * scale
            painter.setBrush(QBrush(_colour(palette.danger, 70)))
            painter.setPen(QPen(_colour(palette.danger), 1, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(x, bar_y - 8, max((hi - lo) * scale, 2.0), bar_height + 22))

        # Legend.
        painter.setFont(QFont("Heebo", 9))
        entries = [("עיבוד", palette.accent), ("מלחציים", palette.success)]
        if self._collisions:
            entries.append((f"{len(self._collisions)} collision(s)", palette.danger))
        x = left
        for label, colour in entries:
            painter.setBrush(QBrush(_colour(colour, 200)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(x, self.height() - 22, 10, 10))
            painter.setPen(QPen(_colour(palette.text_muted)))
            painter.drawText(QPointF(x + 15, self.height() - 13), ltr(label))
            x += 40 + len(label) * 6

        painter.end()


@dataclass
class _SheetGeometry:
    """Layout constants for the glass cutting map, in pixels."""

    gap: int = 22
    label_height: int = 22
    margin: int = 16
    #: A sheet is never drawn taller than this. Scaling purely to the widget's
    #: width fills the viewport with one plate, and a cutting plan is reviewed
    #: by comparing sheets against each other, not by admiring one of them.
    max_sheet_height: int = 300
    #: Below this a placed part gets no text; a squeezed label is worse than none.
    min_label_width: int = 54
    min_label_height: int = 26


class SheetView(QWidget):
    """Draws the sheet layouts from a 2D nesting result.

    Sheets are stacked vertically and each is scaled to the widget's width, so
    the map reads the way a stack of jumbo plates is worked through: one sheet
    at a time, top to bottom, in cutting order.

    The y axis is flipped on the way in. The nester's origin is the bottom-left
    corner of the usable area, because that is where a cutting table's origin
    is; Qt's is the top-left. Drawing the two the same way round would mirror
    every layout vertically, which looks plausible and is wrong.
    """

    def __init__(self, palette: Palette = DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.palette_colours = palette
        self.setObjectName("Canvas")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(280)
        self.setMouseTracking(True)

        self._result: Any = None
        self._geometry = _SheetGeometry()
        self._hover: int | None = None

    def set_result(self, result: Any) -> None:
        self._result = result
        self._resize_to_content()
        self.update()

    def _sheet_height(self, layout: Any, scale: float) -> float:
        return layout.stock.height * scale

    def _scale(self, layout: Any) -> float:
        geometry = self._geometry
        usable = max(self.width() - 2 * geometry.margin, 80)
        return min(
            usable / max(layout.stock.width, 1.0),
            geometry.max_sheet_height / max(layout.stock.height, 1.0),
        )

    def _resize_to_content(self) -> None:
        if self._result is None or not self._result.layouts:
            self.setMinimumHeight(280)
            return
        geometry = self._geometry
        total = geometry.margin
        for layout in self._result.layouts:
            total += (
                self._sheet_height(layout, self._scale(layout))
                + geometry.label_height
                + geometry.gap
            )
        self.setMinimumHeight(int(max(280, total)))

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._resize_to_content()

    def _sheet_at(self, y: float) -> int | None:
        if self._result is None:
            return None
        geometry = self._geometry
        cursor = geometry.margin
        for index, layout in enumerate(self._result.layouts):
            height = (
                self._sheet_height(layout, self._scale(layout)) + geometry.label_height
            )
            if cursor <= y <= cursor + height:
                return index
            cursor += height + geometry.gap
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._hover = self._sheet_at(event.position().y())
        self.update()

    def leaveEvent(self, event: Any) -> None:
        self._hover = None
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette_colours
        painter.fillRect(self.rect(), _colour(palette.surface_sunken))

        if self._result is None or not self._result.layouts:
            painter.setPen(QPen(_colour(palette.text_faint)))
            painter.setFont(QFont("Heebo", 12))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "שבץ את הזכוכית כדי לראות את מפות החיתוך",
            )
            painter.end()
            return

        geometry = self._geometry
        cursor = float(geometry.margin)
        for index, layout in enumerate(self._result.layouts):
            scale = self._scale(layout)
            height = self._sheet_height(layout, scale)
            self._paint_sheet(painter, layout, index, cursor, scale, height)
            cursor += height + geometry.label_height + geometry.gap

        painter.end()

    def _paint_sheet(
        self, painter: QPainter, layout: Any, index: int, top: float,
        scale: float, height: float,
    ) -> None:
        palette = self.palette_colours
        geometry = self._geometry
        width = layout.stock.width * scale
        # Centred: once the height cap governs the scale, a left-aligned plate
        # leaves the map hanging off one edge of a mostly empty panel.
        left = max(float(geometry.margin), (self.width() - width) / 2.0)

        if index == self._hover:
            painter.fillRect(
                QRectF(0, top - 4, self.width(), height + geometry.label_height + 8),
                _colour(palette.surface_raised),
            )

        # The bought sheet.
        painter.setBrush(QBrush(_colour(palette.bar_stock)))
        painter.setPen(QPen(_colour(palette.border_strong), 1))
        painter.drawRect(QRectF(left, top, width, height))

        # The usable area inside the edge trim, dashed because it is a boundary
        # rather than a cut.
        trim = layout.spec.edge_trim * scale
        if trim > 0.4:
            pen = QPen(_colour(palette.border_strong), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(
                QRectF(left + trim, top + trim, width - 2 * trim, height - 2 * trim)
            )

        def to_x(value: float) -> float:
            return left + trim + value * scale

        def to_y(value: float) -> float:
            """Usable-area y (up) to widget y (down)."""
            return top + height - trim - value * scale

        # Reusable off-cuts, drawn under the parts so a part always wins.
        for rect in layout.reusable_offcuts():
            box = QRectF(
                to_x(rect.x),
                to_y(rect.top),
                rect.width * scale,
                rect.height * scale,
            )
            painter.setBrush(QBrush(_colour(palette.warning, 40)))
            painter.setPen(QPen(_colour(palette.warning, 150), 1, Qt.PenStyle.DashLine))
            painter.drawRect(box)
            if box.width() > 60 and box.height() > 18:
                painter.setFont(QFont(MONO_FONTS[0], 8))
                painter.setPen(QPen(_colour(palette.warning)))
                painter.drawText(
                    box,
                    Qt.AlignmentFlag.AlignCenter,
                    ltr(f"{rect.width:.0f}x{rect.height:.0f}"),
                )

        for position, placement in enumerate(layout.placements):
            box = QRectF(
                to_x(placement.x),
                to_y(placement.top),
                max(placement.width * scale - 1.0, 1.0),
                max(placement.height * scale - 1.0, 1.0),
            )
            colour = palette.bar_piece if position % 2 == 0 else palette.bar_piece_alt
            painter.setBrush(QBrush(_colour(colour, 200)))
            painter.setPen(QPen(_colour(colour), 1))
            painter.drawRect(box)

            if (
                box.width() < geometry.min_label_width
                or box.height() < geometry.min_label_height
            ):
                continue
            painter.setFont(QFont(MONO_FONTS[0], 8))
            painter.setPen(
                QPen(
                    _colour(
                        palette.text_inverse
                        if palette.mode.value == "light"
                        else "#0f1216"
                    )
                )
            )
            turned = " R" if placement.rotated else ""
            painter.drawText(
                box,
                Qt.AlignmentFlag.AlignCenter,
                ltr(f"{placement.part.name}\n{placement.width:.0f}x{placement.height:.0f}{turned}"),
            )

        # Caption under the sheet.
        painter.setFont(QFont(MONO_FONTS[0], 9))
        painter.setPen(QPen(_colour(palette.text_muted)))
        stages = f", {layout.stages_used}-stage" if layout.stages_used else ""
        painter.drawText(
            QRectF(left, top + height + 2, width, geometry.label_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            ltr(
                f"sheet {index + 1}  {layout.stock.name}  "
                f"{layout.piece_count} pieces  {layout.yield_pct:.1f}% yield{stages}"
            ),
        )


__all__ = [
    "CanvasView", "SectionView", "ElevationView", "NestingView", "ClampView",
    "SheetView",
]
