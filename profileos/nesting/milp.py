"""Exact cutting-stock optimisation by column generation (Gilmore-Gomory).

Formulating the 1D cutting-stock problem by enumerating every possible cutting
pattern is hopeless: a 6000 mm bar and twenty distinct piece sizes admit
astronomically many patterns. Gilmore and Gomory's insight is that only a
handful of patterns ever appear in an optimal solution, and those can be
generated on demand.

The method alternates between two problems:

**Master (restricted LP)** — over the patterns generated so far

.. math::
    \\min \\sum_j c_j x_j \\quad\\text{s.t.}\\quad
    \\sum_j a_{ij} x_j \\ge d_i \\;\\forall i, \\quad x_j \\ge 0

Its dual prices ``y_i`` say how valuable one more piece of size ``i`` is.

**Pricing (knapsack)** — find a *new* pattern worth adding

.. math::
    \\max \\sum_i y_i a_i \\quad\\text{s.t.}\\quad
    \\sum_i \\ell_i a_i \\le L_k, \\quad 0 \\le a_i \\le d_i,\\ a_i \\in \\mathbb{Z}

If the best such pattern has value above the bar's cost, it has negative reduced
cost and enters the master. When no pattern prices out, the LP is optimal.

The LP relaxation is then solved as an integer program over the generated
columns. That final step is what makes the answer usable — you cannot cut 3.4
bars — and it is where the (small) remaining gap to the true optimum lives, so
:attr:`~profileos.nesting.model.NestingResult.optimal` is only set when the
solver proves it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from ..core.errors import NestingError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .model import BarLayout, NestingProblem, Pattern, Placement

_log = get_logger("nesting.milp")

#: Reduced-cost improvement below this is treated as no improvement.
PRICING_TOLERANCE = 1e-6


def ortools_available() -> bool:
    """True when OR-Tools can be imported."""
    try:
        from ortools.linear_solver import pywraplp  # noqa: F401
        from ortools.sat.python import cp_model  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class ColumnGenerationStats:
    """Diagnostics from one column-generation run."""

    iterations: int = 0
    columns_generated: int = 0
    initial_columns: int = 0
    lp_objective: float = 0.0
    integer_objective: float = 0.0
    lp_bound_bars: float = 0.0
    proved_optimal: bool = False
    time_limit_hit: bool = False
    solve_time_s: float = 0.0
    messages: list[str] = field(default_factory=list)

    @property
    def integrality_gap(self) -> float:
        """Relative gap between the integer solution and the LP bound."""
        if self.lp_objective <= 0:
            return 0.0
        return (self.integer_objective - self.lp_objective) / self.lp_objective


def _pattern_cost(problem: NestingProblem, stock_index: int) -> float:
    """Cost of consuming one bar of the given stock definition.

    With no explicit price, the bar's length is used, which makes the objective
    "minimise total material consumed". Remnants are discounted heavily so the
    optimiser prefers to drain inventory — they are already paid for.
    """
    stock = problem.stock[stock_index]
    if stock.cost > 0:
        return stock.cost
    return stock.length * (0.05 if stock.is_remnant else 1.0)


def _solve_pricing(
    problem: NestingProblem,
    duals: list[float],
    stock_index: int,
    *,
    time_limit_s: float = 5.0,
) -> tuple[dict[int, int], float] | None:
    """Bounded knapsack: the most valuable pattern for one stock length.

    Returns ``(counts, value)`` or ``None`` when nothing fits.
    """
    from ortools.sat.python import cp_model

    capacity = problem.cut_spec.usable_length(problem.stock[stock_index].length)
    # CP-SAT is integral, so lengths are scaled to 0.01 mm and truncated. The
    # capacity rounds *down* and item lengths round *up*, keeping every
    # generated pattern physically feasible rather than a hair too long.
    scale = 100.0
    capacity_i = int(math.floor(capacity * scale))

    model = cp_model.CpModel()
    variables = []
    lengths = []
    for index, demand in enumerate(problem.demands):
        length_i = int(math.ceil(demand.effective_length * scale))
        if length_i > capacity_i:
            variables.append(None)
            lengths.append(length_i)
            continue
        upper = min(demand.quantity, capacity_i // length_i)
        variables.append(model.new_int_var(0, max(upper, 0), f"a{index}"))
        lengths.append(length_i)

    active = [(i, v) for i, v in enumerate(variables) if v is not None]
    if not active:
        return None

    model.add(sum(lengths[i] * v for i, v in active) <= capacity_i)
    # Duals are floats; scale them to integers for CP-SAT's objective.
    dual_scale = 1000.0
    model.maximize(sum(int(round(duals[i] * dual_scale)) * v for i, v in active))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 4
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    counts = {i: int(solver.value(v)) for i, v in active if solver.value(v) > 0}
    if not counts:
        return None
    value = sum(duals[i] * n for i, n in counts.items())
    return counts, value


def _initial_columns(problem: NestingProblem) -> list[Pattern]:
    """Seed columns: heuristic patterns plus one single-size pattern per demand.

    The single-size patterns guarantee the restricted master is feasible from
    the very first LP solve, which matters because an infeasible master has no
    usable duals to drive pricing.
    """
    from .heuristics import best_heuristic, patterns_from_layouts

    seen: set[tuple] = set()
    columns: list[Pattern] = []

    def add(counts: dict[int, int], stock_index: int) -> None:
        pattern = Pattern(
            counts=counts,
            stock_length=problem.stock[stock_index].length,
            stock_index=stock_index,
        )
        signature = pattern.signature()
        if signature not in seen:
            seen.add(signature)
            columns.append(pattern)

    layouts, _, _ = best_heuristic(problem)
    for counts, stock_index in patterns_from_layouts(layouts, problem):
        add(counts, stock_index)

    for index, demand in enumerate(problem.demands):
        for stock_index in range(len(problem.stock)):
            capacity = problem.cut_spec.usable_length(problem.stock[stock_index].length)
            fit = int(capacity // demand.effective_length)
            if fit > 0:
                add({index: min(fit, demand.quantity)}, stock_index)
                break

    return columns


@timed("nesting.milp")
def solve_column_generation(
    problem: NestingProblem,
    *,
    time_limit_s: float = 30.0,
    max_iterations: int = 500,
    max_columns: int = 4000,
) -> tuple[list[BarLayout], ColumnGenerationStats]:
    """Solve the cutting-stock problem by column generation.

    Raises
    ------
    NestingError
        OR-Tools is unavailable, or the restricted master proved infeasible.
    """
    if not ortools_available():
        raise NestingError("OR-Tools is required for the MILP solver (pip install ortools)")

    from ortools.linear_solver import pywraplp

    started = time.perf_counter()
    stats = ColumnGenerationStats()

    columns = _initial_columns(problem)
    stats.initial_columns = len(columns)
    seen = {pattern.signature() for pattern in columns}
    demand_count = len(problem.demands)

    # ---------------------------------------------------------------- #
    # Column generation on the LP relaxation
    # ---------------------------------------------------------------- #
    duals: list[float] = [0.0] * demand_count
    for iteration in range(max_iterations):
        if time.perf_counter() - started > time_limit_s:
            stats.time_limit_hit = True
            stats.messages.append("Column generation stopped at the time limit.")
            break

        lp = pywraplp.Solver.CreateSolver("GLOP")
        if lp is None:  # pragma: no cover - OR-Tools build without GLOP
            raise NestingError("GLOP linear solver unavailable")

        variables = [lp.NumVar(0.0, lp.infinity(), f"x{j}") for j in range(len(columns))]
        constraints = []
        for i in range(demand_count):
            constraint = lp.Constraint(float(problem.demands[i].quantity), lp.infinity())
            for j, pattern in enumerate(columns):
                coefficient = pattern.counts.get(i, 0)
                if coefficient:
                    constraint.SetCoefficient(variables[j], float(coefficient))
            constraints.append(constraint)

        objective = lp.Objective()
        for j, pattern in enumerate(columns):
            objective.SetCoefficient(variables[j], _pattern_cost(problem, pattern.stock_index))
        objective.SetMinimization()

        if lp.Solve() != pywraplp.Solver.OPTIMAL:
            raise NestingError(
                "Restricted master LP is infeasible",
                profile_id=problem.profile_id,
                columns=len(columns),
            )

        stats.lp_objective = objective.Value()
        duals = [constraint.DualValue() for constraint in constraints]
        stats.iterations = iteration + 1

        # Price out a new pattern for each stock length; keep the best.
        best_entry: tuple[float, dict[int, int], int] | None = None
        for stock_index in range(len(problem.stock)):
            priced = _solve_pricing(problem, duals, stock_index)
            if priced is None:
                continue
            counts, value = priced
            reduced_cost = value - _pattern_cost(problem, stock_index)
            if reduced_cost > PRICING_TOLERANCE:
                if best_entry is None or reduced_cost > best_entry[0]:
                    best_entry = (reduced_cost, counts, stock_index)

        if best_entry is None:
            stats.messages.append("No pattern prices out; LP relaxation is optimal.")
            break

        _, counts, stock_index = best_entry
        pattern = Pattern(
            counts=counts,
            stock_length=problem.stock[stock_index].length,
            stock_index=stock_index,
        )
        if pattern.signature() in seen:
            # Numerical stalling: the pricing problem keeps returning a column
            # we already hold. Stop rather than loop.
            stats.messages.append("Pricing returned a duplicate column; stopping.")
            break
        seen.add(pattern.signature())
        columns.append(pattern)
        stats.columns_generated += 1

        publish(
            Topic.NESTING_PROGRESS,
            source=problem.profile_id,
            stage="column_generation",
            iteration=iteration + 1,
            columns=len(columns),
            lp_objective=stats.lp_objective,
        )

        if len(columns) >= max_columns:
            stats.messages.append(f"Column limit ({max_columns}) reached.")
            break

    # ---------------------------------------------------------------- #
    # Integer master over the generated columns
    # ---------------------------------------------------------------- #
    remaining = max(1.0, time_limit_s - (time.perf_counter() - started))
    mip = pywraplp.Solver.CreateSolver("SCIP") or pywraplp.Solver.CreateSolver("CBC")
    if mip is None:  # pragma: no cover - OR-Tools build without a MIP backend
        raise NestingError("No mixed-integer solver available (SCIP/CBC)")
    mip.SetTimeLimit(int(remaining * 1000))

    int_vars = []
    for j, pattern in enumerate(columns):
        upper = mip.infinity()
        stock = problem.stock[pattern.stock_index]
        if stock.available is not None:
            upper = float(stock.available)
        int_vars.append(mip.IntVar(0.0, upper, f"n{j}"))

    # Demand is satisfied with an explicit surplus variable rather than a bare
    # ">=". Two plans can use the same number of bars while one of them cuts
    # pieces nobody ordered; charging the surplus a small penalty makes the
    # solver prefer the plan that does not. The penalty is scaled well below one
    # bar so it only ever breaks ties, never trades a whole bar for it.
    surplus_vars = []
    for i in range(demand_count):
        surplus = mip.IntVar(0.0, mip.infinity(), f"s{i}")
        surplus_vars.append(surplus)
        constraint = mip.Constraint(float(problem.demands[i].quantity), float(problem.demands[i].quantity))
        for j, pattern in enumerate(columns):
            coefficient = pattern.counts.get(i, 0)
            if coefficient:
                constraint.SetCoefficient(int_vars[j], float(coefficient))
        constraint.SetCoefficient(surplus, -1.0)

    # Respect finite stock across all patterns sharing a stock definition.
    for stock_index, stock in enumerate(problem.stock):
        if stock.available is None:
            continue
        constraint = mip.Constraint(0.0, float(stock.available))
        for j, pattern in enumerate(columns):
            if pattern.stock_index == stock_index:
                constraint.SetCoefficient(int_vars[j], 1.0)

    objective = mip.Objective()
    for j, pattern in enumerate(columns):
        objective.SetCoefficient(int_vars[j], _pattern_cost(problem, pattern.stock_index))

    cheapest_bar = min(_pattern_cost(problem, i) for i in range(len(problem.stock)))
    longest_piece = max(d.effective_length for d in problem.demands)
    # Weight one surplus piece well under one bar, so surplus is only ever a
    # tie-breaker between plans that use the same number of bars.
    surplus_weight = cheapest_bar / (longest_piece * (problem.total_pieces + 1) * 10.0)
    for i, surplus in enumerate(surplus_vars):
        objective.SetCoefficient(surplus, problem.demands[i].effective_length * surplus_weight)
    objective.SetMinimization()

    status = mip.Solve()
    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise NestingError(
            "Integer master could not be solved",
            profile_id=problem.profile_id,
            status=int(status),
        )

    stats.integer_objective = objective.Value()
    stats.proved_optimal = status == pywraplp.Solver.OPTIMAL and not stats.time_limit_hit
    stats.solve_time_s = time.perf_counter() - started
    stats.lp_bound_bars = stats.lp_objective

    layouts = _expand_patterns(problem, columns, [int(round(v.solution_value())) for v in int_vars])
    _log.info(
        "MILP: %d columns (%d generated) in %d iterations, %d bars, gap %.2f%%",
        len(columns),
        stats.columns_generated,
        stats.iterations,
        len(layouts),
        stats.integrality_gap * 100.0,
    )
    return layouts, stats


def _expand_patterns(
    problem: NestingProblem, columns: list[Pattern], counts: list[int]
) -> list[BarLayout]:
    """Turn pattern multiplicities into concrete bar layouts.

    Individual pieces are drawn from each demand line in order, so labels,
    marks and machining follow the right physical piece onto the right bar.
    """
    # Per-demand cursor into the list of individual pieces.
    cursors = [0] * len(problem.demands)
    layouts: list[BarLayout] = []
    bar_index = 0

    for pattern, repeat in zip(columns, counts):
        for _ in range(repeat):
            placements: list[Placement] = []
            cursor = problem.cut_spec.trim_start
            # Longest pieces first on the bar: easier to handle at the saw and
            # keeps the usable off-cut in one piece at the trailing end.
            ordered = sorted(
                pattern.counts.items(),
                key=lambda item: problem.demands[item[0]].effective_length,
                reverse=True,
            )
            for demand_index, quantity in ordered:
                demand = problem.demands[demand_index]
                for _ in range(quantity):
                    piece = None
                    if cursors[demand_index] < len(demand.pieces):
                        piece = demand.pieces[cursors[demand_index]]
                        cursors[demand_index] += 1
                    placements.append(
                        Placement(
                            demand_key=demand.key,
                            position=cursor,
                            effective_length=demand.effective_length,
                            piece=piece,
                        )
                    )
                    cursor += demand.effective_length

            stock = problem.stock[pattern.stock_index]
            layouts.append(
                BarLayout(
                    bar_index=bar_index,
                    stock_length=stock.length,
                    placements=placements,
                    is_remnant=stock.is_remnant,
                    remnant_id=stock.remnant_id,
                    trim_start=problem.cut_spec.trim_start,
                )
            )
            bar_index += 1

    return layouts


__all__ = [
    "ColumnGenerationStats",
    "ortools_available",
    "solve_column_generation",
    "PRICING_TOLERANCE",
]
