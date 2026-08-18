"""The machining intermediate representation (IR).

Every machining feature in ProfileOS is expressed once, in machine-neutral
terms, and translated into native code by a driver. That indirection is what
lets one job drive an Elumatec machining centre, an Emmegi saw and a Fanuc
router without re-modelling anything — the same role Uni_Link plays between
design software and the shop floor.

Coordinate system
-----------------
The IR uses a **bar-local right-handed frame**:

* ``X`` runs along the bar, zero at its left end, increasing to the right.
* ``Y`` runs across the machined face, zero at the face's reference edge.
* ``Z`` is depth into the material, measured from the face surface, positive
  going in. An operation's ``depth`` is therefore always a positive number.

Each operation names the :class:`~profileos.models.profile.Face` it is applied
to. Drivers are responsible for mapping this frame onto their machine's own
axis conventions, including which face maps to which spindle or aggregate head.

Why a class per feature rather than raw toolpaths
-------------------------------------------------
Native formats are feature-oriented, not path-oriented: an NCX file says
"drill, diameter 8.5, depth 12", not "plunge to Z-12". Lowering everything to
toolpaths too early would throw away the semantics those formats need and force
each driver to re-infer them. Toolpath generation therefore happens only for
drivers that genuinely need it (ISO G-code), via
:mod:`profileos.cnc.toolpath`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator, Sequence
from uuid import uuid4

from ..core.errors import CncError
from ..models.profile import Face

Point2D = tuple[float, float]


class OperationType(StrEnum):
    """Discriminator used by drivers to dispatch on a feature."""

    DRILL = "drill"
    COUNTERBORE = "counterbore"
    THREAD = "thread"
    RECTANGULAR_POCKET = "rectangular_pocket"
    CIRCULAR_POCKET = "circular_pocket"
    SLOT = "slot"
    CONTOUR = "contour"
    END_NOTCH = "end_notch"
    SAW_CUT = "saw_cut"
    ENGRAVE = "engrave"


class Compensation(StrEnum):
    """Cutter radius compensation side, relative to the direction of travel."""

    NONE = "none"
    LEFT = "left"  # G41
    RIGHT = "right"  # G42

    @property
    def gcode(self) -> str:
        return {"none": "G40", "left": "G41", "right": "G42"}[self.value]


@dataclass
class Operation:
    """Base class for every machining feature.

    Attributes
    ----------
    op_id:
        Stable identifier, emitted into the NC file so an operator can trace a
        line of machine code back to the feature that produced it.
    face:
        Which face of the bar is machined.
    tool_number:
        Magazine pocket. ``None`` asks the driver to select a tool from the
        machine's library by geometry (see :func:`resolve_tools`).
    feed / spindle_speed:
        Overrides for the tool's defaults; ``None`` means "use the tool's".
    """

    face: Face
    op_id: str = field(default_factory=lambda: f"OP{uuid4().hex[:6].upper()}")
    tool_number: int | None = None
    feed: float | None = None
    spindle_speed: int | None = None
    comment: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    #: Overridden by each subclass.
    op_type: OperationType = field(init=False, default=OperationType.DRILL)

    # -- geometry queries --------------------------------------------------- #
    def extent_x(self) -> tuple[float, float]:
        """``(min_x, max_x)`` footprint along the bar [mm].

        Used by the clamp collision checker, so it must cover the whole region
        the tool sweeps, not just the feature's nominal centre.
        """
        raise NotImplementedError

    def max_depth(self) -> float:
        """Deepest Z reached by this operation [mm]."""
        raise NotImplementedError

    def required_tool_diameter(self) -> float | None:
        """Diameter the feature implies, for automatic tool selection [mm]."""
        return None

    def describe(self) -> str:
        """One-line human summary for cut lists and job cards."""
        lo, hi = self.extent_x()
        return (
            f"{self.op_type.value} on {self.face.value} "
            f"X{lo:.1f}..{hi:.1f} depth {self.max_depth():.1f}"
        )

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the operation is sound."""
        problems: list[str] = []
        if self.max_depth() <= 0:
            problems.append(f"{self.op_id}: depth must be positive")
        return problems


