"""Recognising what the machinist sees: grooves, rebates and channels.

A profile drawing arrives as a closed outline. What the shop actually needs to
know from it is not the vertex list but the *features*: where the hardware
groove is, how deep the glazing rebate is and therefore what glass thickness
the section takes, where the gasket sits, where the polyamide strips are rolled
in. This module reads those out of the geometry itself, so a section imported
from a supplier's DXF answers those questions without anybody typing them in.

How a feature is found
----------------------
1. **Pockets.** The convex hull of the outline is computed *keeping collinear
   points*. That detail is the whole trick: along a flat machined face every
   vertex lies on the hull, so a slot cut into the middle of that face shows up
   as a run of vertices that are not on the hull, bounded by two that are. The
   chord between those two is the mouth of the pocket, and the run is its wall.
   Discard the collinear points and the hull edge would leap the whole face and
   the mouth would be measured as the face length, which is meaningless.

2. **Bands.** Inside a pocket the void is measured as a function of depth. The
   width of a polygon along a scan line only changes where a vertex is, so
   sampling once between each pair of consecutive vertex depths describes the
   pocket *exactly* — not approximately — as a stack of bands, each with its
   clear span, its total open width and how many separate openings it has.

3. **Steps.** Adjacent bands of the same span and the same opening count are
   merged. A plain rectangular channel is one step. A T-slot is two: a narrow
   mouth over a wider cavity. A glazing rebate with a gasket groove in its
   floor is two: wide and shallow over narrow and deep.

4. **Classification.** Steps are matched against a table of specifications. The
   table is data, not logic: a system whose hardware groove is 16 mm rather
   than 15 mm is handled by editing a row, not the code.

On the numbers in that table
----------------------------
The shipped windows are the sizes these features commonly come in, and they
are deliberately generous. They are a starting point for recognition, not a
statement of any standard: before a groove dimension is used to order hardware
it has to be checked against the system supplier's own drawing. Every pocket is
reported with its measurements whether or not it matched a row, so nothing the
classifier fails to name is hidden from the operator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Iterable, Sequence

from ..core.logging_setup import get_logger
from .primitives import Point, bounding_box, ensure_orientation, signed_area
from .topology import Region, SectionTopology

if TYPE_CHECKING:  # pragma: no cover - import cycle broken for type checking only
    from ..models.materials import Material
    from ..models.profile import ProfileDefinition
    from . import LoadedSection

_log = get_logger("geometry.features")

#: Below this a "pocket" is drawing noise or a chamfer, not a feature [mm].
MIN_POCKET_DEPTH = 0.8
#: Two depths closer than this are the same depth; two spans closer than this
#: are the same span. Aluminium is extruded to about ±0.1 mm, so 0.05 mm is
#: below what the die itself can hold.
TOLERANCE = 0.05


# --------------------------------------------------------------------------- #
# Convex hull, keeping collinear points
# --------------------------------------------------------------------------- #
def convex_hull_indices(
    points: Sequence[Point], *, keep_collinear: bool = True, tolerance: float = TOLERANCE
) -> list[int]:
    """Indices of the points on the convex hull, in counter-clockwise order.

    With ``keep_collinear`` the points lying *along* a hull edge are kept as
    hull points. That is unusual for a hull routine and essential here: it is
    what makes a groove in a flat face measurable (see the module docstring).

    ``tolerance`` is an absolute distance: a point within it of the line
    through two others counts as collinear, so a face drawn with a 0.01 mm
    kink is still one face.
    """
    unique: list[tuple[float, float, int]] = []
    seen: set[tuple[float, float]] = set()
    for index, (x, y) in enumerate(points):
        key = (round(x, 9), round(y, 9))
        if key in seen:
            continue
        seen.add(key)
        unique.append((x, y, index))
    if len(unique) < 3:
        return [item[2] for item in unique]
    unique.sort()

    def turn(o: tuple[float, float, int], a: tuple[float, float, int],
             b: tuple[float, float, int]) -> float:
        """Twice the signed area of the triangle, scaled to a distance."""
        area2 = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        base = math.hypot(b[0] - o[0], b[1] - o[1])
        return area2 / base if base > tolerance else area2

    def build(source: Iterable[tuple[float, float, int]]) -> list[tuple[float, float, int]]:
        chain: list[tuple[float, float, int]] = []
        for point in source:
            while len(chain) >= 2:
                deviation = turn(chain[-2], chain[-1], point)
                if deviation < -tolerance:
                    chain.pop()
                elif not keep_collinear and deviation <= tolerance:
                    chain.pop()
                else:
                    break
            chain.append(point)
        return chain

    lower = build(unique)
    upper = build(reversed(unique))
    return [item[2] for item in lower[:-1] + upper[:-1]]


# --------------------------------------------------------------------------- #
# What a pocket is made of
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Band:
    """The void of a pocket over one depth interval, measured exactly."""

    #: Depth below the mouth at which the band starts and ends [mm].
    start_depth: float
    end_depth: float
    #: Clear span from the leftmost to the rightmost wall [mm].
    span: float
    #: Total open width, which is less than the span when the void is split by
    #: a tongue of material [mm].
    open_width: float
    #: How many separate openings the scan line crossed.
    parts: int
    #: Offset of the middle of the span from the middle of the mouth [mm].
    offset: float

    @property
    def height(self) -> float:
        return self.end_depth - self.start_depth


@dataclass(frozen=True)
class Step:
    """Consecutive bands of the same span and the same opening count."""

    start_depth: float
    end_depth: float
    span: float
    open_width: float
    parts: int
    offset: float

    @property
    def height(self) -> float:
        return self.end_depth - self.start_depth

    def describe(self) -> str:
        return f"{self.span:.1f} mm wide × {self.height:.1f} mm deep"


@dataclass(frozen=True)
class Pocket:
    """A concavity in the outline: the raw material of every feature."""

    #: The wall, from one lip of the mouth to the other, in drawing coordinates.
    wall: tuple[Point, ...]
    #: Midpoint of the mouth, in drawing coordinates.
    centre: Point
    #: Unit vector pointing out of the material, away from the mouth.
    direction: Point
    #: Unit vector along the mouth, from the first lip to the second.
    axis: Point
    #: Chord across the mouth [mm].
    mouth: float
    #: Deepest point of the void below the mouth [mm].
    depth: float
    #: Void area of the pocket [mm^2].
    area: float
    steps: tuple[Step, ...] = ()
    bands: tuple[Band, ...] = ()

    @property
    def widest(self) -> float:
        return max((step.span for step in self.steps), default=self.mouth)

    @property
    def undercut(self) -> float:
        """How much wider the cavity is than its own mouth [mm].

        Positive means the pocket cannot be milled straight in from the face —
        which is exactly what a hardware groove's retaining lips are for.
        """
        return max(0.0, self.widest - self.mouth)

    @property
    def floor(self) -> Point:
        """The middle of the deepest floor of the pocket, in drawing coordinates.

        Taken as a point on the centreline rather than as whichever wall vertex
        happens to be deepest, so a flat floor gives the same answer however the
        drawing happened to order its vertices.
        """
        if not self.bands:
            return self.centre
        deepest = max(self.bands, key=lambda band: band.end_depth)
        lateral = deepest.offset
        return (
            self.centre[0] - self.direction[0] * self.depth + self.axis[0] * lateral,
            self.centre[1] - self.direction[1] * self.depth + self.axis[1] * lateral,
        )


def _local_frame(a: Point, b: Point) -> tuple[Point, Point, Point]:
    """Mouth axis, inward normal and midpoint for the chord ``a``–``b``.

    The outline is walked counter-clockwise, so material lies to the left of
    every edge and the outward normal of the chord is its right-hand normal.
    """
    dx, dy = b[0] - a[0], b[1] - a[1]
    span = math.hypot(dx, dy)
    if span <= 0:
        return (1.0, 0.0), (0.0, -1.0), a
    axis = (dx / span, dy / span)
    outward = (axis[1], -axis[0])
    inward = (-outward[0], -outward[1])
    return axis, inward, ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _scan(local: Sequence[Point], depth: float) -> list[tuple[float, float]]:
    """Open intervals of the closed polygon ``local`` at the given depth.

    ``local`` is in pocket coordinates: x along the mouth, y down into the
    material. Even-odd crossing with a half-open rule on the lower endpoint,
    which is the standard way to make a vertex exactly on the scan line count
    once rather than twice.
    """
    crossings: list[float] = []
    count = len(local)
    for index in range(count):
        x1, y1 = local[index]
        x2, y2 = local[(index + 1) % count]
        if (y1 <= depth < y2) or (y2 <= depth < y1):
            crossings.append(x1 + (depth - y1) * (x2 - x1) / (y2 - y1))
    crossings.sort()
    return [
        (crossings[i], crossings[i + 1])
        for i in range(0, len(crossings) - 1, 2)
        if crossings[i + 1] - crossings[i] > TOLERANCE
    ]


def _bands(local: Sequence[Point], mouth: float) -> list[Band]:
    """Describe the void exactly, one band per distinct pair of vertex depths."""
    depths = sorted({round(point[1], 6) for point in local})
    merged: list[float] = []
    for depth in depths:
        if not merged or depth - merged[-1] > TOLERANCE:
            merged.append(depth)
    bands: list[Band] = []
    for lower, upper in zip(merged, merged[1:]):
        intervals = _scan(local, (lower + upper) / 2.0)
        if not intervals:
            continue
        span = intervals[-1][1] - intervals[0][0]
        open_width = sum(end - start for start, end in intervals)
        centre = (intervals[0][0] + intervals[-1][1]) / 2.0
        bands.append(
            Band(
                start_depth=lower,
                end_depth=upper,
                span=span,
                open_width=open_width,
                parts=len(intervals),
                offset=centre - mouth / 2.0,
            )
        )
    return bands


def _steps(bands: Sequence[Band]) -> list[Step]:
    """Merge bands that a machinist would call the same cut."""
    steps: list[Step] = []
    for band in bands:
        if (
            steps
            and steps[-1].parts == band.parts
            and abs(steps[-1].span - band.span) <= TOLERANCE
            and abs(steps[-1].end_depth - band.start_depth) <= TOLERANCE
        ):
            previous = steps[-1]
            steps[-1] = Step(
                start_depth=previous.start_depth,
                end_depth=band.end_depth,
                span=max(previous.span, band.span),
                open_width=max(previous.open_width, band.open_width),
                parts=band.parts,
                offset=previous.offset,
            )
        else:
            steps.append(
                Step(
                    start_depth=band.start_depth,
                    end_depth=band.end_depth,
                    span=band.span,
                    open_width=band.open_width,
                    parts=band.parts,
                    offset=band.offset,
                )
            )
    return steps


def find_pockets(
    points: Sequence[Point], *, min_depth: float = MIN_POCKET_DEPTH
) -> list[Pocket]:
    """Every concavity of a closed outline, measured.

    The outline is oriented counter-clockwise first, so "out of the material"
    is well defined no matter how the DXF was drawn.
    """
    outline = ensure_orientation(list(points), True)
    if len(outline) < 4:
        return []
    on_hull = set(convex_hull_indices(outline))
    if len(on_hull) >= len(outline):
        return []  # convex: no pockets at all

    count = len(outline)
    hull_order = sorted(on_hull)
    pockets: list[Pocket] = []
    for position, start in enumerate(hull_order):
        end = hull_order[(position + 1) % len(hull_order)]
        interior = [(start + step) % count for step in range(1, (end - start) % count)]
        if not interior:
            continue
        a, b = outline[start], outline[end]
        wall = [a] + [outline[index] for index in interior] + [b]
        axis, inward, centre = _local_frame(a, b)
        local = [
            (
                (point[0] - a[0]) * axis[0] + (point[1] - a[1]) * axis[1],
                (point[0] - a[0]) * inward[0] + (point[1] - a[1]) * inward[1],
            )
            for point in wall
        ]
        depth = max(point[1] for point in local)
        if depth < min_depth:
            continue
        mouth = math.dist(a, b)
        bands = _bands(local, mouth)
        if not bands:
            continue
        pockets.append(
            Pocket(
                wall=tuple(wall),
                centre=centre,
                direction=(-inward[0], -inward[1]),
                axis=axis,
                mouth=mouth,
                depth=depth,
                area=abs(signed_area(local)),
                steps=tuple(_steps(bands)),
                bands=tuple(bands),
            )
        )
    return pockets


# --------------------------------------------------------------------------- #
# Naming what was found
# --------------------------------------------------------------------------- #
class FeatureKind(StrEnum):
    """What a pocket turned out to be."""

    EURO_GROOVE = "euro_groove"
    GLAZING_REBATE = "glazing_rebate"
    GASKET_GROOVE = "gasket_groove"
    SCREW_PORT = "screw_port"
    THERMAL_BREAK_CHANNEL = "thermal_break_channel"
    BEAD_CLIP = "bead_clip"
    POCKET = "pocket"

    def label(self, language: Any = None) -> str:
        """What this feature is called, in the reader's language."""
        from ..i18n import translate

        return translate(f"feature.{self.value}", language)

    @property
    def hebrew(self) -> str:
        return self.label("he")


