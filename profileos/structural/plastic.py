"""Plastic neutral axis and plastic section moduli.

The plastic neutral axis (PNA) is the line that divides the section into two
equal areas — not the elastic centroid, unless the section is symmetric about
that axis. Once located, the plastic modulus is the first moment of both halves
about the PNA:

.. math::
    Z = A_{top} d_{top} + A_{bot} d_{bot}

Because each half is exactly ``A/2`` by construction, this simplifies to

.. math::
    Z = \\tfrac{A}{2}\\,(\\bar{y}_{top} - \\bar{y}_{bot})

which is what :func:`plastic_modulus_x` evaluates.

The PNA is found by bisection on the "area below the cut" function, which is
continuous and monotonically increasing in the cut position — so bisection is
unconditionally convergent, with no dependence on a good initial guess. Each
evaluation clips the section with a half-plane using Shapely, which handles
multi-region and multi-chamber sections without any special cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..core.errors import DegenerateSectionError
from ..core.logging_setup import get_logger
from ..core.profiling import timed

_log = get_logger("structural.plastic")

#: Bisection converges to this fraction of the section depth.
DEFAULT_TOLERANCE = 1e-9
DEFAULT_MAX_ITERATIONS = 200


@dataclass(frozen=True)
class PlasticResult:
    """Outcome of a plastic-modulus calculation about one axis."""

    #: Position of the plastic neutral axis along the bending-normal direction.
    axis_position: float
    modulus: float
    area_above: float
    area_below: float
    centroid_above: float
    centroid_below: float
    iterations: int
    converged: bool

    @property
    def balance_error(self) -> float:
        """Relative area imbalance between the two halves; ~0 when converged."""
        total = self.area_above + self.area_below
        if total <= 0:
            return 0.0
        return abs(self.area_above - self.area_below) / total


def _half_plane(polygon: Any, position: float, *, vertical: bool, below: bool) -> Any:
    """Clip ``polygon`` to one side of an axis-aligned line.

    ``vertical=False`` cuts on ``y = position``; ``vertical=True`` on
    ``x = position``. ``below`` selects the lower/left portion.
    """
    from shapely.geometry import box

    min_x, min_y, max_x, max_y = polygon.bounds
    # Pad so the clipping box always fully covers the section transversally.
    pad = max(max_x - min_x, max_y - min_y) + 10.0

    if vertical:
        if below:
            clip = box(min_x - pad, min_y - pad, position, max_y + pad)
        else:
            clip = box(position, min_y - pad, max_x + pad, max_y + pad)
    else:
        if below:
            clip = box(min_x - pad, min_y - pad, max_x + pad, position)
        else:
            clip = box(min_x - pad, position, max_x + pad, max_y + pad)

    return polygon.intersection(clip)


def _find_equal_area_axis(
    polygon: Any,
    *,
    vertical: bool,
    tolerance: float = DEFAULT_TOLERANCE,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> tuple[float, int, bool]:
    """Bisect for the line splitting ``polygon`` into two equal areas.

    Returns ``(position, iterations, converged)``.
    """
    total_area = float(polygon.area)
    if total_area <= 0:
        raise DegenerateSectionError("Cannot locate a plastic neutral axis on a zero-area section")

    min_x, min_y, max_x, max_y = polygon.bounds
    low, high = (min_x, max_x) if vertical else (min_y, max_y)
    target = total_area / 2.0
    span = high - low
    if span <= 0:
        raise DegenerateSectionError("Section has zero extent along the bending direction")

    area_below: Callable[[float], float] = lambda p: float(
        _half_plane(polygon, p, vertical=vertical, below=True).area
    )

    iterations = 0
    # Absolute tolerance on position, scaled to the section's own size.
    position_tol = max(span * tolerance, 1e-12)

    while iterations < max_iterations and (high - low) > position_tol:
        mid = 0.5 * (low + high)
        if area_below(mid) < target:
            low = mid
        else:
            high = mid
        iterations += 1

    position = 0.5 * (low + high)
    converged = (high - low) <= position_tol
    if not converged:  # pragma: no cover - bisection on a bounded interval
        _log.warning(
            "PNA bisection stopped after %d iterations with interval %.3e",
            iterations,
            high - low,
        )
    return position, iterations, converged


def _part_area_and_centroid(part: Any, *, vertical: bool) -> tuple[float, float]:
    """Area and the relevant centroid coordinate of a clipped half."""
    area = float(part.area)
    if area <= 0:
        return 0.0, 0.0
    centroid = part.centroid
    return area, float(centroid.x if vertical else centroid.y)


@timed("structural.plastic_x")
def plastic_modulus_x(polygon: Any, **kwargs: Any) -> PlasticResult:
    """Plastic modulus ``Z_x`` for bending about a horizontal axis.

    The PNA is a horizontal line ``y = y_p``; the returned
    :attr:`PlasticResult.axis_position` is that ``y_p`` in drawing coordinates.
    """
    return _plastic_modulus(polygon, vertical=False, **kwargs)


@timed("structural.plastic_y")
def plastic_modulus_y(polygon: Any, **kwargs: Any) -> PlasticResult:
    """Plastic modulus ``Z_y`` for bending about a vertical axis."""
    return _plastic_modulus(polygon, vertical=True, **kwargs)


def _plastic_modulus(
    polygon: Any,
    *,
    vertical: bool,
    tolerance: float = DEFAULT_TOLERANCE,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> PlasticResult:
    position, iterations, converged = _find_equal_area_axis(
        polygon, vertical=vertical, tolerance=tolerance, max_iterations=max_iterations
    )

    below = _half_plane(polygon, position, vertical=vertical, below=True)
    above = _half_plane(polygon, position, vertical=vertical, below=False)

    area_below, centroid_below = _part_area_and_centroid(below, vertical=vertical)
    area_above, centroid_above = _part_area_and_centroid(above, vertical=vertical)

    # Z = A_above * (c_above - pna) + A_below * (pna - c_below). Using each
    # half's own area (rather than assuming A/2) keeps the result accurate even
    # if bisection stopped a hair early.
    modulus = area_above * (centroid_above - position) + area_below * (position - centroid_below)

    return PlasticResult(
        axis_position=position,
        modulus=abs(modulus),
        area_above=area_above,
        area_below=area_below,
        centroid_above=centroid_above,
        centroid_below=centroid_below,
        iterations=iterations,
        converged=converged,
    )


def shape_factor(plastic: float, elastic: float) -> float | None:
    """``Z / S`` — the reserve between first yield and a full plastic hinge.

    Roughly 1.5 for a solid rectangle, 1.1-1.2 for an I-section, and close to
    1.27 for a solid circle. Values far outside 1.0-1.7 usually indicate a
    geometry problem rather than an unusual section.
    """
    if elastic <= 0:
        return None
    return plastic / elastic


__all__ = [
    "PlasticResult",
    "plastic_modulus_x",
    "plastic_modulus_y",
    "shape_factor",
    "DEFAULT_TOLERANCE",
]
