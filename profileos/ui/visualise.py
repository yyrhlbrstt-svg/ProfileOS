"""The new window on the customer's own house.

A photo of the façade (from the phone's site tab, or any picture), four
corners dragged onto the opening, and the element as it will be made — its
real proportions, sashes and finish, drawn straight on — is set into the
photo in perspective. The customer sees their own wall with the shop's
window in it, which no price list can do. Nothing is invented: the picture
is the job's own design, rendered from the same model as the 3D page.

The result is saved into the job's files as a visualisation, where the
quotation and the WhatsApp message can take it from.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

#: Corners in the order they are dragged: top-right, top-left, bottom-left,
#: bottom-right — as a Hebrew reader goes round a window.
CORNER_NAMES = ("למעלה מימין", "למעלה משמאל", "למטה משמאל", "למטה מימין")


def front_image(build: Any, colour: str = "natural", width_px: int = 900) -> QImage:
    """The element straight on, shaded, on a transparent ground — its edges
    are the image's edges, so four corners place it exactly."""
    from PySide6.QtSvg import QSvgRenderer

    from ..viz3d import RenderOptions, ViewStyle, build_element_scene, finish_materials
    from ..viz3d.render import elevation_camera, render_svg

    opening = build.opening
    ratio = max(0.05, float(opening.height) / max(1.0, float(opening.width)))
    height_px = max(40, int(round(width_px * ratio)))
    scene = build_element_scene(build, style=ViewStyle(show_glass=True))
    options = RenderOptions(width=width_px, height=height_px, margin=0.0, background=None,
                            materials=finish_materials(colour), shadow=False)
    svg = render_svg(scene, elevation_camera(scene), options)
    renderer = QSvgRenderer(svg.encode("utf-8"))
    image = QImage(width_px, height_px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, width_px, height_px))
    painter.end()
    return image


def warp(front: QImage, corners: list[QPointF]) -> QTransform | None:
    """The perspective that takes the picture's rectangle onto the four corners."""
    source = QPolygonF([QPointF(front.width(), 0), QPointF(0, 0),
                        QPointF(0, front.height()), QPointF(front.width(), front.height())])
    transform = QTransform()
    if not QTransform.quadToQuad(source, QPolygonF(corners), transform):
        return None
    return transform


def composite(photo: QImage, front: QImage, corners: list[QPointF]) -> QImage:
    """The photo with the element set into the four corners, full size."""
    result = photo.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    transform = warp(front, corners)
    if transform is None:
        return result
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setTransform(transform)
    painter.drawImage(0, 0, front)
    painter.end()
    return result


def starting_corners(photo: QImage, build: Any) -> list[QPointF]:
    """A first guess in the middle of the photo, at the element's proportions."""
    opening = build.opening
    ratio = float(opening.height) / max(1.0, float(opening.width))
    width = photo.width() * 0.32
    height = width * ratio
    if height > photo.height() * 0.7:
        height = photo.height() * 0.7
        width = height / ratio
    left = (photo.width() - width) / 2
    top = (photo.height() - height) / 2
    return [QPointF(left + width, top), QPointF(left, top),
            QPointF(left, top + height), QPointF(left + width, top + height)]


