"""Topological resolution: which ring is a shell, which is a chamber.

An extruded aluminium profile is a set of nested closed rings. The outermost
ring is the profile outline; rings inside it are hollow chambers; rings inside
a chamber are solid again (an internal web island, or the aluminium shell on
the far side of a thermal break). ProfileOS resolves this by nesting depth:

* depth 0, 2, 4 ... -> **solid** material (a shell)
* depth 1, 3, 5 ... -> **void** (a chamber / hole)

Depth is computed by counting how many other rings contain a given ring. Rings
produced by :mod:`profileos.geometry.contour` never cross, so containment is a
strict partial order and this classification is exact.

The result is a :class:`SectionTopology`: a list of :class:`Region` objects,
each a solid shell with the list of holes cut directly out of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import TopologyError
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .contour import Ring
from .primitives import Point, bounding_box, point_in_polygon

_log = get_logger("geometry.topology")


@dataclass
class Region:
    """A solid area: one outer shell minus the holes directly inside it."""

    shell: Ring
    holes: list[Ring] = field(default_factory=list)
    #: Nesting depth of the shell (0 for the outermost material).
    depth: int = 0

    @property
    def area(self) -> float:
        """Net material area: shell area less the area of its direct holes."""
        return self.shell.area - sum(h.area for h in self.holes)

    @property
    def gross_area(self) -> float:
        return self.shell.area

    @property
    def perimeter(self) -> float:
        """Total wetted perimeter, outer plus every hole."""
        return self.shell.perimeter + sum(h.perimeter for h in self.holes)

    def bounds(self) -> tuple[float, float, float, float]:
        return bounding_box(self.shell.points)

    def oriented(self) -> "Region":
        """Return a copy with the shell CCW and every hole CW.

        This is the convention Green's-theorem integration relies on: summing
        the signed contributions of all rings then yields the net area and
        inertia with no special-casing of holes.
        """
        return Region(
            shell=self.shell.oriented(counter_clockwise=True),
            holes=[h.oriented(counter_clockwise=False) for h in self.holes],
            depth=self.depth,
        )


@dataclass
class SectionTopology:
    """The resolved ring hierarchy of one cross-section."""

    regions: list[Region] = field(default_factory=list)
    #: Rings that were discarded as duplicates or degenerate.
    rejected: list[Ring] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_area(self) -> float:
        """Net material area of every region [mm^2]."""
        return sum(region.area for region in self.regions)

    @property
    def total_perimeter(self) -> float:
        return sum(region.perimeter for region in self.regions)

    @property
    def outer_region(self) -> Region:
        """The largest depth-0 region — the profile's main body."""
        candidates = [r for r in self.regions if r.depth == 0]
        if not candidates:
            raise TopologyError("Section has no outer region")
        return max(candidates, key=lambda r: r.gross_area)

    @property
    def chamber_count(self) -> int:
        return sum(len(region.holes) for region in self.regions)

    @property
    def is_multi_part(self) -> bool:
        """True when the section is made of several disconnected shells.

        Typical of a thermally-broken profile: two aluminium shells joined by
        polyamide strips that are modelled as a separate material.
        """
        return sum(1 for r in self.regions if r.depth == 0) > 1

    def bounds(self) -> tuple[float, float, float, float]:
        if not self.regions:
            raise TopologyError("Section has no regions")
        boxes = [region.bounds() for region in self.regions]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def all_rings(self) -> list[Ring]:
        """Every ring, shells first then holes, in region order."""
        rings: list[Ring] = []
        for region in self.regions:
            rings.append(region.shell)
            rings.extend(region.holes)
        return rings


def _representative_point(ring: Ring) -> Point:
    """A point guaranteed to be strictly inside ``ring``.

    The vertex centroid can fall outside a concave ring, so this falls back to
    scanning midpoints between the centroid and each vertex — one of them is
    always interior for a simple polygon.
    """
    from .primitives import centroid

    candidate = centroid(ring.points)
    if point_in_polygon(candidate, ring.points):
        return candidate
    for vertex in ring.points:
        midpoint = ((candidate[0] + vertex[0]) / 2.0, (candidate[1] + vertex[1]) / 2.0)
        if point_in_polygon(midpoint, ring.points):
            return midpoint
    return ring.points[0]


def _contains(outer: Ring, inner: Ring, outer_box: tuple[float, float, float, float]) -> bool:
    """True when ``inner`` lies inside ``outer``.

    Uses a bounding-box reject first, then a single interior-point test — valid
    because reconstructed rings never cross one another.
    """
    inner_box = bounding_box(inner.points)
    if (
        inner_box[0] < outer_box[0] - 1e-9
        or inner_box[1] < outer_box[1] - 1e-9
        or inner_box[2] > outer_box[2] + 1e-9
        or inner_box[3] > outer_box[3] + 1e-9
    ):
        return False
    return point_in_polygon(_representative_point(inner), outer.points)


@timed("geometry.resolve_topology")
def resolve_topology(rings: list[Ring], *, min_area: float = 0.5) -> SectionTopology:
    """Classify ``rings`` into solid regions and their chambers.

    Parameters
    ----------
    rings:
        Closed rings, in any order and with any winding.
    min_area:
        Rings below this area are rejected as noise [mm^2].
    """
    topology = SectionTopology()

    usable = [r for r in rings if r.area >= min_area]
    topology.rejected = [r for r in rings if r.area < min_area]
    if not usable:
        raise TopologyError(
            "No usable closed contours in the section",
            ring_count=len(rings),
            min_area=min_area,
        )

    # Large rings first: a ring can only be contained by a larger one, so this
    # ordering lets the containment scan stop early.
    usable.sort(key=lambda r: r.area, reverse=True)
    boxes = [bounding_box(r.points) for r in usable]

    # parent[i] = index of the smallest ring that directly contains ring i.
    parent: list[int | None] = [None] * len(usable)
    depth: list[int] = [0] * len(usable)

    for i in range(len(usable)):
        for j in range(i):  # only larger rings can contain ring i
            if _contains(usable[j], usable[i], boxes[j]):
                # Deeper parent wins: it is the innermost container.
                if parent[i] is None or depth[j] >= depth[parent[i]]:  # type: ignore[index]
                    parent[i] = j
        depth[i] = 0 if parent[i] is None else depth[parent[i]] + 1  # type: ignore[index]

    # Even depth is material, odd depth is void.
    regions: dict[int, Region] = {}
    for index, ring in enumerate(usable):
        if depth[index] % 2 == 0:
            regions[index] = Region(shell=ring, holes=[], depth=depth[index])

    for index, ring in enumerate(usable):
        if depth[index] % 2 == 1:
            owner = parent[index]
            if owner is None or owner not in regions:  # pragma: no cover - defensive
                topology.warnings.append(
                    f"Chamber with area {ring.area:.1f} mm^2 has no enclosing shell; ignored."
                )
                topology.rejected.append(ring)
                continue
            regions[owner].holes.append(ring)

    topology.regions = [regions[i] for i in sorted(regions, key=lambda i: -usable[i].area)]

    if topology.is_multi_part:
        count = sum(1 for r in topology.regions if r.depth == 0)
        topology.warnings.append(
            f"Section consists of {count} disconnected parts "
            "(expected for a thermally broken profile)."
        )

    _log.info(
        "Topology: %d region(s), %d chamber(s), net area %.1f mm^2",
        len(topology.regions),
        topology.chamber_count,
        topology.total_area,
    )
    return topology


__all__ = ["Region", "SectionTopology", "resolve_topology"]