# --------------------------------------------------------------------------- #
# Hole features
# --------------------------------------------------------------------------- #

@dataclass
class Drill(Operation):
    """A round hole drilled perpendicular to the face."""

    x: float = 0.0
    y: float = 0.0
    diameter: float = 5.0
    depth: float = 10.0
    #: Drill straight through the profile wall (drivers may emit a through cycle).
    through: bool = False
    #: Peck depth for deep holes; ``None`` disables pecking.
    peck_depth: float | None = None
    #: Countersink angle applied at the mouth, if any [deg].
    countersink_angle: float | None = None

    def __post_init__(self) -> None:
        self.op_type = OperationType.DRILL

    def extent_x(self) -> tuple[float, float]:
        radius = self.diameter / 2.0
        return (self.x - radius, self.x + radius)

    def max_depth(self) -> float:
        return self.depth

    def required_tool_diameter(self) -> float | None:
        return self.diameter

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.diameter <= 0:
            problems.append(f"{self.op_id}: drill diameter must be positive")
        if self.peck_depth is not None and self.peck_depth <= 0:
            problems.append(f"{self.op_id}: peck depth must be positive")
        return problems


@dataclass
class Counterbore(Operation):
    """A flat-bottomed enlargement at the mouth of a hole (screw head seat)."""

    x: float = 0.0
    y: float = 0.0
    pilot_diameter: float = 5.0
    bore_diameter: float = 10.0
    bore_depth: float = 4.0
    pilot_depth: float = 12.0

    def __post_init__(self) -> None:
        self.op_type = OperationType.COUNTERBORE

    def extent_x(self) -> tuple[float, float]:
        radius = self.bore_diameter / 2.0
        return (self.x - radius, self.x + radius)

    def max_depth(self) -> float:
        return max(self.pilot_depth, self.bore_depth)

    def required_tool_diameter(self) -> float | None:
        return self.bore_diameter

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.bore_diameter <= self.pilot_diameter:
            problems.append(
                f"{self.op_id}: counterbore diameter must exceed the pilot diameter"
            )
        return problems


@dataclass
class Thread(Operation):
    """A tapped or thread-milled hole."""

    x: float = 0.0
    y: float = 0.0
    nominal_diameter: float = 5.0
    pitch: float = 0.8
    depth: float = 10.0
    #: True to thread-mill with a helical path instead of tapping.
    milled: bool = False

    def __post_init__(self) -> None:
        self.op_type = OperationType.THREAD

    def extent_x(self) -> tuple[float, float]:
        radius = self.nominal_diameter / 2.0
        return (self.x - radius, self.x + radius)

    def max_depth(self) -> float:
        return self.depth

    def required_tool_diameter(self) -> float | None:
        # Tapping drill diameter for an ISO metric coarse thread.
        return self.nominal_diameter - self.pitch

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.pitch <= 0:
            problems.append(f"{self.op_id}: thread pitch must be positive")
        return problems


# --------------------------------------------------------------------------- #
# Pocket and slot features
# --------------------------------------------------------------------------- #

