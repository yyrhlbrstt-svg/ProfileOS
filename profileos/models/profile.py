"""The parametric aluminium profile model.

This is the canonical description of a profile cross-section and everything
machined onto it. It mirrors the ``AluminumProfileParametricSchema`` JSON
schema: an exterior contour, a list of internal chambers (holes), optional
polyamide thermal-break strips, and a list of machining macros.

Vertices carry a ``bulge`` value, the DXF convention for encoding a circular
arc between two polyline vertices: ``bulge = tan(theta / 4)`` where ``theta`` is
the included angle of the arc, positive counter-clockwise. Keeping the bulge on
the vertex means a profile round-trips through DXF without losing arc fidelity;
:mod:`profileos.geometry.primitives` expands them when a flat polygon is needed.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any, Iterator, Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .materials import DEFAULT_MATERIAL_ID, Material, get_material


class Face(StrEnum):
    """The six machinable faces of a bar, in machine convention.

    ``FRONT``/``BACK`` are the two long vertical faces, ``TOP``/``BOTTOM`` the
    two long horizontal faces, and ``LEFT``/``RIGHT`` the two cut ends.
    """

    TOP = "TOP"
    BOTTOM = "BOTTOM"
    FRONT = "FRONT"
    BACK = "BACK"
    LEFT = "LEFT"
    RIGHT = "RIGHT"

    @property
    def is_end_face(self) -> bool:
        """True for the two faces produced by the saw cut."""
        return self in (Face.LEFT, Face.RIGHT)

    @property
    def opposite(self) -> "Face":
        return {
            Face.TOP: Face.BOTTOM,
            Face.BOTTOM: Face.TOP,
            Face.FRONT: Face.BACK,
            Face.BACK: Face.FRONT,
            Face.LEFT: Face.RIGHT,
            Face.RIGHT: Face.LEFT,
        }[self]


class ProfileRole(StrEnum):
    """Where a profile sits in a facade or opening."""

    MULLION = "mullion"
    TRANSOM = "transom"
    FRAME = "frame"
    SASH = "sash"
    VENT = "vent"
    GLAZING_BEAD = "glazing_bead"
    THRESHOLD = "threshold"
    COVER_CAP = "cover_cap"
    ADAPTER = "adapter"
    REINFORCEMENT = "reinforcement"
    OTHER = "other"


class Vertex(BaseModel):
    """A 2D contour vertex with an optional outgoing arc bulge."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    bulge: float = Field(
        default=0.0,
        description="tan(theta/4) of the arc to the next vertex; 0 = straight segment",
    )

    def as_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    def __iter__(self) -> Iterator[float]:  # type: ignore[override]
        yield self.x
        yield self.y


class OuterDimensions(BaseModel):
    """The bounding envelope and nominal wall thickness of the profile."""

    model_config = ConfigDict(extra="forbid")

    width: float = Field(gt=0, description="Extent along X [mm]")
    height: float = Field(gt=0, description="Extent along Y [mm]")
    wall_thickness_nominal: float = Field(
        gt=0, description="Nominal wall thickness quoted by the system supplier [mm]"
    )


class ThermalBreakStrip(BaseModel):
    """A polyamide insulating bar joining two aluminium shells.

    The strip is described by its own closed contour plus the ids of the
    chambers/shells it bridges, which lets the structural engine build a
    composite (multi-material) section instead of treating the profile as one
    monolithic aluminium region.
    """

    model_config = ConfigDict(extra="forbid")

    strip_id: str
    material_id: str = "pa66-gf25"
    contour: list[Vertex] = Field(min_length=3)
    #: Shear transfer efficiency 0..1 used to interpolate between the fully
    #: composite and fully separated bending stiffness of the section.
    shear_transfer_factor: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None

    @property
    def material(self) -> Material:
        return get_material(self.material_id)


