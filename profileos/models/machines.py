"""Machine, tooling and clamp models.

A :class:`MachineDefinition` describes everything the post-processors and the
collision checker need: the working envelope, the spindle/axis configuration,
the tool magazine, and the clamps that hold the bar. Machine definitions are
hot-reloadable data plugins (``kind: "machine"``), so a plant can add a new
machining centre by dropping a JSON file into the machines directory.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .profile import Face


class MachineKind(StrEnum):
    """Broad machine categories, which decide the applicable post-processor."""

    MACHINING_CENTRE = "machining_centre"
    DOUBLE_MITRE_SAW = "double_mitre_saw"
    SINGLE_HEAD_SAW = "single_head_saw"
    CUTTING_CENTRE = "cutting_centre"
    PUNCHING_MACHINE = "punching_machine"
    CRIMPING_MACHINE = "crimping_machine"


class ToolType(StrEnum):
    """Cutting tool geometry families."""

    DRILL = "drill"
    END_MILL = "end_mill"
    SLOT_MILL = "slot_mill"
    DISC_SAW = "disc_saw"
    THREAD_MILL = "thread_mill"
    TAP = "tap"
    CHAMFER = "chamfer"
    COUNTERSINK = "countersink"
    ENGRAVER = "engraver"
    PROBE = "probe"


class Tool(BaseModel):
    """One tool in the machine magazine.

    Feeds and speeds are stored per tool because the post-processors emit them
    directly; an operation may override them, but the tool carries the safe
    default for aluminium.
    """

    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=0, description="Magazine/pocket number referenced by the NC code")
    name: str
    tool_type: ToolType
    diameter: float = Field(gt=0, description="Cutting diameter [mm]")

    flute_length: float | None = Field(default=None, gt=0, description="Usable length [mm]")
    total_length: float | None = Field(default=None, gt=0, description="Overall length [mm]")
    #: Distance from the spindle nose to the tool tip; drives collision checks.
    gauge_length: float | None = Field(default=None, gt=0)
    flutes: int = Field(default=2, ge=1)
    corner_radius: float = Field(default=0.0, ge=0)
    tip_angle: float | None = Field(
        default=None, gt=0, le=180, description="Included point angle for drills [deg]"
    )

    spindle_rpm: int = Field(default=18000, gt=0)
    feed_mm_min: float = Field(default=1200.0, gt=0)
    plunge_feed_mm_min: float | None = Field(default=None, gt=0)
    max_depth_of_cut: float | None = Field(default=None, gt=0)

    #: Faces this tool can reach. An empty set means "any face".
    allowed_faces: set[Face] = Field(default_factory=set)
    #: Which spindle unit holds it, for multi-head machines.
    spindle_id: str | None = None
    coolant: bool = True
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def radius(self) -> float:
        return self.diameter / 2.0

    @property
    def effective_plunge_feed(self) -> float:
        """Plunge feed, defaulting to 40 % of the contouring feed."""
        return self.plunge_feed_mm_min or self.feed_mm_min * 0.4

    def can_machine_face(self, face: Face) -> bool:
        return not self.allowed_faces or face in self.allowed_faces

    def can_reach_depth(self, depth: float) -> bool:
        """True when the tool is long enough for ``depth``."""
        if self.flute_length is None:
            return True
        return depth <= self.flute_length


class ToolLibrary(BaseModel):
    """A named collection of tools, hot-reloadable as ``kind: "tool_library"``."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str = "1.0"
    tools: list[Tool] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_numbers(self) -> "ToolLibrary":
        seen: set[int] = set()
        for tool in self.tools:
            if tool.number in seen:
                raise ValueError(f"duplicate tool number {tool.number} in library {self.id!r}")
            seen.add(tool.number)
        return self

    def by_number(self, number: int) -> Tool | None:
        return next((t for t in self.tools if t.number == number), None)

    def find(
        self,
        *,
        tool_type: ToolType | None = None,
        diameter: float | None = None,
        tolerance: float = 0.01,
        face: Face | None = None,
    ) -> list[Tool]:
        """Search the magazine, e.g. for "a 8.5 mm drill that reaches TOP"."""
        results = list(self.tools)
        if tool_type is not None:
            results = [t for t in results if t.tool_type == tool_type]
        if diameter is not None:
            results = [t for t in results if abs(t.diameter - diameter) <= tolerance]
        if face is not None:
            results = [t for t in results if t.can_machine_face(face)]
        return results


class ClampType(StrEnum):
    VICE = "vice"
    PNEUMATIC = "pneumatic"
    TOP_DOWN = "top_down"
    SIDE = "side"


