"""The parametric opening model: frames, divisions, cells and sashes.

An :class:`Opening` is a rectangle of wall filled with aluminium. It is divided
by mullions (vertical) and transoms (horizontal) into a grid of :class:`Cell`
objects, and each cell is either glazed directly or holds an opening
:class:`Sash`. That single recursive-free structure covers everything from a
one-pane fixed light to a multi-bay curtain wall.

Divisions are given as **free positions** in millimetres from the frame's inner
edge, not as equal splits, because real elevations are rarely equal — a door
bay next to two window bays is the norm.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ..models.base import RoundTrips


class OpeningType(StrEnum):
    """How a sash opens, which decides its hardware and its machining."""

    FIXED = "fixed"
    CASEMENT = "casement"
    TILT_TURN = "tilt_turn"
    TOP_HUNG = "top_hung"
    BOTTOM_HUNG = "bottom_hung"
    SLIDING = "sliding"
    LIFT_SLIDE = "lift_slide"
    DOOR = "door"
    PIVOT = "pivot"

    @property
    def is_operable(self) -> bool:
        return self is not OpeningType.FIXED

    @property
    def hardware_group(self) -> str:
        """The key used to look up hardware rules for this opening type."""
        return {
            OpeningType.FIXED: "fixed",
            OpeningType.CASEMENT: "casement",
            OpeningType.TILT_TURN: "tilt_turn",
            OpeningType.TOP_HUNG: "casement",
            OpeningType.BOTTOM_HUNG: "casement",
            OpeningType.SLIDING: "sliding",
            OpeningType.LIFT_SLIDE: "sliding",
            OpeningType.DOOR: "door",
            OpeningType.PIVOT: "casement",
        }[self]


class HingeSide(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class ElementKind(StrEnum):
    """What the whole assembly is, for reporting and pricing."""

    WINDOW = "window"
    DOOR = "door"
    CURTAIN_WALL = "curtain_wall"
    SHOPFRONT = "shopfront"
    SLIDING_UNIT = "sliding_unit"


class Sash(BaseModel):
    """An opening leaf inside a cell."""

    model_config = ConfigDict(extra="forbid")

    sash_id: str = Field(default_factory=lambda: f"S-{uuid4().hex[:6].upper()}")
    opening_type: OpeningType = OpeningType.CASEMENT
    hinge_side: HingeSide = HingeSide.LEFT
    #: Handle height from the sash bottom [mm]; ``None`` centres it.
    handle_height: float | None = None
    glass_spec_id: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Cell(BaseModel):
    """One bay of the division grid.

    ``column`` and ``row`` index into the grid produced by the opening's
    division positions. A cell either carries a :class:`Sash` or is glazed
    directly into the frame.
    """

    model_config = ConfigDict(extra="forbid")

    column: int = Field(ge=0)
    row: int = Field(ge=0)
    sash: Sash | None = None
    glass_spec_id: str | None = None
    #: Solid infill (a panel rather than glass) — e.g. a spandrel.
    panel: bool = False
    label: str | None = None

    @property
    def is_glazed(self) -> bool:
        return not self.panel

    @property
    def is_operable(self) -> bool:
        return self.sash is not None and self.sash.opening_type.is_operable

    @property
    def key(self) -> tuple[int, int]:
        return (self.column, self.row)


class Opening(RoundTrips):
    """A complete window, door or curtain-wall element.

    ``width`` and ``height`` are the **outer frame dimensions**, which is what
    a fabricator quotes and what the cut list is derived from. The structural
    opening in the wall is larger by the installation clearance.
    """

    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(default_factory=lambda: f"EL-{uuid4().hex[:6].upper()}")
    name: str = "Element"
    kind: ElementKind = ElementKind.WINDOW
    system_id: str = "generic"

    width: float = Field(gt=0, description="Outer frame width [mm]")
    height: float = Field(gt=0, description="Outer frame height [mm]")
    quantity: int = Field(default=1, ge=1)

    #: Mullion centre positions, measured from the frame's inner left edge [mm].
    mullion_positions: list[float] = Field(default_factory=list)
    #: Transom centre positions, measured from the frame's inner bottom edge [mm].
    transom_positions: list[float] = Field(default_factory=list)

    cells: list[Cell] = Field(default_factory=list)
    #: Default glass for cells that do not name their own.
    glass_spec_id: str | None = None
    reference: str | None = None
    finish: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- validation --------------------------------------------------------- #
    @model_validator(mode="after")
    def _check_divisions(self) -> "Opening":
        for name, positions, limit in (
            ("mullion", self.mullion_positions, self.width),
            ("transom", self.transom_positions, self.height),
        ):
            for position in positions:
                if not (0.0 < position < limit):
                    raise ValueError(
                        f"{name} position {position} must lie strictly inside the "
                        f"{limit} mm element"
                    )
            if len(set(positions)) != len(positions):
                raise ValueError(f"duplicate {name} positions")

        # Sort so grid indexing is always left-to-right, bottom-to-top.
        self.mullion_positions.sort()
        self.transom_positions.sort()

        for cell in self.cells:
            if cell.column >= self.column_count or cell.row >= self.row_count:
                raise ValueError(
                    f"cell ({cell.column}, {cell.row}) is outside the "
                    f"{self.column_count}x{self.row_count} grid"
                )
        keys = [cell.key for cell in self.cells]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate cell coordinates")
        return self

    # -- grid --------------------------------------------------------------- #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def column_count(self) -> int:
        return len(self.mullion_positions) + 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def row_count(self) -> int:
        return len(self.transom_positions) + 1

    @property
    def area(self) -> float:
        """Outer frame area [m^2] — the unit facade work is priced in."""
        return self.width * self.height / 1_000_000.0

    def cell_at(self, column: int, row: int) -> Cell:
        """The cell at a grid position, creating a plain glazed one if absent."""
        for cell in self.cells:
            if cell.key == (column, row):
                return cell
        return Cell(column=column, row=row)

    def all_cells(self) -> Iterator[Cell]:
        """Every cell in the grid, filling in defaults for undeclared ones."""
        for row in range(self.row_count):
            for column in range(self.column_count):
                yield self.cell_at(column, row)

    def operable_cells(self) -> list[Cell]:
        return [cell for cell in self.all_cells() if cell.is_operable]

    def set_cell(self, cell: Cell) -> "Opening":
        """Add or replace a cell definition."""
        self.cells = [c for c in self.cells if c.key != cell.key]
        self.cells.append(cell)
        return self

    def divide_evenly(self, columns: int = 1, rows: int = 1) -> "Opening":
        """Replace the divisions with an even ``columns`` x ``rows`` grid."""
        self.mullion_positions = [
            self.width * (i + 1) / columns for i in range(columns - 1)
        ]
        self.transom_positions = [self.height * (i + 1) / rows for i in range(rows - 1)]
        return self

    def describe(self) -> str:
        grid = f"{self.column_count}x{self.row_count}"
        operable = len(self.operable_cells())
        return (
            f"{self.name}: {self.width:.0f} x {self.height:.0f} mm, {grid} grid, "
            f"{operable} operable, {self.area:.2f} m^2 x{self.quantity}"
        )


class ElevationSet(BaseModel):
    """A job's openings as they appear on the elevation drawings.

    This is the input the fabricator actually has before anything is
    calculated: a schedule of openings with sizes, quantities and reference
    marks. Everything downstream — cut lists, glass sizes, hardware, quotation
    — is derived from it, so it is the one file worth keeping under the
    customer's job number.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(default_factory=lambda: f"ELV-{uuid4().hex[:8].upper()}")
    name: str
    customer: str | None = None
    reference: str | None = None
    site: str | None = None
    notes: str | None = None
    openings: list[Opening] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_element_ids(self) -> "ElevationSet":
        ids = [opening.element_id for opening in self.openings]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate element_id values: {', '.join(duplicates)}")
        return self

    @property
    def total_units(self) -> int:
        """Physical elements to be made, quantities included."""
        return sum(opening.quantity for opening in self.openings)

    @property
    def total_area(self) -> float:
        """Total elevation area [m^2]."""
        return sum(opening.area * opening.quantity for opening in self.openings)

    def describe(self) -> str:
        return (
            f"{self.name}: {len(self.openings)} opening types, "
            f"{self.total_units} units, {self.total_area:.2f} m^2"
        )


__all__ = [
    "OpeningType",
    "HingeSide",
    "ElementKind",
    "Sash",
    "Cell",
    "Opening",
    "ElevationSet",
]
