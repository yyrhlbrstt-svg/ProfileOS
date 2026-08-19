"""Geometry engine: DXF ingestion, contour reconstruction and topology.

The high-level entry point is :func:`load_section`, which runs the whole
pipeline and returns a :class:`LoadedSection` carrying the resolved topology,
the Shapely geometry, and a validation report::

    from profileos.geometry import load_section

    section = load_section("MB70_mullion.dxf")
    print(section.topology.total_area, section.validation.ok)

To go straight to a :class:`~profileos.models.profile.ProfileDefinition`, use
:func:`profile_from_dxf`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import GeometryDefaults, get_settings
from ..core.errors import ContourError, GeometryError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..models.profile import (
    OuterDimensions,
    ProfileDefinition,
    ProfileRole,
    ProfileSectionGeometry,
    Vertex,
)
from ..models.results import GeometryReport
from .contour import ContourChainer, Ring, Segment, rings_from_segments
from .dxf_reader import DxfExtraction, DxfReader, DxfReadOptions, read_dxf
from .features import (
    DetectedFeature,
    FeatureKind,
    FeatureSpec,
    Pocket,
    ProfileFeatureReport,
    ThermalStrip,
    describe_section,
    detect_features,
    features_for_profile,
    features_for_section,
    find_pockets,
    paint_area_per_metre,
)
from .primitives import Point, bounding_box
from .shapely_bridge import (
    points_to_polygon,
    polygon_rings_coordinates,
    polygon_to_rings,
    topology_to_polygon,
)
from .topology import Region, SectionTopology, resolve_topology
from .validation import (
    ValidationResult,
    detect_thermal_break,
    measure_wall_thickness,
    validate_section,
)

_log = get_logger("geometry")


@dataclass
class LoadedSection:
    """A fully processed cross-section."""

    topology: SectionTopology
    polygon: Any
    report: GeometryReport
    validation: ValidationResult
    rings: list[Ring] = field(default_factory=list)
    source: str | None = None

    @property
    def area(self) -> float:
        """Net material area [mm^2]."""
        return float(self.polygon.area)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return tuple(float(v) for v in self.polygon.bounds)  # type: ignore[return-value]

    @property
    def width(self) -> float:
        min_x, _, max_x, _ = self.bounds
        return max_x - min_x

    @property
    def height(self) -> float:
        _, min_y, _, max_y = self.bounds
        return max_y - min_y

    @property
    def ok(self) -> bool:
        return self.report.ok and self.validation.ok

    def to_section_geometry(self) -> ProfileSectionGeometry:
        """Convert to the serialisable :class:`ProfileSectionGeometry` model.

        The largest region becomes the exterior contour; its chambers become
        internal chambers. Additional disconnected regions (a thermally broken
        profile's second shell) are appended as extra chambers' siblings via the
        metadata-free route: they are preserved as separate exterior rings in
        ``internal_chambers`` only if they are genuine holes, so instead we take
        the outer region and record the rest in the caller's metadata.
        """
        outer = self.topology.outer_region
        return ProfileSectionGeometry(
            exterior_contour=[Vertex(x=x, y=y) for x, y in outer.shell.oriented(True).points],
            internal_chambers=[
                [Vertex(x=x, y=y) for x, y in hole.oriented(False).points]
                for hole in outer.holes
            ],
        )


@timed("geometry.load_section")
def load_section(
    source: str | Path,
    *,
    options: DxfReadOptions | None = None,
    defaults: GeometryDefaults | None = None,
    validate: bool = True,
) -> LoadedSection:
    """Run the full DXF -> topology pipeline on ``source``.

    Raises
    ------
    DxfReadError
        The file cannot be read or contains no boundary geometry.
    ContourError
        No closed contour could be reconstructed from the segments.
    TopologyError
        Closed contours exist but cannot be resolved into regions.
    """
    defaults = defaults or get_settings().geometry
    options = options or DxfReadOptions(sagitta=defaults.arc_sagitta_mm)

    extraction = read_dxf(source, options)
    report = extraction.report

    rings, chainer = rings_from_segments(
        extraction.segments,
        tolerance=defaults.chain_tolerance_mm,
        min_area=defaults.min_contour_area_mm2,
    )
    report.contours_found = len(rings)
    report.closed_contours = len(rings)
    report.open_chains = len(chainer.open_chains)
    report.repaired_gaps = chainer.repaired_gaps
    report.discarded_tiny = chainer.discarded_tiny

    if chainer.open_chains:
        report.add_warning(
            f"{len(chainer.open_chains)} contour(s) could not be closed and were ignored. "
            "Check for gaps or duplicated entities in the drawing."
        )
    if not rings:
        publish(Topic.GEOMETRY_FAILED, source=str(source), reason="no closed contours")
        raise ContourError(
            "No closed contour could be reconstructed from the drawing",
            source=str(source),
            segments=len(extraction.segments),
            open_chains=len(chainer.open_chains),
        )

    topology = resolve_topology(rings, min_area=defaults.min_contour_area_mm2)
    report.holes_found = topology.chamber_count

    polygon = topology_to_polygon(topology)

    validation = (
        validate_section(topology, polygon, defaults=defaults)
        if validate
        else ValidationResult()
    )
    for issue in validation.issues:
        if issue.severity == "error":
            report.add_error(issue.message)
        elif issue.severity == "warning":
            report.add_warning(issue.message)

    section = LoadedSection(
        topology=topology,
        polygon=polygon,
        report=report,
        validation=validation,
        rings=rings,
        source=str(source),
    )
    publish(
        Topic.GEOMETRY_LOADED,
        source=str(source),
        area=section.area,
        chambers=topology.chamber_count,
        regions=len(topology.regions),
    )
    _log.info(
        "Loaded section from %s: area %.1f mm^2, %d chamber(s), %.1f x %.1f mm",
        source,
        section.area,
        topology.chamber_count,
        section.width,
        section.height,
    )
    return section


def profile_from_dxf(
    source: str | Path,
    profile_id: str,
    system_series: str,
    *,
    material_id: str | None = None,
    role: ProfileRole = ProfileRole.OTHER,
    options: DxfReadOptions | None = None,
    wall_thickness: float | None = None,
) -> tuple[ProfileDefinition, LoadedSection]:
    """Build a :class:`ProfileDefinition` straight from a DXF drawing.

    Returns the definition together with the :class:`LoadedSection` it came
    from, so the caller can inspect diagnostics without re-running the pipeline.
    """
    section = load_section(source, options=options)
    geometry = section.to_section_geometry()

    nominal_wall = wall_thickness
    if nominal_wall is None and section.validation.thickness is not None:
        # Use the modal-ish measure rather than the minimum: the minimum picks
        # up gasket lips and chamfers, the mean is representative of the walls.
        nominal_wall = round(section.validation.thickness.mean_thickness, 2)
    nominal_wall = nominal_wall or 1.5

    definition = ProfileDefinition(
        profile_id=profile_id,
        system_series=system_series,
        role=role,
        material_id=material_id or "en-aw-6060-t66",
        outer_dimensions=OuterDimensions(
            width=round(section.width, 3),
            height=round(section.height, 3),
            wall_thickness_nominal=nominal_wall,
        ),
        geometry=geometry,
        source_file=str(source),
        metadata={
            "regions": len(section.topology.regions),
            "chambers": section.topology.chamber_count,
            "thermally_broken_guess": detect_thermal_break(section.topology),
        },
    )
    return definition, section


def section_from_profile(profile: ProfileDefinition, *, sagitta: float = 0.02) -> LoadedSection:
    """Rebuild a :class:`LoadedSection` from a stored profile definition.

    This is the inverse of :meth:`LoadedSection.to_section_geometry`, used when
    a profile comes from the library (JSON) rather than from a fresh DXF import.
    """
    from .primitives import flatten_vertices

    exterior = flatten_vertices(
        [(v.x, v.y, v.bulge) for v in profile.geometry.exterior_contour],
        closed=True,
        sagitta=sagitta,
    )
    chambers = [
        flatten_vertices([(v.x, v.y, v.bulge) for v in ring], closed=True, sagitta=sagitta)
        for ring in profile.geometry.internal_chambers
    ]
    if len(exterior) < 3:
        raise GeometryError("Profile exterior contour has fewer than 3 points")

    rings = [Ring(points=exterior)] + [Ring(points=c) for c in chambers if len(c) >= 3]
    topology = resolve_topology(rings, min_area=0.0)
    polygon = topology_to_polygon(topology)

    report = GeometryReport(source=profile.source_file, contours_found=len(rings))
    return LoadedSection(
        topology=topology,
        polygon=polygon,
        report=report,
        validation=ValidationResult(),
        rings=rings,
        source=profile.source_file,
    )


__all__ = [
    # primitives / models
    "Point",
    "Segment",
    "Ring",
    "Region",
    "SectionTopology",
    "LoadedSection",
    # pipeline stages
    "DxfReader",
    "DxfReadOptions",
    "DxfExtraction",
    "read_dxf",
    "ContourChainer",
    "rings_from_segments",
    "resolve_topology",
    "topology_to_polygon",
    "polygon_to_rings",
    "polygon_rings_coordinates",
    "points_to_polygon",
    "bounding_box",
    # validation
    "ValidationResult",
    "validate_section",
    "measure_wall_thickness",
    "detect_thermal_break",
    # feature recognition
    "DetectedFeature",
    "FeatureKind",
    "FeatureSpec",
    "Pocket",
    "ProfileFeatureReport",
    "ThermalStrip",
    "describe_section",
    "detect_features",
    "features_for_profile",
    "features_for_section",
    "find_pockets",
    "paint_area_per_metre",
    # high level
    "load_section",
    "profile_from_dxf",
    "section_from_profile",
]
