"""Data model for 2D sheet nesting: glass, infill panels and sandwich boards.

Why guillotine and not free-form
--------------------------------
Aluminium bars are a one-dimensional problem; glass is not. But glass is also
not the free-form nesting problem that sheet-metal software solves, because a
glass cutting table cannot make a cut that stops in the middle of a sheet. The
cutting wheel scores a line edge to edge and the piece is then snapped along
that score. Every cut therefore separates the current rectangle into exactly
two rectangles — a *guillotine* cut. The same is true of the beam saws used for
composite and sandwich panels.

A layout that is not guillotine-decomposable cannot be produced, no matter how
good its area utilisation looks. This module treats that as a hard constraint
and :func:`~profileos.nesting.guillotine.verify_guillotine` proves it for every
layout the engine emits.

Stages
------
Cutting lines are also limited in how many times they may turn:

``2``
    Cross cuts split the sheet into full-width *strips*; rip cuts then split
    each strip into pieces. Every piece in a strip shares that strip's height.
    This is what an unattended Lisec or Bystronic line runs.
``3``
    A third cut trims individual pieces to their final height, so pieces in a
    strip may differ in height. Most attended tables manage this.
``None``
    Unlimited turns — a full recursive guillotine layout.

Fewer stages means more waste and less scheduling freedom; the shop's machine
decides, so :class:`SheetSpec` carries it as an input rather than a preference.

Coordinates
-----------
The origin is the bottom-left corner of the *usable* area, i.e. after the edge
trim has been taken off all four sides. ``x`` runs along the sheet width and
``y`` along its height, both in millimetres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Iterator

from ..core.errors import InfeasibleNestingError, NestingError


class Grain(StrEnum):
    """Whether a part may be turned 90 degrees when it is nested.

    Rotation is free for clear float glass and for a coated unit whose coating
    is symmetric about both axes. It is forbidden the moment the surface has a
    direction: patterned and fluted glass, screen-printed or digitally printed
    panels, brushed or anodised aluminium composite, and any panel the
    architect has specified with a running grain across an elevation.
    """

    #: Free to rotate; the packer will try both orientations.
    NONE = "none"
    #: The part's height runs along the sheet height. No rotation.
    VERTICAL = "vertical"
    #: The part's height runs along the sheet width. Rotated once, then fixed.
    HORIZONTAL = "horizontal"


class FreeRectRule(StrEnum):
    """How the packer picks which free rectangle receives the next part."""

    BEST_AREA_FIT = "best_area"
    BEST_SHORT_SIDE_FIT = "best_short_side"
    BEST_LONG_SIDE_FIT = "best_long_side"
    WORST_AREA_FIT = "worst_area"
    BOTTOM_LEFT = "bottom_left"


class SplitRule(StrEnum):
    """How the leftover of a free rectangle is split after a part is placed."""

    SHORTER_AXIS = "shorter_axis"
    LONGER_AXIS = "longer_axis"
    SHORTER_LEFTOVER = "shorter_leftover"
    LONGER_LEFTOVER = "longer_leftover"
    MIN_AREA = "min_area"
    MAX_AREA = "max_area"


@dataclass(frozen=True)
class SheetPart:
    """One rectangular part to be cut out of a sheet.

    ``width`` and ``height`` are the finished dimensions. For an insulating
    glass unit these are the *unit* dimensions, i.e. the pane size, not the
    daylight opening — the frame rebate deduction has already been applied by
    the element builder before the part reaches the nester.
    """

    part_id: str
    width: float
    height: float
    quantity: int = 1
    grain: Grain = Grain.NONE
    label: str | None = None
    #: Free-form: build-up code, thickness, element reference, elevation mark.
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise NestingError(
                "Sheet part has a non-positive dimension",
                part_id=self.part_id,
                width=self.width,
                height=self.height,
            )
        if self.quantity < 1:
            raise NestingError(
                "Sheet part quantity must be at least one",
                part_id=self.part_id,
                quantity=self.quantity,
            )

    @property
    def area(self) -> float:
        """Finished area of one piece [mm^2]."""
        return self.width * self.height

    @property
    def name(self) -> str:
        return self.label or self.part_id

    def orientations(self) -> list[tuple[float, float, bool]]:
        """``(width, height, rotated)`` options allowed by the grain rule."""
        if self.grain is Grain.HORIZONTAL:
            return [(self.height, self.width, True)]
        if self.grain is Grain.VERTICAL:
            return [(self.width, self.height, False)]
        if abs(self.width - self.height) < 1e-9:
            # A square has one distinct orientation; offering two only doubles
            # the packer's search for identical results.
            return [(self.width, self.height, False)]
        return [(self.width, self.height, False), (self.height, self.width, True)]

    def expand(self) -> Iterator["SheetPart"]:
        """Yield ``quantity`` single-piece parts, ids suffixed ``#1``, ``#2``."""
        if self.quantity == 1:
            yield self
            return
        for index in range(1, self.quantity + 1):
            yield SheetPart(
                part_id=f"{self.part_id}#{index}",
                width=self.width,
                height=self.height,
                quantity=1,
                grain=self.grain,
                # The copy number belongs on the label too. Two panes marked
                # identically on a cutting map are two panes the operator
                # cannot tell apart when they come off the table.
                label=f"{self.label} #{index}" if self.label else None,
                metadata=dict(self.metadata),
            )


