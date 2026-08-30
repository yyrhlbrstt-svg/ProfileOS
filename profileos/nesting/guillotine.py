"""Guillotine packing and, more importantly, guillotine *verification*.

Two things live here:

:func:`pack_guillotine`
    A free-rectangle packer. It keeps a list of unused rectangles, chooses one
    for each part by a placement rule, and splits what is left by a split rule.
    Because a split always runs the full width or the full height of the
    rectangle being split, every layout it produces is guillotine-cuttable by
    construction — no cut ever stops in the middle of the material.

:func:`pack_strips`
    A two-stage packer for cutting lines that cannot turn more than once. It
    builds full-width strips and fills each with parts of a compatible height.

:func:`verify_guillotine`
    The independent check. It takes a finished layout and searches for an
    actual sequence of edge-to-edge cuts that isolates every part. It knows
    nothing about how the layout was produced, so it catches a packer bug
    rather than agreeing with one, and it reports the number of stages the
    plan needs. The classic pinwheel — four rectangles rotated around a central
    square — has no such sequence, and the verifier rejects it.

Kerf accounting
---------------
The material a cut removes is charged to the right of and above each part, on
the block that is carved out of the free rectangle. The parts touching the far
edges of the sheet are charged a kerf they do not strictly need, which loses
one blade width per row and column. That is conservative: it can waste a
fraction of a percent of a sheet, and it can never produce a layout that does
not fit. Glass, where the wheel scores rather than cuts, has zero kerf anyway.
"""

from __future__ import annotations

import time
from typing import Iterable, Sequence

from ..core.logging_setup import get_logger
from .sheet import (
    FreeRect,
    FreeRectRule,
    PlacedPart,
    SheetLayout,
    SheetPart,
    SheetSpec,
    SheetStock,
    SplitRule,
)

_log = get_logger(__name__)

#: Dimensions closer than this are the same dimension.
TOL = 1e-7


# --------------------------------------------------------------------------- #
# Free-rectangle bookkeeping
# --------------------------------------------------------------------------- #
def _score(rect: FreeRect, width: float, height: float, rule: FreeRectRule) -> tuple:
    """Lower is better. The tuple's tail breaks ties bottom-left."""
    leftover_w = rect.width - width
    leftover_h = rect.height - height
    tiebreak = (rect.y, rect.x)
    if rule is FreeRectRule.BEST_AREA_FIT:
        return (rect.area - width * height, min(leftover_w, leftover_h)) + tiebreak
    if rule is FreeRectRule.BEST_SHORT_SIDE_FIT:
        return (min(leftover_w, leftover_h), max(leftover_w, leftover_h)) + tiebreak
    if rule is FreeRectRule.BEST_LONG_SIDE_FIT:
        return (max(leftover_w, leftover_h), min(leftover_w, leftover_h)) + tiebreak
    if rule is FreeRectRule.WORST_AREA_FIT:
        return (-(rect.area - width * height), min(leftover_w, leftover_h)) + tiebreak
    return tiebreak  # BOTTOM_LEFT


def _split(
    rect: FreeRect, width: float, height: float, rule: SplitRule
) -> list[FreeRect]:
    """Carve a ``width`` x ``height`` block from the corner of ``rect``.

    The block sits at the rectangle's bottom-left. What is left is two
    rectangles, and the split rule decides which of them gets the corner that
    both could claim — a horizontal split gives it to the top rectangle, a
    vertical split to the right one.
    """
    leftover_w = rect.width - width
    leftover_h = rect.height - height

    if rule is SplitRule.SHORTER_AXIS:
        horizontal = rect.width <= rect.height
    elif rule is SplitRule.LONGER_AXIS:
        horizontal = rect.width > rect.height
    elif rule is SplitRule.SHORTER_LEFTOVER:
        horizontal = leftover_w <= leftover_h
    elif rule is SplitRule.LONGER_LEFTOVER:
        horizontal = leftover_w > leftover_h
    elif rule is SplitRule.MIN_AREA:
        horizontal = leftover_w * height <= width * leftover_h
    else:  # MAX_AREA
        horizontal = leftover_w * height > width * leftover_h

    if horizontal:
        # Cut across first: the top rectangle spans the full width.
        right = FreeRect(rect.x + width, rect.y, leftover_w, height)
        top = FreeRect(rect.x, rect.y + height, rect.width, leftover_h)
    else:
        # Cut down first: the right rectangle spans the full height.
        right = FreeRect(rect.x + width, rect.y, leftover_w, rect.height)
        top = FreeRect(rect.x, rect.y + height, width, leftover_h)

    return [r for r in (right, top) if r.width > TOL and r.height > TOL]