@dataclass
class RectangularPocket(Operation):
    """A rectangular milled pocket, positioned by its centre."""

    x: float = 0.0
    y: float = 0.0
    length: float = 20.0  # along X
    width: float = 10.0  # along Y
    depth: float = 3.0
    corner_radius: float = 0.0
    rotation: float = 0.0  # degrees, about the face normal
    #: Maximum axial engagement per pass; ``None`` cuts full depth in one go.
    step_down: float | None = None
    #: Material left for a finishing pass [mm].
    finish_allowance: float = 0.0
    #: True when the pocket breaks through the wall (an aperture, not a recess).
    through: bool = False

    def __post_init__(self) -> None:
        self.op_type = OperationType.RECTANGULAR_POCKET

    def extent_x(self) -> tuple[float, float]:
        if abs(self.rotation) < 1e-9:
            half = self.length / 2.0
        else:
            # Rotated footprint: project both half-extents onto X.
            angle = math.radians(self.rotation)
            half = (
                abs(self.length / 2.0 * math.cos(angle))
                + abs(self.width / 2.0 * math.sin(angle))
            )
        return (self.x - half, self.x + half)

    def max_depth(self) -> float:
        return self.depth

    def required_tool_diameter(self) -> float | None:
        # A corner radius fixes the largest cutter that can reach the corners.
        if self.corner_radius > 0:
            return 2.0 * self.corner_radius
        return None

    def corners(self) -> list[Point2D]:
        """The four corner points in face coordinates, honouring ``rotation``."""
        half_l, half_w = self.length / 2.0, self.width / 2.0
        local = [(-half_l, -half_w), (half_l, -half_w), (half_l, half_w), (-half_l, half_w)]
        angle = math.radians(self.rotation)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return [
            (self.x + lx * cos_a - ly * sin_a, self.y + lx * sin_a + ly * cos_a)
            for lx, ly in local
        ]

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.length <= 0 or self.width <= 0:
            problems.append(f"{self.op_id}: pocket length and width must be positive")
        if self.corner_radius > min(self.length, self.width) / 2.0:
            problems.append(
                f"{self.op_id}: corner radius exceeds half the smaller pocket dimension"
            )
        if self.step_down is not None and self.step_down <= 0:
            problems.append(f"{self.op_id}: step down must be positive")
        return problems


@dataclass
class CircularPocket(Operation):
    """A round milled pocket, larger than any available drill."""

    x: float = 0.0
    y: float = 0.0
    diameter: float = 30.0
    depth: float = 5.0
    step_down: float | None = None
    through: bool = False

    def __post_init__(self) -> None:
        self.op_type = OperationType.CIRCULAR_POCKET

    def extent_x(self) -> tuple[float, float]:
        radius = self.diameter / 2.0
        return (self.x - radius, self.x + radius)

    def max_depth(self) -> float:
        return self.depth

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.diameter <= 0:
            problems.append(f"{self.op_id}: pocket diameter must be positive")
        return problems


@dataclass
class Slot(Operation):
    """A straight slot milled between two points, of a given width."""

    x1: float = 0.0
    y1: float = 0.0
    x2: float = 50.0
    y2: float = 0.0
    width: float = 6.0
    depth: float = 4.0
    through: bool = False
    step_down: float | None = None

    def __post_init__(self) -> None:
        self.op_type = OperationType.SLOT

    def extent_x(self) -> tuple[float, float]:
        radius = self.width / 2.0
        return (min(self.x1, self.x2) - radius, max(self.x1, self.x2) + radius)

    def max_depth(self) -> float:
        return self.depth

    def required_tool_diameter(self) -> float | None:
        # A slot is cut by a tool no wider than the slot itself.
        return self.width

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def angle_deg(self) -> float:
        return math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1))

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.width <= 0:
            problems.append(f"{self.op_id}: slot width must be positive")
        if self.length < 1e-9:
            problems.append(f"{self.op_id}: slot has zero length")
        return problems


@dataclass
class Contour(Operation):
    """An arbitrary milled path, open or closed.

    ``points`` are in face coordinates. ``compensation`` selects cutter radius
    compensation; drivers that support it natively (ISO G41/G42) emit it, and
    those that do not get a pre-offset path from :mod:`profileos.cnc.toolpath`.
    """

    points: list[Point2D] = field(default_factory=list)
    depth: float = 3.0
    closed: bool = False
    compensation: Compensation = Compensation.NONE
    step_down: float | None = None
    tool_diameter: float = 6.0

    def __post_init__(self) -> None:
        self.op_type = OperationType.CONTOUR

    def extent_x(self) -> tuple[float, float]:
        if not self.points:
            return (0.0, 0.0)
        radius = self.tool_diameter / 2.0
        xs = [p[0] for p in self.points]
        return (min(xs) - radius, max(xs) + radius)

    def max_depth(self) -> float:
        return self.depth

    def required_tool_diameter(self) -> float | None:
        return self.tool_diameter

    def validate(self) -> list[str]:
        problems = super().validate()
        if len(self.points) < 2:
            problems.append(f"{self.op_id}: contour needs at least two points")
        if self.closed and len(self.points) < 3:
            problems.append(f"{self.op_id}: a closed contour needs at least three points")
        return problems


