"""How much noise the window stops, and why it usually stops less than that.

Sound reduction is the specification customers on a main road care about most
and the one that is quoted worst, because the figure quoted is the glass
laboratory's ``R_w`` and the thing installed is a window with a joint round
it, an opening leaf and, half the time, a shutter box with a hole in it above.

What is calculated here is an estimate, and it says so. The glazing itself
follows the mass law with the corrections that matter in practice — an
asymmetric pair of panes beats a symmetric one because their coincidence dips
do not land together, and a laminated pane with an acoustic interlayer damps
its own dip. Then the window is penalised for what it actually is: how it
opens, how it seals, and whether the shutter box above it is a hole in the
wall.

Nothing here replaces a test report. It replaces a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SealClass(StrEnum):
    """How well the leaf closes onto the frame."""

    NONE = "none"
    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"

    @property
    def hebrew(self) -> str:
        return {
            "none": "ללא אטם",
            "single": "אטם היקפי אחד",
            "double": "שני אטמים היקפיים",
            "triple": "שלושה אטמים",
        }[self.value]

    @property
    def penalty(self) -> float:
        """How many decibels the joint gives back [dB]."""
        return {"none": 9.0, "single": 5.0, "double": 2.0, "triple": 1.0}[self.value]


#: What each way of opening costs acoustically [dB]. A fixed light has no
#: joint to leak through; a slider has two leaves passing each other and is
#: the worst thing in the catalogue for noise, which is exactly what a
#: customer on a main road is not told when they buy one.
OPENING_PENALTY: dict[str, float] = {
    "fixed": 0.0,
    "casement": 2.0,
    "tilt_turn": 2.0,
    "top_hung": 3.0,
    "bottom_hung": 3.0,
    "pivot": 4.0,
    "door": 4.0,
    "sliding": 7.0,
    "lift_slide": 4.0,
}

#: A built-in shutter box is a cavity through the wall above the window, and
#: unless it is a sealed insulated one it undoes the window below it [dB].
SHUTTER_BOX_PENALTY: dict[str, float] = {
    "built_in": 4.0,
    "surface": 1.0,
    "integrated": 2.0,
}


def pane_reduction(thickness_mm: float, *, laminated: bool = False,
                   acoustic_interlayer: bool = False) -> float:
    """Single-pane weighted sound reduction ``R_w`` [dB].

    The mass law in the form used for glass — about 6 dB per doubling of
    mass — fitted to the ordinary reference that 4 mm float is close to
    29 dB, then credited for the damping a laminated interlayer adds across
    the coincidence dip.
    """
    if thickness_mm <= 0:
        return 0.0
    base = 29.0 + 6.0 * math.log2(thickness_mm / 4.0)
    if laminated:
        base += 3.0
    if acoustic_interlayer:
        base += 2.0
    return base


def unit_reduction(build_up: Any) -> tuple[float, list[str]]:
    """Estimated ``R_w`` of a glazed unit, with what drove the number.

    A sealed unit is two masses on a spring, so it beats the sum of its
    panes — but only if the panes differ. Two identical panes share a
    coincidence frequency and dip together, which is why the classic 4-16-4
    unit measures no better than a single thick pane.
    """
    notes: list[str] = []
    panes = list(build_up.panes)
    if not panes:
        return 0.0, notes

    thicknesses = [getattr(pane, "thickness", 4.0) for pane in panes]
    laminated = [bool(getattr(pane, "interlayer", None)) for pane in panes]
    singles = [
        pane_reduction(t, laminated=lam)
        for t, lam in zip(thicknesses, laminated)
    ]
    if len(panes) == 1:
        return singles[0], notes

    # The cavity adds a few decibels, more as it gets wider, and the widest
    # cavity in the unit is the one doing the work.
    cavity = max(
        (getattr(gap, "width", 12.0) for gap in getattr(build_up, "cavities", [])),
        default=12.0,
    )
    coupling = 2.0 + 4.0 * math.log2(max(cavity, 6.0) / 6.0)
    estimate = max(singles) + coupling

    if len(set(round(t, 1) for t in thicknesses)) == 1:
        estimate -= 2.0
        notes.append(
            "שתי שמשות בעובי זהה — שקעי הקואינצידנציה מצטברים. "
            "עובי שונה בין השמשות שווה כ-⁦2–3⁩ דציבל"
        )
    else:
        notes.append("עוביים שונים בין השמשות — שקעי הקואינצידנציה מפוזרים")
    if any(laminated):
        notes.append("שמשה טריפלקס מרככת את השקע")
    else:
        notes.append("טריפלקס אקוסטי היה מוסיף כ-⁦3–5⁩ דציבל")
    return estimate, notes


@dataclass
class AcousticEstimate:
    """What the window is likely to stop, and what is taking it away."""

    r_window: float
    r_glass: float
    opening_penalty: float
    seal_penalty: float
    shutter_penalty: float
    seal: SealClass
    notes: list[str] = field(default_factory=list)
    source: str = "אומדן הנדסי — אינו תחליף לדוח בדיקה"

    def describe(self) -> str:
        return f"⁦R_w ≈ {self.r_window:.0f}⁩ dB (זיגוג ⁦{self.r_glass:.0f}⁩)"

    def summary_rows(self) -> list[tuple[str, str]]:
        rows = [
            ("⁦R_w⁩ משוער לחלון", f"⁦{self.r_window:.0f}⁩ dB"),
            ("הזיגוג לבדו", f"⁦{self.r_glass:.0f}⁩ dB"),
            ("אופן הפתיחה", f"⁦−{self.opening_penalty:.0f}⁩ dB"),
            ("איטום היקפי", f"⁦−{self.seal_penalty:.0f}⁩ dB · {self.seal.hebrew}"),
        ]
        if self.shutter_penalty:
            rows.append(("ארגז תריס", f"⁦−{self.shutter_penalty:.0f}⁩ dB"))
        return rows


def estimate_acoustic(
    build: Any,
    *,
    seal: SealClass = SealClass.DOUBLE,
) -> AcousticEstimate:
    """Estimate the installed ``R_w`` of a built element."""
    notes: list[str] = []
    reductions: list[float] = []
    for panel in build.glass:
        value, panel_notes = unit_reduction(panel.build_up)
        reductions.append(value)
        for note in panel_notes:
            if note not in notes:
                notes.append(note)
    r_glass = min(reductions) if reductions else 0.0

    # The worst leaf sets the window: sound finds the weakest path.
    opening_penalty = 0.0
    for cell in build.opening.all_cells():
        sash = getattr(cell, "sash", None)
        kind = str(getattr(sash, "opening_type", "fixed")) if sash else "fixed"
        opening_penalty = max(opening_penalty, OPENING_PENALTY.get(kind, 2.0))

    shutter_penalty = 0.0
    fitted = build.opening.metadata.get("accessories", {})
    shutter = fitted.get("shutter") if isinstance(fitted, dict) else None
    if shutter:
        shutter_penalty = SHUTTER_BOX_PENALTY.get(shutter.get("box", "built_in"), 3.0)
        if shutter_penalty >= 3.0:
            notes.append(
                "ארגז תריס בנוי — אטימת הארגז היא מה שיקבע את התוצאה בפועל"
            )

    r_window = max(
        0.0, r_glass - opening_penalty - seal.penalty - shutter_penalty
    )
    return AcousticEstimate(
        r_window=r_window,
        r_glass=r_glass,
        opening_penalty=opening_penalty,
        seal_penalty=seal.penalty,
        shutter_penalty=shutter_penalty,
        seal=seal,
        notes=notes,
    )


__all__ = [
    "AcousticEstimate",
    "OPENING_PENALTY",
    "SHUTTER_BOX_PENALTY",
    "SealClass",
    "estimate_acoustic",
    "pane_reduction",
    "unit_reduction",
]
