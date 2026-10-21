"""The live 3D view: the opening rendered in real time, the way it will be built.

The SVG render in :mod:`profileos.viz3d.render` is for paper. This is for the
screen and for the customer sitting across the desk: a physically based
renderer (Qt Quick 3D) with anodised and powder-coated finishes that read as
metal, glass that reflects, shadows on a studio floor, a camera that turns
with the mouse, and leaves that swing open on their own hinges — a casement
on its side, a top-hung light outwards, a slider along its track — because
that is what the customer is actually buying.

The geometry is the same one that produces the cut list: every member is the
real section swept along the real length. Nothing is dressed up for the
picture.

A machine with no 3D graphics (a remote desktop with no GPU, a headless
test run) gets :attr:`Viewer3D.supported` ``False``; the pages then keep the
SVG render, which needs nothing.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QStackedLayout, QWidget

from ..core.logging_setup import get_logger

_log = get_logger("ui.viewer3d")

QML_FILE = Path(__file__).with_name("qml") / "Viewer3D.qml"

#: The finishes a shop actually sells, in the words it sells them in.
FINISHES: tuple[tuple[str, str], ...] = (
    ("natural", "אלומיניום טבעי"),
    ("silver", "כסף מבריק"),
    ("anthracite", "אנתרציט RAL 7016"),
    ("black", "שחור RAL 9005"),
    ("white", "לבן RAL 9016"),
    ("bronze", "ברונזה"),
    ("champagne", "שמפניה"),
)

#: Preset cameras, named the way people ask for them.
VIEWS: tuple[tuple[str, str], ...] = (
    ("angle", "הדמיה"),
    ("front", "חזית"),
    ("side", "צד"),
    ("inside", "מבפנים"),
    ("above", "מלמעלה"),
    ("detail", "פרט פינה"),
)

#: How far each kind of leaf swings when fully open, in degrees.
_SWING = {"turn": 78.0, "pivot": 82.0, "top": 32.0, "bottom": 14.0}


def _floor_fade() -> str:
    """A soft round fade, opaque in the middle and gone at the rim, as a PNG.

    Written once to the cache folder. It is what lets the floor catch the
    window's shadow without drawing a horizon across the picture.
    """
    import tempfile

    from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient

    path = Path(tempfile.gettempdir()) / "profileos-ui" / "floor-fade.png"
    if path.is_file():
        return str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 512
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        gradient = QRadialGradient(size / 2, size / 2, size / 2)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
        gradient.setColorAt(0.35, QColor(255, 255, 255, 235))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter = QPainter(image)
        painter.fillRect(0, 0, size, size, gradient)
        painter.end()
        image.save(str(path))
    except Exception:  # noqa: BLE001 - a hard floor edge is not worth failing for
        return ""
    return str(path)


def supported() -> bool:
    """Whether this machine can draw real-time 3D at all."""
    if QGuiApplication.platformName() in ("offscreen", "minimal", "vnc", ""):
        return False
    try:
        from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

        return QQuickWindow.graphicsApi() is not QSGRendererInterface.GraphicsApi.Software
    except Exception:  # noqa: BLE001 - no Quick at all
        return False


def _leaf_kind(opening_type: str) -> str:
    return {
        "casement": "turn", "tilt_turn": "turn", "door": "turn",
        "top_hung": "top", "bottom_hung": "bottom",
        "sliding": "slide", "lift_slide": "slide",
        "pivot": "pivot",
    }.get(opening_type, "fixed")


def flat_shaded(meshes: Iterable[Any], offset: tuple[float, float, float]):
    """Positions and face normals, three vertices per triangle, as float32.

    Every extrusion face is flat, so each triangle gets its own normal: the
    edges of a profile stay crisp instead of being smeared round by normals
    averaged across a 90° corner. Returns ``(array, minimum, maximum)`` or
    ``None`` for no triangles.
    """
    import numpy as np

    chunks = []
    for mesh in meshes:
        if not mesh.triangles:
            continue
        vertices = np.asarray(mesh.vertices, dtype=np.float64) - np.asarray(offset)
        corners = vertices[np.asarray(mesh.triangles, dtype=np.int64)]  # (n, 3, 3)
        normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 1e-12)
        normals = np.repeat(normals[:, None, :], 3, axis=1)
        chunks.append(np.concatenate([corners, normals], axis=2).reshape(-1, 6))
    if not chunks:
        return None
    data = np.concatenate(chunks).astype(np.float32)
    return data, data[:, :3].min(axis=0), data[:, :3].max(axis=0)


def _geometry(meshes: list[Any], offset: tuple[float, float, float]) -> Any:
    """A GPU-ready geometry for a group of meshes, or ``None`` if empty."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QVector3D
    from PySide6.QtQuick3D import QQuick3DGeometry

    packed = flat_shaded(meshes, offset)
    if packed is None:
        return None
    data, low, high = packed

    geometry = QQuick3DGeometry()
    geometry.setVertexData(QByteArray(data.tobytes()))
    geometry.setStride(24)
    geometry.setPrimitiveType(QQuick3DGeometry.PrimitiveType.Triangles)
    attribute = QQuick3DGeometry.Attribute
    geometry.addAttribute(attribute.Semantic.PositionSemantic, 0, attribute.ComponentType.F32Type)
    geometry.addAttribute(attribute.Semantic.NormalSemantic, 12, attribute.ComponentType.F32Type)
    geometry.setBounds(QVector3D(*map(float, low)), QVector3D(*map(float, high)))
    return geometry