@dataclass(frozen=True)
class FeatureSpec:
    """One row of the recognition table. Data, not logic — see the docstring."""

    kind: FeatureKind
    mouth: tuple[float, float]
    depth: tuple[float, float]
    #: Widest span anywhere in the pocket. ``None`` accepts any.
    span: tuple[float, float] | None = None
    #: Widest span divided by the mouth. This is what separates a slot that
    #: merely has retaining lips from a cavity that balloons out behind a slit,
    #: which is the difference between a gasket groove and a screw port.
    span_ratio: tuple[float, float] | None = None
    #: ``True`` demands retaining lips, ``False`` demands their absence,
    #: ``None`` does not care.
    undercut: bool | None = None
    note: str = ""

    def matches(self, pocket: Pocket) -> bool:
        if not self.mouth[0] <= pocket.mouth <= self.mouth[1]:
            return False
        if not self.depth[0] <= pocket.depth <= self.depth[1]:
            return False
        if self.span is not None and not self.span[0] <= pocket.widest <= self.span[1]:
            return False
        if self.span_ratio is not None:
            if pocket.mouth <= 0:
                return False
            ratio = pocket.widest / pocket.mouth
            if not self.span_ratio[0] <= ratio <= self.span_ratio[1]:
                return False
        if self.undercut is True and pocket.undercut < 0.4:
            return False
        if self.undercut is False and pocket.undercut >= 0.4:
            return False
        return True


