"""Planar geometric primitives.

Pure-Python, dependency-free helpers used everywhere in the geometry pipeline:
arc flattening, polygon area and centroid, orientation tests, point-in-polygon
and vertex cleaning. Keeping these free of numpy/shapely means the contour
reconstruction can run before any conversion happens, and stays debuggable.

All functions take and return plain ``(x, y)`` float tuples.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]

#: Default coordinate tolerance in millimetres. Architectural profiles are
#: drawn to 0.01 mm at best, so 1 micron is comfortably below drawing noise.
EPS = 1e-9


# --------------------------------------------------------------------------- #
# Vector helpers
# --------------------------------------------------------------------------- #

def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def scale(a: Point, factor: float) -> Point:
    return (a[0] * factor, a[1] * factor)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    """2D scalar cross product ``a x b``."""
    return a[0] * b[1] - a[1] * b[0]


def length(a: Point) -> float:
    return math.hypot(a[0], a[1])


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def distance_sq(a: Point, b: Point) -> float:
    """Squared distance — avoids a sqrt in hot comparison loops."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return dx * dx + dy * dy


def normalise(a: Point) -> Point:
    mag = length(a)
    if mag < EPS:
        return (0.0, 0.0)
    return (a[0] / mag, a[1] / mag)


def rotate(a: Point, angle_rad: float, origin: Point = (0.0, 0.0)) -> Point:
    """Rotate ``a`` about ``origin`` counter-clockwise."""
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    dx, dy = a[0] - origin[0], a[1] - origin[1]
    return (origin[0] + dx * cos_a - dy * sin_a, origin[1] + dx * sin_a + dy * cos_a)


def almost_equal(a: Point, b: Point, tol: float = 1e-6) -> bool:
    return distance_sq(a, b) <= tol * tol


# --------------------------------------------------------------------------- #
# Arc flattening
# --------------------------------------------------------------------------- #

def segments_for_arc(radius: float, sweep_rad: float, sagitta: float) -> int:
    """Number of chords needed to approximate an arc within ``sagitta``.

    The sagitta (maximum deviation between chord and arc) of a chord spanning
    ``delta`` radians on radius ``R`` is ``R (1 - cos(delta/2))``. Inverting for
    ``delta`` and dividing the total sweep gives the segment count.
    """
    sweep = abs(sweep_rad)
    if radius <= EPS or sweep <= EPS:
        return 1
    if sagitta <= 0 or sagitta >= radius:
        return max(1, int(math.ceil(sweep / (math.pi / 8))))

    # delta = 2 * acos(1 - s/R); clamp guards against float drift at s ~ R.
    ratio = max(-1.0, min(1.0, 1.0 - sagitta / radius))
    delta = 2.0 * math.acos(ratio)
    if delta <= EPS:
        return max(1, int(math.ceil(sweep / (math.pi / 8))))
    return max(1, int(math.ceil(sweep / delta)))


def flatten_arc(
    centre: Point,
    radius: float,
    start_angle: float,
    end_angle: float,
    *,
    counter_clockwise: bool = True,
    sagitta: float = 0.02,
    include_start: bool = True,
    include_end: bool = True,
) -> list[Point]:
    """Approximate a circular arc by a chord polyline.

    Angles are in radians. The sweep is taken in the requested direction, so a
    start of 350 deg and an end of 10 deg counter-clockwise sweeps 20 deg, not
    340 deg.
    """
    sweep = end_angle - start_angle
    if counter_clockwise:
        while sweep <= 0:
            sweep += 2.0 * math.pi
    else:
        while sweep >= 0:
            sweep -= 2.0 * math.pi

    # A full circle expressed as identical start/end angles.
    if abs(sweep) < EPS:
        sweep = 2.0 * math.pi if counter_clockwise else -2.0 * math.pi

    count = segments_for_arc(radius, sweep, sagitta)
    points: list[Point] = []
    first = 0 if include_start else 1
    last = count if include_end else count - 1
    for i in range(first, last + 1):
        angle = start_angle + sweep * (i / count)
        points.append((centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle)))
    return points