class Viewer3D(QWidget):
    """A real-time view of one opening, with the controls a showroom needs."""

    #: Emitted with a Hebrew message when 3D cannot be shown here.
    unavailable = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, background: str = "#F5F5F7") -> None:
        super().__init__(parent)
        self._stack = QStackedLayout(self)
        self._fallback = QLabel(
            "תצוגת תלת-ממד חיה צריכה כרטיס מסך עם תמיכה בגרפיקה תלת-ממדית.\n"
            "במחשב הזה מוצגת ההדמיה הרגילה."
        )
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback.setObjectName("Hint")
        self._stack.addWidget(self._fallback)
        self._quick = None
        self._root = None
        #: Geometry objects must outlive the frame that draws them.
        self._keep: list[Any] = []
        self._scene_key: tuple | None = None
        self.supported = supported()
        if self.supported:
            self._start(background)

    # -- set-up ------------------------------------------------------------------ #
    def _start(self, background: str) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtQuickWidgets import QQuickWidget

        quick = QQuickWidget(self)
        quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        quick.setClearColor(QColor(background))
        quick.setSource(QUrl.fromLocalFile(str(QML_FILE)))
        if quick.status() is not QQuickWidget.Status.Ready:
            problems = "; ".join(error.toString() for error in quick.errors())
            _log.error("3D view did not load: %s", problems)
            self.supported = False
            quick.deleteLater()
            return
        quick.setMinimumHeight(360)
        self._quick = quick
        self._root = quick.rootObject()
        self._root.setProperty("background", QColor(background))
        fade = _floor_fade()
        if fade:
            self._root.setProperty("floorFade", QUrl.fromLocalFile(fade))
        self._stack.addWidget(quick)
        self._stack.setCurrentWidget(quick)

    # -- what is shown ---------------------------------------------------------- #
    def show_build(self, build: Any, *, keep_camera: bool = True) -> bool:
        """Model one built opening and show it. Returns whether it is shown."""
        if not self.supported or self._root is None:
            return False
        from ..viz3d import ViewStyle, build_element_scene

        try:
            scene = build_element_scene(build, style=ViewStyle(show_glass=True))
        except Exception:  # noqa: BLE001 - a half-typed form keeps the last picture
            _log.debug("3D scene not built", exc_info=True)
            return False
        self.show_scene(scene, keep_camera=keep_camera)
        return True

    def show_scene(self, scene: Any, *, keep_camera: bool = True) -> None:
        if self._root is None:
            return
        low, high = scene.bounds
        centre = tuple((low[i] + high[i]) / 2.0 for i in range(3))
        size = tuple(high[i] - low[i] for i in range(3))

        frame, glass, panels, gaskets = [], [], [], []
        leaves: dict[str, dict[str, Any]] = {}
        for mesh in scene.meshes:
            leaf = (mesh.metadata or {}).get("leaf")
            kind = _leaf_kind(leaf["type"]) if leaf else "fixed"
            if leaf and kind != "fixed":
                group = leaves.setdefault(leaf["cell"], {"leaf": leaf, "alu": [], "glass": []})
                group["glass" if mesh.material == "glass" else "alu"].append(mesh)
                continue
            {"glass": glass, "panel": panels, "gasket": gaskets}.get(mesh.material, frame).append(mesh)

        keep: list[Any] = []

        def make(meshes: list[Any]) -> Any:
            geometry = _geometry(meshes, centre)
            if geometry is not None:
                keep.append(geometry)
            return geometry

        leaf_items = []
        for group in leaves.values():
            leaf = group["leaf"]
            kind = _leaf_kind(leaf["type"])
            x, y, w, h = leaf["rect"]
            x -= centre[0]
            y -= centre[1]
            z = leaf["z"] - centre[2]
            hinge = leaf["hinge"]
            px, py = x + w / 2.0, y + h / 2.0
            sign = 1.0
            if kind == "turn":
                if hinge == "right":
                    px, sign = x + w, -1.0
                else:
                    px, sign = x, 1.0
            elif kind == "top":
                py = y + h
            elif kind == "bottom":
                py = y
            # A slider runs towards the middle of the element, over its
            # neighbour — the way a two-pane slider actually opens.
            direction = -1.0 if (x + w / 2.0) > 0 else 1.0
            leaf_items.append({
                "kind": kind, "px": px, "py": py, "pz": z, "sign": sign,
                "angle": _SWING.get(kind, 0.0),
                "slide": direction * w * 0.82,
                "alu": make(group["alu"]), "glass": make(group["glass"]),
            })

        root = self._root
        root.setProperty("frameGeometry", make(frame))
        root.setProperty("glassGeometry", make(glass))
        root.setProperty("panelGeometry", make(panels))
        root.setProperty("gasketGeometry", make(gaskets))
        root.setProperty("wallGeometry", make(self._wall(scene, low, high)))
        root.setProperty("leaves", leaf_items)
        root.setProperty("modelHeight", float(size[1]))
        root.setProperty("modelDepth", float(max(size[2], 40.0)))

        # Far enough back that the whole opening fits with a margin, at the
        # camera's 32° field of view — kept when only a dimension changed,
        # so typing does not throw the view around.
        extent = max(size[0], size[1], 600.0)
        radius = extent * 0.62 / math.tan(math.radians(16.0)) * 1.08
        key = (round(size[0], -1), round(size[1], -1))
        root.setProperty("radius", float(radius))
        if not keep_camera or self._scene_key is None:
            QTimerSingleShot(lambda: self.set_view("angle"))
        self._scene_key = key
        self._keep = keep

    @staticmethod
    def _wall(scene: Any, low: tuple, high: tuple) -> list[Any]:
        """A plastered wall around the opening, to see it in place."""
        from ..viz3d.extrude import plate

        margin, thickness = 700.0, 220.0
        width = high[0] - low[0]
        height = high[1] - low[1]
        z = (low[2] + high[2]) / 2.0 - thickness / 2.0 + 20.0
        pieces = [
            # (width, height, centre x, centre y)
            (width + 2 * margin, margin, (low[0] + high[0]) / 2.0, high[1] + margin / 2.0),
            (margin, height, low[0] - margin / 2.0, (low[1] + high[1]) / 2.0),
            (margin, height, high[0] + margin / 2.0, (low[1] + high[1]) / 2.0),
        ]
        # The parapet under the opening, down to the floor the scene stands on.
        parapet = 900.0
        pieces.append((width, parapet, (low[0] + high[0]) / 2.0, low[1] - parapet / 2.0))
        pieces[1] = (margin, height + parapet, low[0] - margin / 2.0,
                     (low[1] - parapet + high[1]) / 2.0)
        pieces[2] = (margin, height + parapet, high[0] + margin / 2.0,
                     (low[1] - parapet + high[1]) / 2.0)
        return [
            plate(w, h, thickness, (cx, cy, z), name="wall", material="wall")
            for w, h, cx, cy in pieces
        ]

    # -- how it is shown ----------------------------------------------------------- #
    def set_finish(self, name: str) -> None:
        if self._root is not None:
            self._root.setProperty("finish", name)

    def set_open(self, fraction: float) -> None:
        if self._root is not None:
            self._root.setProperty("openAmount", max(0.0, min(1.0, float(fraction))))

    def set_show_glass(self, show: bool) -> None:
        if self._root is not None:
            self._root.setProperty("showGlass", bool(show))

    def set_show_wall(self, show: bool) -> None:
        if self._root is not None:
            self._root.setProperty("showWall", bool(show))

    def set_turntable(self, on: bool) -> None:
        if self._root is not None:
            self._root.setProperty("turntable", bool(on))

    def set_view(self, name: str) -> None:
        if self._root is not None:
            from PySide6.QtCore import QMetaObject, Q_ARG

            QMetaObject.invokeMethod(self._root, "setView", Q_ARG("QVariant", name))

    def set_background(self, colour: str, floor: str = "#FFFFFF") -> None:
        if self._root is None:
            return
        from PySide6.QtGui import QColor

        self._root.setProperty("background", QColor(colour))
        self._root.setProperty("floorColour", QColor(floor))
        if self._quick is not None:
            self._quick.setClearColor(QColor(colour))

    def image(self) -> Any:
        """What is on screen now, as an image — for a quotation or WhatsApp."""
        if self._quick is None:
            return None
        return self._quick.grabFramebuffer()


def QTimerSingleShot(callback: Any) -> None:  # noqa: N802 - mirrors QTimer.singleShot
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, callback)


__all__ = ["FINISHES", "VIEWS", "Viewer3D", "flat_shaded", "supported"]