#: The shipped recognition table, tried in order — the first row that fits
#: names the pocket. Narrow, distinctive features come before broad ones, so a
#: hardware groove is never swallowed by the generic rebate row.
DEFAULT_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        kind=FeatureKind.THERMAL_BREAK_CHANNEL,
        mouth=(1.2, 4.5),
        depth=(1.0, 5.0),
        undercut=True,
        note="Rolled-in channel for a polyamide strip; confirmed by finding its pair.",
    ),
    FeatureSpec(
        kind=FeatureKind.SCREW_PORT,
        mouth=(1.5, 6.0),
        depth=(4.0, 12.0),
        span=(4.0, 10.0),
        span_ratio=(1.5, 5.0),
        undercut=True,
        note="Open screw channel; the thread is formed by the screw itself.",
    ),
    FeatureSpec(
        kind=FeatureKind.GASKET_GROOVE,
        mouth=(2.0, 6.0),
        depth=(1.5, 8.0),
        span=(2.0, 8.0),
        span_ratio=(1.0, 1.6),
        note="Takes the barbed foot of an EPDM gasket.",
    ),
    FeatureSpec(
        kind=FeatureKind.EURO_GROOVE,
        mouth=(13.0, 17.0),
        depth=(9.0, 17.0),
        note="Hardware groove. Check the axis dimension against the system drawing.",
    ),
    FeatureSpec(
        kind=FeatureKind.BEAD_CLIP,
        mouth=(4.0, 12.0),
        depth=(2.0, 8.0),
        undercut=True,
        note="Snap seat for a glazing bead.",
    ),
    FeatureSpec(
        kind=FeatureKind.GLAZING_REBATE,
        mouth=(10.0, 90.0),
        depth=(5.0, 60.0),
        note="Receives the glass unit; the bite is read from the shallowest step.",
    ),
)