class ProfileSectionGeometry(BaseModel):
    """Closed exterior contour plus internal chambers and thermal breaks."""

    model_config = ConfigDict(extra="forbid")

    exterior_contour: list[Vertex] = Field(min_length=3)
    internal_chambers: list[list[Vertex]] = Field(default_factory=list)
    thermal_break_strips: list[ThermalBreakStrip] = Field(default_factory=list)

    @field_validator("internal_chambers")
    @classmethod
    def _chambers_have_enough_points(cls, v: list[list[Vertex]]) -> list[list[Vertex]]:
        for index, ring in enumerate(v):
            if len(ring) < 3:
                raise ValueError(f"internal chamber {index} needs at least 3 vertices")
        return v

    # -- convenience ------------------------------------------------------- #
    def exterior_points(self) -> list[tuple[float, float]]:
        return [v.as_tuple() for v in self.exterior_contour]

    def chamber_points(self) -> list[list[tuple[float, float]]]:
        return [[v.as_tuple() for v in ring] for ring in self.internal_chambers]

    def has_arcs(self) -> bool:
        """True when any vertex carries a non-zero bulge."""
        rings: list[Sequence[Vertex]] = [self.exterior_contour, *self.internal_chambers]
        rings.extend(strip.contour for strip in self.thermal_break_strips)
        return any(abs(v.bulge) > 1e-12 for ring in rings for v in ring)

    def bounding_box(self) -> tuple[float, float, float, float]:
        """``(min_x, min_y, max_x, max_y)`` of the exterior contour.

        Bulged segments can bow outside the vertex hull, so this is the vertex
        bounding box; use the flattened geometry from
        :mod:`profileos.geometry` when an exact envelope matters.
        """
        xs = [v.x for v in self.exterior_contour]
        ys = [v.y for v in self.exterior_contour]
        return (min(xs), min(ys), max(xs), max(ys))

    def signed_area(self) -> float:
        """Shoelace area of the exterior ring (chords only), signed by winding."""
        pts = self.exterior_points()
        total = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            total += x1 * y2 - x2 * y1
        return total / 2.0


class MachiningMacro(BaseModel):
    """One machining operation placed on the profile.

    ``macro_id`` refers to a parametric macro in the macro registry (e.g.
    ``"lock.euro_cylinder"``); ``parameters`` supplies its arguments. A macro
    expands into one or more concrete CNC operations at post-processing time.

    Positions are given in the machine coordinate frame: ``position_x`` runs
    along the bar from its left end, ``position_y`` across the machined face.
    """

    model_config = ConfigDict(extra="forbid")

    macro_id: str
    face: Face
    position_x: float = Field(description="Distance along the bar from its left end [mm]")
    position_y: float = Field(default=0.0, description="Offset across the face [mm]")
    depth: float = Field(gt=0, description="Machining depth [mm]")
    tool_id: int = Field(ge=0, description="Tool number in the machine's tool database")

    #: Optional explicit reference: distance measured from the right end instead.
    from_right_end: bool = False
    rotation_deg: float = 0.0
    parameters: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    enabled: bool = True

    def resolved_x(self, bar_length: float) -> float:
        """Absolute X along the bar, honouring :attr:`from_right_end`."""
        return bar_length - self.position_x if self.from_right_end else self.position_x


