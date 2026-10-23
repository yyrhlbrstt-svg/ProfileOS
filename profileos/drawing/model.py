"""A drawing, in millimetres, before anybody decides what to draw it on.

Everything the drawing engine produces is built from a handful of primitives
held in model coordinates — real millimetres of real aluminium and real wall.
Paper, scale and sheet size are applied later, by :mod:`profileos.drawing.sheet`,
which is what lets the same elevation appear at 1:20 on an A3 and 1:10 on an A1
without being redrawn.

Layers are not decoration. An aluminium consultant reviewing a package turns
layers on and off — dimensions off to read the geometry, hatching off to read
the dimensions — and a drawing where everything lands on layer 0 cannot be
reviewed that way. So every entity carries one, and the layer decides colour,
line weight and dash pattern in every output format.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Iterable, Iterator, Sequence

Point = tuple[float, float]


class LineType(StrEnum):
    """The dash patterns a construction drawing actually uses."""

    CONTINUOUS = "continuous"
    DASHED = "dashed"
    #: Long-short-long: a centre line, or an axis.
    CENTRE = "centre"
    #: Very short dashes: something hidden behind what is drawn.
    HIDDEN = "hidden"
    #: The line a section is taken along.
    SECTION = "section"

    def dash_pattern(self, scale: float = 1.0) -> list[float]:
        """Dash lengths in paper millimetres, empty for a solid line."""
        patterns = {
            LineType.CONTINUOUS: [],
            LineType.DASHED: [4.0, 2.0],
            LineType.CENTRE: [12.0, 2.0, 2.0, 2.0],
            LineType.HIDDEN: [1.5, 1.5],
            LineType.SECTION: [18.0, 3.0, 3.0, 3.0],
        }
        return [value * scale for value in patterns[self]]


@dataclass(frozen=True)
class Layer:
    """A named layer with the pen it is drawn with."""

    name: str
    #: Plotted line width in millimetres of paper, not of the model.
    lineweight: float = 0.25
    colour: str = "#111111"
    line_type: LineType = LineType.CONTINUOUS
    #: DXF colour index, for CAD packages that ignore true colour.
    aci: int = 7
    printable: bool = True


class Anchor(StrEnum):
    """Where a text string sits relative to its insertion point."""

    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


@dataclass(frozen=True)
class Entity:
    """Anything that can be drawn."""

    layer: str = "0"

    def bounds(self) -> tuple[float, float, float, float]:
        raise NotImplementedError

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Entity":
        raise NotImplementedError


def _bounds_of(points: Sequence[Point]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class Line(Entity):
    start: Point = (0.0, 0.0)
    end: Point = (0.0, 0.0)

    def bounds(self) -> tuple[float, float, float, float]:
        return _bounds_of([self.start, self.end])

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Line":
        return replace(
            self,
            start=(self.start[0] * scale + dx, self.start[1] * scale + dy),
            end=(self.end[0] * scale + dx, self.end[1] * scale + dy),
        )


@dataclass(frozen=True)
class Polyline(Entity):
    points: tuple[Point, ...] = ()
    closed: bool = False
    #: Painted solid rather than outlined — an arrowhead, a section flag.
    filled: bool = False

    def bounds(self) -> tuple[float, float, float, float]:
        return _bounds_of(self.points)

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Polyline":
        return replace(
            self,
            points=tuple((x * scale + dx, y * scale + dy) for x, y in self.points),
        )


@dataclass(frozen=True)
class Circle(Entity):
    centre: Point = (0.0, 0.0)
    radius: float = 1.0

    def bounds(self) -> tuple[float, float, float, float]:
        x, y = self.centre
        return (x - self.radius, y - self.radius, x + self.radius, y + self.radius)

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Circle":
        return replace(
            self,
            centre=(self.centre[0] * scale + dx, self.centre[1] * scale + dy),
            radius=self.radius * scale,
        )


@dataclass(frozen=True)
class Arc(Entity):
    """Counter-clockwise from ``start_angle`` to ``end_angle``, in degrees."""

    centre: Point = (0.0, 0.0)
    radius: float = 1.0
    start_angle: float = 0.0
    end_angle: float = 90.0

    def sample(self, segments: int = 24) -> list[Point]:
        sweep = (self.end_angle - self.start_angle) % 360.0 or 360.0
        step = sweep / max(segments, 1)
        return [
            (
                self.centre[0] + self.radius * math.cos(math.radians(self.start_angle + step * i)),
                self.centre[1] + self.radius * math.sin(math.radians(self.start_angle + step * i)),
            )
            for i in range(segments + 1)
        ]

    def bounds(self) -> tuple[float, float, float, float]:
        # The extreme of an arc may be a quadrant point rather than an end, so
        # the sampled points are used rather than the two endpoints.
        return _bounds_of(self.sample(48))

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Arc":
        return replace(
            self,
            centre=(self.centre[0] * scale + dx, self.centre[1] * scale + dy),
            radius=self.radius * scale,
        )


@dataclass(frozen=True)
class Text(Entity):
    """A string, sized in *paper* millimetres.

    Text does not scale with the drawing. A 2.5 mm note is 2.5 mm on the paper
    whether the detail around it is at 1:5 or 1:50, which is the whole point of
    a drawing standard — so the height here is a paper height and the sheet
    applies it without multiplying by the view scale.
    """

    position: Point = (0.0, 0.0)
    value: str = ""
    height: float = 2.5
    rotation: float = 0.0
    anchor: Anchor = Anchor.LEFT
    bold: bool = False

    def bounds(self) -> tuple[float, float, float, float]:
        # Text has no reliable extent in model space; the insertion point is
        # used so a stray label cannot silently inflate a view's extents.
        return (*self.position, *self.position)

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Text":
        return replace(
            self,
            position=(self.position[0] * scale + dx, self.position[1] * scale + dy),
        )


class HatchPattern(StrEnum):
    """What a material looks like in section, in the usual conventions."""

    NONE = "none"
    CONCRETE = "concrete"
    BLOCKWORK = "blockwork"
    INSULATION = "insulation"
    ALUMINIUM = "aluminium"
    GLASS = "glass"
    SEALANT = "sealant"
    STONE = "stone"
    TIMBER = "timber"
    EARTH = "earth"

    @property
    def hebrew(self) -> str:
        return {
            HatchPattern.NONE: "",
            HatchPattern.CONCRETE: "בטון",
            HatchPattern.BLOCKWORK: "בלוקים",
            HatchPattern.INSULATION: "בידוד",
            HatchPattern.ALUMINIUM: "אלומיניום",
            HatchPattern.GLASS: "זכוכית",
            HatchPattern.SEALANT: "סיליקון",
            HatchPattern.STONE: "אבן",
            HatchPattern.TIMBER: "עץ",
            HatchPattern.EARTH: "מילוי",
        }[self]


@dataclass(frozen=True)
class Hatch(Entity):
    """A filled region: the material a section cuts through."""

    boundary: tuple[Point, ...] = ()
    pattern: HatchPattern = HatchPattern.NONE
    #: Solid fill colour, used where a pattern would be too fine to read.
    fill: str | None = None
    #: Pattern spacing in paper millimetres.
    spacing: float = 2.0
    angle: float = 45.0
    holes: tuple[tuple[Point, ...], ...] = ()

    def bounds(self) -> tuple[float, float, float, float]:
        return _bounds_of(self.boundary)

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Hatch":
        return replace(
            self,
            boundary=tuple((x * scale + dx, y * scale + dy) for x, y in self.boundary),
            holes=tuple(
                tuple((x * scale + dx, y * scale + dy) for x, y in hole) for hole in self.holes
            ),
        )


# --------------------------------------------------------------------------- #
# The standard layer set
# --------------------------------------------------------------------------- #
#: The layers a shop drawing package uses, with the pen weights a plotter needs
#: to make the drawing readable: heavy for what is cut, light for what is behind.
STANDARD_LAYERS: dict[str, Layer] = {
    layer.name: layer
    for layer in (
        Layer("ALU-CUT", lineweight=0.50, colour="#1b3a5c", aci=5),
        Layer("ALU-SEEN", lineweight=0.25, colour="#2f6ea8", aci=4),
        Layer("GLASS", lineweight=0.18, colour="#3f8fbf", aci=4),
        Layer("GASKET", lineweight=0.18, colour="#404040", aci=8),
        Layer("STRUCTURE", lineweight=0.50, colour="#2b2b2b", aci=7),
        Layer("INSULATION", lineweight=0.18, colour="#7a6a3a", aci=42),
        Layer("CLADDING", lineweight=0.35, colour="#6b5a4a", aci=33),
        Layer("MEMBRANE", lineweight=0.25, colour="#a33", aci=1, line_type=LineType.DASHED),
        Layer("FLASHING", lineweight=0.35, colour="#555", aci=8),
        Layer("FIXING", lineweight=0.35, colour="#333", aci=7),
        Layer("SEALANT", lineweight=0.18, colour="#333", aci=9),
        Layer("DIM", lineweight=0.13, colour="#8a1f1f", aci=1),
        Layer("TEXT", lineweight=0.18, colour="#111111", aci=7),
        Layer("SYMBOL", lineweight=0.18, colour="#1b3a5c", aci=5),
        # Opening symbols get their own two layers so the reader can tell which
        # way a leaf swings from the line style alone, and can switch them off.
        Layer("OPEN-IN", lineweight=0.18, colour="#1b3a5c", aci=5, line_type=LineType.DASHED),
        Layer("OPEN-OUT", lineweight=0.18, colour="#2f6ea8", aci=4),
        Layer("HIDDEN", lineweight=0.13, colour="#777", aci=8, line_type=LineType.HIDDEN),
        Layer("CENTRE", lineweight=0.13, colour="#8a1f1f", aci=1, line_type=LineType.CENTRE),
        Layer("SHEET", lineweight=0.35, colour="#111111", aci=7),
        Layer("SHEET-HEAVY", lineweight=0.70, colour="#111111", aci=7),
        Layer("GRID", lineweight=0.13, colour="#999", aci=8, line_type=LineType.CENTRE),
    )
}


@dataclass
class Drawing:
    """A set of entities in model millimetres, with the layers they use."""

    name: str = "drawing"
    entities: list[Entity] = field(default_factory=list)
    layers: dict[str, Layer] = field(default_factory=lambda: dict(STANDARD_LAYERS))

    def add(self, entity: Entity) -> Entity:
        if entity.layer not in self.layers:
            # An unknown layer is a typo more often than an intention, and a
            # typo that silently creates a layer is one nobody finds.
            raise KeyError(
                f"Layer {entity.layer!r} is not defined. Known layers: "
                + ", ".join(sorted(self.layers))
            )
        self.entities.append(entity)
        return entity

    def extend(self, entities: Iterable[Entity]) -> None:
        for entity in entities:
            self.add(entity)

    def __iter__(self) -> Iterator[Entity]:
        return iter(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    def on_layer(self, name: str) -> list[Entity]:
        return [entity for entity in self.entities if entity.layer == name]

    def bounds(self) -> tuple[float, float, float, float]:
        """Extents of everything drawn, or a unit box when nothing is."""
        boxes = [entity.bounds() for entity in self.entities]
        if not boxes:
            return (0.0, 0.0, 1.0, 1.0)
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    @property
    def width(self) -> float:
        left, _, right, _ = self.bounds()
        return right - left

    @property
    def height(self) -> float:
        _, bottom, _, top = self.bounds()
        return top - bottom

    def transformed(self, dx: float, dy: float, scale: float = 1.0) -> "Drawing":
        return Drawing(
            name=self.name,
            entities=[entity.transformed(dx, dy, scale) for entity in self.entities],
            layers=dict(self.layers),
        )

    def merged(self, other: "Drawing") -> "Drawing":
        return Drawing(
            name=self.name,
            entities=[*self.entities, *other.entities],
            layers={**self.layers, **other.layers},
        )


# --------------------------------------------------------------------------- #
# Small helpers used by every drawing generator
# --------------------------------------------------------------------------- #
def rectangle(
    x: float, y: float, width: float, height: float, layer: str = "0", *, closed: bool = True
) -> Polyline:
    return Polyline(
        layer=layer,
        points=((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
        closed=closed,
    )


def cross(centre: Point, size: float, layer: str = "SYMBOL") -> list[Entity]:
    half = size / 2.0
    return [
        Line(layer=layer, start=(centre[0] - half, centre[1]), end=(centre[0] + half, centre[1])),
        Line(layer=layer, start=(centre[0], centre[1] - half), end=(centre[0], centre[1] + half)),
    ]


__all__ = [
    "Anchor",
    "Arc",
    "Circle",
    "Drawing",
    "Entity",
    "Hatch",
    "HatchPattern",
    "Layer",
    "Line",
    "LineType",
    "Point",
    "Polyline",
    "STANDARD_LAYERS",
    "Text",
    "cross",
    "rectangle",
]
