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
UNIT_HEBREW = {"bar": "מוטות", "m": "מ׳", "pc": "יח׳", "m2": "מ״ר", "kg": "ק״ג", "set": "סט",
               "roll": "גלילים"}

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
    #: Where a bar's length came from: the supplier's list, or empty when the
    #: shop's general setting decided it.
    length_source: str = ""
    #: Priced per kilogram through the catalogue's weight per metre.
    by_weight: bool = False

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
        if self.unit == "roll" and self.length_mm:
            return (f"⁦{quantity}⁩ גלילים ⁦{self.code}⁩{what} — גליל של "
                    f"⁦{self.length_mm / 1000:g}⁩ מ׳")
        return f"⁦{quantity}⁩ {self.unit_hebrew} ⁦{self.code}⁩{what}"


@dataclass
class Proposal:
    lines: list[ProposedLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Offcuts from the rack the bars count on: (remnant_id, profile, length).
    from_rack: list[tuple[str, str, float]] = field(default_factory=list)

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


def _price(code: str, options: list[tuple[str, float]],
           category: str | None = None) -> tuple[float, float, Any, str] | None:
    """Price ``code`` in the first unit some list sells it in.

    ``options`` are ``(unit, quantity in that unit)``. Returns the total, the
    quantity it was priced on, and the supplier.
    """
    from ..quoting.suppliers import find_price

    for unit, quantity in options:
        if quantity <= 0:
            continue
        found = find_price(code, quantity, unit=unit, currency="ILS", category=category)
        if found is not None:
            supplier, total = found
            return total, quantity, supplier, unit
    return None


def _source(supplier: Any) -> str:
    return (getattr(supplier, "notes", "") or getattr(supplier, "name", "")).split(" · ")[0]


def _options(line: "ProposedLine",
             mass_per_metre: Callable[[str], float | None]) -> list[tuple[str, float]]:
    """The units a line can be priced in, the quotation's own way first."""
    if line.unit in ("bar", "roll") and line.length_mm:
        metres = line.quantity * line.length_mm / 1000.0
        # Metres first, as the quotation prices a profile: a list that does
        # not say its unit is read the same way, never as a price per bar six
        # times too low. A price per bar (or roll) of a known length is
        # already a price per metre of it. Per kilogram and per bar only when
        # the list says so.
        options = [("m", metres)]
        weight = mass_per_metre(line.code) if line.unit == "bar" else None
        if weight:
            options.append(("kg", metres * weight))
        options.append((line.unit, line.quantity))
        return options
    return [(line.unit, line.quantity)]


def price_line(line: "ProposedLine", *,
               mass_per_metre: Callable[[str], float | None] = lambda _code: None,
               supplier: Any = None) -> "ProposedLine":
    """Price one line at its quantity — in place.

    From ``supplier`` when given (the one whose bar length the line was cut
    to: a length from one supplier and a price from another is no price),
    else from the cheapest list in force. Used again when a quantity is
    changed, so a price break or a minimum applies to what is ordered.
    """
    line.unit_price, line.price_source = 0.0, ""
    if supplier is None:
        line.supplier = ""
    if line.quantity <= 0:
        return line
    options = _options(line, mass_per_metre)
    found = None
    if supplier is not None:
        for unit, quantity in options:
            if quantity <= 0:
                continue
            total = supplier.net_price(line.code, quantity, unit, "ILS")
            if total is not None:
                found = (total, unit, supplier)
                break
    else:
        priced = _price(line.code, options, "profile" if line.category == "profile" else line.category)
        if priced is not None:
            total, _quantity, chosen, unit = priced
            found = (total, unit, chosen)
    if found is None:
        return line
    total, unit, chosen = found
    line.unit_price = round(total / line.quantity, 4)
    line.supplier = chosen.name
    line.price_source = _source(chosen)
    entry = chosen.entry(line.code)
    line.by_weight = unit == "kg" or bool(entry is not None and entry.metadata.get("price_per_kg"))
    if entry is not None and not line.description:
        line.description = entry.description or ""
    return line


def _supplier_for(code: str, category: str,
                  mass_per_metre: Callable[[str], float | None]) -> Any:
    """The list a code is bought from: the cheapest that sells it at all."""
    options = [("m", 1.0)]
    weight = mass_per_metre(code)
    if weight:
        options.append(("kg", weight))
    options += [("bar", 1.0), ("pc", 1.0), ("roll", 1.0)]
    priced = _price(code, options, category)
    return priced[2] if priced is not None else None


def _add_profiles(proposal: "Proposal", items: list[Any], name: str, *,
                  weights: Any, defaults: Any, strategy: str, rack: Any = None) -> None:
    """Profiles in whole bars: one supplier each, its bar length, its price."""
    from ..core.config import get_settings
    from ..models.orders import Project, StockBar
    from ..nesting import nest_project

    if not items:
        return
    settings_lengths = list((defaults or get_settings().nesting).stock_lengths_mm or [6000.0])
    general = float(settings_lengths[0])
    chosen: dict[str, Any] = {}
    length_of: dict[str, float] = {}
    length_by: dict[str, str] = {}
    by_setting: list[str] = []
    for pid in sorted({item.profile_id for item in items}):
        supplier = _supplier_for(pid, "profile", weights)
        chosen[pid] = supplier
        entry = supplier.entry(pid) if supplier is not None else None
        length = float(entry.metadata.get("bar_length_mm") or 0.0) if entry is not None else 0.0
        if length:
            length_of[pid] = length
            length_by[pid] = str(entry.metadata.get("length_said_by") or supplier.name)
        else:
            # One length, never a mix of the shop's lengths: a supplier sells
            # a profile in one bar.
            length_of[pid] = general
            by_setting.append(pid)
    project = Project(
        name=name, items=items,
        stock_bars=[StockBar(profile_id=pid, length=length) for pid, length in length_of.items()],
    )
    try:
        report = nest_project(project, strategy=strategy, defaults=defaults,  # type: ignore[arg-type]
                              inventory=rack)
    except Exception as exc:  # noqa: BLE001 - said, and the job's other lines still come
        proposal.warnings.append(f"חישוב המוטות נכשל: {exc}")
        report = None
    if by_setting and report is not None:
        proposal.warnings.append(
            f"אורך המוט ⁦{general / 1000:g}⁩ מ׳ לפי הגדרת המפעל, כי המחירון לא אומר אותו: "
            + ", ".join(f"⁦{pid}⁩" for pid in by_setting[:6])
            + " — ודאו מול הספק, או הזינו אורך מוט במחירון."
        )
    if report is not None:
        for profile_id, failure in report.failures.items():
            proposal.warnings.append(f"⁦{profile_id}⁩: המוטות לא חושבו ({failure})")
        for profile_id, result in sorted(report.results.items()):
            lengths_used = Counter(round(layout.stock_length) for layout in result.layouts
                                   if not layout.is_remnant)
            for length, bars in sorted(lengths_used.items()):
                line = ProposedLine("profile", profile_id, "", float(bars), "bar", float(length))
                line.length_source = length_by.get(profile_id, "")
                price_line(line, mass_per_metre=weights, supplier=chosen.get(profile_id))
                proposal.lines.append(line)
                _check_bar_price(line, chosen.get(profile_id), proposal)
        if rack is None:
            proposal.warnings.append(
                "שאריות שכבר במדף לא נלקחו בחשבון — אם יש, הורידו מוטות מהכמות."
            )
        else:
            from ..nesting.rack import used_from_rack

            used = used_from_rack(report)
            proposal.from_rack += used
            if used:
                proposal.warnings.append(
                    f"נלקחו ⁦{len(used)}⁩ שאריות מהמדף, ולכן מזמינים פחות מוטות: "
                    + ", ".join(f"⁦{code}⁩ ⁦{length / 1000:.2f}⁩ מ׳"
                                for _rid, code, length in used[:6])
                    + (" ועוד" if len(used) > 6 else "")
                    + ". עם רישום ההזמנה הן נשמרות בצד לפרויקט הזה."
                )
    if any(getattr(line, "by_weight", False) for line in proposal.lines):
        proposal.warnings.append(
            "חלק מהפרופילים מתומחרים לפי ק״ג, במשקל למטר שבקטלוג הספק. המשקל "
            "בחשבונית (צבוע או שקול) עשוי להיות שונה מעט."
        )



def propose(job: Any, *, mass_per_metre: Callable[[str], float | None] | None = None,
            strategy: str = "auto", defaults: Any = None, rack: Any = None) -> Proposal:
    """What to order for this job, priced from the lists in force."""
    import math

    from ..elements import collect_cut_items
    from ..quoting.bom import build_bom
    from ..quoting.pricelists import catalogue_weights

    proposal = Proposal()
    builds, problems = build_job(job)
    proposal.warnings += [f"פתח לא נבנה ולא נכלל: {problem}" for problem in problems]
    if not builds:
        proposal.warnings.append("אין בפרויקט פתחים מתוכננים — אין מה להזמין")
        return proposal
    weights = mass_per_metre or catalogue_weights()

    _add_profiles(proposal, collect_cut_items(builds), getattr(job, "name", "") or "פרויקט",
                  weights=weights, defaults=defaults, strategy=strategy, rack=rack)

    # -- everything else that is bought, not cut ---------------------------- #
    bom = build_bom(builds, project_id=getattr(job, "job_id", ""), currency="ILS")
    # What the bill could not size (a shutter, an accessory) is said, not lost.
    proposal.warnings += [w for w in bom.warnings if "אורכים נטו" not in w]
    for bom_line in bom.sorted_lines():
        category = bom_line.category.value
        if category == "profile" or category in LEFT_OUT:
            continue
        unit = bom_line.unit.value
        description = hebrew_name(bom_line.description)
        quantity = float(bom_line.quantity)
        supplier = _supplier_for(bom_line.code, category, weights)
        entry = supplier.entry(bom_line.code) if supplier is not None else None
        roll = float(entry.metadata.get("roll_length_m") or 0.0) if entry is not None else 0.0
        if unit == "m" and roll:
            # Sold by the roll: whole rolls of the supplier's length.
            line = ProposedLine(category, bom_line.code, description,
                                float(math.ceil(quantity / roll)), "roll", roll * 1000.0)
        else:
            if category == "gasket" and unit == "m":
                quantity = float(math.ceil(quantity))  # by the metre: whole metres
            line = ProposedLine(category, bom_line.code, description, round(quantity, 3), unit)
        price_line(line, supplier=supplier)
        proposal.lines.append(line)
    return proposal


@dataclass
class Combined:
    """One supplier order for several jobs, cut together.

    Each job cut on its own leaves a part-used last bar for every profile in
    it; cut together, those ends become the next job's pieces. ``lines`` is
    what is bought; ``shares`` is each job's part of it, so each job's own
    cost and "material ordered" stay right; ``saved`` is what cutting
    together saves against ordering each job alone.
    """

    lines: list[ProposedLine] = field(default_factory=list)
    shares: dict[str, list[ProposedLine]] = field(default_factory=dict)
    #: profile id -> bars saved against separate orders
    saved_bars: dict[str, float] = field(default_factory=dict)
    saved_money: float = 0.0
    warnings: list[str] = field(default_factory=list)
    from_rack: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    def describe_saving(self) -> str:
        bars = sum(self.saved_bars.values())
        if bars <= 0:
            return "חיתוך משותף לא חוסך מוטות בפרויקטים האלה — אפשר להזמין בנפרד"
        text = f"חיתוך משותף חוסך ⁦{bars:g}⁩ מוטות מול הזמנה נפרדת לכל פרויקט"
        if self.saved_money > 0:
            text += f" — כ־⁦{self.saved_money:,.0f} ₪⁩ לפני מע״מ"
        return text


def propose_combined(jobs: list[Any], *, defaults: Any = None, strategy: str = "auto",
                     mass_per_metre: Callable[[str], float | None] | None = None,
                     rack: Any = None) -> Combined:
    """What to order for these jobs together, and each one's part of it."""
    from collections import defaultdict

    from ..elements import collect_cut_items
    from ..quoting.pricelists import catalogue_weights

    weights = mass_per_metre or catalogue_weights()
    combined = Combined()
    separate: dict[str, Proposal] = {}
    items: list[Any] = []
    cut_length: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for job in jobs:
        alone = propose(job, mass_per_metre=weights, strategy=strategy, defaults=defaults)
        separate[job.job_id] = alone
        combined.warnings += [f"⁦{job.job_id}⁩: {w}" for w in alone.warnings
                              if "שאריות שכבר במדף" not in w]
        builds, _problems = build_job(job)
        for item in collect_cut_items(builds):
            items.append(item)
            cut_length[item.profile_id][job.job_id] += item.length * item.quantity

    together = Proposal()
    _add_profiles(together, items, "הזמנה משותפת", weights=weights, defaults=defaults,
                  strategy=strategy, rack=rack)
    combined.from_rack = list(together.from_rack)
    combined.warnings += [w for w in together.warnings if w not in combined.warnings]
    combined.lines += together.lines

    # What each job would have bought alone, per profile and bar length.
    alone_bars: dict[tuple[str, float], float] = defaultdict(float)
    for proposal in separate.values():
        for line in proposal.lines:
            if line.category == "profile":
                alone_bars[(line.code, line.length_mm)] += line.quantity
    # The saving from cutting together is measured without the rack: the
    # offcuts would have served the jobs ordered apart just as well.
    compare = together
    if together.from_rack:
        compare = Proposal()
        _add_profiles(compare, items, "הזמנה משותפת", weights=weights, defaults=defaults,
                      strategy=strategy)
    together_bars: dict[tuple[str, float], float] = defaultdict(float)
    price_of: dict[tuple[str, float], float] = {}
    for line in compare.lines:
        if line.category == "profile":
            together_bars[(line.code, line.length_mm)] += line.quantity
            price_of[(line.code, line.length_mm)] = line.unit_price
    for proposal in separate.values():
        for line in proposal.lines:
            if line.category == "profile":
                price_of.setdefault((line.code, line.length_mm), line.unit_price)
    # Net, bar length by bar length — one cut together that needs a bar
    # more (or a different length) counts against the saving.
    for key in set(alone_bars) | set(together_bars):
        saved = alone_bars.get(key, 0.0) - together_bars.get(key, 0.0)
        if saved:
            combined.saved_bars[key[0]] = combined.saved_bars.get(key[0], 0.0) + saved
            combined.saved_money += saved * price_of.get(key, 0.0)
    combined.saved_money = round(combined.saved_money, 2)

    # Everything bought, not cut: the jobs' lines added up.
    bought: dict[tuple[str, str, float], ProposedLine] = {}
    for proposal in separate.values():
        for line in proposal.lines:
            if line.category == "profile":
                continue
            key = (line.code, line.unit, line.length_mm)
            if key in bought:
                bought[key].quantity = round(bought[key].quantity + line.quantity, 3)
            else:
                bought[key] = ProposedLine(**{**line.__dict__})
    combined.lines += list(bought.values())

    # Each job's part: its own bought lines, and the bars in proportion to
    # the length it cuts from them.
    for job in jobs:
        share = [ProposedLine(**{**line.__dict__}) for line in separate[job.job_id].lines
                 if line.category != "profile"]
        for line in together.lines:
            lengths = cut_length.get(line.code, {})
            total = sum(lengths.values())
            part = lengths.get(job.job_id, 0.0)
            if total <= 0 or part <= 0:
                continue
            piece = ProposedLine(**{**line.__dict__})
            piece.quantity = round(line.quantity * part / total, 2)
            share.append(piece)
        combined.shares[job.job_id] = share
    return combined


def combined_message(lines: list[ProposedLine], *, brand_name: str, jobs: list[Any],
                     batch: str, needed_by: str = "", note: str = "") -> str:
    """One WhatsApp message for the whole combined order."""
    text = ["שלום,", "", f"הזמנת חומר מ{brand_name} — הזמנה משותפת ⁦{batch}⁩",
            "לפרויקטים: " + ", ".join(f"⁦{job.job_id}⁩ {job.name}" for job in jobs)]
    if needed_by:
        text.append(f"נדרש עד: ⁦{_short(needed_by)}⁩")
    text.append("")
    text += [f"• {line.describe()}" for line in lines]
    if note:
        text += ["", note]
    text += ["", "תודה, נא לאשר זמינות, מחיר ומועד אספקה."]
    return "\n".join(text)


def _check_bar_price(line: "ProposedLine", supplier: Any, proposal: "Proposal") -> None:
    """A price per bar with no length on the list is said to be unverified."""
    if supplier is None or line.unit_price <= 0:
        return
    entry = supplier.entry(line.code)
    if entry is not None and entry.unit == "bar" and not entry.metadata.get("bar_length_mm"):
        proposal.warnings.append(
            f"⁦{line.code}⁩: המחיר במחירון הוא למוט, בלי אורך — ודאו שהוא למוט של "
            f"⁦{line.length_mm / 1000:g}⁩ מ׳, או הזינו אורך מוט במחירון."
        )


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
        elif line.unit == "גלילים" and line.length_mm:
            lines.append(f"• ⁦{quantity}⁩ גלילים ⁦{line.code}⁩{what} — גליל של "
                         f"⁦{line.length_mm / 1000:g}⁩ מ׳")
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
    "Combined",
    "Proposal",
    "combined_message",
    "propose_combined",
    "ProposedLine",
    "build_job",
    "order_message",
    "price_line",
    "propose",
    "to_order",
]
