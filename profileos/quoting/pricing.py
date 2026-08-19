"""Costing and quotation.

Pricing runs in two stages, deliberately separated:

1. **Cost** — what the job costs the fabricator: materials at supplier rates,
   plus labour, finishing and delivery.
2. **Price** — what the customer is charged: cost plus margin, plus overheads,
   plus tax.

Keeping them apart means the margin is visible and adjustable rather than baked
into a single number, which is what makes a quote defensible when a customer
pushes back on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..core.errors import QuotingError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..elements.builder import ElementBuild
from .bom import BillOfMaterials, BomCategory, BomLine, Unit
from .suppliers import Supplier, find_price, get_supplier

_log = get_logger("quoting.pricing")


@dataclass
class LabourRates:
    """Labour model, in hours per unit of work."""

    hourly_rate: float = 45.0
    currency: str = "EUR"

    #: Hours to cut and machine one profile piece.
    hours_per_cut: float = 0.05
    #: Hours to assemble one element frame.
    hours_per_element: float = 1.2
    #: Additional hours per operable sash (hardware fitting and adjustment).
    hours_per_sash: float = 0.8
    #: Hours per square metre of glazing.
    hours_per_glass_m2: float = 0.25
    #: Hours per element for final QC and packing.
    hours_per_element_qc: float = 0.3

    def hours_for(self, builds: list[ElementBuild]) -> dict[str, float]:
        """Break the labour down by activity, so the estimate is auditable."""
        cutting = 0.0
        assembly = 0.0
        glazing = 0.0
        hardware = 0.0
        qc = 0.0

        for build in builds:
            multiplier = build.opening.quantity
            pieces = sum(cut.quantity for cut in build.cuts)
            cutting += pieces * self.hours_per_cut * multiplier
            assembly += self.hours_per_element * multiplier
            qc += self.hours_per_element_qc * multiplier
            glazing += (
                sum(panel.total_area for panel in build.glass)
                * self.hours_per_glass_m2
                * multiplier
            )
            hardware += (
                len(build.opening.operable_cells()) * self.hours_per_sash * multiplier
            )

        return {
            "cutting_machining": round(cutting, 2),
            "assembly": round(assembly, 2),
            "glazing": round(glazing, 2),
            "hardware_fitting": round(hardware, 2),
            "quality_packing": round(qc, 2),
        }


@dataclass
class PricingPolicy:
    """Commercial parameters applied on top of cost."""

    #: Gross margin as a percentage of the selling price.
    margin_pct: float = 25.0
    #: Overhead recovery as a percentage of direct cost.
    overhead_pct: float = 12.0
    #: Value added tax percentage.
    tax_pct: float = 17.0
    #: Contingency on material cost, for price volatility.
    contingency_pct: float = 3.0
    #: Delivery and installation, as a percentage of cost.
    delivery_pct: float = 4.0
    #: Fixed charges added once per quotation.
    fixed_charges: float = 0.0
    currency: str = "EUR"
    valid_days: int = 30

    def apply_margin(self, cost: float) -> float:
        """Convert cost to selling price at the configured gross margin.

        Margin is expressed on the **selling price**, not on cost, which is the
        convention in construction tendering: a 25% margin means the price is
        ``cost / 0.75``, not ``cost * 1.25``.
        """
        if self.margin_pct >= 100.0:
            raise QuotingError("Margin must be below 100% of selling price", margin=self.margin_pct)
        return cost / (1.0 - self.margin_pct / 100.0)


@dataclass
class QuoteLine:
    """One line on the customer-facing quotation."""

    description: str
    quantity: float
    unit: str
    unit_price: float
    category: str = "material"
    code: str | None = None

    @property
    def total(self) -> float:
        return self.unit_price * self.quantity


@dataclass
class Quotation:
    """A complete priced quotation."""

    quote_id: str = field(default_factory=lambda: f"Q-{uuid4().hex[:8].upper()}")
    project_name: str = ""
    customer: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    currency: str = "EUR"
    policy: PricingPolicy = field(default_factory=PricingPolicy)

    lines: list[QuoteLine] = field(default_factory=list)
    #: Cost breakdown before margin.
    material_cost: float = 0.0
    labour_cost: float = 0.0
    labour_hours: dict[str, float] = field(default_factory=dict)
    unpriced_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- derived totals ------------------------------------------------------ #
    @property
    def direct_cost(self) -> float:
        return self.material_cost + self.labour_cost

    @property
    def contingency(self) -> float:
        return self.material_cost * self.policy.contingency_pct / 100.0

    @property
    def overhead(self) -> float:
        return self.direct_cost * self.policy.overhead_pct / 100.0

    @property
    def delivery(self) -> float:
        return self.direct_cost * self.policy.delivery_pct / 100.0

    @property
    def total_cost(self) -> float:
        return (
            self.direct_cost
            + self.contingency
            + self.overhead
            + self.delivery
            + self.policy.fixed_charges
        )

    @property
    def net_price(self) -> float:
        """Selling price before tax."""
        return self.policy.apply_margin(self.total_cost)

    @property
    def margin_value(self) -> float:
        return self.net_price - self.total_cost

    @property
    def tax(self) -> float:
        return self.net_price * self.policy.tax_pct / 100.0

    @property
    def gross_price(self) -> float:
        """The figure the customer pays."""
        return self.net_price + self.tax

    @property
    def valid_until(self) -> date:
        return (self.created_at + timedelta(days=self.policy.valid_days)).date()

    def price_per_m2(self, area_m2: float) -> float | None:
        """Selling price per square metre — the industry's comparison metric."""
        if area_m2 <= 0:
            return None
        return self.net_price / area_m2

    def breakdown(self) -> list[tuple[str, float]]:
        """Ordered cost-to-price waterfall, for the quotation document."""
        return [
            ("Materials", self.material_cost),
            ("Labour", self.labour_cost),
            (f"Contingency ({self.policy.contingency_pct:g}%)", self.contingency),
            (f"Overhead ({self.policy.overhead_pct:g}%)", self.overhead),
            (f"Delivery ({self.policy.delivery_pct:g}%)", self.delivery),
            ("Fixed charges", self.policy.fixed_charges),
            ("Total cost", self.total_cost),
            (f"Margin ({self.policy.margin_pct:g}%)", self.margin_value),
            ("Net price", self.net_price),
            (f"Tax ({self.policy.tax_pct:g}%)", self.tax),
            ("Gross price", self.gross_price),
        ]

    def summary(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "project": self.project_name,
            "customer": self.customer,
            "currency": self.currency,
            "material_cost": round(self.material_cost, 2),
            "labour_cost": round(self.labour_cost, 2),
            "total_cost": round(self.total_cost, 2),
            "net_price": round(self.net_price, 2),
            "tax": round(self.tax, 2),
            "gross_price": round(self.gross_price, 2),
            "margin_pct": self.policy.margin_pct,
            "unpriced_codes": len(self.unpriced_codes),
            "valid_until": self.valid_until.isoformat(),
        }