@dataclass(frozen=True)
class DetectedFeature:
    """A pocket, named, with the measurements a shop would write down."""

    kind: FeatureKind
    pocket: Pocket
    note: str = ""
    #: Filled in for a rebate: the widest step is what the glass sits in.
    glass_capacity: float | None = None
    #: Filled in for a rebate: how far the glass is held [mm].
    bite: float | None = None
    #: Filled in for a thermal-break channel once its partner is found [mm].
    strip_width: float | None = None

    @property
    def position(self) -> Point:
        return self.pocket.centre

    def describe(self) -> str:
        parts = [
            f"{self.kind.hebrew} ({self.kind.value})",
            f"mouth {self.pocket.mouth:.1f} mm",
            f"depth {self.pocket.depth:.1f} mm",
        ]
        if self.pocket.undercut > 0.4:
            parts.append(f"undercut {self.pocket.undercut:.1f} mm")
        if self.glass_capacity is not None:
            parts.append(f"glass up to {self.glass_capacity:.1f} mm")
        if self.strip_width is not None:
            parts.append(f"strip {self.strip_width:.1f} mm")
        return ", ".join(parts)


#: Polyamide strip widths that are commonly stocked [mm]. A measured gap within
#: half a millimetre of one of these is reported as that size; anything else is
#: reported as measured, because inventing a size is worse than admitting one.
COMMON_STRIP_WIDTHS: tuple[float, ...] = (
    14.8, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 35.0, 36.0, 40.0,
)


