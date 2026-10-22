"""Ordering the material for a job: bars, gaskets, fittings — priced, sent, kept.

A won job turns into a phone call to the supplier: so many bars of this, so
many of that. The numbers were worked out on paper or from the cutting
optimiser's screen, the price was whatever the supplier said, and nothing
recorded that the order went out — so the price-list warning "order before
the increase" could not tell an ordered job from one nobody had touched, and
nobody could see that material came in at more than the quotation allowed.

Here the order is proposed from the job itself:

* profiles in **whole bars**, from the cutting optimiser, per stock length —
  what is actually bought, offcut included;
* gaskets, fittings and accessories from the bill of materials;
* glass is left out on purpose: it is ordered to size from the glass page.

Each line is priced from the supplier price list in force, after the shop's
own discount, with the list named; a line no list prices stays at zero and is
said to be unpriced. Once sent, the order is kept on the job, and its arrival
is recorded when it comes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.logging_setup import get_logger

_log = get_logger("projects.materials")

#: Units as an order is read at the supplier's counter.
UNIT_HEBREW = {"bar": "מוטות", "m": "מ׳", "pc": "יח׳", "m2": "מ״ר", "kg": "ק״ג", "set": "סט"}

#: The engine's names for what it adds, as a supplier's counter says them.
HEBREW_NAMES = {
    "Inner glazing gasket": "גומיית זיגוג פנימית",
    "Outer glazing gasket": "גומיית זיגוג חיצונית",
    "Centre gasket": "גומייה מרכזית",
    "Frame gasket": "גומיית משקוף",
    "Sash gasket": "גומיית כנף",
    "Sash weatherstrip": "גומיית אטימה לכנף",
    "Additional hinge for tall sash": "ציר נוסף לכנף גבוהה",
}


def hebrew_name(text: str) -> str:
    if text in HEBREW_NAMES:
        return HEBREW_NAMES[text]
    if text.startswith("Infill panel "):
        return "פאנל מילוי ⁦" + text.removeprefix("Infill panel ").replace(" mm", "") + "⁩ מ״מ"
    return text

#: What is not ordered from here: glass is ordered to size from its own page.
LEFT_OUT = ("glass",)


def build_job(job: Any) -> tuple[list[Any], list[str]]:
    """The job's openings built the way the design page builds them."""
    from ..elements.builder import ElementBuilder

    schedule = getattr(job, "schedule", None)
    if schedule is None or not getattr(schedule, "openings", None):
        return [], []
    builds, problems = [], []
    for opening in schedule.openings:
        try:
            builder = ElementBuilder.for_system(opening.system_id or schedule.system_id)
        except Exception:  # noqa: BLE001 - the same fallback as the design page
            builder = ElementBuilder()
        try:
            builds.append(builder.build(opening, sill_height=schedule.sill_height))
        except Exception as exc:  # noqa: BLE001 - one opening, said, not the whole job
            problems.append(f"{getattr(opening, 'name', '') or opening.element_id}: {exc}")
    return builds, problems


@dataclass
class ProposedLine:
    category: str
    code: str
    description: str
    quantity: float
    #: "bar", "m", "pc", "m2" …
    unit: str
    length_mm: float = 0.0
    unit_price: float = 0.0
    price_source: str = ""
    supplier: str = ""

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    @property
    def unit_hebrew(self) -> str:
        return UNIT_HEBREW.get(self.unit, self.unit)

    def describe(self) -> str:
        """One line as the person at the counter reads it."""
        quantity = f"{self.quantity:g}"
        what = f" {self.description}" if self.description and self.description != self.code else ""
        if self.unit == "bar" and self.length_mm:
            return (f"⁦{quantity}⁩ מוטות ⁦{self.code}⁩{what} — אורך "
                    f"⁦{self.length_mm / 1000:g}⁩ מ׳")
        return f"⁦{quantity}⁩ {self.unit_hebrew} ⁦{self.code}⁩{what}"


