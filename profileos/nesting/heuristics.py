"""Greedy packing heuristics for the 1D cutting-stock problem.

These serve three purposes:

1. **Fast answers.** A 2000-piece job packs in milliseconds, which is what the
   interactive editor needs while an operator is still editing the cut list.
2. **Initial columns.** The column-generation solver starts from the patterns
   these produce, which gives it a feasible basis immediately.
3. **Fallback.** If OR-Tools is unavailable or the MILP hits its time limit,
   the best heuristic result is still a valid, shippable cutting plan.

First-Fit-Decreasing has a well-known worst case of ``11/9 OPT + 6/9`` bars for
bin packing, and in practice on architectural cut lists it lands within a few
per cent of optimal. Best-Fit-Decreasing is usually a shade better because it
prefers the bar that will be left with the least waste.

Remnants are always offered before fresh stock, so inventory drains instead of
accumulating — the single biggest lever on real material yield across a series
of jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .model import BarLayout, DemandLine, NestingProblem, Placement, StockDefinition

_log = get_logger("nesting.heuristics")

Strategy = Literal["ffd", "bfd", "ffd_shortest"]


@dataclass
class _OpenBar:
    """A bar being filled during packing."""

    stock: StockDefinition
    stock_index: int
    bar_index: int
    trim_start: float
    remaining: float
    placements: list[Placement] = field(default_factory=list)
    cursor: float = 0.0

    def place(self, demand: DemandLine, piece_index: int) -> None:
        piece = demand.pieces[piece_index] if piece_index < len(demand.pieces) else None
        self.placements.append(
            Placement(
                demand_key=demand.key,
                position=self.trim_start + self.cursor,
                effective_length=demand.effective_length,
                piece=piece,
            )
        )
        self.cursor += demand.effective_length
        self.remaining -= demand.effective_length

    def fits(self, length: float, tolerance: float = 1e-9) -> bool:
        return length <= self.remaining + tolerance

    def to_layout(self) -> BarLayout:
        return BarLayout(
            bar_index=self.bar_index,
            stock_length=self.stock.length,
            placements=list(self.placements),
            is_remnant=self.stock.is_remnant,
            remnant_id=self.stock.remnant_id,
            trim_start=self.trim_start,
        )


class _StockPool:
    """Tracks how much of each stock definition is still available."""

    def __init__(self, problem: NestingProblem) -> None:
        self.problem = problem
        self._remaining: dict[int, int | None] = {
            index: stock.available for index, stock in enumerate(problem.stock)
        }
        # Remnants first (consume inventory), then shortest bar that can work —
        # a long bar is worth more as future stock than as a part-used offcut.
        self._order = sorted(
            range(len(problem.stock)),
            key=lambda i: (not problem.stock[i].is_remnant, problem.stock[i].length),
        )

    def candidates(self) -> list[int]:
        return [i for i in self._order if self._remaining[i] is None or self._remaining[i] > 0]

    def take(self, index: int) -> None:
        remaining = self._remaining[index]
        if remaining is not None:
            if remaining <= 0:
                raise RuntimeError(f"Stock {index} exhausted")
            self._remaining[index] = remaining - 1

    def usable_length(self, index: int) -> float:
        return self.problem.cut_spec.usable_length(self.problem.stock[index].length)


def _open_new_bar(
    problem: NestingProblem, pool: _StockPool, required: float, bar_index: int
) -> _OpenBar | None:
    """Open the cheapest bar that can hold a piece of ``required`` length."""
    for index in pool.candidates():
        if pool.usable_length(index) + 1e-9 >= required:
            pool.take(index)
            return _OpenBar(
                stock=problem.stock[index],
                stock_index=index,
                bar_index=bar_index,
                trim_start=problem.cut_spec.trim_start,
                remaining=pool.usable_length(index),
            )
    return None


def _pack(
    problem: NestingProblem,
    *,
    select: Callable[[list[_OpenBar], float], _OpenBar | None],
) -> tuple[list[BarLayout], list[DemandLine]]:
    """Shared packing loop; ``select`` decides which open bar receives a piece."""
    pool = _StockPool(problem)
    open_bars: list[_OpenBar] = []
    unplaced: list[DemandLine] = []
    bar_counter = 0

    # Expand demand into individual placements, longest first.
    for demand in sorted(problem.demands, key=lambda d: d.effective_length, reverse=True):
        placed_here = 0
        for piece_index in range(demand.quantity):
            target = select(open_bars, demand.effective_length)
            if target is None:
                target = _open_new_bar(
                    problem, pool, demand.effective_length, bar_counter
                )
                if target is None:
                    # No stock left that can take this piece.
                    break
                bar_counter += 1
                open_bars.append(target)
            target.place(demand, piece_index)
            placed_here += 1

        if placed_here < demand.quantity:
            shortfall = demand.quantity - placed_here
            unplaced.append(
                DemandLine(
                    key=demand.key,
                    quantity=shortfall,
                    effective_length=demand.effective_length,
                    pieces=demand.pieces[placed_here:],
                )
            )

    layouts = [bar.to_layout() for bar in open_bars if bar.placements]
    return layouts, unplaced


def _first_fit(open_bars: list[_OpenBar], length: float) -> _OpenBar | None:
    for bar in open_bars:
        if bar.fits(length):
            return bar
    return None


def _best_fit(open_bars: list[_OpenBar], length: float) -> _OpenBar | None:
    """The bar that will have the least remaining space after placing."""
    best: _OpenBar | None = None
    best_residual = float("inf")
    for bar in open_bars:
        if bar.fits(length):
            residual = bar.remaining - length
            if residual < best_residual:
                best, best_residual = bar, residual
    return best


@timed("nesting.ffd")
def first_fit_decreasing(problem: NestingProblem) -> tuple[list[BarLayout], list[DemandLine]]:
    """Pack longest-first into the first bar with room."""
    layouts, unplaced = _pack(problem, select=_first_fit)
    _log.debug("FFD packed %d pieces into %d bars", problem.total_pieces, len(layouts))
    return layouts, unplaced


@timed("nesting.bfd")
def best_fit_decreasing(problem: NestingProblem) -> tuple[list[BarLayout], list[DemandLine]]:
    """Pack longest-first into the bar left with the least waste."""
    layouts, unplaced = _pack(problem, select=_best_fit)
    _log.debug("BFD packed %d pieces into %d bars", problem.total_pieces, len(layouts))
    return layouts, unplaced


def best_heuristic(
    problem: NestingProblem,
) -> tuple[list[BarLayout], list[DemandLine], str]:
    """Run every heuristic and keep the plan using the fewest bars.

    Ties break on total remnant length, preferring the plan that leaves its
    waste concentrated in one long reusable off-cut rather than spread thinly
    across several unusable ones.
    """
    candidates: list[tuple[str, list[BarLayout], list[DemandLine]]] = []
    for name, solver in (("ffd", first_fit_decreasing), ("bfd", best_fit_decreasing)):
        layouts, unplaced = solver(problem)
        candidates.append((name, layouts, unplaced))

    def score(entry: tuple[str, list[BarLayout], list[DemandLine]]) -> tuple:
        _, layouts, unplaced = entry
        shortfall = sum(line.quantity for line in unplaced)
        longest_remnant = max((l.remnant_length for l in layouts), default=0.0)
        return (shortfall, len(layouts), -longest_remnant)

    name, layouts, unplaced = min(candidates, key=score)
    return layouts, unplaced, name


def patterns_from_layouts(
    layouts: list[BarLayout], problem: NestingProblem
) -> list[tuple[dict[int, int], int]]:
    """Summarise layouts as ``(pattern counts, stock index)`` pairs.

    Used to seed the column-generation solver with the heuristic's columns.
    """
    index_of = {demand.key: i for i, demand in enumerate(problem.demands)}
    stock_of = {stock.length: i for i, stock in enumerate(problem.stock)}

    patterns: list[tuple[dict[int, int], int]] = []
    for layout in layouts:
        counts: dict[int, int] = {}
        for placement in layout.placements:
            index = index_of.get(placement.demand_key)
            if index is not None:
                counts[index] = counts.get(index, 0) + 1
        if counts:
            patterns.append((counts, stock_of.get(layout.stock_length, 0)))
    return patterns


__all__ = [
    "first_fit_decreasing",
    "best_fit_decreasing",
    "best_heuristic",
    "patterns_from_layouts",
]
