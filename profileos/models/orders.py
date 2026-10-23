"""Order, project and inventory models feeding the nesting engine.

A :class:`Project` holds :class:`CutItem` demand lines. Each demand line is a
nominal length with its two end-cut angles and a quantity. The nesting engine
expands demand lines into individual :class:`CutPiece` instances, assigns them
to stock bars, and writes back the resulting remnants as
:class:`RemnantBar` inventory.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Iterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .base import RoundTrips

from .profile import MachiningMacro


class CutOrientation(StrEnum):
    """Whether a piece may be turned end-for-end when nesting.

    A symmetric piece can be flipped freely. An asymmetric one (different
    machining at each end, or a directional surface finish) must not be, or the
    machining ends up mirrored on the wrong end of the bar.
    """

    SYMMETRIC = "symmetric"
    FIXED = "fixed"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def weight(self) -> float:
        """Multiplier used when the optimiser breaks ties between orders."""
        return {"low": 0.5, "normal": 1.0, "high": 2.0, "urgent": 4.0}[self.value]


class CutItem(BaseModel):
    """A demand line: ``quantity`` pieces of one length and angle pair.

    ``length`` is the *nominal* finished length of the piece, measured on the
    reference face. The nesting engine converts it to a consumed length by
    adding the miter allowance and the blade kerf — see
    :mod:`profileos.nesting.kerf`.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(default_factory=lambda: f"IT-{uuid4().hex[:8].upper()}")
    profile_id: str
    length: float = Field(gt=0, description="Nominal finished length [mm]")
    quantity: int = Field(default=1, ge=1)

    angle_left: float = Field(default=90.0, gt=0, lt=180, description="Left end cut angle [deg]")
    angle_right: float = Field(default=90.0, gt=0, lt=180, description="Right end cut angle [deg]")

    orientation: CutOrientation = CutOrientation.SYMMETRIC
    priority: Priority = Priority.NORMAL
    #: Position mark printed on the label, e.g. "W-04 head".
    mark: str | None = None
    #: Which opening / element this piece belongs to, for grouping and labels.
    element_ref: str | None = None
    machining_macros: list[MachiningMacro] = Field(default_factory=list)

    #: Tolerance the saw must hold on this piece [mm].
    length_tolerance: float = Field(default=0.5, gt=0)
    #: Forbid this piece from sharing a bar with other elements (batch integrity).
    dedicated_bar: bool = False
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("angle_left", "angle_right")
    @classmethod
    def _sane_angle(cls, v: float) -> float:
        # 90 deg is a square cut; 45 deg a mitre. Angles outside this band are
        # not producible on standard double-mitre saws.
        if not (15.0 <= v <= 165.0):
            raise ValueError(f"cut angle {v} deg is outside the producible range 15-165 deg")
        return v

    @property
    def is_mitred(self) -> bool:
        return abs(self.angle_left - 90.0) > 1e-9 or abs(self.angle_right - 90.0) > 1e-9

    @property
    def is_symmetric_cut(self) -> bool:
        return abs(self.angle_left - self.angle_right) < 1e-9

    def can_flip(self) -> bool:
        """True when the piece may be reversed on the bar."""
        return self.orientation is CutOrientation.SYMMETRIC and not self.machining_macros

    def expand(self) -> Iterator["CutPiece"]:
        """Yield ``quantity`` individual pieces from this demand line."""
        for index in range(self.quantity):
            yield CutPiece(
                piece_id=f"{self.item_id}-{index + 1:03d}",
                item_id=self.item_id,
                profile_id=self.profile_id,
                length=self.length,
                angle_left=self.angle_left,
                angle_right=self.angle_right,
                mark=self.mark,
                element_ref=self.element_ref,
                priority=self.priority,
                machining_macros=list(self.machining_macros),
            )