def classify(pocket: Pocket, specs: Sequence[FeatureSpec] = DEFAULT_SPECS) -> DetectedFeature:
    """Name one pocket, or hand it back unnamed with its measurements intact."""
    for spec in specs:
        if not spec.matches(pocket):
            continue
        glass_capacity = bite = None
        if spec.kind is FeatureKind.GLAZING_REBATE and pocket.steps:
            # The glass sits in the first step; anything deeper is the gasket
            # groove or a drainage slot, not glass.
            first = pocket.steps[0]
            glass_capacity = first.span
            bite = first.height
        return DetectedFeature(
            kind=spec.kind,
            pocket=pocket,
            note=spec.note,
            glass_capacity=glass_capacity,
            bite=bite,
        )
    return DetectedFeature(kind=FeatureKind.POCKET, pocket=pocket)


def _pair_thermal_break(features: list[DetectedFeature]) -> list[DetectedFeature]:
    """Confirm polyamide channels by finding them in facing pairs.

    A single small undercut groove could be anything. Two of them looking at
    each other across a gap, with nothing between, is a thermal break — and the
    distance between their floors is the strip width to order.
    """
    candidates = [
        index
        for index, feature in enumerate(features)
        if feature.kind is FeatureKind.THERMAL_BREAK_CHANNEL
    ]
    paired: dict[int, float] = {}
    for position, first in enumerate(candidates):
        for second in candidates[position + 1 :]:
            one, other = features[first].pocket, features[second].pocket
            facing = (
                one.direction[0] * other.direction[0] + one.direction[1] * other.direction[1]
            )
            if facing > -0.85:  # the two mouths must look at one another
                continue
            # Reject two grooves that face each other but are offset sideways,
            # which happens on opposite corners of the same chamber.
            span = (other.centre[0] - one.centre[0], other.centre[1] - one.centre[1])
            along = span[0] * one.direction[0] + span[1] * one.direction[1]
            across = math.hypot(*span) ** 2 - along**2
            if along <= 0 or math.sqrt(max(0.0, across)) > 6.0:
                continue
            # The strip spans from one floor to the other: the clear gap between
            # the two faces plus the depth it is rolled into on each side.
            gap = along + one.depth + other.depth
            if not 8.0 <= gap <= 60.0:
                continue
            width = min(
                COMMON_STRIP_WIDTHS,
                key=lambda standard: abs(standard - gap),
            )
            resolved = width if abs(width - gap) <= 0.5 else gap
            paired[first] = paired.get(first, resolved)
            paired[second] = paired.get(second, resolved)
    return [
        (
            DetectedFeature(
                kind=feature.kind,
                pocket=feature.pocket,
                note=feature.note,
                strip_width=paired[index],
            )
            if index in paired
            else (
                # An unpaired candidate is demoted: on its own it is just a
                # small undercut groove, and calling it a thermal break would
                # put a polyamide strip on a purchase order that nobody needs.
                DetectedFeature(
                    kind=FeatureKind.GASKET_GROOVE,
                    pocket=feature.pocket,
                    note="Small undercut groove; no facing channel, so not a thermal break.",
                )
                if feature.kind is FeatureKind.THERMAL_BREAK_CHANNEL
                else feature
            )
        )
        for index, feature in enumerate(features)
    ]