def _prune(rects: list[FreeRect]) -> list[FreeRect]:
    """Drop free rectangles wholly inside another one."""
    kept: list[FreeRect] = []
    for index, rect in enumerate(rects):
        if any(
            other.contains(rect)
            for position, other in enumerate(rects)
            if position != index and (other.area > rect.area or position < index)
        ):
            continue
        kept.append(rect)
    return kept


def _merge(rects: list[FreeRect]) -> list[FreeRect]:
    """Fuse free rectangles that share a whole edge.

    Two off-cuts left on either side of a removed part are one usable off-cut,
    but only if their common edge matches exactly — otherwise the fused
    rectangle would claim material that a part is standing on.
    """
    merged = True
    working = list(rects)
    while merged:
        merged = False
        for i in range(len(working)):
            for j in range(i + 1, len(working)):
                a, b = working[i], working[j]
                # Side by side, same height band.
                if (
                    abs(a.y - b.y) < TOL
                    and abs(a.height - b.height) < TOL
                    and (abs(a.right - b.x) < TOL or abs(b.right - a.x) < TOL)
                ):
                    fused = FreeRect(min(a.x, b.x), a.y, a.width + b.width, a.height)
                # Stacked, same width band.
                elif (
                    abs(a.x - b.x) < TOL
                    and abs(a.width - b.width) < TOL
                    and (abs(a.top - b.y) < TOL or abs(b.top - a.y) < TOL)
                ):
                    fused = FreeRect(a.x, min(a.y, b.y), a.width, a.height + b.height)
                else:
                    continue
                working = [r for k, r in enumerate(working) if k not in (i, j)]
                working.append(fused)
                merged = True
                break
            if merged:
                break
    return working


# --------------------------------------------------------------------------- #
# Recursive (n-stage) packer
# --------------------------------------------------------------------------- #
def pack_guillotine(
    parts: Sequence[SheetPart],
    stock: SheetStock,
    spec: SheetSpec,
    *,
    sheet_index: int = 0,
    free_rule: FreeRectRule = FreeRectRule.BEST_AREA_FIT,
    split_rule: SplitRule = SplitRule.MIN_AREA,
) -> tuple[SheetLayout, list[SheetPart]]:
    """Fill one sheet with as many of ``parts`` as fit, in the order given.

    Returns the layout and the parts that did not fit, so the caller can open
    another sheet and carry on.
    """
    usable_w, usable_h = spec.usable(stock)
    layout = SheetLayout(sheet_index=sheet_index, stock=stock, spec=spec)
    if usable_w <= TOL or usable_h <= TOL:
        return layout, list(parts)

    free: list[FreeRect] = [FreeRect(0.0, 0.0, usable_w, usable_h)]
    leftovers: list[SheetPart] = []

    for part in parts:
        best: tuple | None = None
        for part_w, part_h, rotated in spec.orientations(part):
            for index, rect in enumerate(free):
                if not rect.can_hold(part_w, part_h):
                    continue
                # The separating cut is charged to the right of and above the
                # part. Capping the charge at the rectangle it is carved from
                # means a residual strip narrower than the blade is simply
                # consumed — which is what happens on the machine — instead of
                # being offered to a part that could never be freed from it.
                charged_w = min(part_w + spec.kerf, rect.width)
                charged_h = min(part_h + spec.kerf, rect.height)
                score = _score(rect, charged_w, charged_h, free_rule)
                if best is None or score < best[0]:
                    best = (score, index, part_w, part_h, rotated, charged_w, charged_h)
        if best is None:
            leftovers.append(part)
            continue

        _, index, part_w, part_h, rotated, charged_w, charged_h = best
        rect = free.pop(index)
        layout.placements.append(
            PlacedPart(
                part=part,
                x=rect.x,
                y=rect.y,
                width=part_w,
                height=part_h,
                rotated=rotated,
            )
        )
        free.extend(_split(rect, charged_w, charged_h, split_rule))
        free = _prune(free)

    layout.free_rects = _merge(free)
    return layout, leftovers