class ProfileDefinition(BaseModel):
    """A complete profile: identity, material, geometry and machining."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(description="Supplier article number, e.g. 'MB70-MULLION'")
    system_series: str = Field(description="Profile system family, e.g. 'MB-70'")
    name: str | None = None
    role: ProfileRole = ProfileRole.OTHER
    version: str = "1.0"

    material_id: str = DEFAULT_MATERIAL_ID
    outer_dimensions: OuterDimensions
    geometry: ProfileSectionGeometry
    machining_macros: list[MachiningMacro] = Field(default_factory=list)

    #: Stock bar lengths this article is purchased in [mm].
    stock_lengths: list[float] = Field(default_factory=lambda: [6000.0])
    #: Supplier-quoted linear mass; when absent it is computed from the geometry.
    mass_per_metre_declared: float | None = Field(default=None, gt=0)
    surface_finish: str | None = None
    price_per_metre: float | None = Field(default=None, ge=0)
    currency: str = "EUR"

    source_file: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- validation -------------------------------------------------------- #
    @field_validator("profile_id", "system_series")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("stock_lengths")
    @classmethod
    def _positive_stock(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("at least one stock length is required")
        if any(length <= 0 for length in v):
            raise ValueError("stock lengths must be > 0")
        return sorted(set(v))

    @model_validator(mode="after")
    def _check_envelope(self) -> "ProfileDefinition":
        """Warn-level consistency: declared envelope vs. actual vertex extents.

        A mismatch beyond 1 mm usually means the DXF was drawn in the wrong
        units or the header block was copied from another article, so it is
        recorded in metadata for the UI to surface rather than silently ignored.
        """
        min_x, min_y, max_x, max_y = self.geometry.bounding_box()
        actual_w = max_x - min_x
        actual_h = max_y - min_y
        tol = 1.0
        if (
            abs(actual_w - self.outer_dimensions.width) > tol
            or abs(actual_h - self.outer_dimensions.height) > tol
        ):
            self.metadata.setdefault(
                "envelope_mismatch",
                {
                    "declared": [self.outer_dimensions.width, self.outer_dimensions.height],
                    "measured": [round(actual_w, 3), round(actual_h, 3)],
                },
            )
        return self

    # -- derived ----------------------------------------------------------- #
    @property
    def material(self) -> Material:
        return get_material(self.material_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def chamber_count(self) -> int:
        """Number of internal (hollow) chambers."""
        return len(self.geometry.internal_chambers)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_thermally_broken(self) -> bool:
        return bool(self.geometry.thermal_break_strips)

    @property
    def display_name(self) -> str:
        return self.name or f"{self.system_series} {self.profile_id}"

    def macros_for_face(self, face: Face) -> list[MachiningMacro]:
        return [m for m in self.machining_macros if m.enabled and m.face == face]

    def default_stock_length(self) -> float:
        return self.stock_lengths[0]

    def to_json_schema_document(self) -> dict[str, Any]:
        """Serialise into the interchange form used by the parametric schema."""
        return {
            "profile_id": self.profile_id,
            "system_series": self.system_series,
            "outer_dimensions": self.outer_dimensions.model_dump(),
            "geometry": {
                "exterior_contour": [v.model_dump() for v in self.geometry.exterior_contour],
                "internal_chambers": [
                    [v.model_dump() for v in ring] for ring in self.geometry.internal_chambers
                ],
            },
            "machining_macros": [m.model_dump(mode="json") for m in self.machining_macros],
        }


def bulge_to_arc(
    start: tuple[float, float], end: tuple[float, float], bulge: float
) -> tuple[tuple[float, float], float, float, float]:
    """Convert a DXF bulge into an arc description.

    Returns ``(centre, radius, start_angle_rad, end_angle_rad)``. The sweep runs
    counter-clockwise when ``bulge > 0`` and clockwise when ``bulge < 0``.

    Raises
    ------
    ValueError
        If ``bulge`` is zero (the segment is straight) or the endpoints coincide.
    """
    if abs(bulge) < 1e-12:
        raise ValueError("bulge is zero: the segment is a straight line")

    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        raise ValueError("degenerate arc: coincident endpoints")

    # theta is the included angle of the arc; bulge = tan(theta/4).
    theta = 4.0 * math.atan(bulge)
    radius = chord / (2.0 * math.sin(abs(theta) / 2.0))

    # Distance from the chord midpoint to the centre along the +90 degree
    # normal. cos(theta/2) is even in theta, so it alone puts the centre on the
    # correct side only for counter-clockwise sweeps; copysign flips it back for
    # clockwise ones. The cosine still goes negative for reflex arcs (|theta| >
    # pi), which is what places the centre beyond the chord on those.
    apothem = math.copysign(1.0, theta) * radius * math.cos(theta / 2.0)
    mid_x, mid_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    # Unit normal to the chord, rotated +90 degrees.
    nx, ny = -dy / chord, dx / chord
    cx, cy = mid_x + nx * apothem, mid_y + ny * apothem

    start_angle = math.atan2(y1 - cy, x1 - cx)
    end_angle = math.atan2(y2 - cy, x2 - cx)
    return ((cx, cy), radius, start_angle, end_angle)


__all__ = [
    "Face",
    "ProfileRole",
    "Vertex",
    "OuterDimensions",
    "ThermalBreakStrip",
    "ProfileSectionGeometry",
    "MachiningMacro",
    "ProfileDefinition",
    "bulge_to_arc",
]
