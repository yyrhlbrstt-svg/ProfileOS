"""Exact section integrals for arbitrary polygonal regions, via Green's theorem.

Green's theorem converts each area integral over a polygon into a line integral
around its boundary, which for a straight-edged ring collapses to a closed-form
sum over the vertices. For a ring with vertices ``(x_i, y_i)`` and

.. math::  c_i = x_i y_{i+1} - x_{i+1} y_i

the integrals are

.. math::

    A       &= \\tfrac{1}{2} \\sum c_i \\\\
    \\int y\\,dA &= \\tfrac{1}{6} \\sum (y_i + y_{i+1}) c_i \\\\
    \\int x\\,dA &= \\tfrac{1}{6} \\sum (x_i + x_{i+1}) c_i \\\\
    \\int y^2 dA &= \\tfrac{1}{12} \\sum (y_i^2 + y_i y_{i+1} + y_{i+1}^2) c_i \\\\
    \\int x^2 dA &= \\tfrac{1}{12} \\sum (x_i^2 + x_i x_{i+1} + x_{i+1}^2) c_i \\\\
    \\int xy\\,dA &= \\tfrac{1}{24} \\sum
        (x_i y_{i+1} + 2 x_i y_i + 2 x_{i+1} y_{i+1} + x_{i+1} y_i) c_i

These are exact — not approximations — for any simple polygon.

Holes need no special handling: a clockwise ring produces negative ``c_i`` and
therefore subtracts its contribution from every integral. The only requirement
is consistent winding, which
:func:`profileos.geometry.shapely_bridge.normalise_orientation` guarantees
(shells counter-clockwise, holes clockwise).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..core.errors import DegenerateSectionError
from ..core.profiling import timed

Point = tuple[float, float]


@dataclass(frozen=True)
class RawMoments:
    """Area integrals taken about the coordinate origin."""

    area: float
    #: First moment about the x axis, ``\\int y dA`` [mm^3].
    qx: float
    #: First moment about the y axis, ``\\int x dA`` [mm^3].
    qy: float
    #: ``\\int y^2 dA`` about the origin [mm^4].
    ixx_o: float
    #: ``\\int x^2 dA`` about the origin [mm^4].
    iyy_o: float
    #: ``\\int xy dA`` about the origin [mm^4].
    ixy_o: float

    @property
    def centroid(self) -> Point:
        """Centroid ``(x_bar, y_bar)``; raises if the area is degenerate."""
        if abs(self.area) < 1e-12:
            raise DegenerateSectionError(
                "Cannot compute a centroid for a section of zero area", area=self.area
            )
        return (self.qy / self.area, self.qx / self.area)

    def to_centroidal(self) -> "CentroidalMoments":
        """Shift the second moments to the centroid (parallel-axis theorem).

        .. math::
            I_{xx} = \\int y^2 dA - A \\bar{y}^2, \\quad
            I_{yy} = \\int x^2 dA - A \\bar{x}^2, \\quad
            I_{xy} = \\int xy\\,dA - A \\bar{x}\\bar{y}
        """
        cx, cy = self.centroid
        return CentroidalMoments(
            area=self.area,
            centroid_x=cx,
            centroid_y=cy,
            ixx=self.ixx_o - self.area * cy * cy,
            iyy=self.iyy_o - self.area * cx * cx,
            ixy=self.ixy_o - self.area * cx * cy,
        )

    def __add__(self, other: "RawMoments") -> "RawMoments":
        return RawMoments(
            area=self.area + other.area,
            qx=self.qx + other.qx,
            qy=self.qy + other.qy,
            ixx_o=self.ixx_o + other.ixx_o,
            iyy_o=self.iyy_o + other.iyy_o,
            ixy_o=self.ixy_o + other.ixy_o,
        )


@dataclass(frozen=True)
class CentroidalMoments:
    """Second moments referred to the centroid, axes parallel to x/y."""

    area: float
    centroid_x: float
    centroid_y: float
    ixx: float
    iyy: float
    ixy: float

    # -- principal system -------------------------------------------------- #
    @property
    def principal(self) -> tuple[float, float, float]:
        """``(I_1, I_2, theta_deg)`` — major, minor, and the axis angle.

        The principal directions satisfy ``tan(2 theta) = -2 I_xy / (I_x - I_y)``.
        Evaluating the transformed moment at that angle gives the mean plus the
        radius, so ``theta`` is the angle to the **major** axis, measured
        counter-clockwise from x and reported in ``(-90, 90]`` degrees.
        """
        mean = 0.5 * (self.ixx + self.iyy)
        half_diff = 0.5 * (self.ixx - self.iyy)
        radius = math.hypot(half_diff, self.ixy)
        i1 = mean + radius
        i2 = mean - radius

        if abs(self.ixy) < 1e-12 and abs(half_diff) < 1e-12:
            # Rotationally symmetric: every axis is principal, report 0.
            return (i1, i2, 0.0)

        theta = 0.5 * math.atan2(-self.ixy, half_diff)
        degrees = math.degrees(theta)
        # Normalise into (-90, 90].
        while degrees <= -90.0:
            degrees += 180.0
        while degrees > 90.0:
            degrees -= 180.0
        return (i1, i2, degrees)

    @property
    def radii_of_gyration(self) -> tuple[float, float]:
        """``(r_x, r_y) = (sqrt(I_x / A), sqrt(I_y / A))`` [mm]."""
        if self.area <= 0:
            return (0.0, 0.0)
        return (
            math.sqrt(max(self.ixx, 0.0) / self.area),
            math.sqrt(max(self.iyy, 0.0) / self.area),
        )

    def rotated(self, angle_deg: float) -> tuple[float, float, float]:
        """Second moments about axes rotated ``angle_deg`` counter-clockwise.

        .. math::
            I_{x'} = \\tfrac{I_x + I_y}{2} + \\tfrac{I_x - I_y}{2}\\cos 2\\theta
                     - I_{xy}\\sin 2\\theta
        """
        two_theta = 2.0 * math.radians(angle_deg)
        mean = 0.5 * (self.ixx + self.iyy)
        half_diff = 0.5 * (self.ixx - self.iyy)
        cos2, sin2 = math.cos(two_theta), math.sin(two_theta)
        ixx = mean + half_diff * cos2 - self.ixy * sin2
        iyy = mean - half_diff * cos2 + self.ixy * sin2
        ixy = half_diff * sin2 + self.ixy * cos2
        return (ixx, iyy, ixy)


# --------------------------------------------------------------------------- #
# Ring integration
# --------------------------------------------------------------------------- #

def ring_moments(points: Sequence[Point]) -> RawMoments:
    """Green's-theorem integrals of one closed ring about the origin.

    The ring must be simple (non-self-intersecting) and is treated as closed:
    the last vertex connects back to the first. A counter-clockwise ring gives a
    positive area, a clockwise ring a negative one — which is exactly how holes
    subtract themselves.
    """
    n = len(points)
    if n < 3:
        return RawMoments(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    area2 = 0.0  # 2A
    qx6 = 0.0  # 6 * int y dA
    qy6 = 0.0  # 6 * int x dA
    ixx12 = 0.0  # 12 * int y^2 dA
    iyy12 = 0.0  # 12 * int x^2 dA
    ixy24 = 0.0  # 24 * int xy dA

    x1, y1 = points[-1]
    for x2, y2 in points:
        c = x1 * y2 - x2 * y1
        area2 += c
        qx6 += (y1 + y2) * c
        qy6 += (x1 + x2) * c
        ixx12 += (y1 * y1 + y1 * y2 + y2 * y2) * c
        iyy12 += (x1 * x1 + x1 * x2 + x2 * x2) * c
        ixy24 += (x1 * y2 + 2.0 * x1 * y1 + 2.0 * x2 * y2 + x2 * y1) * c
        x1, y1 = x2, y2

    return RawMoments(
        area=area2 / 2.0,
        qx=qx6 / 6.0,
        qy=qy6 / 6.0,
        ixx_o=ixx12 / 12.0,
        iyy_o=iyy12 / 12.0,
        ixy_o=ixy24 / 24.0,
    )


@timed("structural.green")
def section_moments(
    rings: Iterable[Sequence[Point]] | Iterable[tuple[Sequence[Point], bool]],
) -> RawMoments:
    """Sum the integrals of every ring in a section.

    Accepts either bare point sequences (whose winding already encodes whether
    they are shells or holes) or ``(points, is_hole)`` pairs, in which case the
    winding is forced to match ``is_hole``.
    """
    total = RawMoments(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    for entry in rings:
        if (
            isinstance(entry, tuple)
            and len(entry) == 2
            and not isinstance(entry[1], (int, float))
        ):
            points, is_hole = entry  # type: ignore[misc]
            moments = ring_moments(points)
            # Force the sign: a hole must subtract, a shell must add.
            if (moments.area < 0) != bool(is_hole):
                points = list(reversed(list(points)))
                moments = ring_moments(points)
        else:
            moments = ring_moments(entry)  # type: ignore[arg-type]
        total = total + moments

    return total


def moments_from_polygon(polygon: object) -> RawMoments:
    """Integrate a Shapely ``Polygon``/``MultiPolygon``.

    Orientation is normalised first, so holes always subtract correctly
    regardless of how the polygon was constructed or repaired.
    """
    from ..geometry.shapely_bridge import polygon_rings_coordinates

    rings: list[Sequence[Point]] = []
    for shell, holes in polygon_rings_coordinates(polygon):
        rings.append(shell)
        rings.extend(holes)
    return section_moments(rings)


def polygon_perimeter(polygon: object) -> float:
    """Total wetted perimeter of a Shapely polygon: outer plus every hole."""
    from ..geometry.shapely_bridge import polygon_rings_coordinates
    from ..geometry.primitives import perimeter as ring_perimeter

    total = 0.0
    for shell, holes in polygon_rings_coordinates(polygon):
        total += ring_perimeter(shell, closed=True)
        total += sum(ring_perimeter(hole, closed=True) for hole in holes)
    return total


def polygon_outer_perimeter(polygon: object) -> float:
    """Only the outside of the section — what a coating can actually reach.

    The difference from :func:`polygon_perimeter` is every internal chamber,
    and on a thermally broken profile that is most of the wetted length.
    Charging a customer for anodising the inside of a chamber that no bath
    ever touches is how a coating estimate ends up twice the invoice.
    """
    from ..geometry.shapely_bridge import polygon_rings_coordinates
    from ..geometry.primitives import perimeter as ring_perimeter

    return sum(
        ring_perimeter(shell, closed=True)
        for shell, _holes in polygon_rings_coordinates(polygon)
    )


__all__ = [
    "RawMoments",
    "CentroidalMoments",
    "ring_moments",
    "section_moments",
    "moments_from_polygon",
    "polygon_perimeter",
    "polygon_outer_perimeter",
]