def detect_features(
    outline: Sequence[Point],
    *,
    specs: Sequence[FeatureSpec] = DEFAULT_SPECS,
    min_depth: float = MIN_POCKET_DEPTH,
) -> list[DetectedFeature]:
    """Every feature on one closed outline, deepest first."""
    found = [classify(pocket, specs) for pocket in find_pockets(outline, min_depth=min_depth)]
    found = _pair_thermal_break(found)
    found.sort(key=lambda feature: feature.pocket.depth, reverse=True)
    return found


# --------------------------------------------------------------------------- #
# Closed screw ports, which are chambers rather than pockets
# --------------------------------------------------------------------------- #
def circularity(area: float, perimeter: float) -> float:
    """4 pi A / P^2 — exactly 1 for a circle, less for anything else."""
    if perimeter <= 0:
        return 0.0
    return 4.0 * math.pi * area / (perimeter * perimeter)


def closed_screw_ports(
    region: Region, *, diameter: tuple[float, float] = (3.5, 10.0), roundness: float = 0.80
) -> list[tuple[Point, float]]:
    """Chambers round enough and small enough to be screw ports.

    Returns the centre and equivalent diameter of each. A boss that has been
    drawn closed rather than slit is otherwise indistinguishable from a small
    chamber, and the difference matters: one takes a screw, the other is air.
    """
    ports: list[tuple[Point, float]] = []
    for hole in region.holes:
        area = hole.area
        if area <= 0:
            continue
        equivalent = 2.0 * math.sqrt(area / math.pi)
        if not diameter[0] <= equivalent <= diameter[1]:
            continue
        if circularity(area, hole.perimeter) < roundness:
            continue
        min_x, min_y, max_x, max_y = bounding_box(hole.points)
        ports.append((((min_x + max_x) / 2.0, (min_y + max_y) / 2.0), equivalent))
    return ports


# --------------------------------------------------------------------------- #
# Polyamide drawn as its own part
# --------------------------------------------------------------------------- #
#: Layer names that say "this is the insulating bar", in the spellings the
#: suppliers actually use in their DXFs.
THERMAL_LAYER_HINTS: tuple[str, ...] = (
    "thermal", "polyamide", "isolator", "isolierung", "insul", "pa66", "_pa",
)


@dataclass(frozen=True)
class ThermalStrip:
    """A polyamide bar that was drawn as a separate closed region."""

    #: Centre of the strip [mm].
    centre: Point
    #: Width across the gap it bridges — the size to order [mm].
    width: float
    #: Section area of the strip [mm^2].
    area: float
    #: Why it was taken to be polyamide: the layer it was drawn on, its
    #: position between two shells, or both.
    evidence: tuple[str, ...] = ()


def _centroid(region: Region) -> Point:
    min_x, min_y, max_x, max_y = region.bounds()
    return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)


def _named_thermal(region: Region) -> bool:
    layers = " ".join(region.shell.source_layers).casefold()
    return any(hint in layers for hint in THERMAL_LAYER_HINTS)