@dataclass
class Proposal:
    lines: list[ProposedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def unpriced(self) -> list[ProposedLine]:
        return [line for line in self.lines if line.unit_price <= 0]

    def suppliers(self) -> list[str]:
        """The suppliers whose lists price these lines, most lines first."""
        counts = Counter(line.supplier for line in self.lines if line.supplier)
        return [name for name, _ in counts.most_common()]

    def for_supplier(self, supplier: str) -> list[ProposedLine]:
        """What goes on one supplier's order: its own lines and the unpriced."""
        return [line for line in self.lines if line.supplier in (supplier, "")]


def _price(code: str, options: list[tuple[str, float]]) -> tuple[float, float, Any] | None:
    """Price ``code`` in the first unit some list sells it in.

    ``options`` are ``(unit, quantity in that unit)``. Returns the total, the
    quantity it was priced on, and the supplier.
    """
    from ..quoting.suppliers import find_price

    for unit, quantity in options:
        if quantity <= 0:
            continue
        found = find_price(code, quantity, unit=unit, currency="ILS")
        if found is not None:
            supplier, total = found
            return total, quantity, supplier
    return None


def _source(supplier: Any) -> str:
    return (getattr(supplier, "notes", "") or getattr(supplier, "name", "")).split(" · ")[0]


def propose(job: Any, *, mass_per_metre: Callable[[str], float | None] | None = None,
            strategy: str = "auto") -> Proposal:
    """What to order for this job, priced from the lists in force."""
    from ..elements import collect_cut_items
    from ..models.orders import Project
    from ..nesting import nest_project
    from ..quoting.bom import build_bom
    from ..quoting.pricelists import catalogue_weights

    proposal = Proposal()
    builds, problems = build_job(job)
    proposal.warnings += [f"פתח לא נבנה ולא נכלל: {problem}" for problem in problems]
    if not builds:
        proposal.warnings.append("אין בפרויקט פתחים מתוכננים — אין מה להזמין")
        return proposal
    weights = mass_per_metre or catalogue_weights()

    # -- profiles, in whole bars ------------------------------------------- #
    project = Project(name=getattr(job, "name", "") or "פרויקט",
                      items=collect_cut_items(builds))
    try:
        report = nest_project(project, strategy=strategy)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - said, and the job's other lines still come
        proposal.warnings.append(f"חישוב המוטות נכשל: {exc}")
        report = None
    if report is not None:
        for profile_id, failure in report.failures.items():
            proposal.warnings.append(f"⁦{profile_id}⁩: המוטות לא חושבו ({failure})")
        for profile_id, result in sorted(report.results.items()):
            lengths = Counter(round(layout.stock_length) for layout in result.layouts
                              if not layout.is_remnant)
            for length, bars in sorted(lengths.items()):
                metres = bars * length / 1000.0
                options = [("bar", float(bars)), ("m", metres)]
                weight = weights(profile_id)
                if weight:
                    options.append(("kg", metres * weight))
                line = ProposedLine("profile", profile_id, "", float(bars), "bar", float(length))
                priced = _price(profile_id, options)
                if priced is not None:
                    total, _quantity, supplier = priced
                    line.unit_price = round(total / bars, 4)
                    line.supplier = supplier.name
                    line.price_source = _source(supplier)
                    entry = supplier.entry(profile_id)
                    line.description = (entry.description if entry is not None else "") or ""
                proposal.lines.append(line)

    # -- everything else that is bought, not cut ---------------------------- #
    bom = build_bom(builds, project_id=getattr(job, "job_id", ""), currency="ILS")
    for bom_line in bom.sorted_lines():
        category = bom_line.category.value
        if category == "profile" or category in LEFT_OUT:
            continue
        unit = bom_line.unit.value
        description = hebrew_name(bom_line.description)
        # Gasket comes on a roll: whole metres, rounded up.
        quantity = float(bom_line.quantity)
        if category == "gasket" and unit == "m":
            import math

            quantity = float(math.ceil(quantity))
        line = ProposedLine(category, bom_line.code, description, round(quantity, 3), unit)
        priced = _price(bom_line.code, [(unit, quantity)])
        if priced is not None:
            total, quantity, supplier = priced
            line.unit_price = round(total / quantity, 4) if quantity else 0.0
            line.supplier = supplier.name
            line.price_source = _source(supplier)
        proposal.lines.append(line)
    return proposal


def order_message(order: Any, *, brand_name: str, job: Any) -> str:
    """The order as one WhatsApp message to the supplier."""
    lines = ["שלום,", "", f"הזמנת חומר מ{brand_name} — ⁦{job.job_id}⁩ {job.name}".rstrip()]
    if getattr(job, "site_address", ""):
        lines.append(f"לאתר: {job.site_address}")
    if order.needed_by:
        lines.append(f"נדרש עד: ⁦{_short(order.needed_by)}⁩")
    lines.append(f"מספר הזמנה: ⁦{order.order_id}⁩")
    lines.append("")
    for line in order.lines:
        quantity = f"{line.quantity:g}"
        what = f" {line.description}" if line.description and line.description != line.code else ""
        if line.unit == "מוטות" and line.length_mm:
            lines.append(f"• ⁦{quantity}⁩ מוטות ⁦{line.code}⁩{what} — ⁦{line.length_mm / 1000:g}⁩ מ׳")
        else:
            lines.append(f"• ⁦{quantity}⁩ {line.unit} ⁦{line.code}⁩{what}")
    if order.note:
        lines += ["", order.note]
    lines += ["", "תודה, נא לאשר זמינות, מחיר ומועד אספקה."]
    return "\n".join(lines)


def _short(text: str) -> str:
    from datetime import date

    try:
        return f"{date.fromisoformat(text[:10]):%d/%m/%Y}"
    except ValueError:
        return text


def to_order(proposal_lines: list[ProposedLine], *, supplier: str, needed_by: str = "",
             note: str = "") -> Any:
    """Proposed lines as an order kept on the job."""
    from .model import MaterialOrder, MaterialOrderLine

    return MaterialOrder(
        supplier=supplier, needed_by=needed_by, note=note,
        lines=[
            MaterialOrderLine(
                code=line.code, description=line.description, quantity=line.quantity,
                unit=line.unit_hebrew, length_mm=line.length_mm,
                unit_price=line.unit_price, price_source=line.price_source,
            )
            for line in proposal_lines
        ],
    )


__all__ = [
    "Proposal",
    "ProposedLine",
    "build_job",
    "order_message",
    "propose",
    "to_order",
]
