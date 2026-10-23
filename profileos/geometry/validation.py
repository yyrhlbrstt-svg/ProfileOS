"""Geometry validation: wall thickness, integrity and plausibility checks.

Two things go wrong with imported profile drawings often enough to be worth
checking automatically:

* **Wall thickness.** Extruders have a minimum producible wall (roughly 1.2 mm
  for architectural aluminium). A section whose scan finds thinner walls is
  usually a units error, a mis-scaled block, or a drawing that includes gasket
  lips as if they were aluminium.
* **Envelope plausibility.** Architectural profiles are tens to a few hundred
  millimetres across. A section that comes out 0.07 mm or 7000 mm wide is a
  ``$INSUNITS`` problem, not a real profile.

The wall-thickness scan walks every boundary ring, and at each sample point
casts a ray along the inward normal, taking the distance to the first opposing
boundary as the local wall thickness. It is a ray-cast rather than a medial
axis because it is exact for the parallel-wall geometry that dominates extruded
profiles, and needs no skeletonisation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.config import GeometryDefaults, get_settings
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..models.results import WallThicknessReport
from .primitives import Point, distance
from .topology import SectionTopology

_log = get_logger("geometry.validation")

#: Architectural aluminium profiles fall comfortably inside this envelope [mm].
PLAUSIBLE_MIN_EXTENT = 5.0
PLAUSIBLE_MAX_EXTENT = 1200.0


@dataclass
class ValidationIssue:
    """One problem found while validating a section."""

    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    location: Point | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = f" at ({self.location[0]:.1f}, {self.location[1]:.1f})" if self.location else ""
        return f"[{self.severity}] {self.code}: {self.message}{where}"


@dataclass
class ValidationResult:
    """Aggregated outcome of every check run on a section."""

    issues: list[ValidationIssue] = field(default_factory=list)
    thickness: WallThicknessReport | None = None

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(
        self, severity: str, code: str, message: str, location: Point | None = None
    ) -> None:
        self.issues.append(ValidationIssue(severity, code, message, location))


# --------------------------------------------------------------------------- #
# Wall thickness
# --------------------------------------------------------------------------- #

def _sample_ring(coords: Sequence[Point], spacing: float) -> list[tuple[Point, Point]]:
    """Sample ``(point, inward_normal)`` pairs along a closed ring.

    The ring must follow the Shapely convention (exterior counter-clockwise,
    holes clockwise), under which the material always lies to the **left** of
    the direction of travel — so the inward normal is the left normal of the
    tangent for both kinds of ring.
    """
    samples: list[tuple[Point, Point]] = []
    count = len(coords)
    if count < 3:
        return samples

    for i in range(count):
        a = coords[i]
        b = coords[(i + 1) % count]
        edge_length = distance(a, b)
        if edge_length < 1e-9:
            continue

        tx, ty = (b[0] - a[0]) / edge_length, (b[1] - a[1]) / edge_length
        normal = (-ty, tx)  # left normal -> points into the material

        # At least one sample per edge, more on long edges.
        steps = max(1, int(math.ceil(edge_length / max(spacing, 1e-6))))
        for step in range(steps):
            # Offset to the middle of each sub-interval, avoiding the corners
            # where the normal is ambiguous.
            t = (step + 0.5) / steps
            samples.append((((a[0] + (b[0] - a[0]) * t), (a[1] + (b[1] - a[1]) * t)), normal))
    return samples


def _ray_hit_distance(boundary: Any, origin: Point, direction: Point, max_length: float) -> float | None:
    """Distance from ``origin`` to the first boundary crossing along ``direction``.

    Returns ``None`` when the ray leaves the section without hitting anything
    within ``max_length``.
    """
    from shapely.geometry import LineString, Point as ShapelyPoint

    # Start slightly off the boundary so the origin's own edge is not counted.
    epsilon = 1e-7
    start = (origin[0] + direction[0] * epsilon, origin[1] + direction[1] * epsilon)
    end = (origin[0] + direction[0] * max_length, origin[1] + direction[1] * max_length)

    hit = LineString([start, end]).intersection(boundary)
    if hit.is_empty:
        return None

    origin_point = ShapelyPoint(origin)
    best: float | None = None
    # The intersection may be points, line segments (ray grazing an edge), or a
    # collection of both; walk every coordinate it exposes.
    candidates = getattr(hit, "geoms", None) or [hit]
    for geom in candidates:
        coords = list(getattr(geom, "coords", []))
        if not coords:
            centroid = geom.centroid
            coords = [(centroid.x, centroid.y)]
        for x, y in coords:
            d = origin_point.distance(ShapelyPoint(x, y))
            if d > epsilon * 10 and (best is None or d < best):
                best = d
    return best


@timed("geometry.wall_thickness")
def measure_wall_thickness(
    polygon: Any,
    *,
    threshold: float = 1.2,
    spacing: float = 2.0,
    max_thickness: float = 200.0,
) -> WallThicknessReport:
    """Scan a section's walls and report the thickness distribution.

    Parameters
    ----------
    polygon:
        Shapely ``Polygon`` or ``MultiPolygon`` of the section.
    threshold:
        Thickness below which a sample is recorded as a thin spot [mm].
    spacing:
        Approximate distance between samples along the boundary [mm].
    max_thickness:
        Ray length; also the cap on a reported thickness [mm].
    """
    from .shapely_bridge import polygon_rings_coordinates

    boundary = polygon.boundary
    thicknesses: list[float] = []
    thin_spots: list[tuple[float, float, float]] = []

    for shell, holes in polygon_rings_coordinates(polygon):
        rings: list[Sequence[Point]] = [shell, *holes]
        for ring in rings:
            for point, normal in _sample_ring(ring, spacing):
                hit = _ray_hit_distance(boundary, point, normal, max_thickness)
                if hit is None:
                    continue
                thicknesses.append(hit)
                if hit < threshold:
                    thin_spots.append((point[0], point[1], hit))

    if not thicknesses:
        return WallThicknessReport(
            min_thickness=0.0,
            max_thickness=0.0,
            mean_thickness=0.0,
            threshold=threshold,
            sample_count=0,
        )

    return WallThicknessReport(
        min_thickness=min(thicknesses),
        max_thickness=max(thicknesses),
        mean_thickness=sum(thicknesses) / len(thicknesses),
        thin_spots=thin_spots,
        threshold=threshold,
        sample_count=len(thicknesses),
    )


# --------------------------------------------------------------------------- #
# Section validation
# --------------------------------------------------------------------------- #

@timed("geometry.validate")
def validate_section(
    topology: SectionTopology,
    polygon: Any | None = None,
    *,
    defaults: GeometryDefaults | None = None,
    check_thickness: bool = True,
) -> ValidationResult:
    """Run every integrity and plausibility check on a resolved section."""
    defaults = defaults or get_settings().geometry
    result = ValidationResult()

    # -- structural sanity ------------------------------------------------- #
    if not topology.regions:
        result.add("error", "no-regions", "The section contains no solid regions.")
        return result

    if topology.total_area <= 0:
        result.add("error", "zero-area", "The section has zero or negative net area.")
        return result

    for warning in topology.warnings:
        result.add("info", "topology", warning)

    # -- envelope plausibility --------------------------------------------- #
    min_x, min_y, max_x, max_y = topology.bounds()
    width, height = max_x - min_x, max_y - min_y
    for name, extent in (("width", width), ("height", height)):
        if extent < PLAUSIBLE_MIN_EXTENT:
            result.add(
                "warning",
                "implausible-size",
                f"Section {name} is {extent:.3f} mm, below the plausible minimum of "
                f"{PLAUSIBLE_MIN_EXTENT} mm. Check the drawing units ($INSUNITS).",
            )
        elif extent > PLAUSIBLE_MAX_EXTENT:
            result.add(
                "warning",
                "implausible-size",
                f"Section {name} is {extent:.1f} mm, above the plausible maximum of "
                f"{PLAUSIBLE_MAX_EXTENT} mm. Check the drawing units ($INSUNITS).",
            )

    # -- solidity ----------------------------------------------------------- #
    outer = topology.outer_region
    if outer.gross_area > 0:
        fill_ratio = topology.total_area / outer.gross_area
        if fill_ratio > 0.95 and topology.chamber_count == 0:
            result.add(
                "info",
                "solid-section",
                "The section is solid (no chambers found). This is expected for "
                "beads and cover caps, but unusual for a mullion or frame.",
            )
        elif fill_ratio < 0.05:
            result.add(
                "warning",
                "hollow-section",
                f"Only {fill_ratio * 100:.1f}% of the outline is material; the "
                "chamber detection may have mis-classified rings.",
            )

    # -- wall thickness ----------------------------------------------------- #
    if check_thickness:
        if polygon is None:
            from .shapely_bridge import topology_to_polygon

            polygon = topology_to_polygon(topology)
        report = measure_wall_thickness(
            polygon,
            threshold=defaults.min_wall_thickness_mm,
            spacing=max(1.0, min(width, height) / 40.0),
        )
        result.thickness = report
        if report.sample_count == 0:
            result.add(
                "warning", "thickness-unmeasurable", "Wall thickness could not be measured."
            )
        elif report.thin_spots:
            worst = min(report.thin_spots, key=lambda s: s[2])
            result.add(
                "warning",
                "thin-wall",
                f"{len(report.thin_spots)} sample(s) below the {report.threshold} mm minimum "
                f"wall thickness; thinnest is {worst[2]:.3f} mm.",
                location=(worst[0], worst[1]),
            )

    _log.info(
        "Validation: %d error(s), %d warning(s)", len(result.errors), len(result.warnings)
    )
    return result


def detect_thermal_break(topology: SectionTopology) -> bool:
    """Heuristic: does this section look thermally broken?

    A thermally-broken profile imports as two or more disconnected aluminium
    shells (the polyamide strips are usually drawn on a separate layer, or not
    at all). More than one depth-0 region is therefore a strong indicator.
    """
    return topology.is_multi_part


__all__ = [
    "PLAUSIBLE_MIN_EXTENT",
    "PLAUSIBLE_MAX_EXTENT",
    "ValidationIssue",
    "ValidationResult",
    "measure_wall_thickness",
    "validate_section",
    "detect_thermal_break",
]
