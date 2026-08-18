"""Supplier catalogues and price lists.

A price list maps a material code to a rate, with optional quantity breaks and
a validity window. Catalogues are hot-reloadable data plugins
(``kind: "price_list"``), so updating prices is dropping a file in a directory
rather than a software release — the mechanism that removes the recurring
catalogue-update fee that legacy systems in this market charge for.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.hotreload import DataSchema
from ..core.registry import SUPPLIERS


class PriceBreak(BaseModel):
    """A quantity break: this rate applies from ``min_quantity`` upwards."""

    model_config = ConfigDict(extra="forbid")

    min_quantity: float = Field(ge=0)
    price: float = Field(ge=0)


class PriceEntry(BaseModel):
    """The price of one purchasable item."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str | None = None
    price: float = Field(ge=0, description="Base unit price")
    unit: str = "pc"
    #: Quantity breaks, applied in descending order of ``min_quantity``.
    breaks: list[PriceBreak] = Field(default_factory=list)
    #: Minimum billable quantity (e.g. glass is often billed to 0.5 m^2 minimum).
    minimum_quantity: float = Field(default=0.0, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0)
    #: Supplier's own article number, printed on the purchase order.
    article_number: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("code")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip()

    def price_for(self, quantity: float) -> float:
        """Unit price at the given quantity, honouring the breaks."""
        applicable = [b for b in self.breaks if quantity >= b.min_quantity]
        if not applicable:
            return self.price
        return min(applicable, key=lambda b: -b.min_quantity).price

    def billable_quantity(self, quantity: float) -> float:
        return max(quantity, self.minimum_quantity)

    def total_for(self, quantity: float) -> float:
        billable = self.billable_quantity(quantity)
        return self.price_for(billable) * billable


class Supplier(BaseModel):
    """A supplier and its price list."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str = "1.0"
    kind: str = "price_list"
    currency: str = "EUR"
    country: str | None = None
    contact: str | None = None

    valid_from: date | None = None
    valid_to: date | None = None
    #: Percentage discount applied to every line from this supplier.
    discount_pct: float = Field(default=0.0, ge=0, le=100)
    #: Surcharge percentage (metal surcharge, energy surcharge...).
    surcharge_pct: float = Field(default=0.0, ge=0)
    minimum_order_value: float = Field(default=0.0, ge=0)

    entries: list[PriceEntry] = Field(default_factory=list)
    #: Categories this supplier serves, for automatic assignment.
    categories: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _unique_codes(self) -> "Supplier":
        codes = [entry.code for entry in self.entries]
        if len(codes) != len(set(codes)):
            raise ValueError(f"duplicate price codes in supplier {self.id!r}")
        return self

    @property
    def is_current(self) -> bool:
        """True when today falls inside the price list's validity window."""
        today = datetime.now(timezone.utc).date()
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        return True

    def entry(self, code: str) -> PriceEntry | None:
        return next((e for e in self.entries if e.code == code), None)

    def net_price(self, code: str, quantity: float) -> float | None:
        """Total price after discount and surcharge, or ``None`` if not listed."""
        entry = self.entry(code)
        if entry is None:
            return None
        gross = entry.total_for(quantity)
        after_discount = gross * (1.0 - self.discount_pct / 100.0)
        return after_discount * (1.0 + self.surcharge_pct / 100.0)

    def serves(self, category: str) -> bool:
        return not self.categories or category in self.categories


def get_supplier(supplier_id: str) -> Supplier | None:
    """Look up a supplier in the hot-reloadable registry."""
    entry = SUPPLIERS.get_or_none(supplier_id)
    return entry if isinstance(entry, Supplier) else None


def all_suppliers() -> list[Supplier]:
    return [item for _, item in SUPPLIERS.items() if isinstance(item, Supplier)]


def register_supplier(supplier: Supplier) -> None:
    SUPPLIERS.add(supplier.id, supplier, version=supplier.version, source="api")


def find_price(
    code: str, quantity: float, *, category: str | None = None
) -> tuple[Supplier, float] | None:
    """Cheapest current supplier for ``code``, or ``None`` if nobody lists it."""
    best: tuple[Supplier, float] | None = None
    for supplier in all_suppliers():
        if not supplier.is_current:
            continue
        if category is not None and not supplier.serves(category):
            continue
        price = supplier.net_price(code, quantity)
        if price is None:
            continue
        if best is None or price < best[1]:
            best = (supplier, price)
    return best


def _validate_supplier(document: dict[str, Any]) -> Supplier:
    return Supplier.model_validate(document)


#: Registers ``kind: "price_list"`` documents as hot-reloadable plugins.
PRICE_LIST_SCHEMA = DataSchema(
    kind="price_list",
    model=_validate_supplier,
    registry=SUPPLIERS,
    key_field="id",
)


__all__ = [
    "PriceBreak",
    "PriceEntry",
    "Supplier",
    "get_supplier",
    "all_suppliers",
    "register_supplier",
    "find_price",
    "PRICE_LIST_SCHEMA",
]
