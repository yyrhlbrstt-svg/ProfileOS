"""Nesting engine: 1D cutting-stock optimisation for profile bars.

Typical use::

    from profileos.nesting import build_problem, nest

    problem = build_problem("MB70-MULLION", pieces, profile=profile)
    result = nest(problem)
    print(result.summary())

The engine combines greedy packing heuristics with Gilmore-Gomory column
generation, always returning a usable cutting plan even when the exact solver
is unavailable or hits its time limit.
"""

from __future__ import annotations

from .engine import (
    MILP_SIZE_LIMIT,
    ProjectNestingReport,
    Strategy,
    build_problem,
    nest,
    nest_project,
)
from .heuristics import best_fit_decreasing, best_heuristic, first_fit_decreasing
from .inventory import InventoryStats, RemnantInventory
from .kerf import (
    CutSpec,
    LengthReference,
    complementary_angle,
    cot_deg,
    effective_length,
    is_square_cut,
    waste_from_angles,
)
from .milp import ColumnGenerationStats, ortools_available, solve_column_generation
from .model import (
    BarLayout,
    DemandKey,
    DemandLine,
    NestingProblem,
    NestingResult,
    Pattern,
    Placement,
    StockDefinition,
    aggregate_demand,
)

__all__ = [
    # kerf
    "CutSpec",
    "LengthReference",
    "effective_length",
    "cot_deg",
    "is_square_cut",
    "complementary_angle",
    "waste_from_angles",
    # model
    "DemandKey",
    "DemandLine",
    "StockDefinition",
    "NestingProblem",
    "Placement",
    "Pattern",
    "BarLayout",
    "NestingResult",
    "aggregate_demand",
    # solvers
    "first_fit_decreasing",
    "best_fit_decreasing",
    "best_heuristic",
    "solve_column_generation",
    "ColumnGenerationStats",
    "ortools_available",
    # inventory
    "RemnantInventory",
    "InventoryStats",
    # orchestration
    "Strategy",
    "ProjectNestingReport",
    "build_problem",
    "nest",
    "nest_project",
    "MILP_SIZE_LIMIT",
]
