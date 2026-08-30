"""Anodising and paint, priced on the area a bath actually touches.

Coating is charged by the square metre, and almost every shop estimates that
square metre from the length of the bar and a factor somebody wrote down
years ago. Two things go wrong with that. The factor is per profile and gets
used for all of them; and the obvious way to compute it — the section's
wetted perimeter — includes every internal chamber, which no bath reaches and
no coater charges for. On a thermally broken mullion the wetted perimeter is
more than double the outer one, so an estimate built on it is twice the
invoice, and a quote built on the invoice is a quote that loses the job.

So the area here comes from the outside of the section, measured off the
drawing the shop already imported, multiplied by the length actually cut.
Everything else follows: minimum charges, the second pass a wood-effect
finish needs, and the fact that a coater's price per kilo and per square
metre answer differently for a heavy profile than for a light one.

Figures are the shop's own: a price list nobody has entered stays empty and
says so rather than pricing the work at zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.errors import ProfileOSError


class FinishKind(StrEnum):
    """What is done to the surface."""

    MILL = "mill"
    ANODISED = "anodised"
    ANODISED_COLOUR = "anodised_colour"
    POWDER = "powder"
    POWDER_TEXTURED = "powder_textured"
    WOOD_EFFECT = "wood_effect"
    ANTI_BACTERIAL = "anti_bacterial"

    @property
    def hebrew(self) -> str:
        return {
            "mill": "ללא גימור — אלומיניום גולמי",
            "anodised": "אנודייז טבעי",
            "anodised_colour": "אנודייז בגוון",
            "powder": "צבע בתנור",
            "powder_textured": "צבע בתנור מחוספס",
            "wood_effect": "הדמיית עץ",
            "anti_bacterial": "צבע אנטיבקטריאלי",
        }[self.value]

    @property
    def passes(self) -> int:
        """How many times through the line.

        A wood effect is a powder base coat and then a sublimation pass over
        it, so it is two jobs, two lead times and two minimum charges — which
        is why it costs what it costs and why quoting it as one pass loses
        money on every unit.
        """
        return 2 if self is FinishKind.WOOD_EFFECT else 1

    @property
    def is_coating(self) -> bool:
        return self is not FinishKind.MILL


@dataclass(frozen=True)
class FinishSpec:
    """A finish as it is ordered: what, in which colour, to which standard."""

    kind: FinishKind = FinishKind.POWDER
    #: RAL number, anodising shade, or the coater's own code.
    colour: str = ""
    #: Coating thickness asked for [micron]. Marine and desert exposure ask
    #: for more, and the coater prices by it.
    microns: float = 0.0
    #: Both faces get coated unless the profile is buried in the wall.
    both_faces: bool = True
    quality_label: str = ""

    def describe(self) -> str:
        parts = [self.kind.hebrew]
        if self.colour:
            parts.append(self.colour)
        if self.microns:
            parts.append(f"⁦{self.microns:.0f}⁩ מיקרון")
        return " · ".join(parts)


@dataclass
class FinishPrices:
    """What the coater charges. Empty until the shop enters their price list.

    Both bases are kept because coaters quote both, and which one is cheaper
    flips between a heavy mullion and a light bead. Where both are given, the
    higher of the two is charged, which is what the invoice does.
    """

    #: Price per square metre of coated surface.
    per_m2: float = 0.0
    #: Price per kilogram of aluminium sent.
    per_kg: float = 0.0
    #: Charged once per delivery however small it is.
    minimum_charge: float = 0.0
    #: A bar shorter than this is charged as if it were this long [mm].
    minimum_length: float = 0.0
    #: Working days the coater takes.
    lead_days: int = 7
    currency: str = "ILS"
    source: str = ""

    @property
    def is_entered(self) -> bool:
        return bool(self.per_m2 or self.per_kg)

    @property
    def is_confirmed(self) -> bool:
        """Whether the figures came from a quotation somebody kept."""
        return self.is_entered and bool(self.source.strip())


def coating_area_per_metre(properties: Any) -> float:
    """Coated area of one metre of this profile [m^2/m].

    Taken from the outside of the section only. The chambers are sealed and
    the bath never enters them, so their perimeter is not surface area for
    this purpose however much of the drawing it is.
    """
    outer = float(getattr(properties, "outer_perimeter", 0.0) or 0.0)
    if outer <= 0:
        # An older analysis without the outer figure: fall back to the wetted
        # perimeter, and say so rather than silently halving or doubling it.
        wetted = float(getattr(properties, "perimeter", 0.0) or 0.0)
        if wetted <= 0:
            raise ProfileOSError(
                "אין היקף לחתך — נתח את השרטוט לפני חישוב שטח הצביעה"
            )
        raise ProfileOSError(
            "החתך נותח לפני שהופרד ההיקף החיצוני — נתח אותו שוב כדי "
            "לחשב שטח צביעה נכון"
        )
    return outer / 1000.0


@dataclass
class FinishOrder:
    """What goes to the coater, and what it should cost."""

    spec: FinishSpec
    #: Coated area [m^2].
    area: float = 0.0
    #: Aluminium sent [kg].
    mass: float = 0.0
    pieces: int = 0
    #: Length of the longest bar, which decides whether it fits the line [mm].
    longest: float = 0.0
    price: float = 0.0
    priced_on: str = ""
    currency: str = "ILS"
    warnings: list[str] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)

    @property
    def area_per_piece(self) -> float:
        return round(self.area / self.pieces, 4) if self.pieces else 0.0

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("גימור", self.spec.describe()),
            ("שטח לצביעה", f"⁦{self.area:.2f}⁩ m²"),
            ("משקל", f"⁦{self.mass:.1f}⁩ ק״ג"),
            ("מוטות", f"⁦{self.pieces}⁩"),
            ("המוט הארוך ביותר", f"⁦{self.longest:,.0f}⁩ מ״מ"),
            ("מעברים בקו", f"⁦{self.spec.kind.passes}⁩"),
            ("מחיר", f"⁦{self.price:,.2f}⁩ {self.currency}" if self.price else "לא תומחר"),
            ("בסיס התמחור", self.priced_on or "אין מחירון"),
        ]


#: The longest bar the ordinary anodising and powder lines here will take
#: [mm]. Past it the work goes to a specialist, at a different price.
LINE_LENGTH_LIMIT = 6500.0


def order_finish(
    cuts: Any,
    properties_by_profile: dict[str, Any],
    spec: FinishSpec | None = None,
    *,
    prices: FinishPrices | None = None,
    mass_by_profile: dict[str, float] | None = None,
) -> FinishOrder:
    """Work out what a coating order actually is, from the cut list.

    ``cuts`` are the pieces to be coated; ``properties_by_profile`` the
    analysed section for each profile in them. A profile with no analysis
    cannot have its area computed and is reported rather than guessed.
    """
    spec = spec or FinishSpec()
    prices = prices or FinishPrices()
    mass_by_profile = mass_by_profile or {}

    order = FinishOrder(spec=spec, currency=prices.currency)
    if not spec.kind.is_coating:
        order.priced_on = "ללא גימור"
        return order

    missing: set[str] = set()
    for cut in cuts:
        profile_id = getattr(cut, "profile_id", "")
        quantity = int(getattr(cut, "quantity", 1))
        length = float(getattr(cut, "length", 0.0))
        properties = properties_by_profile.get(profile_id)
        if properties is None:
            missing.add(profile_id)
            continue
        try:
            per_metre = coating_area_per_metre(properties)
        except ProfileOSError as exc:
            order.warnings.append(f"{profile_id}: {exc}")
            continue

        billed = max(length, prices.minimum_length)
        area = per_metre * billed / 1000.0 * quantity * spec.kind.passes
        mass = mass_by_profile.get(profile_id, 0.0) * length / 1000.0 * quantity

        order.area += area
        order.mass += mass
        order.pieces += quantity
        order.longest = max(order.longest, length)
        order.lines.append({
            "profile_id": profile_id,
            "length_mm": round(length, 1),
            "quantity": quantity,
            "area_m2": round(area, 4),
            "mass_kg": round(mass, 2),
        })

    if missing:
        order.warnings.append(
            "אין חתך מנותח ל: " + ", ".join(sorted(missing))
            + " — שטח הצביעה שלהם לא נכלל"
        )
    if order.longest > LINE_LENGTH_LIMIT:
        order.warnings.append(
            f"מוט של ⁦{order.longest:,.0f}⁩ מ״מ ארוך מקו הצביעה הרגיל "
            f"(⁦{LINE_LENGTH_LIMIT:,.0f}⁩ מ״מ) — נדרש ספק אחר"
        )
    if spec.kind is FinishKind.WOOD_EFFECT:
        order.warnings.append(
            "הדמיית עץ היא שני מעברים — צבע בסיס ואז סובלימציה. "
            "זמן האספקה והמינימום נספרים פעמיים"
        )

    order.area = round(order.area, 3)
    order.mass = round(order.mass, 2)

    # -- price it, or say that nobody has entered a price list -------------- #
    if not prices.is_entered:
        order.priced_on = ""
        order.warnings.append(
            "לא הוזן מחירון צביעה — העבודה לא מתומחרת ולא מוערכת באפס"
        )
        return order

    by_area = order.area * prices.per_m2 if prices.per_m2 else 0.0
    by_mass = order.mass * prices.per_kg if prices.per_kg else 0.0
    price = max(by_area, by_mass, prices.minimum_charge)
    if price == prices.minimum_charge and prices.minimum_charge:
        order.priced_on = "מינימום הזמנה"
    elif by_area >= by_mass:
        order.priced_on = f"לפי שטח — ⁦{prices.per_m2:,.2f}⁩ ל-m²"
    else:
        order.priced_on = f"לפי משקל — ⁦{prices.per_kg:,.2f}⁩ לק״ג"
    order.price = round(price, 2)
    if not prices.is_confirmed:
        order.warnings.append("המחירון לא נרשם ממקור — ודא מול הצַבָּע")
    return order


__all__ = [
    "LINE_LENGTH_LIMIT",
    "FinishKind",
    "FinishOrder",
    "FinishPrices",
    "FinishSpec",
    "coating_area_per_metre",
    "order_finish",
]
