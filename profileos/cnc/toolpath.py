"""Toolpath generation.

Most native formats in this market are *feature*-oriented: an NCX file names a
drill and its diameter, and the control's own cycle generator turns that into
motion. ISO G-code is the exception — it needs explicit moves — so toolpaths
are generated only for the drivers that require them.

The generator produces a flat list of :class:`Move` records in the bar-local
frame (X along the bar, Y across the face, Z into the material). A driver maps
that frame onto its machine's axes.

Cutter radius compensation
--------------------------
Two routes are supported:

* **Control-side** (``G41``/``G42``): the path is emitted on the nominal
  contour and the control offsets it by the tool radius in its table. Preferred
  where available, because the operator can tune the offset at the machine to
  correct for tool wear.
* **Path-side** (:func:`offset_polyline`): ProfileOS offsets the path itself,
  for controls with no compensation support. The offset is a straight
  miter-joined parallel curve, which is exact for the polyline geometry that
  profile machining produces.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterator, Sequence

from ..core.errors import CncError
from ..models.machines import Tool
from .operations import (
    CircularPocket,
    Compensation,
    Contour,
    Drill,
    EndNotch,
    Operation,
    RectangularPocket,
    Slot,
)

Point2D = tuple[float, float]


class MoveType(StrEnum):
    RAPID = "rapid"  # G0
    LINEAR = "linear"  # G1
    ARC_CW = "arc_cw"  # G2
    ARC_CCW = "arc_ccw"  # G3
    DRILL_CYCLE = "drill_cycle"  # G81/G83
    DWELL = "dwell"  # G4


@dataclass
class Move:
    """One motion command in the bar-local frame."""

    move_type: MoveType
    x: float | None = None
    y: float | None = None
    z: float | None = None
    feed: float | None = None
    #: Arc centre offsets relative to the start point (I/J words).
    i: float | None = None
    j: float | None = None
    #: Cycle parameters for canned drilling.
    retract_z: float | None = None
    peck: float | None = None
    dwell_s: float | None = None
    comment: str | None = None

    @property
    def is_cutting(self) -> bool:
        return self.move_type is not MoveType.RAPID


@dataclass
class Toolpath:
    """The moves for one operation, plus the context a driver needs."""

    operation: Operation
    moves: list[Move] = field(default_factory=list)
    tool_number: int | None = None
    tool_diameter: float = 0.0
    compensation: Compensation = Compensation.NONE
    spindle_speed: int = 0
    feed: float = 0.0

    def __iter__(self) -> Iterator[Move]:
        return iter(self.moves)

    def __len__(self) -> int:
        return len(self.moves)

    def add(self, move: Move) -> "Toolpath":
        self.moves.append(move)
        return self

    def cutting_length(self) -> float:
        """Total distance travelled while cutting [mm], for cycle-time estimates."""
        total = 0.0
        last: tuple[float, float, float] | None = None
        for move in self.moves:
            if move.x is None and move.y is None and move.z is None:
                continue
            current = (
                move.x if move.x is not None else (last[0] if last else 0.0),
                move.y if move.y is not None else (last[1] if last else 0.0),
                move.z if move.z is not None else (last[2] if last else 0.0),
            )
            if last is not None and move.is_cutting:
                total += math.dist(last, current)
            last = current
        return total

    def estimated_time_s(self, rapid_rate: float = 20000.0) -> float:
        """Rough cycle time [s], ignoring acceleration and tool changes."""
        seconds = 0.0
        last: tuple[float, float, float] | None = None
        for move in self.moves:
            current = (
                move.x if move.x is not None else (last[0] if last else 0.0),
                move.y if move.y is not None else (last[1] if last else 0.0),
                move.z if move.z is not None else (last[2] if last else 0.0),
            )
            if last is not None:
                distance = math.dist(last, current)
                rate = rapid_rate if move.move_type is MoveType.RAPID else (move.feed or self.feed)
                if rate > 0:
                    seconds += distance / rate * 60.0
            last = current
        return seconds


# --------------------------------------------------------------------------- #
# Path geometry
# --------------------------------------------------------------------------- #

def offset_polyline(
    points: Sequence[Point2D], distance: float, *, closed: bool = False
) -> list[Point2D]:
    """Offset a polyline by ``distance`` (positive = left of travel).

    Each segment is shifted along its normal and consecutive segments are
    intersected to form a mitred join. Near-parallel segments fall back to the
    shifted endpoint, which avoids the intersection shooting off to infinity.
    """
    if abs(distance) < 1e-12 or len(points) < 2:
        return list(points)

    pts = list(points)
    if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()

    count = len(pts)
    segments: list[tuple[Point2D, Point2D]] = []
    limit = count if closed else count - 1
    for i in range(limit):
        a, b = pts[i], pts[(i + 1) % count]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-12:
            continue
        # Left normal of the direction of travel.
        nx, ny = -dy / length * distance, dx / length * distance
        segments.append(((a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny)))

    if not segments:
        return list(points)

    result: list[Point2D] = []
    if not closed:
        result.append(segments[0][0])

    for index in range(len(segments) if closed else len(segments) - 1):
        current = segments[index]
        following = segments[(index + 1) % len(segments)]
        joint = _intersect(current, following)
        result.append(joint if joint is not None else current[1])

    if not closed:
        result.append(segments[-1][1])
    elif result:
        # For a closed path the first join is the last corner; rotate it front.
        result = result[-1:] + result[:-1]

    return result


def _intersect(
    first: tuple[Point2D, Point2D], second: tuple[Point2D, Point2D]
) -> Point2D | None:
    """Intersection of two infinite lines, or ``None`` when near-parallel."""
    (x1, y1), (x2, y2) = first
    (x3, y3), (x4, y4) = second
    denominator = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(denominator) < 1e-9:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / denominator
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def depth_passes(total_depth: float, step_down: float | None) -> list[float]:
    """Depths of successive passes, ending exactly on ``total_depth``.

    Passes are equalised rather than leaving a thin final skim: cutting
    3 x 2.0 mm is kinder to the tool than 2 x 2.5 mm + 1 x 0.1 mm.
    """
    if step_down is None or step_down <= 0 or step_down >= total_depth:
        return [total_depth]
    count = math.ceil(total_depth / step_down)
    increment = total_depth / count
    return [increment * (i + 1) for i in range(count)]


def rectangle_path(
    centre: Point2D,
    length: float,
    width: float,
    *,
    rotation: float = 0.0,
    corner_radius: float = 0.0,
) -> list[Point2D]:
    """Corner points of a rectangle, with radiused corners approximated."""
    half_l, half_w = length / 2.0, width / 2.0
    if corner_radius <= 0:
        local = [(-half_l, -half_w), (half_l, -half_w), (half_l, half_w), (-half_l, half_w)]
    else:
        r = min(corner_radius, half_l, half_w)
        local = []
        corners = [
            (half_l - r, half_w - r, 0.0),
            (-half_l + r, half_w - r, math.pi / 2),
            (-half_l + r, -half_w + r, math.pi),
            (half_l - r, -half_w + r, 3 * math.pi / 2),
        ]
        for cx, cy, start in corners:
            for step in range(5):
                angle = start + (math.pi / 2) * (step / 4)
                local.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))

    angle = math.radians(rotation)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return [
        (centre[0] + lx * cos_a - ly * sin_a, centre[1] + lx * sin_a + ly * cos_a)
        for lx, ly in local
    ]


def zigzag_clearing(
    centre: Point2D,
    length: float,
    width: float,
    tool_diameter: float,
    *,
    stepover_ratio: float = 0.6,
) -> list[Point2D]:
    """A zigzag raster clearing a rectangular pocket.

    Runs along the pocket's long axis so the tool spends most of its time in a
    straight cut rather than turning, and steps over by a fraction of the tool
    diameter each pass.
    """
    radius = tool_diameter / 2.0
    inner_l = max(length / 2.0 - radius, 0.0)
    inner_w = max(width / 2.0 - radius, 0.0)
    if inner_l <= 0 or inner_w <= 0:
        return [centre]

    stepover = max(tool_diameter * stepover_ratio, 0.1)
    passes = max(1, math.ceil((2 * inner_w) / stepover))
    path: list[Point2D] = []
    for index in range(passes + 1):
        y = -inner_w + (2 * inner_w) * (index / passes)
        left_to_right = index % 2 == 0
        x_start, x_end = (-inner_l, inner_l) if left_to_right else (inner_l, -inner_l)
        path.append((centre[0] + x_start, centre[1] + y))
        path.append((centre[0] + x_end, centre[1] + y))
    return path


def spiral_clearing(
    centre: Point2D, diameter: float, tool_diameter: float, *, stepover_ratio: float = 0.6
) -> list[Point2D]:
    """An outward spiral clearing a circular pocket."""
    radius = diameter / 2.0 - tool_diameter / 2.0
    if radius <= 0:
        return [centre]

    stepover = max(tool_diameter * stepover_ratio, 0.1)
    turns = max(1, math.ceil(radius / stepover))
    steps_per_turn = 32
    path: list[Point2D] = []
    for step in range(turns * steps_per_turn + 1):
        fraction = step / (turns * steps_per_turn)
        r = radius * fraction
        angle = 2 * math.pi * turns * fraction
        path.append((centre[0] + r * math.cos(angle), centre[1] + r * math.sin(angle)))
    # Finish with a full circle at the final radius to clean the wall.
    for step in range(steps_per_turn + 1):
        angle = 2 * math.pi * step / steps_per_turn
        path.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
    return path


# --------------------------------------------------------------------------- #
# Operation -> toolpath
# --------------------------------------------------------------------------- #

def generate_toolpath(
    operation: Operation,
    tool: Tool | None = None,
    *,
    safe_z: float = 25.0,
    feed: float | None = None,
    plunge_feed: float | None = None,
    spindle_speed: int | None = None,
    use_control_compensation: bool = False,
) -> Toolpath:
    """Lower one IR operation into explicit moves.

    Raises
    ------
    CncError
        The operation type has no toolpath representation.
    """
    diameter = tool.diameter if tool else (operation.required_tool_diameter() or 6.0)
    cutting_feed = feed if feed is not None else (tool.feed_mm_min if tool else 1200.0)
    plunge = plunge_feed if plunge_feed is not None else (
        tool.effective_plunge_feed if tool else cutting_feed * 0.4
    )
    speed = spindle_speed if spindle_speed is not None else (tool.spindle_rpm if tool else 18000)

    path = Toolpath(
        operation=operation,
        tool_number=operation.tool_number,
        tool_diameter=diameter,
        spindle_speed=speed,
        feed=cutting_feed,
    )

    if isinstance(operation, Drill):
        _drill_path(path, operation, safe_z, plunge)
    elif isinstance(operation, RectangularPocket):
        _rectangular_pocket_path(path, operation, diameter, safe_z, cutting_feed, plunge)
    elif isinstance(operation, CircularPocket):
        _circular_pocket_path(path, operation, diameter, safe_z, cutting_feed, plunge)
    elif isinstance(operation, Slot):
        _slot_path(path, operation, diameter, safe_z, cutting_feed, plunge)
    elif isinstance(operation, Contour):
        _contour_path(
            path, operation, diameter, safe_z, cutting_feed, plunge, use_control_compensation
        )
    elif isinstance(operation, EndNotch):
        _notch_path(path, operation, diameter, safe_z, cutting_feed, plunge)
    else:
        raise CncError(
            "Operation has no toolpath representation",
            op_type=operation.op_type.value,
            op_id=operation.op_id,
        )

    path.add(Move(MoveType.RAPID, z=safe_z, comment="retract"))
    return path


def _drill_path(path: Toolpath, op: Drill, safe_z: float, plunge: float) -> None:
    path.add(Move(MoveType.RAPID, x=op.x, y=op.y, comment=f"drill {op.diameter:g} mm"))
    path.add(Move(MoveType.RAPID, z=safe_z))
    path.add(
        Move(
            MoveType.DRILL_CYCLE,
            x=op.x,
            y=op.y,
            z=-op.depth,
            feed=plunge,
            retract_z=safe_z,
            peck=op.peck_depth,
        )
    )


def _rectangular_pocket_path(
    path: Toolpath,
    op: RectangularPocket,
    diameter: float,
    safe_z: float,
    feed: float,
    plunge: float,
) -> None:
    clearing = zigzag_clearing((op.x, op.y), op.length, op.width, diameter)
    walls = rectangle_path(
        (op.x, op.y),
        op.length - diameter,
        op.width - diameter,
        rotation=op.rotation,
        corner_radius=max(op.corner_radius - diameter / 2.0, 0.0),
    )

    for depth in depth_passes(op.depth, op.step_down):
        path.add(Move(MoveType.RAPID, x=clearing[0][0], y=clearing[0][1]))
        path.add(Move(MoveType.LINEAR, z=-depth, feed=plunge, comment=f"plunge {depth:.2f}"))
        for x, y in clearing[1:]:
            path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
        # Finish the walls after clearing, so the pocket ends up to size.
        for x, y in walls:
            path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
        path.add(Move(MoveType.LINEAR, x=walls[0][0], y=walls[0][1], feed=feed))
        path.add(Move(MoveType.RAPID, z=safe_z))


def _circular_pocket_path(
    path: Toolpath,
    op: CircularPocket,
    diameter: float,
    safe_z: float,
    feed: float,
    plunge: float,
) -> None:
    spiral = spiral_clearing((op.x, op.y), op.diameter, diameter)
    for depth in depth_passes(op.depth, op.step_down):
        path.add(Move(MoveType.RAPID, x=spiral[0][0], y=spiral[0][1]))
        path.add(Move(MoveType.LINEAR, z=-depth, feed=plunge))
        for x, y in spiral[1:]:
            path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
        path.add(Move(MoveType.RAPID, z=safe_z))


def _slot_path(
    path: Toolpath, op: Slot, diameter: float, safe_z: float, feed: float, plunge: float
) -> None:
    # A tool narrower than the slot must make two passes, one along each wall.
    offset = (op.width - diameter) / 2.0
    centre_line = [(op.x1, op.y1), (op.x2, op.y2)]
    lines = (
        [centre_line]
        if offset <= 1e-6
        else [offset_polyline(centre_line, offset), offset_polyline(centre_line, -offset)]
    )

    for depth in depth_passes(op.depth, op.step_down):
        for line in lines:
            path.add(Move(MoveType.RAPID, x=line[0][0], y=line[0][1]))
            path.add(Move(MoveType.LINEAR, z=-depth, feed=plunge))
            for x, y in line[1:]:
                path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
            path.add(Move(MoveType.RAPID, z=safe_z))


def _contour_path(
    path: Toolpath,
    op: Contour,
    diameter: float,
    safe_z: float,
    feed: float,
    plunge: float,
    use_control_compensation: bool,
) -> None:
    points = list(op.points)
    if op.compensation is not Compensation.NONE and not use_control_compensation:
        # Offset the path ourselves: left compensation puts the tool to the
        # left of travel, which is a positive offset in our convention.
        sign = 1.0 if op.compensation is Compensation.LEFT else -1.0
        points = offset_polyline(points, sign * diameter / 2.0, closed=op.closed)

    path.compensation = op.compensation if use_control_compensation else Compensation.NONE

    for depth in depth_passes(op.depth, op.step_down):
        path.add(Move(MoveType.RAPID, x=points[0][0], y=points[0][1]))
        path.add(Move(MoveType.LINEAR, z=-depth, feed=plunge))
        for x, y in points[1:]:
            path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
        if op.closed:
            path.add(Move(MoveType.LINEAR, x=points[0][0], y=points[0][1], feed=feed))
        path.add(Move(MoveType.RAPID, z=safe_z))


def _notch_path(
    path: Toolpath,
    op: EndNotch,
    diameter: float,
    safe_z: float,
    feed: float,
    plunge: float,
) -> None:
    lo, hi = op.extent_x()
    width = op.width if op.width > 0 else diameter * 4.0
    centre = ((lo + hi) / 2.0, 0.0)
    clearing = zigzag_clearing(centre, hi - lo, width, diameter)

    for depth in depth_passes(op.depth, None):
        path.add(Move(MoveType.RAPID, x=clearing[0][0], y=clearing[0][1]))
        path.add(Move(MoveType.LINEAR, z=-depth, feed=plunge, comment="notch"))
        for x, y in clearing[1:]:
            path.add(Move(MoveType.LINEAR, x=x, y=y, feed=feed))
        path.add(Move(MoveType.RAPID, z=safe_z))


__all__ = [
    "MoveType",
    "Move",
    "Toolpath",
    "offset_polyline",
    "depth_passes",
    "rectangle_path",
    "zigzag_clearing",
    "spiral_clearing",
    "generate_toolpath",
]