# --------------------------------------------------------------------------- #
# Two-stage (strip) packer
# --------------------------------------------------------------------------- #
def pack_strips(
    parts: Sequence[SheetPart],
    stock: SheetStock,
    spec: SheetSpec,
    *,
    sheet_index: int = 0,
    third_stage: bool = False,
) -> tuple[SheetLayout, list[SheetPart]]:
    """Fill one sheet as full-width strips — a two- or three-stage plan.

    With ``third_stage`` false a part must be exactly as tall as its strip,
    because nothing can trim it afterwards. With ``third_stage`` true a shorter
    part may sit in a taller strip and be trimmed to height by a third cut, and
    the remainder above it is booked as off-cut.
    """
    usable_w, usable_h = spec.usable(stock)
    layout = SheetLayout(sheet_index=sheet_index, stock=stock, spec=spec)
    if usable_w <= TOL or usable_h <= TOL:
        return layout, list(parts)

    #: (y, height, cursor_x) for each open strip.
    strips: list[list[float]] = []
    used_height = 0.0
    leftovers: list[SheetPart] = []

    for part in parts:
        placed = False
        for part_w, part_h, rotated in spec.orientations(part):
            for strip in strips:
                strip_y, strip_h, cursor = strip
                height_ok = (
                    part_h <= strip_h + TOL
                    if third_stage
                    else abs(part_h - strip_h) < TOL
                )
                if not height_ok or cursor + part_w > usable_w + TOL:
                    continue
                layout.placements.append(
                    PlacedPart(part, cursor, strip_y, part_w, part_h, rotated)
                )
                strip[2] = cursor + part_w + spec.kerf
                placed = True
                break
            if placed:
                break
        if placed:
            continue

        # Open a new strip whose height is this part's height.
        for part_w, part_h, rotated in spec.orientations(part):
            if part_w > usable_w + TOL:
                continue
            if used_height + part_h > usable_h + TOL:
                continue
            layout.placements.append(
                PlacedPart(part, 0.0, used_height, part_w, part_h, rotated)
            )
            strips.append([used_height, part_h, part_w + spec.kerf])
            used_height += part_h + spec.kerf
            placed = True
            break
        if not placed:
            leftovers.append(part)

    free: list[FreeRect] = []
    for strip_y, strip_h, cursor in strips:
        if usable_w - cursor > TOL:
            free.append(FreeRect(cursor, strip_y, usable_w - cursor, strip_h))
        # With a third stage a short part leaves a band above it inside its own
        # column. That band is reachable — the column is freed by the rip cut,
        # the band by the trim — so it is a real off-cut, not scrap.
        if third_stage:
            for placement in layout.placements:
                if abs(placement.y - strip_y) > TOL:
                    continue
                gap = strip_y + strip_h - placement.top
                if gap > TOL:
                    free.append(
                        FreeRect(placement.x, placement.top, placement.width, gap)
                    )
    if usable_h - used_height > TOL:
        free.append(FreeRect(0.0, used_height, usable_w, usable_h - used_height))
    layout.free_rects = _merge(free)
    return layout, leftovers


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def find_overlaps(layout: SheetLayout) -> list[tuple[str, str]]:
    """Pairs of parts whose rectangles intersect. Empty means the plan is sane."""
    clashes: list[tuple[str, str]] = []
    placements = layout.placements
    for i in range(len(placements)):
        for j in range(i + 1, len(placements)):
            if placements[i].overlaps(placements[j]):
                clashes.append((placements[i].part.part_id, placements[j].part.part_id))
    return clashes


