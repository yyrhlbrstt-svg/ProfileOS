"""Clamp collision detection and dynamic repositioning.

A machining centre holds the bar in several clamps. A tool that tries to work
where a clamp is standing will hit it — destroying the tool, the clamp, or
both. Every serious CAM package for aluminium therefore checks tool/clamp
interference and moves the clamps out of the way before posting the program.

The model
---------
Each clamp occupies an interval along the bar, ``[centre - width/2, centre +
width/2]``, and blocks a known set of faces. Each operation sweeps an interval
along the bar too — :meth:`Operation.extent_x`, which already covers the tool's
diameter rather than just the feature's centre. A collision is an overlap
between the two intervals, grown by a safety clearance, on a face the clamp
blocks.

Reducing the check to 1D intervals is exact for this machine class: the clamps
span the full profile cross-section, so if the tool is over a clamp in X it
will hit it regardless of Y and Z. A full 3D sweep would add cost without
adding answers.

Repositioning
-------------
Moving clamps is a placement problem with three competing requirements:

1. **Clear of the work.** No clamp may sit under any operation.
2. **The bar must stay held.** Clamps have to be spread out, and the free
   overhang at either end bounded, or the bar chatters or drops.
3. **Minimal disturbance.** Every move costs machine time, so a clamp should
   stay as close to where it was as the other two rules allow.

:func:`reposition_clamps` computes the free intervals (the complement of the
forbidden zones), then places clamps greedily nearest-first — largest clamps
first, since they are hardest to fit. The result is reported honestly: if a
clamp cannot be placed at all, the plan says so rather than silently emitting
a program that will crash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..core.errors import CollisionError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from ..models.machines import Clamp, MachineDefinition
from ..models.profile import Face
from .operations import Operation

_log = get_logger("cnc.clamps")

Interval = tuple[float, float]

#: Longest span of bar that may sit unsupported between clamps [mm].
DEFAULT_MAX_UNSUPPORTED_SPAN = 1500.0
#: Longest free end of bar beyond the outermost clamp [mm].
DEFAULT_MAX_OVERHANG = 700.0


@dataclass
class Collision:
    """One tool/clamp interference."""

    operation: Operation
    clamp: Clamp
    #: The overlapping interval along the bar [mm].
    overlap: Interval
    clearance: float

    @property
    def depth(self) -> float:
        """How deep the overlap is [mm] — the distance the clamp must move."""
        return self.overlap[1] - self.overlap[0]

    def __str__(self) -> str:  # pragma: no cover - presentation
        return (
            f"{self.operation.op_id} ({self.operation.op_type.value} on "
            f"{self.operation.face.value}) collides with clamp {self.clamp.id} "
            f"over X {self.overlap[0]:.1f}..{self.overlap[1]:.1f} mm"
        )


@dataclass
class ClampMove:
    """A repositioning instruction for one clamp."""

    clamp_id: str
    from_position: float
    to_position: float

    @property
    def distance(self) -> float:
        return abs(self.to_position - self.from_position)


@dataclass
class ClampPlan:
    """The outcome of clamp planning for one piece."""

    clamps: list[Clamp] = field(default_factory=list)
    moves: list[ClampMove] = field(default_factory=list)
    unresolved: list[Collision] = field(default_factory=list)
    #: Clamps that had to be disabled because nowhere on the bar was free.
    disabled: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unresolved

    @property
    def moved_count(self) -> int:
        return len(self.moves)

    @property
    def total_travel(self) -> float:
        return sum(move.distance for move in self.moves)

    def active_clamps(self) -> list[Clamp]:
        return [clamp for clamp in self.clamps if clamp.enabled]

    def summary(self) -> str:  # pragma: no cover - presentation
        if self.ok and not self.moves:
            return "No clamp interference; clamps unchanged."
        parts = []
        if self.moves:
            parts.append(f"{len(self.moves)} clamp(s) repositioned ({self.total_travel:.0f} mm)")
        if self.disabled:
            parts.append(f"{len(self.disabled)} clamp(s) disabled")
        if self.unresolved:
            parts.append(f"{len(self.unresolved)} unresolved collision(s)")
        return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Interval helpers
# --------------------------------------------------------------------------- #

def merge_intervals(intervals: Iterable[Interval], *, gap: float = 0.0) -> list[Interval]:
    """Merge overlapping intervals, joining any that are within ``gap``."""
    ordered = sorted((lo, hi) for lo, hi in intervals if hi >= lo)
    if not ordered:
        return []

    merged: list[Interval] = [ordered[0]]
    for lo, hi in ordered[1:]:
        last_lo, last_hi = merged[-1]
        if lo <= last_hi + gap:
            merged[-1] = (last_lo, max(last_hi, hi))
        else:
            merged.append((lo, hi))
    return merged


def complement(intervals: Sequence[Interval], domain: Interval) -> list[Interval]:
    """The parts of ``domain`` not covered by ``intervals``."""
    start, end = domain
    free: list[Interval] = []
    cursor = start
    for lo, hi in merge_intervals(intervals):
        if hi <= start or lo >= end:
            continue
        lo, hi = max(lo, start), min(hi, end)
        if lo > cursor:
            free.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < end:
        free.append((cursor, end))
    return free


def _overlap(a: Interval, b: Interval) -> Interval | None:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return (lo, hi) if hi > lo else None


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

@timed("cnc.detect_collisions")
def detect_collisions(
    operations: Sequence[Operation],
    clamps: Sequence[Clamp],
    *,
    clearance: float = 15.0,
) -> list[Collision]:
    """Find every tool/clamp interference for one piece.

    A collision is reported when the gap between the clamp body and the
    operation footprint is smaller than ``clearance``. The clearance is applied
    to the operation only, never to both sides: :func:`forbidden_zones` grows
    the operation by the same amount when planning, so charging it twice would
    make a clamp parked exactly at the safe boundary read as a collision and
    the planner would chase a position that does not exist.
    """
    collisions: list[Collision] = []
    for clamp in clamps:
        if not clamp.enabled:
            continue
        clamp_span = clamp.span(0.0)
        for operation in operations:
            if not operation.enabled or not clamp.blocks(operation.face):
                continue
            lo, hi = operation.extent_x()
            overlap = _overlap((lo - clearance, hi + clearance), clamp_span)
            if overlap is not None:
                collisions.append(
                    Collision(
                        operation=operation,
                        clamp=clamp,
                        overlap=overlap,
                        clearance=clearance,
                    )
                )
    return collisions


def forbidden_zones(
    operations: Sequence[Operation],
    *,
    faces: Iterable[Face] | None = None,
    clearance: float = 15.0,
) -> list[Interval]:
    """Intervals along the bar where no clamp may stand.

    Only operations on ``faces`` are considered, which is what lets a clamp
    that blocks only the top face ignore work happening on the front.
    """
    face_set = set(faces) if faces is not None else None
    zones = [
        (op.extent_x()[0] - clearance, op.extent_x()[1] + clearance)
        for op in operations
        if op.enabled and (face_set is None or op.face in face_set)
    ]
    return merge_intervals(zones)


# --------------------------------------------------------------------------- #
# Repositioning
# --------------------------------------------------------------------------- #

@timed("cnc.reposition_clamps")
def reposition_clamps(
    bar_length: float,
    operations: Sequence[Operation],
    clamps: Sequence[Clamp],
    *,
    clearance: float = 15.0,
    edge_margin: float = 20.0,
    max_unsupported_span: float = DEFAULT_MAX_UNSUPPORTED_SPAN,
    max_overhang: float = DEFAULT_MAX_OVERHANG,
) -> ClampPlan:
    """Move clamps clear of the work while keeping the bar properly held.

    Clamps are placed largest-first (hardest to fit), each as close to its
    original position as the free space and its neighbours allow. Immovable
    clamps keep their position and simply reserve their space.
    """
    plan = ClampPlan()
    if bar_length <= 0:
        raise CollisionError("Bar length must be positive to plan clamps", bar_length=bar_length)

    domain: Interval = (edge_margin, max(edge_margin, bar_length - edge_margin))
    placed: list[tuple[float, float, Clamp]] = []  # (start, end, clamp)

    # Fixed clamps are obstacles, not candidates: reserve their space first.
    movable = [c for c in clamps if c.enabled and c.movable]
    fixed = [c for c in clamps if c.enabled and not c.movable]
    for clamp in fixed:
        placed.append((clamp.start, clamp.end, clamp))

    # Big clamps first — a wide clamp has fewer places it can go.
    order = sorted(movable, key=lambda c: c.width, reverse=True)
    result: dict[str, Clamp] = {c.id: c for c in clamps if not c.enabled}
    for clamp in fixed:
        result[clamp.id] = clamp

    for clamp in order:
        blocked = forbidden_zones(
            operations, faces=clamp.blocks_faces, clearance=clearance
        )
        # Space already taken by other clamps, grown by the required gap.
        neighbours = [
            (start - clamp.min_gap, end + clamp.min_gap) for start, end, _ in placed
        ]
        free = complement(blocked + neighbours, domain)

        position = _nearest_feasible_position(free, clamp)
        if position is None:
            disabled = clamp.model_copy(update={"enabled": False})
            result[clamp.id] = disabled
            plan.disabled.append(clamp.id)
            plan.warnings.append(
                f"Clamp {clamp.id} has nowhere clear to stand and was disabled."
            )
            continue

        moved = clamp.moved_to(position)
        result[clamp.id] = moved
        placed.append((moved.start, moved.end, moved))
        if abs(position - clamp.position) > 1e-6:
            plan.moves.append(
                ClampMove(clamp_id=clamp.id, from_position=clamp.position, to_position=position)
            )

    plan.clamps = [result[c.id] for c in clamps]

    # Anything still colliding after the move is reported, never swallowed.
    plan.unresolved = detect_collisions(
        operations, plan.active_clamps(), clearance=clearance
    )
    plan.warnings.extend(
        _support_warnings(
            bar_length,
            plan.active_clamps(),
            max_unsupported_span=max_unsupported_span,
            max_overhang=max_overhang,
        )
    )

    if plan.moves:
        _log.info(
            "Repositioned %d clamp(s), total travel %.0f mm",
            len(plan.moves),
            plan.total_travel,
        )
    if plan.unresolved:
        publish(
            Topic.CNC_COLLISION,
            source="clamps",
            unresolved=len(plan.unresolved),
            details=[str(c) for c in plan.unresolved[:5]],
        )
    return plan


def _nearest_feasible_position(free: Sequence[Interval], clamp: Clamp) -> float | None:
    """The clamp-centre position closest to its current one, inside ``free``.

    A clamp of width ``w`` fits in ``[lo, hi]`` only where its centre lies in
    ``[lo + w/2, hi - w/2]``, so intervals narrower than the clamp are skipped.
    """
    half = clamp.width / 2.0
    lower_limit = clamp.min_position
    upper_limit = clamp.max_position

    best: float | None = None
    best_distance = float("inf")

    for lo, hi in free:
        centre_lo, centre_hi = lo + half, hi - half
        if centre_hi < centre_lo:
            continue  # the gap is narrower than the clamp
        if lower_limit is not None:
            centre_lo = max(centre_lo, lower_limit)
        if upper_limit is not None:
            centre_hi = min(centre_hi, upper_limit)
        if centre_hi < centre_lo:
            continue  # outside the clamp's own travel limits

        # Project the current position onto the feasible sub-interval.
        candidate = min(max(clamp.position, centre_lo), centre_hi)
        distance = abs(candidate - clamp.position)
        if distance < best_distance:
            best, best_distance = candidate, distance

    return best


def _support_warnings(
    bar_length: float,
    clamps: Sequence[Clamp],
    *,
    max_unsupported_span: float,
    max_overhang: float,
) -> list[str]:
    """Check that the surviving clamps still hold the bar adequately."""
    warnings: list[str] = []
    if not clamps:
        return ["The bar has no active clamps; it cannot be machined safely."]
    if len(clamps) == 1:
        warnings.append(
            "Only one clamp is holding the bar; it may rotate or chatter during machining."
        )

    positions = sorted(clamp.position for clamp in clamps)
    if positions[0] > max_overhang:
        warnings.append(
            f"Unsupported overhang of {positions[0]:.0f} mm at the left end "
            f"exceeds the {max_overhang:.0f} mm limit."
        )
    right_overhang = bar_length - positions[-1]
    if right_overhang > max_overhang:
        warnings.append(
            f"Unsupported overhang of {right_overhang:.0f} mm at the right end "
            f"exceeds the {max_overhang:.0f} mm limit."
        )

    for left, right in zip(positions, positions[1:]):
        span = right - left
        if span > max_unsupported_span:
            warnings.append(
                f"Unsupported span of {span:.0f} mm between clamps at "
                f"{left:.0f} mm and {right:.0f} mm exceeds the "
                f"{max_unsupported_span:.0f} mm limit."
            )
    return warnings


def plan_clamps_for_machine(
    machine: MachineDefinition,
    bar_length: float,
    operations: Sequence[Operation],
    *,
    clearance: float | None = None,
) -> ClampPlan:
    """Convenience wrapper using a machine's own clamps and clearance."""
    return reposition_clamps(
        bar_length,
        operations,
        machine.active_clamps(),
        clearance=clearance if clearance is not None else machine.clamp_clearance,
    )


__all__ = [
    "Interval",
    "Collision",
    "ClampMove",
    "ClampPlan",
    "merge_intervals",
    "complement",
    "detect_collisions",
    "forbidden_zones",
    "reposition_clamps",
    "plan_clamps_for_machine",
    "DEFAULT_MAX_UNSUPPORTED_SPAN",
    "DEFAULT_MAX_OVERHANG",
]