@dataclass(frozen=True)
class SheetStock:
    """A stock sheet size the optimiser may cut from."""

    width: float
    height: float
    #: ``None`` means unlimited supply.
    available: int | None = None
    cost: float = 0.0
    label: str | None = None
    #: True for an off-cut drawn back out of the rack.
    is_offcut: bool = False
    offcut_id: str | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise NestingError(
                "Stock sheet has a non-positive dimension",
                width=self.width,
                height=self.height,
            )

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        kind = "offcut" if self.is_offcut else "sheet"
        return f"{self.width:g}x{self.height:g} {kind}"


@dataclass(frozen=True)
class SheetSpec:
    """Machine and material rules that govern how a sheet may be cut."""

    #: Material removed by the cut [mm]. A glass scoring wheel removes nothing
    #: — it scores and the piece is snapped — so the default is zero. A beam
    #: saw cutting composite panel removes its blade width, typically 4-5 mm.
    kerf: float = 0.0
    #: Strip taken off all four sides before any part is placed [mm]. Float
    #: glass arrives with damaged arrises and, on coated stock, a deleted
    #: coating band at the edge; neither may end up in a finished unit.
    edge_trim: float = 0.0
    #: Maximum number of times the cutting direction may turn. See module docs.
    stages: int | None = None
    #: Off-cuts with both sides at least this long go back to the rack [mm].
    min_offcut_side: float = 300.0
    #: ...and at least this much area, so slivers are not booked as stock.
    min_offcut_area: float = 250_000.0
    #: Global override: ``False`` pins every part to its drawn orientation.
    allow_rotation: bool = True

    def __post_init__(self) -> None:
        if self.kerf < 0:
            raise NestingError("Kerf cannot be negative", kerf=self.kerf)
        if self.edge_trim < 0:
            raise NestingError("Edge trim cannot be negative", edge_trim=self.edge_trim)
        if self.stages is not None and self.stages < 2:
            raise NestingError(
                "A guillotine cutting plan needs at least two stages",
                stages=self.stages,
            )

    def usable(self, stock: SheetStock) -> tuple[float, float]:
        """Width and height left after the edge trim [mm]."""
        return (
            stock.width - 2.0 * self.edge_trim,
            stock.height - 2.0 * self.edge_trim,
        )

    def usable_area(self, stock: SheetStock) -> float:
        width, height = self.usable(stock)
        return max(width, 0.0) * max(height, 0.0)

    def fits(self, part: SheetPart, stock: SheetStock) -> bool:
        """True when at least one allowed orientation fits inside the trim."""
        width, height = self.usable(stock)
        for part_w, part_h, rotated in self.orientations(part):
            if rotated and not self.allow_rotation:
                continue
            if part_w <= width + 1e-9 and part_h <= height + 1e-9:
                return True
        return False

    def orientations(self, part: SheetPart) -> list[tuple[float, float, bool]]:
        """Orientation options for ``part`` after the global rotation switch."""
        options = part.orientations()
        if self.allow_rotation:
            return options
        return [option for option in options if not option[2]] or options[:1]


@dataclass(frozen=True)
class PlacedPart:
    """One part positioned on one sheet, in usable-area coordinates."""

    part: SheetPart
    x: float
    y: float
    width: float
    height: float
    rotated: bool = False

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    def overlaps(self, other: "PlacedPart", tol: float = 1e-7) -> bool:
        return (
            self.x < other.right - tol
            and other.x < self.right - tol
            and self.y < other.top - tol
            and other.y < self.top - tol
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "part_id": self.part.part_id,
            "label": self.part.name,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "rotated": self.rotated,
        }


