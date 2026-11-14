"""The U-value of the window, not of the glass.

A glass supplier quotes ``U_g``, the centre-pane figure, and it is the number
that ends up on the quote because it is the number somebody was given. It is
also the most flattering number in the building: the frame is two to four
times worse than the glass, and the edge of the sealed unit is worse than
either because the spacer bridges straight across the cavity.

What a thermal regulation asks for is ``U_w``, the whole window, which
EN ISO 10077-1 assembles by area weighting:

    U_w = (A_g U_g + A_f U_f + l_g psi_g) / A_w

with ``A_g`` the visible glass, ``A_f`` the frame it sits in, ``l_g`` the
total glass perimeter and ``psi_g`` the linear transmittance of the edge. The
areas come from the element that was actually drawn, so a window divided into
six small panes correctly reports worse than the same opening glazed once —
which is exactly the trade-off an architect is making when they ask for the
divisions.

Frame values are typical for the construction, not any manufacturer's tested
figure, and every result says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.errors import ProfileOSError


class FrameClass(StrEnum):
    """What the frame is thermally, which is nearly all of what it is."""

    PLAIN = "plain"
    THERMAL_BREAK = "thermal_break"
    THERMAL_BREAK_WIDE = "thermal_break_wide"
    TIMBER_CLAD = "timber_clad"

    @property
    def hebrew(self) -> str:
        return {
            "plain": "פרופיל רגיל, ללא שבירה תרמית",
            "thermal_break": "שבירה תרמית רגילה",
            "thermal_break_wide": "שבירה תרמית רחבה",
            "timber_clad": "עץ מחופה אלומיניום",
        }[self.value]

    @property
    def u_frame(self) -> float:
        """Typical frame transmittance ``U_f`` [W/(m^2 K)].

        These are the ranges the constructions fall in, not any one system's
        tested value. A supplier's own figure, once it is in the catalogue,
        beats these and the result says which was used.
        """
        return {
            "plain": 5.9,
            "thermal_break": 3.2,
            "thermal_break_wide": 2.2,
            "timber_clad": 1.6,
        }[self.value]


class Spacer(StrEnum):
    """The bar around the edge of the sealed unit."""

    ALUMINIUM = "aluminium"
    WARM_EDGE = "warm_edge"
    STAINLESS = "stainless"

    @property
    def hebrew(self) -> str:
        return {
            "aluminium": "מרווח אלומיניום",
            "warm_edge": "מרווח חם",
            "stainless": "מרווח נירוסטה",
        }[self.value]

    def psi(self, frame: FrameClass, panes: int) -> float:
        """Linear transmittance of the glass edge ``psi_g`` [W/(m K)].

        Typical values from the EN ISO 10077-1 tables' ranges: the aluminium
        bar bridges the cavity and costs the most, a warm edge roughly halves
        it, and a triple unit has two cavities to bridge.
        """
        base = {
            "aluminium": 0.08 if frame is not FrameClass.PLAIN else 0.06,
            "warm_edge": 0.05 if frame is not FrameClass.PLAIN else 0.04,
            "stainless": 0.06 if frame is not FrameClass.PLAIN else 0.05,
        }[self.value]
        return base * (1.0 if panes <= 2 else 1.25)


@dataclass
class WindowThermal:
    """The whole-window result, with every part it was assembled from."""

    u_window: float
    u_glass: float
    u_frame: float
    glass_area: float
    frame_area: float
    total_area: float
    glass_perimeter: float
    psi: float
    frame_class: FrameClass
    spacer: Spacer
    #: Solar heat gain of the glazing, area-weighted onto the whole window.
    g_window: float = 0.0
    source: str = "ערכי מסגרת טיפוסיים — לא מנתוני היצרן"
    notes: list[str] = field(default_factory=list)

    @property
    def frame_fraction(self) -> float:
        """How much of the opening is frame — the number that explains the rest."""
        return self.frame_area / self.total_area if self.total_area else 0.0

    def describe(self) -> str:
        return (
            f"⁦U_w = {self.u_window:.2f}⁩ W/m²K "
            f"(זיגוג ⁦{self.u_glass:.2f}⁩, מסגרת ⁦{self.u_frame:.2f}⁩, "
            f"⁦{self.frame_fraction * 100:.0f}%⁩ מסגרת)"
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("⁦U_w⁩ החלון כולו", f"⁦{self.u_window:.2f}⁩ W/m²K"),
            ("⁦U_g⁩ הזיגוג", f"⁦{self.u_glass:.2f}⁩ W/m²K"),
            ("⁦U_f⁩ המסגרת", f"⁦{self.u_frame:.2f}⁩ W/m²K · {self.frame_class.hebrew}"),
            ("⁦Ψ_g⁩ קצה הזיגוג", f"⁦{self.psi:.3f}⁩ W/mK · {self.spacer.hebrew}"),
            ("שטח זיגוג", f"⁦{self.glass_area:.2f}⁩ m²"),
            ("שטח מסגרת", f"⁦{self.frame_area:.2f}⁩ m² (⁦{self.frame_fraction * 100:.0f}%⁩)"),
            ("היקף זיגוג", f"⁦{self.glass_perimeter:.2f}⁩ m"),
            ("⁦g_w⁩ מקדם הצללה",
             f"⁦{self.g_window:.2f}⁩" if self.g_window else "לא פורסם על ידי ספק הזכוכית"),
        ]


def window_u_value(
    build: Any,
    *,
    frame_class: FrameClass | None = None,
    spacer: Spacer = Spacer.WARM_EDGE,
    u_frame: float | None = None,
) -> WindowThermal:
    """Assemble ``U_w`` for a built element, EN ISO 10077-1 area weighting.

    The glass areas and perimeters are the ones the builder produced, so this
    is the window that was drawn rather than a rectangle of the same size.
    """
    opening = build.opening
    total_area = opening.width * opening.height / 1_000_000.0
    if total_area <= 0:
        raise ProfileOSError("לפתח אין שטח")

    glass_area = 0.0
    solar_area = 0.0
    perimeter = 0.0
    u_glass_weighted = 0.0
    g_weighted = 0.0
    panes = 1
    for panel in build.glass:
        area = panel.width * panel.height / 1_000_000.0 * panel.quantity
        glass_area += area
        perimeter += 2.0 * (panel.width + panel.height) / 1000.0 * panel.quantity
        build_up = panel.build_up
        u_glass_weighted += build_up.u_value() * area
        # The solar factor is the supplier's to publish; where it has not been
        # given it stays out of the average rather than being guessed at.
        if build_up.g_value is not None:
            g_weighted += build_up.g_value * area
            solar_area += area
        panes = max(panes, len(build_up.panes))

    if glass_area <= 0:
        raise ProfileOSError("לפתח אין זיגוג, אז אין מה לשקלל")
    if glass_area > total_area:
        raise ProfileOSError("שטח הזיגוג גדול משטח הפתח")

    u_glass = u_glass_weighted / glass_area
    g_glass = g_weighted / solar_area if solar_area else 0.0
    frame_area = max(total_area - glass_area, 0.0)

    if frame_class is None:
        frame_class = _frame_class_of(build)
    frame_u = u_frame if u_frame is not None else frame_class.u_frame
    psi = spacer.psi(frame_class, panes)

    from ..glazing import area_weighted_u

    u_window = area_weighted_u(
        glass_area=glass_area,
        glass_u=u_glass,
        frame_area=frame_area,
        frame_u=frame_u,
        perimeter=perimeter,
        psi=psi,
    )

    notes: list[str] = []
    if frame_area / total_area > 0.35:
        notes.append(
            f"⁦{frame_area / total_area * 100:.0f}%⁩ מהפתח הוא מסגרת — "
            "חלוקה גסה יותר תשפר את הבידוד"
        )
    if spacer is Spacer.ALUMINIUM:
        notes.append("מרווח אלומיניום — מרווח חם משפר את ⁦U_w⁩ בכ-⁦0.1–0.2⁩")

    source = (
        "ערך מסגרת שהוזן" if u_frame is not None
        else "ערכי מסגרת טיפוסיים — לא מנתוני היצרן"
    )
    return WindowThermal(
        u_window=u_window,
        u_glass=u_glass,
        u_frame=frame_u,
        glass_area=glass_area,
        frame_area=frame_area,
        total_area=total_area,
        glass_perimeter=perimeter,
        psi=psi,
        frame_class=frame_class,
        spacer=spacer,
        g_window=g_glass * glass_area / total_area,
        source=source,
        notes=notes,
    )


def _frame_class_of(build: Any) -> FrameClass:
    """What the frame is, read off the system rather than asked for again."""
    system_id = getattr(build.opening, "system_id", "generic")
    try:
        from ..systems import DIRECTORY

        entry = DIRECTORY.get(system_id)
        if entry is not None and entry.thermally_broken:
            return FrameClass.THERMAL_BREAK
    except Exception:  # noqa: BLE001 - the directory is not required to answer
        pass
    return FrameClass.PLAIN


__all__ = ["FrameClass", "Spacer", "WindowThermal", "window_u_value"]