def thermal_break_strips(topology: SectionTopology) -> list[ThermalStrip]:
    """Polyamide bars drawn as their own regions rather than as channels.

    Most suppliers draw the insulating bar on its own layer, so the layer name
    is taken as evidence when it is there. It is not relied on alone: a strip
    also has to sit in the gap between two aluminium shells and be small
    compared with them, which is what stops a mullion drawn on a layer called
    ``TB-MULLION`` from being ordered as polyamide.
    """
    shells = [region for region in topology.regions if region.depth == 0]
    if len(shells) < 3:
        return []  # two shells and no third part: nothing was drawn as a strip
    largest = max(region.area for region in shells)

    candidates = [
        region for region in shells
        if region.area <= 0.5 * largest or _named_thermal(region)
    ]
    frames = [region for region in shells if region not in candidates]
    if len(frames) < 2:
        return []

    strips: list[ThermalStrip] = []
    for candidate in candidates:
        centre = _centroid(candidate)
        near = sorted(frames, key=lambda frame: math.dist(_centroid(frame), centre))[:2]
        one, other = _centroid(near[0]), _centroid(near[1])
        span = (other[0] - one[0], other[1] - one[1])
        length = math.hypot(*span)
        if length <= 0:
            continue
        axis = (span[0] / length, span[1] / length)

        def along(point: Point) -> float:
            return (point[0] - one[0]) * axis[0] + (point[1] - one[1]) * axis[1]

        # The strip has to lie between the two shells, not beside them.
        if not 0.0 < along(centre) < length:
            continue
        projections = [along(point) for point in candidate.shell.points]
        width = max(projections) - min(projections)
        if width <= 0:
            continue
        nearest = min(COMMON_STRIP_WIDTHS, key=lambda standard: abs(standard - width))
        evidence = ["between two shells"]
        if _named_thermal(candidate):
            evidence.insert(0, "drawn on an insulation layer")
        strips.append(
            ThermalStrip(
                centre=centre,
                width=nearest if abs(nearest - width) <= 0.5 else width,
                area=candidate.area,
                evidence=tuple(evidence),
            )
        )
    return strips