@dataclass
class FreeRect:
    """An unused rectangle of a sheet, in usable-area coordinates."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def short_side(self) -> float:
        return min(self.width, self.height)

    @property
    def long_side(self) -> float:
        return max(self.width, self.height)

    def can_hold(self, width: float, height: float, tol: float = 1e-9) -> bool:
        return width <= self.width + tol and height <= self.height + tol

    def contains(self, other: "FreeRect", tol: float = 1e-9) -> bool:
        return (
            other.x >= self.x - tol
            and other.y >= self.y - tol
            and other.right <= self.right + tol
            and other.top <= self.top + tol
        )


@dataclass
class SheetLayout:
    """One physical sheet with everything cut from it."""

    sheet_index: int
    stock: SheetStock
    spec: SheetSpec
    placements: list[PlacedPart] = field(default_factory=list)
    free_rects: list[FreeRect] = field(default_factory=list)
    #: Filled in by the verifier: how many direction changes the plan needs.
    stages_used: int | None = None

    @property
    def usable_width(self) -> float:
        return self.spec.usable(self.stock)[0]

    @property
    def usable_height(self) -> float:
        return self.spec.usable(self.stock)[1]

    @property
    def usable_area(self) -> float:
        return self.spec.usable_area(self.stock)

    @property
    def placed_area(self) -> float:
        return sum(placement.area for placement in self.placements)

    @property
    def piece_count(self) -> int:
        return len(self.placements)

    @property
    def yield_pct(self) -> float:
        """Finished area as a fraction of the *bought* sheet, trim included."""
        if self.stock.area <= 0:
            return 0.0
        return 100.0 * self.placed_area / self.stock.area

    @property
    def trim_area(self) -> float:
        return max(self.stock.area - self.usable_area, 0.0)

    def reusable_offcuts(self) -> list[FreeRect]:
        """Free rectangles big enough to book back into the off-cut rack."""
        return [
            rect
            for rect in self.free_rects
            if rect.width >= self.spec.min_offcut_side
            and rect.height >= self.spec.min_offcut_side
            and rect.area >= self.spec.min_offcut_area
        ]

    def scrap_area(self) -> float:
        """Usable area that is neither a part nor a bookable off-cut [mm^2]."""
        reusable = sum(rect.area for rect in self.reusable_offcuts())
        return max(self.usable_area - self.placed_area - reusable, 0.0)

    def summary(self) -> dict[str, object]:
        return {
            "sheet": self.sheet_index,
            "stock": self.stock.name,
            "pieces": self.piece_count,
            "yield_pct": round(self.yield_pct, 2),
            "offcuts": len(self.reusable_offcuts()),
            "stages": self.stages_used,
        }


@dataclass
class SheetNestingProblem:
    """A fully specified 2D nesting instance for one material."""

    material_id: str
    parts: list[SheetPart]
    stock: list[SheetStock]
    spec: SheetSpec = field(default_factory=SheetSpec)

    def __post_init__(self) -> None:
        if not self.parts:
            raise InfeasibleNestingError(
                "No parts supplied", profile_id=self.material_id
            )
        if not self.stock:
            raise InfeasibleNestingError(
                "No stock sheets available", profile_id=self.material_id
            )
        for part in self.parts:
            if not any(self.spec.fits(part, stock) for stock in self.stock):
                width, height = max(
                    (self.spec.usable(s) for s in self.stock), key=lambda wh: wh[0] * wh[1]
                )
                raise InfeasibleNestingError(
                    "A part does not fit on any stock sheet in any allowed orientation",
                    profile_id=self.material_id,
                    part_id=part.part_id,
                    part_size=f"{part.width:g}x{part.height:g}",
                    grain=str(part.grain),
                    largest_usable=f"{width:g}x{height:g}",
                )

    @property
    def total_pieces(self) -> int:
        return sum(part.quantity for part in self.parts)

    @property
    def total_part_area(self) -> float:
        return sum(part.area * part.quantity for part in self.parts)

    def expanded_parts(self) -> list[SheetPart]:
        """One :class:`SheetPart` per physical piece."""
        return [single for part in self.parts for single in part.expand()]

    def area_lower_bound(self) -> int:
        """Sheets needed if every square millimetre could be used.

        Continuous relaxation of the bin count: it can never be beaten, so a
        solution matching it is provably optimal in sheet count.
        """
        best = max(self.spec.usable_area(stock) for stock in self.stock)
        if best <= 0:
            return 0
        return math.ceil(self.total_part_area / best - 1e-9)

    def large_piece_lower_bound(self) -> int:
        """Sheets forced by pieces that cannot share a sheet with each other.

        Two pieces each covering more than half the usable width *and* more
        than half the usable height cannot both fit, whatever the layout, so
        each needs its own sheet. This dominates the area bound on jobs made of
        a few big panels and is what stops the engine reporting a hopeless
        optimality gap on exactly those jobs.
        """
        best_stock = max(self.stock, key=lambda s: self.spec.usable_area(s))
        width, height = self.spec.usable(best_stock)
        if width <= 0 or height <= 0:
            return 0
        count = 0
        for part in self.expanded_parts():
            options = self.spec.orientations(part)
            # A piece only counts when *every* orientation it is allowed to
            # take is oversized; otherwise it might be turned to share a sheet.
            if all(
                part_w > width / 2.0 + 1e-9 and part_h > height / 2.0 + 1e-9
                for part_w, part_h, _ in options
            ):
                count += 1
        return count

    def lower_bound_sheets(self) -> int:
        return max(self.area_lower_bound(), self.large_piece_lower_bound())


@dataclass
class SheetNestingResult:
    """The complete outcome of a 2D nesting run."""

    material_id: str
    layouts: list[SheetLayout]
    spec: SheetSpec
    strategy: str = "unknown"
    solve_time_s: float = 0.0
    #: True when the sheet count provably cannot be beaten.
    optimal: bool = False
    lower_bound: int = 0
    unplaced: list[SheetPart] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def sheet_count(self) -> int:
        return len(self.layouts)

    @property
    def total_stock_area(self) -> float:
        return sum(layout.stock.area for layout in self.layouts)

    @property
    def total_placed_area(self) -> float:
        return sum(layout.placed_area for layout in self.layouts)

    @property
    def total_pieces(self) -> int:
        return sum(layout.piece_count for layout in self.layouts)

    @property
    def yield_pct(self) -> float:
        if self.total_stock_area <= 0:
            return 0.0
        return 100.0 * self.total_placed_area / self.total_stock_area

    @property
    def waste_pct(self) -> float:
        return 100.0 - self.yield_pct

    @property
    def total_cost(self) -> float:
        return sum(layout.stock.cost for layout in self.layouts)

    @property
    def stages_used(self) -> int | None:
        stages = [layout.stages_used for layout in self.layouts if layout.stages_used]
        return max(stages) if stages else None

    def reusable_offcuts(self) -> list[tuple[int, FreeRect]]:
        return [
            (layout.sheet_index, rect)
            for layout in self.layouts
            for rect in layout.reusable_offcuts()
        ]

    def cut_length(self) -> float:
        """Total scored/sawn length, a proxy for cycle time [mm].

        Every placed part contributes the two cuts that free it on its right
        and top; the sheet's own trim contributes its perimeter.
        """
        total = 0.0
        for layout in self.layouts:
            total += 2.0 * (layout.stock.width + layout.stock.height)
            for placement in layout.placements:
                total += placement.width + placement.height
        return total

    def summary(self) -> dict[str, object]:
        return {
            "material_id": self.material_id,
            "strategy": self.strategy,
            "sheets": self.sheet_count,
            "pieces": self.total_pieces,
            "stock_area_m2": round(self.total_stock_area / 1e6, 3),
            "placed_area_m2": round(self.total_placed_area / 1e6, 3),
            "yield_pct": round(self.yield_pct, 2),
            "waste_pct": round(self.waste_pct, 2),
            "offcuts": len(self.reusable_offcuts()),
            "cut_length_m": round(self.cut_length() / 1000.0, 2),
            "stages": self.stages_used,
            "lower_bound": self.lower_bound,
            "optimal": self.optimal,
            "solve_time_s": round(self.solve_time_s, 3),
        }


#: Stock plates a glass processor actually buys. The float ribbon is trimmed to
#: a 6000 x 3210 "jumbo"; merchants cut that down to the DLO and PLF sizes
#: below. A tall unit that will not fit a 2250 plate is a stock-selection
#: problem, not a nesting one, so the sizes are named where an estimator can
#: see them rather than buried in a default.
STANDARD_GLASS_STOCK: tuple[SheetStock, ...] = (
    SheetStock(6000.0, 3210.0, label="jumbo (PLF)"),
    SheetStock(3210.0, 2550.0, label="half jumbo"),
    SheetStock(3210.0, 2250.0, label="DLF"),
    SheetStock(2550.0, 1605.0, label="split"),
    SheetStock(2140.0, 1650.0, label="band"),
)


def aggregate_parts(parts: Iterable[SheetPart]) -> list[SheetPart]:
    """Merge identical parts so quantities add up instead of repeating."""
    grouped: dict[tuple, SheetPart] = {}
    for part in parts:
        key = (round(part.width, 3), round(part.height, 3), part.grain, part.name)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = part
        else:
            grouped[key] = SheetPart(
                part_id=existing.part_id,
                width=existing.width,
                height=existing.height,
                quantity=existing.quantity + part.quantity,
                grain=existing.grain,
                label=existing.label,
                metadata=existing.metadata,
            )
    return sorted(grouped.values(), key=lambda p: p.area, reverse=True)


__all__ = [
    "Grain",
    "FreeRectRule",
    "SplitRule",
    "SheetPart",
    "SheetStock",
    "SheetSpec",
    "PlacedPart",
    "FreeRect",
    "SheetLayout",
    "SheetNestingProblem",
    "SheetNestingResult",
    "aggregate_parts",
    "STANDARD_GLASS_STOCK",
]