def find_outside(layout: SheetLayout) -> list[str]:
    """Parts sticking out of the usable area."""
    width, height = layout.usable_width, layout.usable_height
    return [
        placement.part.part_id
        for placement in layout.placements
        if placement.x < -TOL
        or placement.y < -TOL
        or placement.right > width + TOL
        or placement.top > height + TOL
    ]


class _StageBudgetExceeded(Exception):
    """The cutting-sequence search hit its node budget."""


#: Nodes the stage search may expand before it gives up. A guillotine layout
#: from this module's packers resolves in a few hundred; the budget only ever
#: fires on a pathological layout handed in from outside.
STAGE_SEARCH_BUDGET = 200_000


def _stage_search(
    boxes: tuple[tuple[float, float, float, float], ...],
    region: tuple[float, float, float, float],
    last_vertical: bool | None,
    memo: dict,
    budget: list[int],
) -> int | None:
    """Fewest cutting stages that isolate every box, or ``None`` if impossible.

    A *stage* is a run of cuts in one direction. One row of parts spanning the
    full sheet is one stage; a sheet split into strips that are then cut into
    parts is two. Recursing on both sides of every candidate cut and taking the
    minimum makes the answer the friendliest the layout can be to a
    stage-limited machine, not merely the sequence a packer happened to use.

    Trim cuts count. A cut with parts on one side and waste on the other is
    still a cut the table has to make, and a part is not finished until nothing
    but the part is left in its region — so a region is only complete when the
    boxes fill it exactly. Because such a trim continues whatever direction is
    already running, a strip stack that stops 20 mm short of the sheet edge is
    still a two-stage plan; treating the trim as a third stage would condemn a
    perfectly ordinary layout.
    """
    if not boxes:
        return 0

    x0, y0, x1, y1 = region
    bbox = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    filled = (
        abs(bbox[0] - x0) < TOL
        and abs(bbox[1] - y0) < TOL
        and abs(bbox[2] - x1) < TOL
        and abs(bbox[3] - y1) < TOL
    )
    if len(boxes) == 1 and filled:
        return 0

    key = (boxes, region, last_vertical)
    if key in memo:
        return memo[key]

    budget[0] -= 1
    if budget[0] <= 0:
        raise _StageBudgetExceeded

    best: int | None = None

    for vertical in (True, False):
        # Candidate lines are the far edges of the boxes. A cut anywhere else
        # either separates the same groups as one of these or slices a part,
        # so restricting the search to them loses nothing.
        edges = sorted({b[2] for b in boxes} if vertical else {b[3] for b in boxes})
        span_lo, span_hi = (x0, x1) if vertical else (y0, y1)
        turn = 1 if last_vertical is None or last_vertical != vertical else 0

        for cut in edges:
            if cut <= span_lo + TOL or cut >= span_hi - TOL:
                continue
            low: list[tuple[float, float, float, float]] = []
            high: list[tuple[float, float, float, float]] = []
            clean = True
            for box in boxes:
                lo, hi = (box[0], box[2]) if vertical else (box[1], box[3])
                if hi <= cut + TOL:
                    low.append(box)
                elif lo >= cut - TOL:
                    high.append(box)
                else:
                    clean = False
                    break
            if not clean:
                continue

            low_region = (x0, y0, cut, y1) if vertical else (x0, y0, x1, cut)
            high_region = (cut, y0, x1, y1) if vertical else (x0, cut, x1, y1)

            left = _stage_search(tuple(low), low_region, vertical, memo, budget)
            if left is None:
                continue
            right = _stage_search(tuple(high), high_region, vertical, memo, budget)
            if right is None:
                continue

            total = max(left, right) + turn
            if best is None or total < best:
                best = total
        if best is not None and best <= 1:
            break

    memo[key] = best
    return best