def flatten_bulge(start: Point, end: Point, bulge: float, sagitta: float = 0.02) -> list[Point]:
    """Expand a DXF bulge segment into points from ``start`` to ``end``.

    The returned list always begins with ``start`` and ends with ``end``. A zero
    bulge yields the two endpoints unchanged.
    """
    if abs(bulge) < 1e-12:
        return [start, end]

    from ..models.profile import bulge_to_arc  # local import avoids a cycle

    try:
        centre, radius, start_angle, end_angle = bulge_to_arc(start, end, bulge)
    except ValueError:
        return [start, end]

    points = flatten_arc(
        centre,
        radius,
        start_angle,
        end_angle,
        counter_clockwise=bulge > 0,
        sagitta=sagitta,
    )
    # Pin the endpoints exactly: trigonometry can drift them by ~1e-13, and
    # exact endpoints are what lets the contour chainer match nodes.
    points[0] = start
    points[-1] = end
    return points


def flatten_vertices(
    vertices: Sequence[tuple[float, float, float]],
    *,
    closed: bool = True,
    sagitta: float = 0.02,
) -> list[Point]:
    """Flatten ``(x, y, bulge)`` triples into a plain point list.

    For a closed ring the final vertex is joined back to the first (honouring
    its bulge) and the duplicate closing point is not repeated.
    """
    if not vertices:
        return []
    if len(vertices) == 1:
        return [(vertices[0][0], vertices[0][1])]

    points: list[Point] = []
    count = len(vertices)
    limit = count if closed else count - 1
    for i in range(limit):
        x1, y1, bulge = vertices[i]
        x2, y2, _ = vertices[(i + 1) % count]
        segment = flatten_bulge((x1, y1), (x2, y2), bulge, sagitta)
        # Drop the segment's first point; it duplicates the previous endpoint.
        points.extend(segment if i == 0 else segment[1:])

    if closed and points and almost_equal(points[0], points[-1], 1e-9):
        points.pop()
    return points


# --------------------------------------------------------------------------- #
# Polygon measures
# --------------------------------------------------------------------------- #

