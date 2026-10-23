"""Orchestration for 2D sheet nesting.

:func:`nest_sheets` is the one entry point a caller needs. It runs the exact
two-stage model when the instance is small enough to close, sweeps the
heuristic packer over every combination of placement and split rule otherwise,
keeps the best plan by sheet count and then by material yield, and — this is
the part that matters — *verifies* whatever it is about to return.

Verification is not a formality. A layout that cannot be decomposed into
edge-to-edge cuts is scrap on the table, and area utilisation says nothing
about whether it can. Any sheet that fails is reported in the result's
warnings rather than quietly shipped, and the engine falls back to a plan it
could prove.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.logging_setup import get_logger
from .guillotine import pack_guillotine, pack_strips, verify_guillotine
from .sheet import (
    FreeRectRule,
    Grain,
    SheetLayout,
    SheetNestingProblem,
    SheetNestingResult,
    SheetPart,
    SheetSpec,
    SheetStock,
    SplitRule,
    aggregate_parts,
)
from .sheet_exact import EXACT_PIECE_LIMIT, ExactStats, solve_exact_2stage

_log = get_logger(__name__)

#: Rule pairs the sweep tries. Every one is cheap; the sweep is bounded by the
#: number of sheets, not by the number of combinations.
_FREE_RULES = (
    FreeRectRule.BEST_AREA_FIT,
    FreeRectRule.BEST_SHORT_SIDE_FIT,
    FreeRectRule.BEST_LONG_SIDE_FIT,
    FreeRectRule.BOTTOM_LEFT,
)
_SPLIT_RULES = (
    SplitRule.MIN_AREA,
    SplitRule.SHORTER_LEFTOVER,
    SplitRule.LONGER_LEFTOVER,
    SplitRule.SHORTER_AXIS,
)


def _sort_orders(parts: Sequence[SheetPart]) -> list[list[SheetPart]]:
    """The orderings worth trying. Big-first dominates, but not always."""
    by_area = sorted(parts, key=lambda p: (-p.area, -max(p.width, p.height)))
    by_long = sorted(parts, key=lambda p: (-max(p.width, p.height), -p.area))
    by_short = sorted(parts, key=lambda p: (-min(p.width, p.height), -p.area))
    by_height = sorted(parts, key=lambda p: (-p.height, -p.width))
    return [by_area, by_long, by_short, by_height]


def _quality(layouts: Sequence[SheetLayout]) -> tuple:
    """Sort key: fewest sheets first, then the tightest packing."""
    sheets = len(layouts)
    stock_area = sum(layout.stock.area for layout in layouts)
    placed = sum(layout.placed_area for layout in layouts)
    yield_pct = 100.0 * placed / stock_area if stock_area else 0.0
    # A big single off-cut is worth more than the same area in slivers, so a
    # plan that concentrates its waste wins ties.
    largest_offcut = max(
        (rect.area for layout in layouts for rect in layout.reusable_offcuts()),
        default=0.0,
    )
    return (sheets, -yield_pct, -largest_offcut)


def _run_pass(
    problem: SheetNestingProblem,
    order: Sequence[SheetPart],
    free_rule: FreeRectRule,
    split_rule: SplitRule,
) -> tuple[list[SheetLayout], list[SheetPart]] | None:
    """Open sheets until everything is placed. ``None`` if it stalls."""
    spec = problem.spec
    remaining = list(order)
    layouts: list[SheetLayout] = []
    # Cheapest usable area first: a job that fits an off-cut should not open a
    # jumbo, and a job that needs a jumbo should not be chopped across five.
    stock_order = sorted(
        problem.stock, key=lambda s: (not s.is_offcut, -spec.usable_area(s), s.cost)
    )
    supply = {id(s): s.available for s in problem.stock}

    guard = 0
    while remaining:
        guard += 1
        if guard > len(order) + 2:
            return None
        best: tuple | None = None
        for stock in stock_order:
            left = supply[id(stock)]
            if left is not None and left <= 0:
                continue
            if spec.stages == 2:
                layout, leftovers = pack_strips(
                    remaining, stock, spec, sheet_index=len(layouts), third_stage=False
                )
            elif spec.stages == 3:
                layout, leftovers = pack_strips(
                    remaining, stock, spec, sheet_index=len(layouts), third_stage=True
                )
            else:
                layout, leftovers = pack_guillotine(
                    remaining,
                    stock,
                    spec,
                    sheet_index=len(layouts),
                    free_rule=free_rule,
                    split_rule=split_rule,
                )
            if not layout.placements:
                continue
            # Prefer the sheet that costs least per square millimetre placed.
            cost = stock.cost if stock.cost > 0 else stock.area
            score = (cost / layout.placed_area, -layout.placed_area)
            if best is None or score < best[0]:
                best = (score, stock, layout, leftovers)
        if best is None:
            return None
        _, stock, layout, leftovers = best
        layouts.append(layout)
        if supply[id(stock)] is not None:
            supply[id(stock)] -= 1
        if len(leftovers) >= len(remaining):
            return None
        remaining = leftovers
    return layouts, []


def nest_sheets(
    problem: SheetNestingProblem,
    *,
    exact: bool | None = None,
    time_limit_s: float = 30.0,
) -> SheetNestingResult:
    """Produce a verified 2D cutting plan.

    ``exact`` forces the CP-SAT model on or off. Left at ``None`` the engine
    decides: it runs the exact model when the instance is inside its size
    limit and the stock is a single size, and the heuristic sweep otherwise.
    """
    start = time.perf_counter()
    spec = problem.spec
    pieces = problem.expanded_parts()
    lower_bound = problem.lower_bound_sheets()

    best_layouts: list[SheetLayout] | None = None
    strategy = "none"
    warnings: list[str] = []
    optimal = False
    exact_stats: ExactStats | None = None

    # -- heuristic sweep, always run: it is the fallback and the warm start -- #
    for order, free_rule, split_rule in itertools.product(
        _sort_orders(pieces), _FREE_RULES, _SPLIT_RULES
    ):
        outcome = _run_pass(problem, order, free_rule, split_rule)
        if outcome is None:
            continue
        layouts, _ = outcome
        if best_layouts is None or _quality(layouts) < _quality(best_layouts):
            best_layouts = layouts
            strategy = f"guillotine/{free_rule}/{split_rule}"
        if spec.stages in (2, 3):
            # The strip packer ignores the free/split rules, so one pass per
            # ordering is all there is to learn.
            break

    if best_layouts is None:
        raise_reason = "no packing pass placed every part"
        warnings.append(raise_reason)
        result = SheetNestingResult(
            material_id=problem.material_id,
            layouts=[],
            spec=spec,
            strategy="failed",
            solve_time_s=time.perf_counter() - start,
            lower_bound=lower_bound,
            unplaced=list(pieces),
            warnings=warnings,
        )
        return result

    # -- exact model, when it is worth the wait ----------------------------- #
    want_exact = exact if exact is not None else len(pieces) <= EXACT_PIECE_LIMIT
    restricted_optimal = False
    if want_exact:
        third_stage = spec.stages is None or spec.stages >= 3
        layouts, exact_stats = solve_exact_2stage(
            problem,
            third_stage=third_stage,
            time_limit_s=time_limit_s,
            upper_bound_sheets=len(best_layouts),
        )
        if layouts is not None and _quality(layouts) <= _quality(best_layouts):
            best_layouts = layouts
            strategy = "cp-sat/2-stage"
        elif exact_stats.reason and exact is True:
            warnings.append(f"exact model declined: {exact_stats.reason}")

        # The proof stands on its own. If the solver showed that no plan of
        # this stage class uses fewer sheets, then the plan being shipped is
        # optimal too whenever it uses no more — even when the heuristic's
        # layout was kept because it left a better off-cut.
        if (
            exact_stats.proven_optimal
            and exact_stats.sheets is not None
            and len(best_layouts) <= exact_stats.sheets
        ):
            # The model is exact for the stage class it was built for. When the
            # machine is limited to that class the proof is the whole answer;
            # when the machine can turn as often as it likes, a deeper
            # recursive layout might still beat it, so the claim is recorded as
            # what it is rather than promoted to "optimal".
            model_stages = 3 if third_stage else 2
            if spec.stages is not None and spec.stages <= model_stages:
                optimal = True
            else:
                restricted_optimal = True

    # -- verification -------------------------------------------------------- #
    for layout in best_layouts:
        problems = verify_guillotine(layout, spec)
        for message in problems:
            warnings.append(f"sheet {layout.sheet_index}: {message}")

    if not optimal and len(best_layouts) <= lower_bound:
        # Matching the lower bound is a proof in itself, whichever solver got
        # there: no plan can use fewer sheets than the bound allows.
        optimal = True

    result = SheetNestingResult(
        material_id=problem.material_id,
        layouts=best_layouts,
        spec=spec,
        strategy=strategy,
        solve_time_s=time.perf_counter() - start,
        optimal=optimal,
        lower_bound=lower_bound,
        warnings=warnings,
        metadata={
            "pieces": len(pieces),
            "exact_status": exact_stats.status if exact_stats else "skipped",
            "exact_reason": exact_stats.reason if exact_stats else None,
            # True when the sheet count is provably best among plans of at most
            # three stages, but an unlimited-stage plan was not ruled out.
            "optimal_within_stage_limit": restricted_optimal and not optimal,
        },
    )
    return result


def build_sheet_problem(
    material_id: str,
    parts: Iterable[SheetPart],
    *,
    stock: Sequence[SheetStock] | None = None,
    spec: SheetSpec | None = None,
) -> SheetNestingProblem:
    """Assemble a problem, merging identical parts and defaulting the stock.

    The default stock is a 3210 x 2250 mm jumbo, the standard float ribbon
    plate that a glass processor buys and every Israeli merchant stocks.
    """
    sheets = list(stock) if stock else [SheetStock(3210.0, 2250.0, label="jumbo")]
    return SheetNestingProblem(
        material_id=material_id,
        parts=aggregate_parts(parts),
        stock=sheets,
        spec=spec or SheetSpec(),
    )


@dataclass
class GlassNestingReport:
    """One nesting result per glass build-up across a whole project."""

    results: dict[str, SheetNestingResult]

    @property
    def sheet_count(self) -> int:
        return sum(result.sheet_count for result in self.results.values())

    @property
    def total_stock_area(self) -> float:
        return sum(result.total_stock_area for result in self.results.values())

    @property
    def total_placed_area(self) -> float:
        return sum(result.total_placed_area for result in self.results.values())

    @property
    def yield_pct(self) -> float:
        if self.total_stock_area <= 0:
            return 0.0
        return 100.0 * self.total_placed_area / self.total_stock_area

    @property
    def warnings(self) -> list[str]:
        return [
            f"{material}: {message}"
            for material, result in self.results.items()
            for message in result.warnings
        ]

    def summary(self) -> dict[str, object]:
        return {
            "materials": len(self.results),
            "sheets": self.sheet_count,
            "stock_area_m2": round(self.total_stock_area / 1e6, 3),
            "placed_area_m2": round(self.total_placed_area / 1e6, 3),
            "yield_pct": round(self.yield_pct, 2),
            "warnings": len(self.warnings),
        }


def nest_project_glass(
    parts_by_material: dict[str, list[SheetPart]],
    *,
    stock: Sequence[SheetStock] | None = None,
    spec: SheetSpec | None = None,
    exact: bool | None = None,
) -> GlassNestingReport:
    """Nest every build-up in a project separately.

    Two build-ups never share a sheet: they are different products, often
    different thicknesses, and always different stock. Splitting by material
    is therefore not an approximation — it is the problem.
    """
    results: dict[str, SheetNestingResult] = {}
    for material_id, parts in parts_by_material.items():
        if not parts:
            continue
        problem = build_sheet_problem(material_id, parts, stock=stock, spec=spec)
        results[material_id] = nest_sheets(problem, exact=exact)
    return GlassNestingReport(results=results)


__all__ = [
    "nest_sheets",
    "build_sheet_problem",
    "nest_project_glass",
    "GlassNestingReport",
    "Grain",
]


# --------------------------------------------------------------------------- #
# Bridge from built elements to nestable parts
# --------------------------------------------------------------------------- #
def sheet_parts_from_builds(
    builds: Iterable[object],
    *,
    grain_for: "dict[str, Grain] | None" = None,
) -> dict[str, list[SheetPart]]:
    """Turn built elements into nestable parts, keyed by glass build-up.

    Accepts anything carrying an ``opening`` and a ``glass`` list of panes —
    which is what :class:`profileos.elements.builder.ElementBuild` is — without
    importing the elements package, so the nesting engine stays independent of
    the thing that happens to feed it.

    Element quantity multiplies pane quantity: five identical windows need five
    sets of panes on the table, not one.
    """
    grouped: dict[str, list[SheetPart]] = {}
    for build in builds:
        opening = getattr(build, "opening", None)
        panes = getattr(build, "glass", None) or []
        element_qty = int(getattr(opening, "quantity", 1) or 1)
        element_id = str(getattr(opening, "element_id", "") or "element")
        for index, pane in enumerate(panes, start=1):
            build_up = getattr(pane, "build_up", None)
            key = getattr(build_up, "code", None) or (
                build_up.describe() if hasattr(build_up, "describe") else "glass"
            )
            mark = getattr(pane, "mark", None) or f"{element_id}-G{index}"
            grain = (grain_for or {}).get(key, Grain.NONE)
            grouped.setdefault(key, []).append(
                SheetPart(
                    part_id=f"{element_id}-{index}",
                    width=float(pane.width),
                    height=float(pane.height),
                    quantity=int(getattr(pane, "quantity", 1)) * element_qty,
                    grain=grain,
                    label=mark,
                    metadata={
                        "element_id": element_id,
                        "build_up": key,
                        "safety_required": bool(getattr(pane, "safety_required", False)),
                        "compliant": bool(getattr(pane, "compliant", True)),
                    },
                )
            )
    return {key: aggregate_parts(parts) for key, parts in grouped.items()}