def price_bom(
    bom: BillOfMaterials,
    *,
    default_supplier: Supplier | None = None,
    fallback_rates: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    """Attach unit prices to every BOM line.

    Returns ``(total_material_cost, unpriced_codes)``. A code nobody lists is
    reported rather than silently priced at zero — a quote with an invisible
    zero in it is how a fabricator loses money on a job.
    """
    fallback_rates = fallback_rates or {}
    total = 0.0
    unpriced: list[str] = []

    for line in bom.lines:
        supplier_price: float | None = None
        supplier_id: str | None = None

        if line.supplier_id:
            supplier = get_supplier(line.supplier_id)
            if supplier is not None:
                supplier_price = supplier.net_price(line.code, line.quantity)
                supplier_id = supplier.id

        if supplier_price is None:
            found = find_price(line.code, line.quantity, category=line.category.value)
            if found is not None:
                supplier_obj, supplier_price = found
                supplier_id = supplier_obj.id

        if supplier_price is None and default_supplier is not None:
            supplier_price = default_supplier.net_price(line.code, line.quantity)
            if supplier_price is not None:
                supplier_id = default_supplier.id

        if supplier_price is None and line.code in fallback_rates:
            supplier_price = fallback_rates[line.code] * line.quantity
            supplier_id = "fallback"

        if supplier_price is None:
            unpriced.append(line.code)
            continue

        line.unit_price = supplier_price / line.quantity if line.quantity else 0.0
        line.supplier_id = supplier_id
        line.currency = bom.currency
        total += supplier_price

    return total, unpriced


def build_quotation(
    builds: list[ElementBuild],
    bom: BillOfMaterials,
    *,
    project_name: str = "",
    customer: str | None = None,
    policy: PricingPolicy | None = None,
    labour: LabourRates | None = None,
    default_supplier: Supplier | None = None,
    fallback_rates: dict[str, float] | None = None,
) -> Quotation:
    """Price a bill of materials and produce a customer quotation."""
    policy = policy or PricingPolicy()
    labour = labour or LabourRates()

    quote = Quotation(
        project_name=project_name,
        customer=customer,
        currency=policy.currency,
        policy=policy,
    )

    material_cost, unpriced = price_bom(
        bom, default_supplier=default_supplier, fallback_rates=fallback_rates
    )
    quote.material_cost = material_cost
    quote.unpriced_codes = unpriced
    if unpriced:
        quote.warnings.append(
            f"{len(unpriced)} material code(s) have no price and are excluded "
            f"from the cost: {', '.join(sorted(set(unpriced))[:8])}"
        )

    hours = labour.hours_for(builds)
    quote.labour_hours = hours
    quote.labour_cost = sum(hours.values()) * labour.hourly_rate

    # Customer-facing lines: one per element, priced pro rata by area.
    total_area = sum(b.opening.area * b.opening.quantity for b in builds)
    if total_area > 0:
        rate = quote.net_price / total_area
        for build in builds:
            opening = build.opening
            quote.lines.append(
                QuoteLine(
                    description=(
                        f"{opening.name} - {opening.kind.value.replace('_', ' ')} "
                        f"{opening.width:.0f} x {opening.height:.0f} mm"
                    ),
                    quantity=float(opening.quantity),
                    unit="pc",
                    unit_price=round(rate * opening.area, 2),
                    category="element",
                    code=opening.element_id,
                )
            )

    from ..branding import active_brand

    brand = active_brand()
    quote.metadata["letterhead"] = brand.letterhead()
    quote.metadata["issued_by"] = brand.document_name
    quote.metadata["total_area_m2"] = round(total_area, 3)
    quote.metadata["price_per_m2"] = (
        round(quote.price_per_m2(total_area) or 0.0, 2) if total_area else None
    )
    for warning in bom.warnings:
        quote.warnings.append(warning)

    publish(
        Topic.QUOTE_UPDATED,
        source="quoting",
        quote_id=quote.quote_id,
        net_price=quote.net_price,
        currency=quote.currency,
    )
    _log.info(
        "Quoted %s: material %.2f + labour %.2f -> net %.2f %s",
        project_name or quote.quote_id,
        quote.material_cost,
        quote.labour_cost,
        quote.net_price,
        quote.currency,
    )
    return quote


__all__ = [
    "LabourRates",
    "PricingPolicy",
    "QuoteLine",
    "Quotation",
    "price_bom",
    "build_quotation",
]
