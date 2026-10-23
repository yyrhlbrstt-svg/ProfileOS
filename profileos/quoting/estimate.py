"""A price over the phone, in the time it takes to say the window.

Half the enquiries a small shop gets start with "כמה עולה בערך חלון הזזה
240 על 210?". Answering that today means either guessing — and being held to
the guess — or "I'll get back to you", which loses the call to whoever
answers first. The big packages answer it only after a full element has been
designed.

This takes the sentence the customer said, finds the opening it describes in
the type library, builds it with the same rules as a real quotation and
prices it the same way. The result is plainly labelled an estimate: it uses
typical figures where the shop has not entered its own, and it says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass
class Estimate:
    """What the phone answer rests on."""

    title: str
    width: float
    height: float
    quantity: int
    net: float
    gross: float
    preset: Any = None
    warnings: list[str] = field(default_factory=list)

    @property
    def area_m2(self) -> float:
        return self.width * self.height * self.quantity / 1e6

    @property
    def per_m2(self) -> float:
        return self.gross / self.area_m2 if self.area_m2 else 0.0

    @property
    def per_unit(self) -> float:
        return self.gross / self.quantity if self.quantity else self.gross

    def describe(self) -> str:
        unit = f" · ⁦{self.per_unit:,.0f} ₪⁩ ליחידה" if self.quantity > 1 else ""
        return (
            f"{self.title} · ⁦{self.width:.0f} × {self.height:.0f}⁩ מ״מ"
            f"{f' · ⁦{self.quantity}⁩ יח׳' if self.quantity > 1 else ''}\n"
            f"כ-⁦{self.gross:,.0f} ₪⁩ כולל מע״מ{unit} · ⁦{self.per_m2:,.0f} ₪⁩ למ״ר"
        )


def opening_from_preset(preset: Any, name: str = "הערכה") -> Any:
    """A real :class:`Opening` from a library preset — the same one the form makes."""
    from ..elements import Cell, ElementKind, Opening, OpeningType, Sash

    opening = Opening(
        name=name,
        kind=ElementKind(preset.kind),
        width=preset.width,
        height=preset.height,
        quantity=preset.quantity,
        glass_spec_id=preset.glass,
        system_id=preset.system_id or "generic",
        shape=getattr(preset, "shape", "rectangle") or "rectangle",
        height_right=getattr(preset, "height_right", None),
        rise=getattr(preset, "rise", None),
    )
    opening.divide_evenly(preset.columns, preset.rows)
    sash_type = OpeningType(preset.sash_type)
    if sash_type is not OpeningType.FIXED:
        column = min(preset.sash_column, opening.column_count - 1)
        row = min(preset.sash_row, opening.row_count - 1)
        opening.set_cell(Cell(column=column, row=row, sash=Sash(opening_type=sash_type)))
    return opening


def estimate(text: str) -> Estimate | None:
    """Price the opening a sentence describes, or ``None`` if none matches.

    >>> e = estimate("הזזה 2 כנפיים 240/210")   # doctest: +SKIP
    """
    from ..library import search_openings
    from .editor import QuoteDraft
    from .rates import shop_labour, shop_rates

    found = search_openings(text, limit=1)
    if not found:
        return None
    preset = found[0]
    opening = opening_from_preset(preset)
    system = preset.system_id or "generic"
    try:
        draft = QuoteDraft.start(
            [opening], project_name="הערכה טלפונית", system_id=system,
            glass_id=preset.glass, fallback_rates=shop_rates(), labour=shop_labour(),
        )
    except Exception:  # noqa: BLE001 - an unclassified series: price it generic
        system = "generic"
        opening.system_id = system
        draft = QuoteDraft.start(
            [opening], project_name="הערכה טלפונית", system_id=system,
            glass_id=preset.glass, fallback_rates=shop_rates(), labour=shop_labour(),
        )
    totals = draft.totals()
    return Estimate(
        title=preset.title,
        width=preset.width,
        height=preset.height,
        quantity=preset.quantity,
        net=totals["net"],
        gross=totals["gross"],
        preset=preset,
        warnings=list(draft.variant().warnings),
    )


__all__ = ["Estimate", "estimate", "opening_from_preset"]
