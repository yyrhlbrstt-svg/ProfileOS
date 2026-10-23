"""Contour reconstruction: chaining loose segments into closed rings.

DXF cross-sections are rarely tidy. A profile is often drawn as dozens of
independent LINE and ARC entities whose endpoints coincide only to within
drawing tolerance, sometimes with duplicated overlapping entities and small
gaps at tangent points. Before any area or inertia can be computed, those
segments must be assembled into closed, correctly-wound rings.

The chainer works on flattened polylines:

1. Every entity contributes a :class:`Segment` (an ordered point list).
2. Endpoints are inserted into a spatial hash whose cell size is the snap
   tolerance, giving O(1) neighbour lookup instead of an O(n^2) scan.
3. Chains are grown greedily from an unused segment, walking whichever
   unused segment starts (or ends) nearest the current chain tip.
4. A chain whose two ends meet within tolerance becomes a closed ring.
5. Remaining open chains are optionally bridged if the residual gap is below
   the repair threshold, which fixes the common "0.01 mm gap at a tangent"
   drawing defect without silently inventing geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .primitives import (
    Point,
    almost_equal,
    dedupe,
    distance,
    distance_sq,
    ensure_orientation,
    perimeter,
    remove_collinear,
    signed_area,
)

_log = get_logger("geometry.contour")


@dataclass
class Segment:
    """A flattened piece of boundary, from one entity."""

    points: list[Point]
    source: str | None = None
    layer: str | None = None
    #: True when the source entity was itself already closed (a circle, a
    #: closed polyline); such segments become rings directly.
    closed: bool = False

    def __post_init__(self) -> None:
        self.points = dedupe(self.points, tolerance=1e-9) if self.closed else list(self.points)

    @property
    def start(self) -> Point:
        return self.points[0]

    @property
    def end(self) -> Point:
        return self.points[-1]

    @property
    def length(self) -> float:
        return perimeter(self.points, closed=self.closed)

    def reversed_copy(self) -> "Segment":
        return Segment(
            points=list(reversed(self.points)),
            source=self.source,
            layer=self.layer,
            closed=self.closed,
        )

    def is_degenerate(self, tolerance: float) -> bool:
        """True for zero-length stubs that carry no boundary information."""
        if len(self.points) < 2:
            return True
        return not self.closed and self.length <= tolerance


@dataclass
class Ring:
    """A closed contour with its orientation resolved."""

    points: list[Point]
    source_layers: set[str] = field(default_factory=set)
    #: Total gap length bridged while assembling this ring [mm].
    repaired_gap: float = 0.0

    @property
    def signed_area(self) -> float:
        return signed_area(self.points)

    @property
    def area(self) -> float:
        return abs(self.signed_area)

    @property
    def perimeter(self) -> float:
        return perimeter(self.points, closed=True)

    @property
    def is_ccw(self) -> bool:
        return self.signed_area > 0.0

    def oriented(self, counter_clockwise: bool = True) -> "Ring":
        return Ring(
            points=ensure_orientation(self.points, counter_clockwise),
            source_layers=set(self.source_layers),
            repaired_gap=self.repaired_gap,
        )


class _SpatialHash:
    """Buckets points into square cells of side ``cell`` for near-neighbour queries."""

    def __init__(self, cell: float) -> None:
        self.cell = max(cell, 1e-9)
        self._buckets: dict[tuple[int, int], list[int]] = {}

    def _key(self, point: Point) -> tuple[int, int]:
        return (int(math.floor(point[0] / self.cell)), int(math.floor(point[1] / self.cell)))

    def insert(self, point: Point, index: int) -> None:
        self._buckets.setdefault(self._key(point), []).append(index)

    def query(self, point: Point) -> list[int]:
        """Indices in the 3x3 cell neighbourhood around ``point``."""
        cx, cy = self._key(point)
        out: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.extend(self._buckets.get((cx + dx, cy + dy), ()))
        return out

    def remove(self, point: Point, index: int) -> None:
        bucket = self._buckets.get(self._key(point))
        if bucket and index in bucket:
            bucket.remove(index)


class ContourChainer:
    """Assembles :class:`Segment` objects into closed :class:`Ring` objects.

    Parameters
    ----------
    tolerance:
        Endpoints closer than this are treated as the same node [mm].
    repair_tolerance:
        Maximum gap that may be bridged with a straight segment when closing an
        otherwise-complete chain. Defaults to 20x ``tolerance``; set to 0 to
        disable repair entirely.
    min_area:
        Rings smaller than this are discarded as construction noise [mm^2].
    """

    def __init__(
        self,
        tolerance: float = 0.05,
        *,
        repair_tolerance: float | None = None,
        min_area: float = 0.5,
        simplify_collinear: bool = True,
    ) -> None:
        self.tolerance = max(tolerance, 1e-9)
        self.repair_tolerance = (
            repair_tolerance if repair_tolerance is not None else self.tolerance * 20.0
        )
        self.min_area = min_area
        self.simplify_collinear = simplify_collinear

        self.open_chains: list[list[Point]] = []
        self.discarded_tiny: int = 0
        self.repaired_gaps: int = 0

    # -- public API -------------------------------------------------------- #
    @timed("geometry.chain")
    def chain(self, segments: Iterable[Segment]) -> list[Ring]:
        """Assemble ``segments`` into closed rings.

        Segments that cannot be closed are collected in :attr:`open_chains`
        rather than discarded, so the caller can report them to the user.
        """
        self.open_chains = []
        self.discarded_tiny = 0
        self.repaired_gaps = 0

        rings: list[Ring] = []
        pending: list[Segment] = []

        for segment in segments:
            if segment.is_degenerate(self.tolerance):
                continue
            if segment.closed:
                ring = self._finalise(segment.points, {segment.layer} if segment.layer else set())
                if ring is not None:
                    rings.append(ring)
            else:
                pending.append(segment)

        rings.extend(self._chain_open(pending))
        rings.sort(key=lambda r: r.area, reverse=True)
        _log.debug(
            "Chained %d segments into %d rings (%d open chains, %d repaired gaps)",
            len(pending),
            len(rings),
            len(self.open_chains),
            self.repaired_gaps,
        )
        return rings

    # -- internals --------------------------------------------------------- #
    def _chain_open(self, segments: Sequence[Segment]) -> list[Ring]:
        if not segments:
            return []

        # Endpoint index -> segment index. Each segment owns slots 2i (start)
        # and 2i+1 (end), so a slot identifies both segment and which end.
        spatial = _SpatialHash(self.tolerance * 2.0)
        for i, segment in enumerate(segments):
            spatial.insert(segment.start, 2 * i)
            spatial.insert(segment.end, 2 * i + 1)

        used = [False] * len(segments)
        rings: list[Ring] = []
        tol_sq = self.tolerance * self.tolerance

        for seed in range(len(segments)):
            if used[seed]:
                continue
            used[seed] = True
            chain = list(segments[seed].points)
            layers: set[str] = set()
            if segments[seed].layer:
                layers.add(segments[seed].layer)
            gap_total = 0.0

            # Grow forward from the chain tail, then backward from its head.
            for direction in ("forward", "backward"):
                while True:
                    tip = chain[-1] if direction == "forward" else chain[0]
                    if distance_sq(tip, chain[0] if direction == "forward" else chain[-1]) <= tol_sq:
                        if len(chain) > 2:
                            break  # already closed
                    match = self._find_match(segments, spatial, used, tip, tol_sq)
                    if match is None:
                        break
                    index, flipped, gap = match
                    used[index] = True
                    gap_total += gap
                    piece = segments[index]
                    points = list(reversed(piece.points)) if flipped else list(piece.points)
                    if piece.layer:
                        layers.add(piece.layer)
                    if direction == "forward":
                        chain.extend(points[1:])
                    else:
                        chain[:0] = list(reversed(points[1:]))

            ring = self._try_close(chain, layers, gap_total)
            if ring is not None:
                rings.append(ring)
            else:
                self.open_chains.append(chain)

        return rings

    def _find_match(
        self,
        segments: Sequence[Segment],
        spatial: _SpatialHash,
        used: list[bool],
        tip: Point,
        tol_sq: float,
    ) -> tuple[int, bool, float] | None:
        """Nearest unused segment endpoint within tolerance of ``tip``.

        Returns ``(segment_index, needs_flip, gap)``.
        """
        best: tuple[int, bool, float] | None = None
        best_dist = tol_sq
        for slot in spatial.query(tip):
            index, is_end = divmod(slot, 2)
            if used[index]:
                continue
            candidate = segments[index].end if is_end else segments[index].start
            d = distance_sq(tip, candidate)
            if d <= best_dist:
                best_dist = d
                # Matching a segment's *end* means it must be reversed so the
                # chain continues from its start.
                best = (index, bool(is_end), math.sqrt(d))
        return best

    def _try_close(
        self, chain: list[Point], layers: set[str], gap_total: float
    ) -> Ring | None:
        """Close a chain if its ends meet, optionally bridging a small gap."""
        if len(chain) < 3:
            return None
        gap = distance(chain[0], chain[-1])
        if gap <= self.tolerance:
            return self._finalise(chain, layers, gap_total)
        if 0.0 < self.repair_tolerance and gap <= self.repair_tolerance:
            self.repaired_gaps += 1
            _log.debug("Bridged a %.4f mm gap while closing a contour", gap)
            return self._finalise(chain, layers, gap_total + gap)
        return None

    def _finalise(
        self, points: Sequence[Point], layers: set[str], repaired: float = 0.0
    ) -> Ring | None:
        """Clean a closed point list into a :class:`Ring`, or reject it."""
        cleaned = dedupe(points, tolerance=self.tolerance * 0.5)
        if len(cleaned) < 3:
            return None
        if self.simplify_collinear:
            cleaned = remove_collinear(cleaned, tolerance=self.tolerance * 0.02)
        if len(cleaned) < 3:
            return None

        ring = Ring(points=cleaned, source_layers=set(layers), repaired_gap=repaired)
        if ring.area < self.min_area:
            self.discarded_tiny += 1
            return None
        return ring


def rings_from_segments(
    segments: Iterable[Segment],
    *,
    tolerance: float = 0.05,
    repair_tolerance: float | None = None,
    min_area: float = 0.5,
) -> tuple[list[Ring], ContourChainer]:
    """Convenience wrapper returning the rings plus the chainer's diagnostics."""
    chainer = ContourChainer(
        tolerance=tolerance, repair_tolerance=repair_tolerance, min_area=min_area
    )
    return chainer.chain(segments), chainer


__all__ = ["Segment", "Ring", "ContourChainer", "rings_from_segments"]
