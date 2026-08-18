"""Kerf and mitre compensation.

The length a piece *consumes* on a bar is not the length written on the cutting
list. Two effects intervene:

**Mitre allowance.** A cut at an angle other than 90 degrees produces a sloped
face whose extent along the bar is ``H * cot(theta)``, where ``H`` is the
profile depth normal to the cut. Where the nominal length is measured on that
sloped piece therefore matters, and shops differ:

``LengthReference.CENTRELINE``
    Length measured at mid-depth — the geometric convention, and the one the
    system specification uses. Each end adds ``(H/2) |cot(theta)|``:

    .. math::
        L_{eff} = L + \\tfrac{H}{2}\\,(|\\cot\\theta_1| + |\\cot\\theta_2|) + K

``LengthReference.OUTER``
    Length measured tip to tip on the long face — what most window fabricators
    put on a cutting list for a mitred frame member. Nothing is added but the
    kerf, because the quoted length *is* the consumed length.

``LengthReference.INNER``
    Length measured on the short face. Each end adds the full ``H |cot(theta)|``.

**Blade kerf.** The saw removes ``K`` mm of material at every cut. Charging each
piece for one kerf, plus a single leading trim on the bar, matches how a bar is
actually consumed: N pieces need N cuts to free them from the remaining stock.

Angles are in degrees, measured between the cut face and the bar axis, so 90 is
a square cut and 45 a standard mitre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..core.errors import NestingError


class LengthReference(StrEnum):
    """Where on the profile a nominal cut length is measured."""

    CENTRELINE = "centreline"
    OUTER = "outer"
    INNER = "inner"


#: Angles this close to 90 degrees are treated as square cuts.
SQUARE_TOLERANCE_DEG = 1e-6


def cot_deg(angle_deg: float) -> float:
    """``cot`` of an angle in degrees, with 90 degrees mapped exactly to zero.

    Raises
    ------
    NestingError
        The angle is a multiple of 180 degrees, where the cut is parallel to the
        bar axis and consumes infinite length.
    """
    normalised = angle_deg % 180.0
    if abs(normalised - 90.0) < SQUARE_TOLERANCE_DEG:
        return 0.0
    if normalised < SQUARE_TOLERANCE_DEG or abs(normalised - 180.0) < SQUARE_TOLERANCE_DEG:
        raise NestingError(
            "Cut angle is parallel to the bar axis and cannot be produced",
            angle=angle_deg,
        )
    return 1.0 / math.tan(math.radians(normalised))


@dataclass(frozen=True)
class CutSpec:
    """Everything needed to convert a nominal length into a consumed length."""

    #: Blade kerf [mm].
    kerf: float = 3.5
    #: Profile depth normal to the cut [mm] — the section height for a mitre.
    profile_depth: float = 0.0
    reference: LengthReference = LengthReference.CENTRELINE
    #: Material trimmed from the leading end of every fresh bar [mm].
    trim_start: float = 10.0
    #: Material that must remain unusable at the trailing end [mm].
    trim_end: float = 0.0

    def __post_init__(self) -> None:
        if self.kerf < 0:
            raise NestingError("Kerf must be >= 0", kerf=self.kerf)
        if self.profile_depth < 0:
            raise NestingError(
                "Profile depth must be >= 0", profile_depth=self.profile_depth
            )

    def mitre_allowance(self, angle_left: float, angle_right: float) -> float:
        """Extra length consumed by the two angled end faces [mm]."""
        if self.profile_depth <= 0:
            return 0.0
        cot_sum = abs(cot_deg(angle_left)) + abs(cot_deg(angle_right))
        if self.reference is LengthReference.CENTRELINE:
            return 0.5 * self.profile_depth * cot_sum
        if self.reference is LengthReference.INNER:
            return self.profile_depth * cot_sum
        return 0.0  # OUTER: the quoted length already spans tip to tip

    def effective_length(
        self, nominal_length: float, angle_left: float = 90.0, angle_right: float = 90.0
    ) -> float:
        """Length of bar consumed by one piece, including its kerf [mm]."""
        if nominal_length <= 0:
            raise NestingError("Piece length must be > 0", length=nominal_length)
        return nominal_length + self.mitre_allowance(angle_left, angle_right) + self.kerf

    def usable_length(self, stock_length: float) -> float:
        """Bar length available for pieces after the end trims [mm]."""
        usable = stock_length - self.trim_start - self.trim_end
        if usable <= 0:
            raise NestingError(
                "Trim allowances consume the whole bar",
                stock_length=stock_length,
                trim_start=self.trim_start,
                trim_end=self.trim_end,
            )
        return usable

    def net_length(
        self, effective_length: float, angle_left: float = 90.0, angle_right: float = 90.0
    ) -> float:
        """Inverse of :meth:`effective_length`: recover the nominal length."""
        return effective_length - self.mitre_allowance(angle_left, angle_right) - self.kerf


def effective_length(
    nominal_length: float,
    *,
    angle_left: float = 90.0,
    angle_right: float = 90.0,
    kerf: float = 3.5,
    profile_depth: float = 0.0,
    reference: LengthReference = LengthReference.CENTRELINE,
) -> float:
    """Convenience wrapper around :meth:`CutSpec.effective_length`."""
    spec = CutSpec(
        kerf=kerf, profile_depth=profile_depth, reference=reference
    )
    return spec.effective_length(nominal_length, angle_left, angle_right)


def is_square_cut(angle_deg: float) -> bool:
    """True when the angle is a 90 degree (square) cut."""
    return abs((angle_deg % 180.0) - 90.0) < SQUARE_TOLERANCE_DEG


def complementary_angle(angle_deg: float) -> float:
    """The angle of the mating face when two mitred pieces meet in a corner.

    Two pieces forming a square corner carry 45/45; in general a corner of
    included angle ``a`` splits into two cuts of ``a/2``, so the complement of a
    cut is ``180 - angle``.
    """
    return 180.0 - (angle_deg % 180.0)


def waste_from_angles(
    profile_depth: float, angle_left: float, angle_right: float
) -> float:
    """Material lost purely to the two mitre wedges [mm of bar length].

    Useful for reporting: it separates unavoidable mitre loss from the
    end-of-bar remnant, which is what an operator can actually influence.
    """
    if profile_depth <= 0:
        return 0.0
    return 0.5 * profile_depth * (abs(cot_deg(angle_left)) + abs(cot_deg(angle_right)))


__all__ = [
    "LengthReference",
    "CutSpec",
    "cot_deg",
    "effective_length",
    "is_square_cut",
    "complementary_angle",
    "waste_from_angles",
    "SQUARE_TOLERANCE_DEG",
]
