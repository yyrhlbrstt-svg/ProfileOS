"""Triangle meshes, and the arithmetic that keeps them honest.

A presentation render is the one output a customer looks at, so it is also the
one place where a mistake is invisible until it is embarrassing: a frame member
a few millimetres too deep, a sash that intersects its own frame, glass floating
in front of the rebate. None of that shows up in a cut list.

The defence is that a mesh built here can be measured. :func:`volume` applies
the divergence theorem to the triangles, so the solid produced by sweeping a
section can be checked against ``area × length`` — a number that came from a
completely different calculation. If the two agree the sweep is right, and if
they disagree the render is wrong in a way that would otherwise have shipped.

Conventions
-----------
* Right-handed coordinates: **X** across the element, **Y** up, **Z** out of
  the wall towards the viewer.
* Triangles wind counter-clockwise seen from outside, so the normal points out.
  Back-face culling and lighting both depend on it, and a mesh with mixed
  winding renders as though it has holes.
* Millimetres throughout, as everywhere else in the suite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from ..core.errors import ProfileOSError


class MeshError(ProfileOSError):
    """A mesh could not be built or is not a usable solid."""


Vec3 = tuple[float, float, float]
Vec2 = tuple[float, float]


# --------------------------------------------------------------------------- #
# Vector arithmetic
# --------------------------------------------------------------------------- #
def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalise(a: Vec3) -> Vec3:
    magnitude = length(a)
    if magnitude < 1e-12:
        raise MeshError("Cannot normalise a zero-length vector")
    return (a[0] / magnitude, a[1] / magnitude, a[2] / magnitude)


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


# --------------------------------------------------------------------------- #
# Polygon triangulation
# --------------------------------------------------------------------------- #
def signed_area(ring: Sequence[Vec2]) -> float:
    """Twice the signed area, positive for a counter-clockwise ring."""
    total = 0.0
    for index in range(len(ring)):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


#: Cross products of millimetre coordinates run to about 1e6, so this is far
#: below any real geometry and far above floating-point noise.
_ON_EDGE = 1e-9


def _point_in_triangle(p: Vec2, a: Vec2, b: Vec2, c: Vec2) -> bool:
    """Strictly inside — a point *on* an edge does not count.

    Bridging a hole into the outer ring deliberately creates two coincident
    edges and repeats two vertices. Those repeats lie exactly on the bridge,
    so a containment test that accepts on-edge points rejects every candidate
    ear near the bridge and the triangulation stalls with the polygon still
    unclipped.
    """
    d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
    d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
    d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
    return (
        (d1 > _ON_EDGE and d2 > _ON_EDGE and d3 > _ON_EDGE)
        or (d1 < -_ON_EDGE and d2 < -_ON_EDGE and d3 < -_ON_EDGE)
    )


def _same_point(a: Vec2, b: Vec2, tolerance: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def _bridge_holes(outer: list[Vec2], holes: Sequence[Sequence[Vec2]]) -> list[Vec2]:
    """Cut each hole into the outer ring, producing one simple polygon.

    Ear clipping only handles simple polygons. The standard way to admit holes
    is to join each one to the outer boundary with a pair of coincident edges —
    a zero-width bridge — which leaves the shape simple and the area unchanged.

    The bridge is taken from the hole's rightmost vertex to the nearest outer
    vertex. That is not the textbook visibility test, and on a pathological
    section it can produce a bridge that crosses the boundary; the triangulator
    checks its own output area afterwards, so such a case is caught rather than
    rendered wrong.
    """
    ring = list(outer)
    for hole in holes:
        if len(hole) < 3:
            continue
        hole_list = list(hole)
        # Holes wind opposite to the outer ring so the bridge does not fold.
        if signed_area(hole_list) > 0:
            hole_list.reverse()

        start = max(range(len(hole_list)), key=lambda i: hole_list[i][0])
        anchor = hole_list[start]
        nearest = min(
            range(len(ring)),
            key=lambda i: (ring[i][0] - anchor[0]) ** 2 + (ring[i][1] - anchor[1]) ** 2,
        )
        rotated = hole_list[start:] + hole_list[:start]
        ring = (
            ring[: nearest + 1]
            + rotated
            + [rotated[0]]
            + ring[nearest:]
        )
    return ring


def triangulate(
    outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]] = ()
) -> list[tuple[int, int, int]]:
    """Ear-clip a polygon into triangles, returning indices into the fused ring.

    The returned indices address the ring that :func:`fuse_rings` produces for
    the same inputs, so callers keep one vertex list for the cap and its sides.

    Raises
    ------
    MeshError
        The triangles do not add up to the polygon's own area, which means the
        input was not a simple polygon once its holes were bridged in.
    """
    ring = _bridge_holes(list(outer), holes)
    if len(ring) < 3:
        raise MeshError("A polygon needs at least three points", points=len(ring))

    # Work counter-clockwise so a convex corner has positive cross product.
    indices = list(range(len(ring)))
    if signed_area(ring) < 0:
        indices.reverse()

    triangles: list[tuple[int, int, int]] = []
    guard = 0
    while len(indices) > 3:
        guard += 1
        if guard > len(ring) * len(ring) + 64:
            raise MeshError(
                "Ear clipping made no progress; the outline is self-intersecting "
                "or has a repeated point",
                points=len(ring),
            )
        clipped = False
        for position in range(len(indices)):
            previous = indices[position - 1]
            current = indices[position]
            following = indices[(position + 1) % len(indices)]
            a, b, c = ring[previous], ring[current], ring[following]

            # Convex corner?
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 0:
                continue
            # No other vertex inside the candidate ear? A vertex that merely
            # coincides with one of the corners is a bridge repeat, not an
            # obstruction.
            if any(
                _point_in_triangle(ring[other], a, b, c)
                for other in indices
                if other not in (previous, current, following)
                and not _same_point(ring[other], a)
                and not _same_point(ring[other], b)
                and not _same_point(ring[other], c)
            ):
                continue

            triangles.append((previous, current, following))
            indices.pop(position)
            clipped = True
            break
        if not clipped:
            raise MeshError(
                "No ear found; the outline is self-intersecting", points=len(ring)
            )
    triangles.append((indices[0], indices[1], indices[2]))

    produced = sum(
        abs(signed_area([ring[i], ring[j], ring[k]])) for i, j, k in triangles
    )
    expected = abs(signed_area(ring))
    # Compare against whichever is larger. Testing only ``expected`` lets a
    # bow-tie through: its two lobes wind opposite ways, so its signed area is
    # zero, the check is skipped, and two overlapping triangles are returned as
    # though they were a polygon.
    magnitude = max(expected, produced)
    if magnitude > 1e-9 and abs(produced - expected) > max(1e-6, magnitude * 1e-6):
        raise MeshError(
            "Triangulation does not cover the polygon; the outline is "
            "self-intersecting, or its holes cross the boundary once bridged in",
            polygon_area=round(expected, 4),
            triangle_area=round(produced, 4),
        )
    return triangles


def fuse_rings(
    outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]] = ()
) -> list[Vec2]:
    """The single ring the triangulation indices refer to."""
    return _bridge_holes(list(outer), holes)


# --------------------------------------------------------------------------- #
# Meshes
# --------------------------------------------------------------------------- #
@dataclass
class Mesh:
    """Vertices and triangles, with a name and a material."""

    name: str = "mesh"
    vertices: list[Vec3] = field(default_factory=list)
    triangles: list[tuple[int, int, int]] = field(default_factory=list)
    material: str = "aluminium"
    #: Free-form: the profile id, the element it belongs to, the pane's mark.
    metadata: dict[str, object] = field(default_factory=dict)

    def add_vertex(self, point: Vec3) -> int:
        self.vertices.append(point)
        return len(self.vertices) - 1

    def add_triangle(self, a: int, b: int, c: int) -> None:
        if a == b or b == c or a == c:
            return  # degenerate: two corners in the same place
        self.triangles.append((a, b, c))

    def add_quad(self, a: int, b: int, c: int, d: int) -> None:
        self.add_triangle(a, b, c)
        self.add_triangle(a, c, d)

    def extend(self, other: "Mesh") -> "Mesh":
        offset = len(self.vertices)
        self.vertices.extend(other.vertices)
        self.triangles.extend(
            (a + offset, b + offset, c + offset) for a, b, c in other.triangles
        )
        return self

    def triangle_points(self) -> Iterator[tuple[Vec3, Vec3, Vec3]]:
        for a, b, c in self.triangles:
            yield self.vertices[a], self.vertices[b], self.vertices[c]

    # -- measurement --------------------------------------------------------- #
    @property
    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.vertices:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    @property
    def centre(self) -> Vec3:
        low, high = self.bounds
        return ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2)

    def surface_area(self) -> float:
        total = 0.0
        for a, b, c in self.triangle_points():
            total += length(cross(sub(b, a), sub(c, a))) / 2.0
        return total

    def volume(self) -> float:
        """Enclosed volume by the divergence theorem.

        Each triangle contributes the signed volume of the tetrahedron it makes
        with the origin; over a closed surface those cancel except for the
        interior. A positive result means the winding is outward, so the sign
        is itself a check on the mesh.
        """
        total = 0.0
        for a, b, c in self.triangle_points():
            total += dot(a, cross(b, c)) / 6.0
        return total

    def is_closed(self, tolerance: float = 1e-7) -> bool:
        """True when every edge is shared by exactly two triangles.

        An open mesh has a volume but the number means nothing, so this is what
        makes the volume check trustworthy.
        """
        edges: dict[tuple[int, int], int] = {}
        for a, b, c in self.triangles:
            for start, end in ((a, b), (b, c), (c, a)):
                key = (min(start, end), max(start, end))
                edges[key] = edges.get(key, 0) + 1
        return all(count == 2 for count in edges.values())

    def transformed(self, offset: Vec3) -> "Mesh":
        return Mesh(
            name=self.name,
            vertices=[add(v, offset) for v in self.vertices],
            triangles=list(self.triangles),
            material=self.material,
            metadata=dict(self.metadata),
        )

    def summary(self) -> dict[str, object]:
        low, high = self.bounds
        return {
            "name": self.name,
            "material": self.material,
            "vertices": len(self.vertices),
            "triangles": len(self.triangles),
            "closed": self.is_closed(),
            "volume_mm3": round(self.volume(), 1),
            "size": [round(high[i] - low[i], 1) for i in range(3)],
        }


@dataclass
class Scene:
    """Everything to be drawn, and where the eye should start."""

    name: str = "element"
    meshes: list[Mesh] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def add(self, mesh: Mesh) -> Mesh:
        self.meshes.append(mesh)
        return mesh

    @property
    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.meshes:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        lows, highs = zip(*(mesh.bounds for mesh in self.meshes if mesh.vertices))
        return (
            (min(p[0] for p in lows), min(p[1] for p in lows), min(p[2] for p in lows)),
            (max(p[0] for p in highs), max(p[1] for p in highs), max(p[2] for p in highs)),
        )

    @property
    def centre(self) -> Vec3:
        low, high = self.bounds
        return ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2)

    @property
    def size(self) -> Vec3:
        low, high = self.bounds
        return (high[0] - low[0], high[1] - low[1], high[2] - low[2])

    @property
    def triangle_count(self) -> int:
        return sum(len(mesh.triangles) for mesh in self.meshes)

    def by_material(self) -> dict[str, list[Mesh]]:
        grouped: dict[str, list[Mesh]] = {}
        for mesh in self.meshes:
            grouped.setdefault(mesh.material, []).append(mesh)
        return grouped

    def aluminium_volume(self) -> float:
        """Total metal volume [mm^3] — cross-checkable against the cut list."""
        return sum(
            mesh.volume() for mesh in self.meshes if mesh.material == "aluminium"
        )

    def summary(self) -> dict[str, object]:
        size = self.size
        return {
            "name": self.name,
            "meshes": len(self.meshes),
            "triangles": self.triangle_count,
            "size_mm": [round(value, 1) for value in size],
            "materials": {
                material: len(meshes) for material, meshes in sorted(self.by_material().items())
            },
            "aluminium_volume_mm3": round(self.aluminium_volume(), 1),
        }


def merge(meshes: Iterable[Mesh], name: str = "merged") -> Mesh:
    result = Mesh(name=name)
    for mesh in meshes:
        result.extend(mesh)
    return result


__all__ = [
    "MeshError",
    "Vec2",
    "Vec3",
    "add",
    "sub",
    "scale",
    "dot",
    "cross",
    "length",
    "normalise",
    "lerp",
    "signed_area",
    "triangulate",
    "fuse_rings",
    "Mesh",
    "Scene",
    "merge",
]
