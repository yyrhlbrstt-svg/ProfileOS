"""Conversion between the ProfileOS ring model and Shapely geometry.

Shapely gives robust boolean operations, buffering and distance queries, which
the wall-thickness scan, the clamp-collision checker and the plotting layer all
use. This module is the single place where the conversion happens, so the rest
of the codebase never has to think about ring winding or Shapely's validity
rules.

Shapely convention: a ``Polygon`` is ``(shell, [holes...])`` with the shell
counter-clockwise and holes clockwise. :func:`topology_to_polygon` produces
exactly that, and repairs self-intersections with a zero-width buffer when the
source drawing has small overlaps.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..core.errors import GeometryError
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .contour import Ring
from .primitives import Point
from .topology import Region, SectionTopology

_log = get_logger("geometry.shapely")


def _require_shapely() -> Any:
    try:
        import shapely  # noqa: F401
        from shapely import geometry

        return geometry
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise GeometryError(
            "shapely is required for this operation (pip install shapely)"
        ) from exc


def ring_to_linearring(ring: Ring, counter_clockwise: bool = True) -> Any:
    """Convert a :class:`Ring` to a ``shapely.geometry.LinearRing``."""
    geometry = _require_shapely()
    points = ring.oriented(counter_clockwise=counter_clockwise).points
    return geometry.LinearRing(points)


def region_to_polygon(region: Region, *, repair: bool = True) -> Any:
    """Convert one :class:`Region` into a ``shapely.geometry.Polygon``."""
    geometry = _require_shapely()
    oriented = region.oriented()
    polygon = geometry.Polygon(
        oriented.shell.points, [hole.points for hole in oriented.holes]
    )
    if repair and not polygon.is_valid:
        polygon = _repair(polygon)
    return polygon


@timed("geometry.to_shapely")
def topology_to_polygon(topology: SectionTopology, *, repair: bool = True) -> Any:
    """Convert a whole section into a ``Polygon`` or ``MultiPolygon``.

    A thermally-broken profile with two separate aluminium shells yields a
    ``MultiPolygon``; a monolithic profile yields a ``Polygon``.
    """
    geometry = _require_shapely()
    polygons = [region_to_polygon(region, repair=repair) for region in topology.regions]
    polygons = [p for p in polygons if not p.is_empty and p.area > 0]

    if not polygons:
        raise GeometryError("Section produced no valid Shapely geometry")
    if len(polygons) == 1:
        return polygons[0]

    result = geometry.MultiPolygon(polygons)
    if repair and not result.is_valid:
        result = _repair(result)
    return result


def _repair(geom: Any) -> Any:
    """Fix an invalid polygon with a zero-width buffer.

    ``buffer(0)`` resolves self-intersections and duplicate rings; it is the
    standard Shapely repair idiom and is safe here because profile outlines are
    small, well-scaled and non-pathological.
    """
    repaired = geom.buffer(0)
    if repaired.is_empty or not repaired.is_valid:
        try:
            from shapely.validation import make_valid

            repaired = make_valid(geom)
        except Exception as exc:  # noqa: BLE001
            raise GeometryError(f"Cannot repair invalid section geometry: {exc}") from exc
    _log.debug("Repaired invalid geometry (area %.3f -> %.3f)", geom.area, repaired.area)
    return repaired


def polygon_to_rings(polygon: Any) -> list[Ring]:
    """Convert a Shapely ``Polygon``/``MultiPolygon`` back into rings."""
    rings: list[Ring] = []
    parts = getattr(polygon, "geoms", None)
    if parts is None:
        parts = [polygon]
    for part in parts:
        if part.is_empty:
            continue
        rings.append(Ring(points=[(float(x), float(y)) for x, y in part.exterior.coords[:-1]]))
        for interior in part.interiors:
            rings.append(Ring(points=[(float(x), float(y)) for x, y in interior.coords[:-1]]))
    return rings


def normalise_orientation(polygon: Any) -> Any:
    """Return ``polygon`` with every shell counter-clockwise and every hole clockwise.

    Shapely preserves whatever winding it is handed, and ``buffer(0)`` — which
    :func:`_repair` uses — actively inverts it. Anything that reasons about
    which side of an edge the material lies on (the wall-thickness ray cast,
    Green's-theorem integration) must therefore normalise first rather than
    trust the incoming winding.
    """
    from shapely.geometry import MultiPolygon
    from shapely.geometry.polygon import orient

    parts = getattr(polygon, "geoms", None)
    if parts is None:
        return orient(polygon, 1.0)
    return MultiPolygon([orient(part, 1.0) for part in parts if not part.is_empty])


def polygon_rings_coordinates(polygon: Any) -> list[tuple[list[Point], list[list[Point]]]]:
    """Flatten a Shapely polygon into ``(shell_points, hole_point_lists)`` pairs.

    Rings come back in canonical winding — shells counter-clockwise, holes
    clockwise — so callers can rely on the material lying to the left of every
    ring's direction of travel.
    """
    polygon = normalise_orientation(polygon)
    parts = getattr(polygon, "geoms", None)
    if parts is None:
        parts = [polygon]

    out: list[tuple[list[Point], list[list[Point]]]] = []
    for part in parts:
        if part.is_empty or part.area <= 0:
            continue
        shell = [(float(x), float(y)) for x, y in part.exterior.coords[:-1]]
        holes = [
            [(float(x), float(y)) for x, y in interior.coords[:-1]] for interior in part.interiors
        ]
        out.append((shell, holes))
    return out


def points_to_polygon(shell: Sequence[Point], holes: Sequence[Sequence[Point]] = ()) -> Any:
    """Build a Shapely polygon from raw coordinate rings, repairing if needed."""
    geometry = _require_shapely()
    polygon = geometry.Polygon(shell, [list(h) for h in holes])
    if not polygon.is_valid:
        polygon = _repair(polygon)
    return polygon


def offset_polygon(polygon: Any, distance: float) -> Any:
    """Offset (buffer) a polygon; negative shrinks it.

    Uses mitred joins with a generous limit so right-angled profile corners stay
    sharp instead of being rounded off.
    """
    return polygon.buffer(distance, join_style=2, mitre_limit=10.0)


def polygon_area_and_centroid(polygon: Any) -> tuple[float, Point]:
    """Area and centroid of a Shapely polygon, as plain floats."""
    centroid = polygon.centroid
    return float(polygon.area), (float(centroid.x), float(centroid.y))


__all__ = [
    "ring_to_linearring",
    "region_to_polygon",
    "topology_to_polygon",
    "polygon_to_rings",
    "normalise_orientation",
    "polygon_rings_coordinates",
    "points_to_polygon",
    "offset_polygon",
    "polygon_area_and_centroid",
]
