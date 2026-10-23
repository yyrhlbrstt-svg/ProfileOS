"""Nesting orchestration.

:func:`nest` runs one cutting-stock instance with the best strategy available;
:func:`nest_project` runs a whole project, one instance per profile, and returns
a :class:`ProjectNestingReport` with the combined statistics.

Strategy selection is deliberate rather than automatic-magic:

* ``"auto"`` (default) runs the MILP when OR-Tools is present and the instance
  is small enough to solve within the time limit, otherwise the heuristics.
  If the MILP fails or times out, the heuristic result is used and the reason
  is recorded in the result's warnings — the caller always gets a usable plan.
* ``"milp"`` forces column generation and raises if it cannot run.
* ``"ffd"`` / ``"bfd"`` / ``"heuristic"`` force the greedy packers.

Whichever runs, the heuristic result is computed first and kept as a floor: the
MILP result is only accepted if it actually uses no more bars.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from ..core.config import NestingDefaults, get_settings
from ..core.errors import NestingError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..models.orders import CutPiece, Project, RemnantBar
from ..models.profile import ProfileDefinition
from .heuristics import best_fit_decreasing, best_heuristic, first_fit_decreasing
from .inventory import RemnantInventory
from .kerf import CutSpec, LengthReference
from .milp import ortools_available, solve_column_generation
from .model import (
    BarLayout,
    DemandLine,
    NestingProblem,
    NestingResult,
    StockDefinition,
    aggregate_demand,
)

_log = get_logger("nesting.engine")

Strategy = Literal["auto", "milp", "heuristic", "ffd", "bfd"]

#: Instances beyond this many distinct sizes are left to the heuristics under
#: "auto", because column generation stops paying for itself.
MILP_SIZE_LIMIT = 120


@dataclass
class ProjectNestingReport:
    """Combined result for every profile in a project."""

    project_id: str
    results: dict[str, NestingResult] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    solve_time_s: float = 0.0

    @property
    def total_bars(self) -> int:
        return sum(result.bar_count for result in self.results.values())

    @property
    def total_stock_length(self) -> float:
        return sum(result.total_stock_length for result in self.results.values())

    @property
    def total_net_length(self) -> float:
        return sum(result.total_net_length for result in self.results.values())

    @property
    def overall_yield_pct(self) -> float:
        if self.total_stock_length <= 0:
            return 0.0
        return 100.0 * self.total_net_length / self.total_stock_length

    def meets_target(self, target_pct: float) -> bool:
        return self.overall_yield_pct >= target_pct

    def all_remnants(self, threshold: float = 300.0) -> list[RemnantBar]:
        return [
            remnant
            for result in self.results.values()
            for remnant in result.to_remnants(threshold, self.project_id)
        ]

    def summary(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "profiles": len(self.results),
            "failed_profiles": len(self.failures),
            "bars": self.total_bars,
            "stock_length_mm": round(self.total_stock_length, 1),
            "net_length_mm": round(self.total_net_length, 1),
            "yield_pct": round(self.overall_yield_pct, 2),
            "waste_pct": round(100.0 - self.overall_yield_pct, 2),
            "solve_time_s": round(self.solve_time_s, 3),
        }


def build_problem(
    profile_id: str,
    pieces: list[CutPiece],
    *,
    stock_lengths: list[float] | None = None,
    profile: ProfileDefinition | None = None,
    inventory: RemnantInventory | None = None,
    defaults: NestingDefaults | None = None,
    kerf: float | None = None,
    length_reference: LengthReference = LengthReference.CENTRELINE,
    profile_depth: float | None = None,
) -> NestingProblem:
    """Assemble a :class:`NestingProblem` from pieces and available stock.

    The profile's own section height is used as the mitre depth when the profile
    definition is supplied, which is what makes the mitre allowance correct
    without the caller having to look it up.
    """
    defaults = defaults or get_settings().nesting

    depth = profile_depth
    if depth is None:
        depth = profile.outer_dimensions.height if profile is not None else 0.0

    cut_spec = CutSpec(
        kerf=kerf if kerf is not None else defaults.kerf_mm,
        profile_depth=depth,
        reference=length_reference,
        trim_start=defaults.trim_start_mm,
        trim_end=defaults.trim_end_mm,
    )

    demands = aggregate_demand(pieces, cut_spec)

    lengths = stock_lengths
    if lengths is None:
        lengths = (
            list(profile.stock_lengths) if profile is not None else list(defaults.stock_lengths_mm)
        )

    stock: list[StockDefinition] = []
    if inventory is not None:
        # Only offer remnants long enough to be worth handling.
        stock.extend(
            inventory.as_stock(profile_id, min_length=defaults.min_reusable_remnant_mm)
        )
    stock.extend(StockDefinition(length=length) for length in sorted(set(lengths)))

    return NestingProblem(
        profile_id=profile_id,
        demands=demands,
        stock=stock,
        cut_spec=cut_spec,
        target_yield=defaults.target_yield_pct,
        min_reusable_remnant=defaults.min_reusable_remnant_mm,
    )


@timed("nesting.nest")
def nest(
    problem: NestingProblem,
    *,
    strategy: Strategy = "auto",
    time_limit_s: float | None = None,
    max_columns: int | None = None,
) -> NestingResult:
    """Solve one cutting-stock instance and return the cutting plan."""
    defaults = get_settings().nesting
    limit = time_limit_s if time_limit_s is not None else defaults.solver_time_limit_s
    columns = max_columns if max_columns is not None else defaults.max_patterns

    publish(
        Topic.NESTING_STARTED,
        source=problem.profile_id,
        pieces=problem.total_pieces,
        sizes=len(problem.demands),
        strategy=strategy,
    )
    started = time.perf_counter()
    warnings: list[str] = []

    # Always compute a heuristic plan: it is the fallback and the quality floor.
    if strategy == "ffd":
        layouts, unplaced = first_fit_decreasing(problem)
        chosen = "ffd"
    elif strategy == "bfd":
        layouts, unplaced = best_fit_decreasing(problem)
        chosen = "bfd"
    else:
        layouts, unplaced, chosen = best_heuristic(problem)

    optimal = False
    metadata: dict[str, object] = {}

    use_milp = strategy == "milp" or (
        strategy == "auto"
        and ortools_available()
        and len(problem.demands) <= MILP_SIZE_LIMIT
        and not unplaced
    )

    if strategy == "milp" and not ortools_available():
        raise NestingError("Strategy 'milp' requested but OR-Tools is not installed")

    if use_milp:
        try:
            milp_layouts, stats = solve_column_generation(
                problem, time_limit_s=limit, max_columns=columns
            )
            metadata.update(
                {
                    "milp_iterations": stats.iterations,
                    "milp_columns": stats.initial_columns + stats.columns_generated,
                    "lp_objective": round(stats.lp_objective, 3),
                    "integer_objective": round(stats.integer_objective, 3),
                    "integrality_gap": round(stats.integrality_gap, 6),
                }
            )
            warnings.extend(stats.messages)
            # Accept the MILP plan only if it is genuinely no worse.
            if len(milp_layouts) <= len(layouts):
                layouts, unplaced = milp_layouts, []
                chosen = "milp"
                optimal = stats.proved_optimal
            else:
                warnings.append(
                    f"MILP produced {len(milp_layouts)} bars against the heuristic's "
                    f"{len(layouts)}; keeping the heuristic plan."
                )
        except NestingError as exc:
            if strategy == "milp":
                raise
            warnings.append(f"MILP unavailable, used heuristic instead: {exc}")
            _log.warning("MILP failed (%s); falling back to %s", exc, chosen)

    result = NestingResult(
        profile_id=problem.profile_id,
        layouts=layouts,
        cut_spec=problem.cut_spec,
        strategy=chosen,
        solve_time_s=time.perf_counter() - started,
        optimal=optimal,
        unplaced=unplaced,
        warnings=warnings,
        metadata=metadata,
    )

    if unplaced:
        shortfall = sum(line.quantity for line in unplaced)
        result.warnings.append(
            f"{shortfall} piece(s) could not be placed: available stock is exhausted."
        )
    if result.yield_pct < problem.target_yield:
        result.warnings.append(
            f"Yield {result.yield_pct:.2f}% is below the {problem.target_yield:.1f}% target."
        )

    publish(
        Topic.NESTING_COMPLETED,
        source=problem.profile_id,
        bars=result.bar_count,
        yield_pct=result.yield_pct,
        strategy=chosen,
    )
    _log.info(
        "Nested %s: %d bars, %.2f%% yield via %s in %.3f s",
        problem.profile_id,
        result.bar_count,
        result.yield_pct,
        chosen,
        result.solve_time_s,
    )
    return result


def nest_project(
    project: Project,
    *,
    profiles: dict[str, ProfileDefinition] | None = None,
    inventory: RemnantInventory | None = None,
    strategy: Strategy = "auto",
    defaults: NestingDefaults | None = None,
    update_inventory: bool = False,
) -> ProjectNestingReport:
    """Nest every profile in a project.

    Parameters
    ----------
    profiles:
        Profile definitions keyed by ``profile_id``, used for stock lengths and
        the mitre depth. Missing entries fall back to the configured defaults.
    inventory:
        Remnant store; offered to the optimiser and, with ``update_inventory``,
        updated with the new off-cuts afterwards.
    """
    defaults = defaults or get_settings().nesting
    profiles = profiles or {}
    report = ProjectNestingReport(project_id=project.project_id)
    started = time.perf_counter()

    for profile_id in project.profile_ids():
        pieces = project.expand_pieces(profile_id)
        if not pieces:
            continue

        explicit_stock = [s.length for s in project.stock_for_profile(profile_id)]
        try:
            problem = build_problem(
                profile_id,
                pieces,
                stock_lengths=explicit_stock or None,
                profile=profiles.get(profile_id),
                inventory=inventory,
                defaults=defaults,
            )
            report.results[profile_id] = nest(problem, strategy=strategy)
        except NestingError as exc:
            _log.error("Nesting failed for %s: %s", profile_id, exc)
            report.failures[profile_id] = str(exc)

    report.solve_time_s = time.perf_counter() - started

    if update_inventory and inventory is not None:
        # Consume the remnants that were used, then bank the new off-cuts.
        for result in report.results.values():
            for layout in result.layouts:
                if layout.is_remnant and layout.remnant_id:
                    inventory.consume(layout.remnant_id)
        inventory.extend(report.all_remnants(defaults.min_reusable_remnant_mm))

    _log.info(
        "Project %s nested: %d bars across %d profile(s), %.2f%% yield",
        project.project_id,
        report.total_bars,
        len(report.results),
        report.overall_yield_pct,
    )
    return report


__all__ = [
    "Strategy",
    "ProjectNestingReport",
    "build_problem",
    "nest",
    "nest_project",
    "MILP_SIZE_LIMIT",
]