class CutPiece(BaseModel):
    """One physical piece to be cut — the unit the optimiser places on bars."""

    model_config = ConfigDict(extra="forbid")

    piece_id: str
    item_id: str
    profile_id: str
    length: float = Field(gt=0)
    angle_left: float = 90.0
    angle_right: float = 90.0

    mark: str | None = None
    element_ref: str | None = None
    priority: Priority = Priority.NORMAL
    machining_macros: list[MachiningMacro] = Field(default_factory=list)
    #: Set by the nesting engine once the piece is placed.
    flipped: bool = False

    @property
    def label(self) -> str:
        """Short human label for the cut list and the printed sticker."""
        parts = [self.mark or self.item_id, f"{self.length:.1f}"]
        if self.angle_left != 90.0 or self.angle_right != 90.0:
            parts.append(f"{self.angle_left:g}/{self.angle_right:g}")
        return " | ".join(parts)


class RemnantBar(BaseModel):
    """A reusable off-cut held in stock.

    Remnants are consumed before fresh stock bars, which is what keeps real
    material yield high across a series of jobs rather than only within one.
    """

    model_config = ConfigDict(extra="forbid")

    remnant_id: str = Field(default_factory=lambda: f"RM-{uuid4().hex[:8].upper()}")
    profile_id: str
    length: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)

    #: The angle already present on the left end (90 = square).
    angle_left: float = 90.0
    angle_right: float = 90.0
    location: str | None = Field(default=None, description="Rack / bin identifier")
    source_project: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reserved: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return not self.reserved and self.quantity > 0


class StockBar(BaseModel):
    """A purchasable full-length bar of one profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    length: float = Field(gt=0)
    #: ``None`` means unlimited supply, which is the usual purchasing assumption.
    available: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0, description="Cost of one full bar")
    currency: str = "EUR"
    supplier_id: str | None = None
    lead_time_days: int | None = Field(default=None, ge=0)


class Project(RoundTrips):
    """A job: customer, demand lines, and the stock available to satisfy them."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(default_factory=lambda: f"PRJ-{uuid4().hex[:8].upper()}")
    name: str
    customer: str | None = None
    reference: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    due_date: date | None = None

    items: list[CutItem] = Field(default_factory=list)
    stock_bars: list[StockBar] = Field(default_factory=list)
    remnants: list[RemnantBar] = Field(default_factory=list)
    currency: str = "EUR"
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_item_ids(self) -> "Project":
        ids = [i.item_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item_id values in project items")
        return self

    # -- queries ----------------------------------------------------------- #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_pieces(self) -> int:
        return sum(item.quantity for item in self.items)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_length(self) -> float:
        """Sum of nominal finished lengths [mm] (excludes kerf and mitre allowance)."""
        return sum(item.length * item.quantity for item in self.items)

    def profile_ids(self) -> list[str]:
        """Distinct profiles referenced by the demand, in first-seen order."""
        seen: dict[str, None] = {}
        for item in self.items:
            seen.setdefault(item.profile_id, None)
        return list(seen)

    def items_for_profile(self, profile_id: str) -> list[CutItem]:
        return [i for i in self.items if i.profile_id == profile_id]

    def stock_for_profile(self, profile_id: str) -> list[StockBar]:
        return [s for s in self.stock_bars if s.profile_id == profile_id]

    def remnants_for_profile(self, profile_id: str) -> list[RemnantBar]:
        return [r for r in self.remnants if r.profile_id == profile_id and r.is_available]

    def expand_pieces(self, profile_id: str | None = None) -> list[CutPiece]:
        """All individual pieces, optionally filtered to one profile."""
        items = self.items if profile_id is None else self.items_for_profile(profile_id)
        return [piece for item in items for piece in item.expand()]

    def add_item(self, item: CutItem) -> "Project":
        """Append a demand line, rejecting a duplicate id."""
        if any(existing.item_id == item.item_id for existing in self.items):
            raise ValueError(f"item_id {item.item_id!r} already exists in this project")
        self.items.append(item)
        return self


__all__ = [
    "CutOrientation",
    "Priority",
    "CutItem",
    "CutPiece",
    "RemnantBar",
    "StockBar",
    "Project",
]