# --------------------------------------------------------------------------- #
# The report a profile carries once it is loaded
# --------------------------------------------------------------------------- #
@dataclass
class ProfileFeatureReport:
    """Everything read off a section the moment it is loaded.

    The three headline numbers are the ones an estimator reaches for first:
    what the bar weighs, how much surface has to be coated, and whether it fits
    the machine. They are derived from the geometry, so they cannot drift out of
    step with the drawing the way a typed-in figure does.
    """

    #: Overall envelope (min_x, min_y, max_x, max_y) [mm].
    bounds: tuple[float, float, float, float]
    #: Linear mass [kg/m].
    mass_per_metre: float
    #: Coated surface per metre of bar [m^2/m]. Only outward-facing aluminium
    #: is counted: chambers are not painted, and polyamide is not aluminium.
    paint_area_per_metre: float
    features: list[DetectedFeature] = field(default_factory=list)
    screw_ports: list[tuple[Point, float]] = field(default_factory=list)
    #: Polyamide bars that were drawn as their own regions.
    strips: list[ThermalStrip] = field(default_factory=list)
    #: Set when a thermal break was confirmed, by a strip or a facing pair of
    #: channels.
    thermal_break_width: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    def of_kind(self, kind: FeatureKind) -> list[DetectedFeature]:
        return [feature for feature in self.features if feature.kind is kind]

    @property
    def glass_capacity(self) -> float | None:
        """The thickest glass the deepest rebate will take [mm]."""
        rebates = self.of_kind(FeatureKind.GLAZING_REBATE)
        capacities = [r.glass_capacity for r in rebates if r.glass_capacity is not None]
        return max(capacities) if capacities else None

    @property
    def takes_euro_hardware(self) -> bool:
        return bool(self.of_kind(FeatureKind.EURO_GROOVE))

    def summary_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for the property panel, in Hebrew."""
        rows = [
            ("מידות חוץ", f"{self.width:.1f} × {self.height:.1f} מ\"מ"),
            ("משקל", f"{self.mass_per_metre:.3f} ק\"ג/מ'"),
            ("שטח צביעה", f"{self.paint_area_per_metre:.4f} מ\"ר/מ'"),
        ]
        if self.glass_capacity is not None:
            rows.append(("עובי זיגוג מרבי", f"{self.glass_capacity:.1f} מ\"מ"))
        if self.takes_euro_hardware:
            rows.append(("חריץ אירו", "כן"))
        if self.thermal_break_width is not None:
            rows.append(("רוחב פוליאמיד", f"{self.thermal_break_width:.1f} מ\"מ"))
        if self.screw_ports:
            rows.append(("תעלות בורג", str(len(self.screw_ports))))
        return rows


def paint_area_per_metre(topology: SectionTopology) -> float:
    """Coated surface of one metre of bar [m^2/m].

    Only the outer boundary of each solid region is counted. The inside of a
    chamber is sealed by the extrusion and never sees paint or anodising, so
    charging for it would overstate every coating line on every quotation.
    """
    outer = sum(region.shell.perimeter for region in topology.regions if region.depth % 2 == 0)
    return outer / 1000.0  # mm of perimeter x 1000 mm of length / 1e6 mm^2 per m^2


def describe_section(
    topology: SectionTopology,
    *,
    mass_per_metre: float,
    specs: Sequence[FeatureSpec] = DEFAULT_SPECS,
) -> ProfileFeatureReport:
    """Read the whole section: envelope, mass, coating area and features."""
    regions = [region for region in topology.regions if region.depth % 2 == 0]
    if not regions:
        raise ValueError("the section has no solid material")

    features: list[DetectedFeature] = []
    ports: list[tuple[Point, float]] = []
    for region in regions:
        features.extend(detect_features(region.shell.points, specs=specs))
        ports.extend(closed_screw_ports(region))

    # Channels on two different shells face each other across the break, so the
    # pairing has to be done once over everything, not per shell.
    features = _pair_thermal_break(features)
    features.sort(key=lambda feature: feature.pocket.depth, reverse=True)

    widths = [
        feature.strip_width
        for feature in features
        if feature.kind is FeatureKind.THERMAL_BREAK_CHANNEL and feature.strip_width
    ]
    strips = thermal_break_strips(topology)
    widths.extend(strip.width for strip in strips)

    warnings: list[str] = []
    shells = [region for region in topology.regions if region.depth == 0]
    if len(shells) > 1 and not widths:
        warnings.append(
            "The section is drawn in more than one part but neither a polyamide "
            "strip nor a facing pair of channels was found; the thermal break "
            "may be on a layer that was not imported."
        )

    return ProfileFeatureReport(
        bounds=topology.bounds(),
        mass_per_metre=mass_per_metre,
        paint_area_per_metre=paint_area_per_metre(topology),
        features=features,
        screw_ports=ports,
        strips=strips,
        thermal_break_width=max(widths) if widths else None,
        warnings=warnings,
    )


def features_for_section(
    section: "LoadedSection",
    *,
    material: "Material | str | None" = None,
    specs: Sequence[FeatureSpec] = DEFAULT_SPECS,
) -> ProfileFeatureReport:
    """Read the features of a freshly imported DXF section."""
    from ..models.materials import get_material

    resolved = material if hasattr(material, "density") else get_material(
        material if isinstance(material, str) else None
    )
    area = sum(region.area for region in section.topology.regions if region.depth % 2 == 0)
    return describe_section(
        section.topology,
        mass_per_metre=resolved.mass_per_metre(area),
        specs=specs,
    )


def features_for_profile(
    profile: "ProfileDefinition", *, specs: Sequence[FeatureSpec] = DEFAULT_SPECS
) -> ProfileFeatureReport:
    """Read the features of a profile already in the library.

    The declared linear mass is preferred when the supplier published one, since
    it accounts for the extrusion tolerance the drawing does not show; the
    computed figure is used otherwise and the difference is reported.
    """
    from . import section_from_profile

    section = section_from_profile(profile)
    report = features_for_section(section, material=profile.material, specs=specs)
    declared = profile.mass_per_metre_declared
    if declared:
        if abs(declared - report.mass_per_metre) > max(0.05, 0.05 * declared):
            report.warnings.append(
                f"The supplier quotes {declared:.3f} kg/m but the drawing gives "
                f"{report.mass_per_metre:.3f} kg/m; one of the two is wrong."
            )
        report.mass_per_metre = declared
    return report


__all__ = [
    "Band",
    "COMMON_STRIP_WIDTHS",
    "DEFAULT_SPECS",
    "DetectedFeature",
    "FeatureKind",
    "FeatureSpec",
    "MIN_POCKET_DEPTH",
    "Pocket",
    "ProfileFeatureReport",
    "Step",
    "THERMAL_LAYER_HINTS",
    "ThermalStrip",
    "thermal_break_strips",
    "describe_section",
    "features_for_profile",
    "features_for_section",
    "circularity",
    "classify",
    "closed_screw_ports",
    "convex_hull_indices",
    "detect_features",
    "find_pockets",
    "paint_area_per_metre",
]