def signed_area(points: Sequence[Point]) -> float:
    """Shoelace signed area. Positive for counter-clockwise winding."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    x_prev, y_prev = points[-1]
    for x, y in points:
        total += x_prev * y - x * y_prev
        x_prev, y_prev = x, y
    return total * 0.5


def area(points: Sequence[Point]) -> float:
    """Unsigned polygon area."""
    return abs(signed_area(points))


def is_counter_clockwise(points: Sequence[Point]) -> bool:
    return signed_area(points) > 0.0


def ensure_orientation(points: Sequence[Point], counter_clockwise: bool = True) -> list[Point]:
    """Return the ring wound in the requested direction."""
    pts = list(points)
    if is_counter_clockwise(pts) != counter_clockwise:
        pts.reverse()
    return pts


def centroid(points: Sequence[Point]) -> Point:
    """Area centroid of a simple polygon.

    Falls back to the vertex average for degenerate (zero-area) rings, which
    keeps callers from having to special-case slivers.
    """
    a = signed_area(points)
    if abs(a) < EPS:
        n = len(points)
        if n == 0:
            return (0.0, 0.0)
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)

    cx = cy = 0.0
    x_prev, y_prev = points[-1]
    for x, y in points:
        factor = x_prev * y - x * y_prev
        cx += (x_prev + x) * factor
        cy += (y_prev + y) * factor
        x_prev, y_prev = x, y
    scale_factor = 1.0 / (6.0 * a)
    return (cx * scale_factor, cy * scale_factor)


def perimeter(points: Sequence[Point], closed: bool = True) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for i in range(len(points) - 1):
        total += distance(points[i], points[i + 1])
    if closed:
        total += distance(points[-1], points[0])
    return total


def bounding_box(points: Iterable[Point]) -> tuple[float, float, float, float]:
    """``(min_x, min_y, max_x, max_y)``; raises on an empty input."""
    iterator = iter(points)
    try:
        x0, y0 = next(iterator)
    except StopIteration as exc:
        raise ValueError("bounding_box() of an empty point set") from exc
    min_x = max_x = x0
    min_y = max_y = y0
    for x, y in iterator:
        if x < min_x:
            min_x = x
        elif x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        elif y > max_y:
            max_y = y
    return (min_x, min_y, max_x, max_y)


# --------------------------------------------------------------------------- #
# Point location
# --------------------------------------------------------------------------- #

def point_in_polygon(point: Point, polygon: Sequence[Point], tolerance: float = 1e-9) -> bool:
    """Winding-number containment test, inclusive of the boundary.

    The winding number is robust for self-touching rings where the classic
    even-odd ray cast is ambiguous — a real concern for extruded profiles whose
    chambers can meet at a single point.
    """
    if len(polygon) < 3:
        return False
    if point_on_boundary(point, polygon, tolerance):
        return True

    px, py = point
    winding = 0
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if y1 <= py:
            if y2 > py and (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) > 0:
                winding += 1
        else:
            if y2 <= py and (x2 - x1) * (py - y1) - (px - x1) * (y2 - y1) < 0:
                winding -= 1
    return winding != 0


def point_on_boundary(point: Point, polygon: Sequence[Point], tolerance: float = 1e-9) -> bool:
    """True when ``point`` lies on any edge of the ring, within ``tolerance``."""
    n = len(polygon)
    for i in range(n):
        if distance_point_to_segment(point, polygon[i], polygon[(i + 1) % n]) <= tolerance:
            return True
    return False


def distance_point_to_segment(point: Point, a: Point, b: Point) -> float:
    """Shortest distance from ``point`` to the segment ``a``-``b``."""
    ab = sub(b, a)
    ab_len_sq = dot(ab, ab)
    if ab_len_sq < EPS:
        return distance(point, a)
    t = dot(sub(point, a), ab) / ab_len_sq
    t = max(0.0, min(1.0, t))
    projection = (a[0] + ab[0] * t, a[1] + ab[1] * t)
    return distance(point, projection)


def polygon_contains_polygon(outer: Sequence[Point], inner: Sequence[Point]) -> bool:
    """True when every vertex of ``inner`` lies inside or on ``outer``.

    Used to build the outer-boundary/hole hierarchy. Rings produced by DXF
    reconstruction never cross each other, so testing the vertices is sufficient
    and much cheaper than a full polygon-in-polygon predicate.
    """
    return all(point_in_polygon(p, outer) for p in inner)


# --------------------------------------------------------------------------- #
# Cleaning and simplification
# --------------------------------------------------------------------------- #

def dedupe(points: Sequence[Point], tolerance: float = 1e-7) -> list[Point]:
    """Drop consecutive duplicate points (and a repeated closing point)."""
    if not points:
        return []
    out: list[Point] = [points[0]]
    tol_sq = tolerance * tolerance
    for p in points[1:]:
        if distance_sq(p, out[-1]) > tol_sq:
            out.append(p)
    if len(out) > 1 and distance_sq(out[0], out[-1]) <= tol_sq:
        out.pop()
    return out


def remove_collinear(points: Sequence[Point], tolerance: float = 1e-9) -> list[Point]:
    """Remove vertices that lie on the straight line between their neighbours.

    Cuts the vertex count of flattened DXF geometry substantially without
    changing the shape, which speeds up every downstream polygon operation.
    """
    n = len(points)
    if n < 3:
        return list(points)

    out: list[Point] = []
    for i in range(n):
        prev_pt = points[(i - 1) % n]
        curr = points[i]
        next_pt = points[(i + 1) % n]
        v1 = sub(curr, prev_pt)
        v2 = sub(next_pt, curr)
        # Compare the triangle's doubled area against the longer edge, so the
        # tolerance behaves like a distance rather than an area.
        scale_ref = max(length(v1), length(v2), EPS)
        if abs(cross(v1, v2)) / scale_ref > tolerance:
            out.append(curr)
    # Never collapse a ring to nothing; fall back to the original.
    return out if len(out) >= 3 else list(points)


def simplify(points: Sequence[Point], tolerance: float) -> list[Point]:
    """Ramer-Douglas-Peucker simplification of an open polyline."""
    if tolerance <= 0 or len(points) < 3:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack: list[tuple[int, int]] = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        max_dist = -1.0
        index = start
        a, b = points[start], points[end]
        for i in range(start + 1, end):
            d = distance_point_to_segment(points[i], a, b)
            if d > max_dist:
                max_dist, index = d, i
        if max_dist > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))

    return [p for p, k in zip(points, keep) if k]


__all__ = [
    "Point",
    "EPS",
    "add",
    "sub",
    "scale",
    "dot",
    "cross",
    "length",
    "distance",
    "distance_sq",
    "normalise",
    "rotate",
    "almost_equal",
    "segments_for_arc",
    "flatten_arc",
    "flatten_bulge",
    "flatten_vertices",
    "signed_area",
    "area",
    "is_counter_clockwise",
    "ensure_orientation",
    "centroid",
    "perimeter",
    "bounding_box",
    "point_in_polygon",
    "point_on_boundary",
    "distance_point_to_segment",
    "polygon_contains_polygon",
    "dedupe",
    "remove_collinear",
    "simplify",
]