class Stage(QWidget):
    """The photo, the element set into it, and four handles to drag."""

    changed = Signal()
    HANDLE = 9.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.photo: QImage | None = None
        self.front: QImage | None = None
        self.corners: list[QPointF] = []
        self._dragging = -1
        self.setMinimumSize(640, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    # The photo is shown fitted into the widget; corners live in photo pixels.
    def _fit(self) -> tuple[float, float, float]:
        if self.photo is None or self.photo.isNull():
            return 1.0, 0.0, 0.0
        scale = min(self.width() / self.photo.width(), self.height() / self.photo.height())
        return (scale, (self.width() - self.photo.width() * scale) / 2,
                (self.height() - self.photo.height() * scale) / 2)

    def _to_screen(self, point: QPointF) -> QPointF:
        scale, dx, dy = self._fit()
        return QPointF(dx + point.x() * scale, dy + point.y() * scale)

    def _to_photo(self, point: QPointF) -> QPointF:
        scale, dx, dy = self._fit()
        return QPointF((point.x() - dx) / scale, (point.y() - dy) / scale)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.photo is None or self.photo.isNull():
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "בחרו צילום של הבית — מהטלפון או מהמחשב")
            painter.end()
            return
        scale, dx, dy = self._fit()
        painter.drawImage(QRectF(dx, dy, self.photo.width() * scale,
                                 self.photo.height() * scale), self.photo)
        if self.front is not None and len(self.corners) == 4:
            transform = warp(self.front, [self._to_screen(c) for c in self.corners])
            if transform is not None:
                painter.save()
                painter.setTransform(transform)
                painter.drawImage(0, 0, self.front)
                painter.restore()
            accent = self.palette().color(self.palette().ColorRole.Highlight)
            painter.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
            painter.drawPolygon(QPolygonF([self._to_screen(c) for c in self.corners]))
            painter.setPen(QPen(QColor(accent), 2))
            painter.setBrush(self.palette().color(self.palette().ColorRole.Base))
            for corner in self.corners:
                painter.drawEllipse(self._to_screen(corner), self.HANDLE, self.HANDLE)
        painter.end()

    def _handle_at(self, point: QPointF) -> int:
        for index, corner in enumerate(self.corners):
            screen = self._to_screen(corner)
            if (screen - point).manhattanLength() <= self.HANDLE * 2.2:
                return index
        return -1

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        self._dragging = self._handle_at(event.position())

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if self._dragging >= 0 and self.photo is not None:
            point = self._to_photo(event.position())
            point.setX(min(max(point.x(), 0.0), float(self.photo.width())))
            point.setY(min(max(point.y(), 0.0), float(self.photo.height())))
            self.corners[self._dragging] = point
            self.update()
            self.changed.emit()
        else:
            hover = self._handle_at(event.position()) >= 0
            self.setCursor(Qt.CursorShape.SizeAllCursor if hover else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        self._dragging = -1


class VisualiseDialog(QDialog):
    """Pick a photo and an opening, drag the corners, keep the picture."""

    def __init__(self, parent: QWidget | None = None, *, builds: list[Any],
                 colour: str = "natural", job: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("הדמיה על הבית של הלקוח")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.resize(1100, 760)
        self.builds = list(builds)
        self.colour = colour
        self.job = job
        self.saved: Path | None = None

        layout = QVBoxLayout(self)
        title = QLabel("הדמיה על הבית של הלקוח")
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        hint = QLabel("בחרו צילום של החזית ופתח מהפרויקט, וגררו את ארבע הפינות אל הפתח "
                      "בצילום. החלון מצויר לפי התכנון — מידות, כנפיים וגוון.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        bar = QHBoxLayout()
        choose = QPushButton("בחירת צילום…")
        choose.clicked.connect(self.choose_photo)
        bar.addWidget(choose)
        self.opening = QComboBox()
        for build in self.builds:
            opening = build.opening
            self.opening.addItem(f"{opening.name or opening.element_id} — "
                                 f"⁦{opening.width:.0f} × {opening.height:.0f}⁩ מ״מ")
        self.opening.currentIndexChanged.connect(self._opening_chosen)
        bar.addWidget(self.opening, 1)
        layout.addLayout(bar)

        self.stage = Stage()
        layout.addWidget(self.stage, 1)
        self.problem = QLabel("")
        self.problem.setObjectName("FieldError")
        layout.addWidget(self.problem)

        buttons = QHBoxLayout()
        self.keep = QPushButton("שמירה בתיק הפרויקט")
        self.keep.setObjectName("Primary")
        self.keep.clicked.connect(self.save)
        self.keep.setEnabled(False)
        close = QPushButton("סגור")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.keep)
        buttons.addWidget(close)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        if not self.builds:
            self.problem.setText("אין עדיין פתחים בפרויקט — תכננו פתח בדף ״פתח״ ואז חזרו לכאן")
        else:
            self._opening_chosen(0)

    def choose_photo(self) -> None:
        from .widgets import documents_dir

        start = documents_dir()
        if self.job is not None:
            from ..projects.attachments import job_folder

            folder = job_folder(self.job.job_id)
            if folder.is_dir():
                start = folder
        name, _ = QFileDialog.getOpenFileName(
            self, "צילום של הבית", str(start), "תמונות (*.jpg *.jpeg *.png *.heic *.webp)")
        if name:
            self.load_photo(Path(name))

    def load_photo(self, path: Path) -> bool:
        image = QImage(str(path))
        if image.isNull():
            self.problem.setText("הצילום לא נפתח — נסו קובץ ‎JPG‎ או ‎PNG‎")
            return False
        self.problem.setText("")
        self.stage.photo = image
        self._place()
        return True

    def _opening_chosen(self, index: int) -> None:
        if not (0 <= index < len(self.builds)):
            return
        try:
            self.stage.front = front_image(self.builds[index], self.colour)
        except Exception as exc:  # noqa: BLE001 - said in the window
            self.problem.setText(f"לא ניתן לצייר את הפתח: {exc}")
            self.stage.front = None
        self._place()

    def _place(self) -> None:
        if self.stage.photo is not None and self.builds:
            if len(self.stage.corners) != 4:
                self.stage.corners = starting_corners(
                    self.stage.photo, self.builds[max(0, self.opening.currentIndex())])
        self.keep.setEnabled(self.stage.photo is not None and self.stage.front is not None
                             and self.job is not None)
        if self.job is None and self.stage.photo is not None:
            self.problem.setText("אפשר לראות את ההדמיה; כדי לשמור אותה פתחו פרויקט")
        self.stage.update()

    def picture(self) -> QImage | None:
        if self.stage.photo is None or self.stage.front is None:
            return None
        # Full resolution: the element is drawn at the photo's own scale.
        width = max(300, int(max(abs(self.stage.corners[0].x() - self.stage.corners[1].x()),
                                 abs(self.stage.corners[3].x() - self.stage.corners[2].x()))) * 2)
        build = self.builds[max(0, self.opening.currentIndex())]
        front = front_image(build, self.colour, min(width, 2400))
        return composite(self.stage.photo, front, self.stage.corners)

    def save(self) -> None:
        import tempfile

        from ..projects.attachments import AttachmentKind, attachments_for

        picture = self.picture()
        if picture is None or self.job is None:
            return
        build = self.builds[max(0, self.opening.currentIndex())]
        mark = build.opening.name or build.opening.element_id
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / f"{mark}-{stamp}.jpg"
            if not picture.save(str(source), "JPG", 90):
                self.problem.setText("ההדמיה לא נשמרה — לא ניתן לכתוב את הקובץ")
                return
            try:
                attachment = attachments_for(self.job.job_id).add(
                    source, kind=AttachmentKind.VISUALISATION,
                    caption=f"הדמיה של {mark} על הבית", element=mark)
            except Exception as exc:  # noqa: BLE001 - said in the window
                self.problem.setText(f"ההדמיה לא נשמרה: {exc}")
                return
        self.saved = attachments_for(self.job.job_id).path_of(attachment)
        self.problem.setText("")
        self.accept()


__all__ = ["CORNER_NAMES", "Stage", "VisualiseDialog", "composite", "front_image",
           "starting_corners", "warp"]