class Clamp(BaseModel):
    """A workpiece clamp, modelled as an axis-aligned box on the bar axis.

    ``position`` is the clamp centre measured along the bar (X). The box spans
    ``position +/- width/2`` in X and is described in Y/Z by its own extents, so
    the collision checker can test a tool envelope against it without any
    solid-modelling kernel.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    clamp_type: ClampType = ClampType.PNEUMATIC
    position: float = Field(description="Centre position along the bar [mm]")
    width: float = Field(gt=0, description="Extent along the bar [mm]")
    height: float = Field(default=80.0, gt=0, description="Extent in Z [mm]")
    depth: float = Field(default=120.0, gt=0, description="Extent in Y [mm]")

    movable: bool = True
    #: Travel limits for the clamp centre; ``None`` means the machine limits apply.
    min_position: float | None = None
    max_position: float | None = None
    #: Faces this clamp physically blocks.
    blocks_faces: set[Face] = Field(
        default_factory=lambda: {Face.TOP, Face.FRONT, Face.BACK, Face.BOTTOM}
    )
    #: Minimum gap this clamp must keep from its neighbours [mm].
    min_gap: float = Field(default=40.0, ge=0)
    enabled: bool = True

    @property
    def start(self) -> float:
        """Left edge along the bar."""
        return self.position - self.width / 2.0

    @property
    def end(self) -> float:
        """Right edge along the bar."""
        return self.position + self.width / 2.0

    def span(self, clearance: float = 0.0) -> tuple[float, float]:
        """Occupied X interval, optionally grown by a safety clearance."""
        return (self.start - clearance, self.end + clearance)

    def blocks(self, face: Face) -> bool:
        return self.enabled and face in self.blocks_faces

    def moved_to(self, position: float) -> "Clamp":
        """Return a copy shifted to ``position`` (clamps are treated as immutable)."""
        return self.model_copy(update={"position": position})


class Axis(BaseModel):
    """A linear or rotary machine axis with its travel limits."""

    model_config = ConfigDict(extra="forbid")

    name: str
    min_travel: float
    max_travel: float
    rapid_rate: float = Field(default=60000.0, gt=0, description="[mm/min] or [deg/min]")
    rotary: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> "Axis":
        if self.min_travel >= self.max_travel:
            raise ValueError(f"axis {self.name}: min_travel must be < max_travel")
        return self

    def contains(self, value: float, tolerance: float = 1e-6) -> bool:
        return self.min_travel - tolerance <= value <= self.max_travel + tolerance


class MachineDefinition(BaseModel):
    """A complete CNC machine or saw definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    vendor: str
    model: str
    kind: MachineKind = MachineKind.MACHINING_CENTRE
    version: str = "1.0"

    #: Post-processor registry key, e.g. ``"elumatec.ncx"``.
    post_processor: str
    axis_count: int = Field(default=3, ge=2, le=6)
    axes: list[Axis] = Field(default_factory=list)

    max_bar_length: float = Field(default=6500.0, gt=0)
    min_bar_length: float = Field(default=150.0, gt=0)
    max_profile_width: float = Field(default=300.0, gt=0)
    max_profile_height: float = Field(default=250.0, gt=0)

    tool_library_id: str | None = None
    clamps: list[Clamp] = Field(default_factory=list)
    #: Faces the machine can reach without re-fixturing the bar.
    machinable_faces: set[Face] = Field(
        default_factory=lambda: {Face.TOP, Face.FRONT, Face.BACK, Face.LEFT, Face.RIGHT}
    )

    #: Saw-specific limits (ignored by machining centres).
    min_cut_angle: float = Field(default=22.5, ge=0, le=180)
    max_cut_angle: float = Field(default=157.5, ge=0, le=180)
    blade_kerf: float = Field(default=3.5, ge=0)
    blade_diameter: float | None = Field(default=None, gt=0)

    #: Safety clearance added around tools when testing clamp collisions [mm].
    clamp_clearance: float = Field(default=15.0, ge=0)
    output_extension: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _normalise_id(cls, v: str) -> str:
        out = v.strip().lower().replace(" ", "-")
        if not out:
            raise ValueError("machine id must not be empty")
        return out

    @model_validator(mode="after")
    def _validate_lengths(self) -> "MachineDefinition":
        if self.min_bar_length >= self.max_bar_length:
            raise ValueError("min_bar_length must be < max_bar_length")
        ids = [c.id for c in self.clamps]
        if len(ids) != len(set(ids)):
            raise ValueError("clamp ids must be unique")
        return self

    # -- queries ----------------------------------------------------------- #
    @property
    def is_saw(self) -> bool:
        return self.kind in (
            MachineKind.DOUBLE_MITRE_SAW,
            MachineKind.SINGLE_HEAD_SAW,
            MachineKind.CUTTING_CENTRE,
        )

    def active_clamps(self) -> list[Clamp]:
        return [c for c in self.clamps if c.enabled]

    def axis(self, name: str) -> Axis | None:
        upper = name.upper()
        return next((a for a in self.axes if a.name.upper() == upper), None)

    def supports_face(self, face: Face) -> bool:
        return face in self.machinable_faces

    def accepts_bar(self, length: float, width: float, height: float) -> tuple[bool, str | None]:
        """Check a bar against the machine envelope.

        Returns ``(ok, reason)`` where ``reason`` explains the first violation.
        """
        if length > self.max_bar_length:
            return False, f"bar length {length:.1f} mm exceeds maximum {self.max_bar_length:.1f} mm"
        if length < self.min_bar_length:
            return False, f"bar length {length:.1f} mm below minimum {self.min_bar_length:.1f} mm"
        if width > self.max_profile_width:
            return False, f"profile width {width:.1f} mm exceeds {self.max_profile_width:.1f} mm"
        if height > self.max_profile_height:
            return False, f"profile height {height:.1f} mm exceeds {self.max_profile_height:.1f} mm"
        return True, None

    def accepts_cut_angle(self, angle_deg: float) -> bool:
        return self.min_cut_angle - 1e-9 <= angle_deg <= self.max_cut_angle + 1e-9

    def with_clamps(self, clamps: Iterable[Clamp]) -> "MachineDefinition":
        """Return a copy carrying a different clamp arrangement."""
        return self.model_copy(update={"clamps": list(clamps)})


__all__ = [
    "MachineKind",
    "ToolType",
    "Tool",
    "ToolLibrary",
    "ClampType",
    "Clamp",
    "Axis",
    "MachineDefinition",
]
