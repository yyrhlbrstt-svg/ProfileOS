"""Data model for the 1D cutting-stock problem and its solution.

A :class:`NestingProblem` is what the solvers consume: aggregated demand lines,
the stock bars available, and the cut specification. A :class:`NestingResult`
is what they produce: a list of :class:`BarLayout` objects, one per physical bar
to be cut, plus the yield statistics and the remnants that go back to stock.

Demand is aggregated by *(length, angle_left, angle_right)* rather than kept as
individual pieces, because the cutting-stock formulation only cares about
distinct sizes and their quantities. The individual piece identities are
reattached when the layouts are expanded for the shop floor, so labels and
machining still follow the right piece.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..core.errors import InfeasibleNestingError
from ..models.orders import CutPiece, RemnantBar
from .kerf import CutSpec


@dataclass(frozen=True)
class DemandKey:
    """The identity of one distinct cut size."""

    length: float
    angle_left: float = 90.0
    angle_right: float = 90.0

    def rounded(self, digits: int = 3) -> "DemandKey":
        return DemandKey(
            round(self.length, digits),
            round(self.angle_left, digits),
            round(self.angle_right, digits),
        )

    def __str__(self) -> str:  # pragma: no cover - presentation
        if self.angle_left == 90.0 and self.angle_right == 90.0:
            return f"{self.length:g}"
        return f"{self.length:g} ({self.angle_left:g}/{self.angle_right:g})"


@dataclass
class DemandLine:
    """One distinct cut size and how many are needed."""

    key: DemandKey
    quantity: int
    #: Length of bar this piece consumes, including mitre allowance and kerf.
    effective_length: float
    #: The individual pieces aggregated into this line, in order.
    pieces: list[CutPiece] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.key.length

    @property
    def total_effective(self) -> float:
        return self.effective_length * self.quantity


@dataclass
class StockDefinition:
    """A bar length the optimiser may cut from."""

    length: float
    #: ``None`` means unlimited supply.
    available: int | None = None
    cost: float = 0.0
    #: True for a reusable off-cut drawn from inventory.
    is_remnant: bool = False
    remnant_id: str | None = None
    label: str | None = None

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        return f"remnant {self.remnant_id or ''}".strip() if self.is_remnant else f"{self.length:g} mm bar"


@dataclass
class NestingProblem:
    """A fully specified cutting-stock instance for one profile."""

    profile_id: str
    demands: list[DemandLine]
    stock: list[StockDefinition]
    cut_spec: CutSpec
    #: Target yield used only for reporting against the configured goal [%].
    target_yield: float = 97.5
    #: Off-cuts at least this long are recorded as reusable stock [mm].
    min_reusable_remnant: float = 300.0

    def __post_init__(self) -> None:
        if not self.demands:
            raise InfeasibleNestingError(
                "No demand lines supplied", profile_id=self.profile_id
            )
        if not self.stock:
            raise InfeasibleNestingError(
                "No stock bars available", profile_id=self.profile_id
            )
        self._check_feasible()

    def _check_feasible(self) -> None:
        """Every piece must fit on at least one available bar."""
        longest_usable = max(
            self.cut_spec.usable_length(stock.length) for stock in self.stock
        )
        for demand in self.demands:
            if demand.effective_length > longest_usable + 1e-9:
                raise InfeasibleNestingError(
                    "A required piece is longer than every available stock bar",
                    profile_id=self.profile_id,
                    piece_length=demand.length,
                    effective_length=round(demand.effective_length, 3),
                    longest_usable_bar=round(longest_usable, 3),
                )

    @property
    def total_pieces(self) -> int:
        return sum(demand.quantity for demand in self.demands)

    @property
    def total_demand_length(self) -> float:
        """Total effective length required, including kerf and mitre [mm]."""
        return sum(demand.total_effective for demand in self.demands)

    @property
    def total_net_length(self) -> float:
        """Total finished length of all pieces, excluding kerf and mitre [mm]."""
        return sum(demand.length * demand.quantity for demand in self.demands)

    def lower_bound_bars(self) -> int:
        """Continuous lower bound on the number of full bars needed."""
        longest = max(self.cut_spec.usable_length(s.length) for s in self.stock)
        return math.ceil(self.total_demand_length / longest) if longest > 0 else 0


@dataclass
class Placement:
    """One piece positioned on a bar."""

    demand_key: DemandKey
    #: Distance from the bar's left end to the piece's left extremity [mm].
    position: float
    effective_length: float
    piece: CutPiece | None = None

    @property
    def end(self) -> float:
        return self.position + self.effective_length

    @property
    def label(self) -> str:
        if self.piece is not None:
            return self.piece.label
        return str(self.demand_key)


@dataclass
class Pattern:
    """A cutting pattern: how many of each demand size come off one bar.

    Patterns are the columns of the Gilmore-Gomory formulation. ``counts`` maps
    a demand index to the number of times that size appears on the bar.
    """

    counts: dict[int, int]
    stock_length: float
    stock_index: int = 0

    def used_length(self, demands: list[DemandLine]) -> float:
        return sum(demands[i].effective_length * n for i, n in self.counts.items())

    def waste(self, demands: list[DemandLine], cut_spec: CutSpec) -> float:
        """Unused bar length after the trims and all pieces [mm]."""
        return cut_spec.usable_length(self.stock_length) - self.used_length(demands)

    def piece_count(self) -> int:
        return sum(self.counts.values())

    def signature(self) -> tuple:
        """Hashable identity, so duplicate columns are not generated twice."""
        return (self.stock_index, tuple(sorted(self.counts.items())))

    def __bool__(self) -> bool:
        return bool(self.counts)


@dataclass
class BarLayout:
    """One physical bar with the pieces cut from it, in order."""

    bar_index: int
    stock_length: float
    placements: list[Placement] = field(default_factory=list)
    is_remnant: bool = False
    remnant_id: str | None = None
    trim_start: float = 0.0

    @property
    def used_length(self) -> float:
        return sum(p.effective_length for p in self.placements)

    @property
    def remnant_length(self) -> float:
        """Bar left over after the last cut [mm]."""
        return self.stock_length - self.trim_start - self.used_length

    @property
    def piece_count(self) -> int:
        return len(self.placements)

    def yield_pct(self, cut_spec: CutSpec) -> float:
        """Fraction of the bar that becomes finished pieces [%]."""
        if self.stock_length <= 0:
            return 0.0
        net = sum(
            cut_spec.net_length(
                p.effective_length, p.demand_key.angle_left, p.demand_key.angle_right
            )
            for p in self.placements
        )
        return 100.0 * net / self.stock_length

    def is_reusable_remnant(self, threshold: float) -> bool:
        return self.remnant_length >= threshold


@dataclass
class NestingResult:
    """The complete outcome of a nesting run for one profile."""

    profile_id: str
    layouts: list[BarLayout]
    cut_spec: CutSpec
    #: Solver that produced this result: "milp", "ffd", "bfd", "hybrid".
    strategy: str = "unknown"
    solve_time_s: float = 0.0
    #: True when the solver proved optimality of the bar count.
    optimal: bool = False
    unplaced: list[DemandLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    # -- headline statistics ------------------------------------------------ #
    @property
    def bar_count(self) -> int:
        return len(self.layouts)

    @property
    def full_bar_count(self) -> int:
        return sum(1 for layout in self.layouts if not layout.is_remnant)

    @property
    def total_stock_length(self) -> float:
        return sum(layout.stock_length for layout in self.layouts)

    @property
    def total_net_length(self) -> float:
        """Finished length delivered, excluding kerf and mitre waste [mm]."""
        return sum(
            self.cut_spec.net_length(
                p.effective_length, p.demand_key.angle_left, p.demand_key.angle_right
            )
            for layout in self.layouts
            for p in layout.placements
        )

    @property
    def total_pieces(self) -> int:
        return sum(layout.piece_count for layout in self.layouts)

    @property
    def yield_pct(self) -> float:
        """Material yield: finished length as a fraction of stock consumed [%]."""
        if self.total_stock_length <= 0:
            return 0.0
        return 100.0 * self.total_net_length / self.total_stock_length

    @property
    def waste_pct(self) -> float:
        return 100.0 - self.yield_pct

    @property
    def kerf_loss(self) -> float:
        """Total length turned into swarf by the blade [mm]."""
        return self.cut_spec.kerf * self.total_pieces

    @property
    def trim_loss(self) -> float:
        return sum(layout.trim_start for layout in self.layouts)

    def reusable_remnants(self, threshold: float | None = None) -> list[BarLayout]:
        """Layouts whose off-cut is long enough to return to stock."""
        limit = threshold if threshold is not None else 300.0
        return [layout for layout in self.layouts if layout.is_reusable_remnant(limit)]

    def scrap_length(self, threshold: float = 300.0) -> float:
        """Off-cut too short to reuse, i.e. genuine scrap [mm]."""
        return sum(
            layout.remnant_length
            for layout in self.layouts
            if not layout.is_reusable_remnant(threshold)
        )

    def to_remnants(
        self, threshold: float = 300.0, project_id: str | None = None
    ) -> list[RemnantBar]:
        """Convert the reusable off-cuts into inventory records."""
        return [
            RemnantBar(
                profile_id=self.profile_id,
                length=round(layout.remnant_length, 2),
                source_project=project_id,
                metadata={"from_bar": layout.bar_index},
            )
            for layout in self.reusable_remnants(threshold)
        ]

    def summary(self) -> dict[str, object]:
        """Flat statistics dictionary for reports and the UI."""
        return {
            "profile_id": self.profile_id,
            "strategy": self.strategy,
            "bars": self.bar_count,
            "full_bars": self.full_bar_count,
            "remnant_bars_used": self.bar_count - self.full_bar_count,
            "pieces": self.total_pieces,
            "stock_length_mm": round(self.total_stock_length, 1),
            "net_length_mm": round(self.total_net_length, 1),
            "yield_pct": round(self.yield_pct, 2),
            "waste_pct": round(self.waste_pct, 2),
            "kerf_loss_mm": round(self.kerf_loss, 1),
            "trim_loss_mm": round(self.trim_loss, 1),
            "optimal": self.optimal,
            "solve_time_s": round(self.solve_time_s, 3),
        }


def aggregate_demand(
    pieces: Iterable[CutPiece], cut_spec: CutSpec, *, digits: int = 3
) -> list[DemandLine]:
    """Group individual pieces into distinct cut sizes.

    Pieces with the same length and angle pair collapse into one demand line,
    which is what makes the cutting-stock formulation tractable — a job with
    400 pieces usually has only 10-20 distinct sizes.
    """
    grouped: dict[DemandKey, DemandLine] = {}
    for piece in pieces:
        key = DemandKey(piece.length, piece.angle_left, piece.angle_right).rounded(digits)
        line = grouped.get(key)
        if line is None:
            grouped[key] = DemandLine(
                key=key,
                quantity=1,
                effective_length=cut_spec.effective_length(
                    key.length, key.angle_left, key.angle_right
                ),
                pieces=[piece],
            )
        else:
            line.quantity += 1
            line.pieces.append(piece)

    # Longest first: every heuristic here places large pieces before small ones.
    return sorted(grouped.values(), key=lambda d: d.effective_length, reverse=True)


__all__ = [
    "DemandKey",
    "DemandLine",
    "StockDefinition",
    "NestingProblem",
    "Placement",
    "Pattern",
    "BarLayout",
    "NestingResult",
    "aggregate_demand",
]