# --------------------------------------------------------------------------- #
# End machining and cutting
# --------------------------------------------------------------------------- #

@dataclass
class EndNotch(Operation):
    """An end notch (AKM), the cut-out that lets a transom meet a mullion.

    ``from_right`` selects which end of the bar is notched. The notch removes
    material over ``length`` from that end, ``depth`` into the face.
    """

    length: float = 20.0
    depth: float = 15.0
    width: float = 0.0  # 0 means the full face width
    from_right: bool = False
    corner_radius: float = 0.0
    #: Bar length, needed to resolve a right-end notch into absolute X.
    bar_length: float = 0.0

    def __post_init__(self) -> None:
        self.op_type = OperationType.END_NOTCH

    def extent_x(self) -> tuple[float, float]:
        if self.from_right:
            return (self.bar_length - self.length, self.bar_length)
        return (0.0, self.length)

    def max_depth(self) -> float:
        return self.depth

    def validate(self) -> list[str]:
        problems = super().validate()
        if self.length <= 0:
            problems.append(f"{self.op_id}: notch length must be positive")
        if self.from_right and self.bar_length <= 0:
            problems.append(
                f"{self.op_id}: a right-end notch needs the bar length to resolve its position"
            )
        return problems


@dataclass
class SawCut(Operation):
    """A saw cut across the bar.

    ``angle`` is measured between the blade and the bar axis in the horizontal
    plane (90 = square). ``tilt`` is the blade tilt out of vertical, used for
    compound mitres on curtain-wall corner posts.
    """

    position: float = 0.0
    angle: float = 90.0
    tilt: float = 0.0
    depth: float = 0.0  # 0 means cut fully through
    blade_kerf: float = 3.5

    def __post_init__(self) -> None:
        self.op_type = OperationType.SAW_CUT
        self.face = Face.TOP if self.face is None else self.face

    def extent_x(self) -> tuple[float, float]:
        half = self.blade_kerf / 2.0
        return (self.position - half, self.position + half)

    def max_depth(self) -> float:
        # A through cut has no finite depth; report the kerf so validation of a
        # positive depth still passes and the clamp checker treats it as full.
        return self.depth if self.depth > 0 else self.blade_kerf

    @property
    def is_through(self) -> bool:
        return self.depth <= 0

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not (0.0 < self.angle < 180.0):
            problems.append(f"{self.op_id}: saw angle must be between 0 and 180 degrees")
        if self.blade_kerf < 0:
            problems.append(f"{self.op_id}: blade kerf cannot be negative")
        return problems


@dataclass
class Engrave(Operation):
    """Text marked on the profile — position marks, order numbers, QR payloads."""

    x: float = 0.0
    y: float = 0.0
    text: str = ""
    height: float = 5.0
    depth: float = 0.3
    rotation: float = 0.0

    def __post_init__(self) -> None:
        self.op_type = OperationType.ENGRAVE

    def extent_x(self) -> tuple[float, float]:
        # Approximate advance width for a single-stroke font.
        width = len(self.text) * self.height * 0.62
        if abs(self.rotation) > 45.0:
            width = self.height
        return (self.x, self.x + width)

    def max_depth(self) -> float:
        return self.depth

    def validate(self) -> list[str]:
        problems = super().validate()
        if not self.text:
            problems.append(f"{self.op_id}: engraving has no text")
        return problems


# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

