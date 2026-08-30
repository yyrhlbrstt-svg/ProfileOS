"""Exact two-stage guillotine nesting through CP-SAT.

The heuristic packer in :mod:`~profileos.nesting.guillotine` is fast and always
returns something. It is not, however, able to say whether a better plan
exists. On a small job — one elevation's worth of glass, a batch of infill
panels — that question is worth answering exactly, because a single extra
jumbo sheet of coated glass costs more than the compute.

Model
-----
This is the two-stage guillotine problem, which is what an unattended cutting
line actually runs: cross cuts divide the sheet into full-width *strips*, then
rip cuts divide each strip into pieces.

* Every piece is placed in exactly one strip, in exactly one orientation.
  Orientations are enumerated up front, so the width and height entering the
  constraints are constants rather than products of variables.
* A strip's pieces must fit across the usable width.
* Strips are stacked up the sheet and must fit within the usable height. The
  strip height multiplied by the strip-to-sheet assignment is the one genuine
  nonlinearity, and it is linearised the standard way with a bounding variable.
* With ``third_stage`` off, every piece in a strip must be exactly as tall as
  the strip, because no cut is left to trim it. With it on, a shorter piece may
  sit in a taller strip.

Symmetry between identical strips and identical sheets is broken by forcing
both to be used in index order, which is what makes instances of this size
close rather than churn.

Scope
-----
The formulation assumes one stock size. Mixed stock turns the objective into a
cost-selection problem on top of the packing and pushes solve times past the
point where an exact answer is worth waiting for, so :func:`solve_exact_2stage`
declines rather than pretending; the caller falls back to the heuristic.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ..core.logging_setup import get_logger
from .sheet import (
    PlacedPart,
    SheetLayout,
    SheetNestingProblem,
    SheetPart,
    SheetSpec,
    SheetStock,
)

_log = get_logger(__name__)

#: Above this many individual pieces the model stops closing in useful time.
EXACT_PIECE_LIMIT = 40

#: Integer model: all lengths are rounded to this many millimetres.
LENGTH_SCALE = 1


def ortools_available() -> bool:
    try:
        from ortools.sat.python import cp_model  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class ExactStats:
    """What the solver did, so the caller can report it honestly."""

    status: str = "not_run"
    sheets: int | None = None
    lower_bound: int | None = None
    wall_time_s: float = 0.0
    proven_optimal: bool = False
    reason: str | None = None


def _mode_table(
    parts: list[SheetPart], spec: SheetSpec
) -> list[list[tuple[int, int, bool]]]:
    """Integer ``(width, height, rotated)`` options per piece, kerf included."""
    table: list[list[tuple[int, int, bool]]] = []
    kerf = spec.kerf
    for part in parts:
        modes: list[tuple[int, int, bool]] = []
        seen: set[tuple[int, int]] = set()
        for width, height, rotated in spec.orientations(part):
            key = (
                int(math.ceil((width + kerf) / LENGTH_SCALE - 1e-9)),
                int(math.ceil((height + kerf) / LENGTH_SCALE - 1e-9)),
            )
            if key in seen:
                continue
            seen.add(key)
            modes.append((key[0], key[1], rotated))
        table.append(modes)
    return table


def solve_exact_2stage(
    problem: SheetNestingProblem,
    *,
    third_stage: bool = True,
    time_limit_s: float = 30.0,
    upper_bound_sheets: int | None = None,
) -> tuple[list[SheetLayout] | None, ExactStats]:
    """Solve the two-stage guillotine problem to proven optimality if possible.

    Returns ``(layouts, stats)``. ``layouts`` is ``None`` when the solver was
    not applicable or found nothing; ``stats.reason`` then says why, so the
    caller can report a real explanation instead of a silent fallback.
    """
    stats = ExactStats()
    start = time.perf_counter()

    if not ortools_available():
        stats.status = "unavailable"
        stats.reason = "OR-Tools is not installed"
        return None, stats

    stock_sizes = {(s.width, s.height) for s in problem.stock}
    if len(stock_sizes) != 1:
        stats.status = "not_applicable"
        stats.reason = "the exact model handles a single stock size only"
        return None, stats

    pieces = problem.expanded_parts()
    if len(pieces) > EXACT_PIECE_LIMIT:
        stats.status = "too_large"
        stats.reason = (
            f"{len(pieces)} pieces exceeds the exact model's limit of {EXACT_PIECE_LIMIT}"
        )
        return None, stats

    from ortools.sat.python import cp_model

    stock = problem.stock[0]
    spec = problem.spec
    usable_w, usable_h = spec.usable(stock)
    # Capacity carries one extra kerf because every piece was charged one on
    # its far side, including the last in a row, which runs off the sheet edge.
    cap_w = int(math.floor((usable_w + spec.kerf) / LENGTH_SCALE + 1e-9))
    cap_h = int(math.floor((usable_h + spec.kerf) / LENGTH_SCALE + 1e-9))

    modes = _mode_table(pieces, spec)
    n = len(pieces)
    n_strips = n
    max_sheets = upper_bound_sheets if upper_bound_sheets is not None else n
    if stock.available is not None:
        max_sheets = min(max_sheets, stock.available)
    if max_sheets < 1:
        stats.status = "infeasible"
        stats.reason = "no stock sheets available"
        return None, stats

    model = cp_model.CpModel()

    # place[i][k][o]: piece i sits in strip k in orientation o.
    place: dict[tuple[int, int, int], object] = {}
    for i in range(n):
        for k in range(n_strips):
            for o in range(len(modes[i])):
                place[i, k, o] = model.NewBoolVar(f"p{i}_{k}_{o}")

    for i in range(n):
        model.AddExactlyOne(
            place[i, k, o] for k in range(n_strips) for o in range(len(modes[i]))
        )

    strip_used = [model.NewBoolVar(f"su{k}") for k in range(n_strips)]
    strip_h = [model.NewIntVar(0, cap_h, f"sh{k}") for k in range(n_strips)]

    for k in range(n_strips):
        members = [
            place[i, k, o] for i in range(n) for o in range(len(modes[i]))
        ]
        # Width across the strip.
        model.Add(
            sum(
                modes[i][o][0] * place[i, k, o]
                for i in range(n)
                for o in range(len(modes[i]))
            )
            <= cap_w
        )
        # Strip height follows its tallest member. Without a third stage
        # there is no cut left to trim a short piece down, so every piece in
        # the strip must already be exactly the strip's height.
        if third_stage:
            model.AddMaxEquality(
                strip_h[k],
                [0]
                + [
                    modes[i][o][1] * place[i, k, o]
                    for i in range(n)
                    for o in range(len(modes[i]))
                ],
            )
        else:
            for i in range(n):
                for o in range(len(modes[i])):
                    model.Add(strip_h[k] == modes[i][o][1]).OnlyEnforceIf(
                        place[i, k, o]
                    )
            model.Add(strip_h[k] == 0).OnlyEnforceIf(strip_used[k].Not())
        model.AddMaxEquality(strip_used[k], members)
        # Break strip symmetry: strips fill up in index order.
        if k > 0:
            model.Add(strip_used[k] <= strip_used[k - 1])
        # A piece may only open a strip at or before its own index, which
        # removes the (n!) relabelling of otherwise identical strips.
        for i in range(n):
            if k > i:
                for o in range(len(modes[i])):
                    model.Add(place[i, k, o] == 0)

    sheet_used = [model.NewBoolVar(f"bu{s}") for s in range(max_sheets)]
    on_sheet = {
        (k, s): model.NewBoolVar(f"on{k}_{s}")
        for k in range(n_strips)
        for s in range(max_sheets)
    }
    # height_on[k][s] == strip_h[k] when strip k is on sheet s, else 0.
    height_on = {
        (k, s): model.NewIntVar(0, cap_h, f"h{k}_{s}")
        for k in range(n_strips)
        for s in range(max_sheets)
    }

    for k in range(n_strips):
        model.Add(sum(on_sheet[k, s] for s in range(max_sheets)) == strip_used[k])
        for s in range(max_sheets):
            model.Add(on_sheet[k, s] <= sheet_used[s])
            model.Add(height_on[k, s] == strip_h[k]).OnlyEnforceIf(on_sheet[k, s])
            model.Add(height_on[k, s] == 0).OnlyEnforceIf(on_sheet[k, s].Not())

    for s in range(max_sheets):
        model.Add(sum(height_on[k, s] for k in range(n_strips)) <= cap_h)
        if s > 0:
            model.Add(sheet_used[s] <= sheet_used[s - 1])

    model.Minimize(sum(sheet_used))
    model.Add(sum(sheet_used) >= problem.lower_bound_sheets())

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    stats.wall_time_s = time.perf_counter() - start
    stats.status = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        stats.reason = f"solver returned {stats.status}"
        return None, stats

    stats.sheets = int(round(solver.ObjectiveValue()))
    stats.lower_bound = int(math.ceil(solver.BestObjectiveBound() - 1e-6))
    stats.proven_optimal = status == cp_model.OPTIMAL

    # -- rebuild the geometry from the assignment -------------------------- #
    layouts: list[SheetLayout] = []
    for s in range(max_sheets):
        if not solver.Value(sheet_used[s]):
            continue
        layout = SheetLayout(
            sheet_index=len(layouts), stock=stock, spec=spec
        )
        cursor_y = 0.0
        strips_here = [k for k in range(n_strips) if solver.Value(on_sheet[k, s])]
        # Tallest strip at the bottom keeps the off-cut band in one piece at
        # the top, which is what makes it worth booking back into the rack.
        strips_here.sort(key=lambda k: -solver.Value(strip_h[k]))
        for k in strips_here:
            height_units = solver.Value(strip_h[k])
            if height_units <= 0:
                continue
            cursor_x = 0.0
            strip_top = 0.0
            for i in range(n):
                for o in range(len(modes[i])):
                    if not solver.Value(place[i, k, o]):
                        continue
                    real_w, real_h, rotated = _real_size(pieces[i], spec, modes[i][o])
                    layout.placements.append(
                        PlacedPart(
                            part=pieces[i],
                            x=cursor_x,
                            y=cursor_y,
                            width=real_w,
                            height=real_h,
                            rotated=rotated,
                        )
                    )
                    cursor_x += real_w + spec.kerf
                    strip_top = max(strip_top, real_h)
            cursor_y += strip_top + spec.kerf
        _fill_free_rects(layout, cursor_y)
        layouts.append(layout)

    return layouts, stats


def _real_size(
    part: SheetPart, spec: SheetSpec, mode: tuple[int, int, bool]
) -> tuple[float, float, bool]:
    """Recover the true millimetre size behind a rounded integer mode."""
    for width, height, rotated in spec.orientations(part):
        if rotated == mode[2]:
            return width, height, rotated
    width, height, rotated = spec.orientations(part)[0]
    return width, height, rotated


def _fill_free_rects(layout: SheetLayout, used_height: float) -> None:
    """Record the band above the strips and the tail of each strip as off-cut."""
    from .guillotine import _merge  # local import keeps the module graph acyclic

    from .sheet import FreeRect

    usable_w = layout.usable_width
    usable_h = layout.usable_height
    rects: list[FreeRect] = []

    by_row: dict[float, list[PlacedPart]] = {}
    for placement in layout.placements:
        by_row.setdefault(round(placement.y, 6), []).append(placement)
    for row_y, row in by_row.items():
        right = max(p.right for p in row)
        height = max(p.height for p in row)
        if usable_w - right > 1e-7:
            rects.append(FreeRect(right, row_y, usable_w - right, height))
        # A part shorter than its strip leaves a band above it that the third
        # stage frees along with the part itself. Booking it is worth real
        # money on coated glass, so it is not left to fall into scrap.
        for placement in row:
            gap = row_y + height - placement.top
            if gap > 1e-7:
                rects.append(FreeRect(placement.x, placement.top, placement.width, gap))
    if usable_h - used_height > 1e-7:
        rects.append(FreeRect(0.0, used_height, usable_w, usable_h - used_height))
    layout.free_rects = _merge(rects)


__all__ = [
    "EXACT_PIECE_LIMIT",
    "ExactStats",
    "ortools_available",
    "solve_exact_2stage",
]