def guillotine_stages(layout: SheetLayout) -> int | None:
    """Cutting stages the layout needs, or ``None`` if it cannot be cut at all.

    ``None`` is the interesting answer: it means no sequence of edge-to-edge
    cuts isolates the parts, so the layout is unproducible on a glass table or
    a beam saw however good its area utilisation looks. The classic pinwheel of
    four rectangles around a central square is the standard example.

    Raises
    ------
    _StageBudgetExceeded
        The search gave up. That is *not* the same as ``None``: the layout may
        well be cuttable, it just was not proven so. Callers must keep the two
        apart rather than condemning a sheet the search merely could not reach.
    """
    if not layout.placements:
        return 0
    boxes = tuple(
        sorted(
            (round(p.x, 6), round(p.y, 6), round(p.right, 6), round(p.top, 6))
            for p in layout.placements
        )
    )
    region = (0.0, 0.0, round(layout.usable_width, 6), round(layout.usable_height, 6))
    return _stage_search(boxes, region, None, {}, [STAGE_SEARCH_BUDGET])


def verify_guillotine(layout: SheetLayout, spec: SheetSpec | None = None) -> list[str]:
    """Full manufacturability check on one layout. Empty list means it is cuttable.

    Checks, in the order a shop would care about them:

    1. no part overlaps another,
    2. no part hangs off the usable area,
    3. an edge-to-edge cutting sequence exists,
    4. that sequence fits within the machine's stage limit.
    """
    rules = spec or layout.spec
    problems: list[str] = []

    for first, second in find_overlaps(layout):
        problems.append(f"parts {first} and {second} overlap")
    for part_id in find_outside(layout):
        problems.append(f"part {part_id} lies outside the usable area")
    if problems:
        return problems

    try:
        stages = guillotine_stages(layout)
    except _StageBudgetExceeded:
        # Not the same finding as "impossible", and saying so would be a lie:
        # the search ran out of budget, so the layout is simply unproven.
        _log.warning(
            "guillotine stage search exhausted its budget on sheet %s with %d parts",
            layout.sheet_index,
            len(layout.placements),
        )
        problems.append(
            "cutting sequence could not be established within the search budget; "
            "verify this sheet by hand before cutting"
        )
        return problems

    if stages is None:
        problems.append(
            "no edge-to-edge cutting sequence exists; this layout cannot be "
            "produced on a guillotine table"
        )
        return problems

    layout.stages_used = stages
    if rules.stages is not None and stages > rules.stages:
        problems.append(
            f"layout needs {stages} cutting stages but the machine allows {rules.stages}"
        )
    return problems


def verify_all(layouts: Iterable[SheetLayout], spec: SheetSpec) -> dict[int, list[str]]:
    """Verify every sheet, keyed by sheet index, dropping the clean ones."""
    report: dict[int, list[str]] = {}
    for layout in layouts:
        problems = verify_guillotine(layout, spec)
        if problems:
            report[layout.sheet_index] = problems
    return report


def timed_verify(layouts: Sequence[SheetLayout], spec: SheetSpec) -> tuple[dict, float]:
    start = time.perf_counter()
    report = verify_all(layouts, spec)
    return report, time.perf_counter() - start


__all__ = [
    "pack_guillotine",
    "pack_strips",
    "find_overlaps",
    "find_outside",
    "guillotine_stages",
    "verify_guillotine",
    "verify_all",
    "timed_verify",
]