@dataclass
class OperationSet:
    """The operations applied to one piece, with convenience queries."""

    operations: list[Operation] = field(default_factory=list)

    def __iter__(self) -> Iterator[Operation]:
        return iter(self.enabled())

    def __len__(self) -> int:
        return len(self.operations)

    def enabled(self) -> list[Operation]:
        return [op for op in self.operations if op.enabled]

    def add(self, operation: Operation) -> "OperationSet":
        self.operations.append(operation)
        return self

    def for_face(self, face: Face) -> list[Operation]:
        return [op for op in self.enabled() if op.face == face]

    def faces(self) -> list[Face]:
        """Distinct faces touched, in a stable order."""
        seen: dict[Face, None] = {}
        for op in self.enabled():
            seen.setdefault(op.face, None)
        return list(seen)

    def by_type(self, op_type: OperationType) -> list[Operation]:
        return [op for op in self.enabled() if op.op_type == op_type]

    def tool_numbers(self) -> list[int]:
        return sorted({op.tool_number for op in self.enabled() if op.tool_number is not None})

    def extent_x(self) -> tuple[float, float]:
        """Combined footprint of every operation along the bar."""
        spans = [op.extent_x() for op in self.enabled()]
        if not spans:
            return (0.0, 0.0)
        return (min(s[0] for s in spans), max(s[1] for s in spans))

    def validate(self) -> list[str]:
        return [problem for op in self.enabled() for problem in op.validate()]

    def sorted_for_machining(self) -> list[Operation]:
        """Order operations to minimise tool changes, then travel.

        Grouping by face and tool is what actually drives cycle time on a
        machining centre: a tool change costs seconds, a rapid move costs
        milliseconds. Within a tool group, operations run left to right.
        """
        def key(op: Operation) -> tuple:
            return (
                op.face.value,
                op.tool_number if op.tool_number is not None else 9999,
                op.extent_x()[0],
            )

        return sorted(self.enabled(), key=key)


def resolve_tools(
    operations: Sequence[Operation],
    tool_library: Any,
    *,
    strict: bool = True,
) -> dict[str, int]:
    """Assign a magazine tool to every operation lacking an explicit one.

    Selection is by geometry: a drill of the exact diameter for a hole, the
    largest end mill that still fits the feature for a pocket or slot. Returns
    ``{op_id: tool_number}`` for the operations that were resolved.

    Raises
    ------
    ToolingError
        ``strict`` is set and no suitable tool exists for some operation.
    """
    from ..core.errors import ToolingError
    from ..models.machines import ToolType

    assigned: dict[str, int] = {}
    for op in operations:
        if op.tool_number is not None:
            continue

        wanted = op.required_tool_diameter()
        candidates: list[Any] = []

        if op.op_type in (OperationType.DRILL, OperationType.COUNTERBORE):
            candidates = tool_library.find(
                tool_type=ToolType.DRILL, diameter=wanted, face=op.face
            )
        elif op.op_type is OperationType.THREAD:
            candidates = tool_library.find(tool_type=ToolType.TAP, face=op.face) or (
                tool_library.find(tool_type=ToolType.THREAD_MILL, face=op.face)
            )
        elif op.op_type is OperationType.SAW_CUT:
            candidates = tool_library.find(tool_type=ToolType.DISC_SAW, face=op.face)
        elif op.op_type is OperationType.ENGRAVE:
            candidates = tool_library.find(tool_type=ToolType.ENGRAVER, face=op.face)
        else:
            # Milling: the widest cutter that still fits the feature, because a
            # bigger tool clears material faster and deflects less.
            mills = [
                t
                for t in tool_library.find(tool_type=ToolType.END_MILL, face=op.face)
                if wanted is None or t.diameter <= wanted + 1e-9
            ]
            mills.extend(
                t
                for t in tool_library.find(tool_type=ToolType.SLOT_MILL, face=op.face)
                if wanted is None or t.diameter <= wanted + 1e-9
            )
            candidates = sorted(mills, key=lambda t: t.diameter, reverse=True)

        # A tool that cannot reach the depth is no use however well it fits.
        candidates = [t for t in candidates if t.can_reach_depth(op.max_depth())]

        if not candidates:
            if strict:
                raise ToolingError(
                    "No suitable tool in the machine library",
                    operation=op.op_id,
                    op_type=op.op_type.value,
                    required_diameter=wanted,
                    depth=op.max_depth(),
                )
            continue

        chosen = candidates[0]
        op.tool_number = chosen.number
        assigned[op.op_id] = chosen.number

    return assigned


__all__ = [
    "Point2D",
    "OperationType",
    "Compensation",
    "Operation",
    "Drill",
    "Counterbore",
    "Thread",
    "RectangularPocket",
    "CircularPocket",
    "Slot",
    "Contour",
    "EndNotch",
    "SawCut",
    "Engrave",
    "OperationSet",
    "resolve_tools",
]
