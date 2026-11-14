"""Sales: order, deliver, invoice, get paid.

VAT
---
Israeli VAT is a single standard rate charged on the net, and the rate has
moved three times in fifteen years — so it is a parameter with a *date*, not a
constant. An invoice raised in December must keep the rate that applied in
December no matter when the books are read, which is why the rate is resolved
from the invoice date rather than from today.

Rounding
--------
VAT is computed on the invoice total, not per line and summed. Rounding each
line and adding gives a figure that differs from the tax authority's by an
agora or two on a long invoice, and an invoice that disagrees with the customer's
own arithmetic gets queried whichever way it errs.

Revenue recognition
-------------------
Delivering goods and invoicing them are separate events and are posted
separately: delivery moves the value out of stock into cost of sales, the
invoice raises the debtor and the revenue. A shop that only posts on invoice
has stock on its books it delivered a month ago.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Iterable

from ..core.errors import ProfileOSError
from .ledger import JournalEntry, Ledger, Posting, money


class SalesError(ProfileOSError):
    """A sales document cannot be produced."""


#: Israeli standard VAT, by the date each rate took effect. Historic rates are
#: kept because a credit note against an old invoice carries the old rate.
ISRAELI_VAT_HISTORY: tuple[tuple[date, float], ...] = (
    (date(2013, 6, 2), 0.18),
    (date(2015, 10, 1), 0.17),
    (date(2025, 1, 1), 0.18),
)


def vat_rate(on: date, history: Iterable[tuple[date, float]] = ISRAELI_VAT_HISTORY) -> float:
    """The standard rate in force on ``on``."""
    rate = None
    for effective, value in sorted(history):
        if on >= effective:
            rate = value
    if rate is None:
        raise SalesError(
            "No VAT rate is recorded for that date; add it rather than "
            "guessing, because an invoice at the wrong rate has to be credited "
            "and reissued",
            on=on.isoformat(),
        )
    return rate


class DocumentState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    DELIVERED = "delivered"
    INVOICED = "invoiced"
    PAID = "paid"
    CANCELLED = "cancelled"


@dataclass
class SalesLine:
    description: str
    quantity: float
    unit_price: float
    """Price of one unit, in minor currency units."""
    unit: str = "unit"
    element_id: str | None = None
    #: Discount as a fraction, applied to this line only.
    discount: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.discount < 1.0:
            raise SalesError(
                "A line discount is a fraction below one",
                description=self.description, discount=self.discount,
            )

    @property
    def net(self) -> int:
        return money(self.quantity * self.unit_price * (1 - self.discount), minor_units=1)


@dataclass
class SalesOrder:
    """A confirmed job: what the customer bought and when they want it."""

    order_id: str
    customer: str
    raised: date
    lines: list[SalesLine] = field(default_factory=list)
    promised: date | None = None
    project_id: str | None = None
    state: DocumentState = DocumentState.DRAFT
    reference: str | None = None
    currency: str = "ILS"

    def __post_init__(self) -> None:
        if not self.lines:
            raise SalesError("A sales order needs at least one line", order=self.order_id)

    @property
    def net(self) -> int:
        return sum(line.net for line in self.lines)


@dataclass
class DeliveryNote:
    """What left the yard."""

    note_id: str
    order_id: str
    on: date
    lines: list[SalesLine] = field(default_factory=list)
    #: What the delivered goods cost, in minor units — used to post cost of sales.
    cost: int = 0
    carrier: str | None = None


@dataclass
class SalesInvoice:
    """What the customer is being charged."""

    invoice_id: str
    customer: str
    on: date
    lines: list[SalesLine] = field(default_factory=list)
    order_id: str | None = None
    #: ``None`` resolves the statutory rate for the invoice date.
    rate: float | None = None
    due_days: int = 30
    currency: str = "ILS"
    note: str | None = None
    #: Set on a credit note; the invoice it reverses.
    credits: str | None = None

    def __post_init__(self) -> None:
        if not self.lines:
            raise SalesError("An invoice needs at least one line", invoice=self.invoice_id)

    @property
    def vat_rate(self) -> float:
        return self.rate if self.rate is not None else vat_rate(self.on)

    @property
    def net(self) -> int:
        return sum(line.net for line in self.lines)

    @property
    def vat(self) -> int:
        # On the total, not line by line: see the module docstring.
        return money(self.net * self.vat_rate, minor_units=1)

    @property
    def gross(self) -> int:
        return self.net + self.vat

    @property
    def due(self) -> date:
        from datetime import timedelta

        return self.on + timedelta(days=self.due_days)

    def as_rows(self) -> list[dict[str, object]]:
        return [
            {
                "description": line.description,
                "quantity": round(line.quantity, 3),
                "unit": line.unit,
                "unit_price": line.unit_price,
                "discount": line.discount,
                "net": line.net,
            }
            for line in self.lines
        ]


def credit_note(
    invoice: SalesInvoice, note_id: str, on: date, *, lines: list[SalesLine] | None = None
) -> SalesInvoice:
    """Reverse an invoice, in whole or in part.

    The original rate is carried across. A credit note raised after a rate
    change must undo the tax that was actually charged, not the tax that would
    be charged today.
    """
    reversed_lines = [
        SalesLine(
            description=line.description,
            quantity=-line.quantity,
            unit_price=line.unit_price,
            unit=line.unit,
            element_id=line.element_id,
            discount=line.discount,
        )
        for line in (lines if lines is not None else invoice.lines)
    ]
    return SalesInvoice(
        invoice_id=note_id,
        customer=invoice.customer,
        on=on,
        lines=reversed_lines,
        order_id=invoice.order_id,
        rate=invoice.vat_rate,
        currency=invoice.currency,
        note=f"Credit note against {invoice.invoice_id}",
        credits=invoice.invoice_id,
    )


def post_delivery(
    note: DeliveryNote,
    ledger: Ledger,
    *,
    cost_of_sales: str = "5100",
    work_in_progress: str = "1500",
) -> JournalEntry | None:
    """Move the delivered value out of work in progress into cost of sales."""
    if note.cost == 0:
        return None
    return ledger.post_simple(
        f"DN-{note.note_id}", note.on,
        f"Delivery {note.note_id} — cost of sales",
        cost_of_sales, work_in_progress, note.cost,
        source="delivery", reference=note.order_id,
    )


def post_sales_invoice(
    invoice: SalesInvoice,
    ledger: Ledger,
    *,
    debtors: str = "1200",
    sales: str = "4100",
    output_vat: str = "2400",
) -> JournalEntry:
    """Raise the debtor, the revenue and the VAT the shop now owes."""
    net, vat, gross = invoice.net, invoice.vat, invoice.gross
    if gross == 0:
        raise SalesError(
            "An invoice for nothing is not a document", invoice=invoice.invoice_id
        )
    postings = [Posting(debtors, gross, invoice.customer), Posting(sales, -net)]
    if vat:
        postings.append(Posting(output_vat, -vat, f"VAT at {invoice.vat_rate:.0%}"))
    return ledger.post(
        JournalEntry(
            entry_id=f"SI-{invoice.invoice_id}",
            date=invoice.on,
            narrative=(
                f"Credit note {invoice.invoice_id}"
                if invoice.credits
                else f"Invoice {invoice.invoice_id} to {invoice.customer}"
            ),
            postings=tuple(postings),
            source="sales_invoice",
            reference=invoice.order_id,
        )
    )


def receive_payment(
    ledger: Ledger,
    customer: str,
    amount: int,
    on: date,
    *,
    entry_id: str,
    bank: str = "1100",
    debtors: str = "1200",
) -> JournalEntry:
    return ledger.post_simple(
        entry_id, on, f"Payment from {customer}", bank, debtors, amount,
        source="receipt", reference=customer,
    )


@dataclass
class AgedRow:
    customer: str
    current: int = 0
    days_30: int = 0
    days_60: int = 0
    days_90: int = 0
    older: int = 0

    @property
    def total(self) -> int:
        return self.current + self.days_30 + self.days_60 + self.days_90 + self.older


def aged_debtors(
    invoices: Iterable[SalesInvoice],
    payments: dict[str, int],
    as_at: date,
) -> list[AgedRow]:
    """Who owes what, and how long it has been outstanding.

    Payments are applied per customer rather than per invoice, oldest first,
    which is what actually happens when a customer pays a round sum on account.
    """
    by_customer: dict[str, list[SalesInvoice]] = {}
    for invoice in invoices:
        by_customer.setdefault(invoice.customer, []).append(invoice)

    rows: list[AgedRow] = []
    for customer, customer_invoices in sorted(by_customer.items()):
        remaining = payments.get(customer, 0)
        row = AgedRow(customer=customer)
        for invoice in sorted(customer_invoices, key=lambda i: i.on):
            outstanding = invoice.gross
            if remaining > 0 and outstanding > 0:
                applied = min(remaining, outstanding)
                outstanding -= applied
                remaining -= applied
            if outstanding == 0:
                continue
            age = (as_at - invoice.due).days
            if age <= 0:
                row.current += outstanding
            elif age <= 30:
                row.days_30 += outstanding
            elif age <= 60:
                row.days_60 += outstanding
            elif age <= 90:
                row.days_90 += outstanding
            else:
                row.older += outstanding
        if row.total:
            rows.append(row)
    return rows


def vat_return(
    ledger: Ledger,
    start: date,
    end: date,
    *,
    output_vat: str = "2400",
    input_vat: str = "1400",
) -> dict[str, object]:
    """The period's VAT position, straight from the accounts."""
    balances = ledger.balances(start, end)
    output = balances[output_vat].credits - balances[output_vat].debits
    recoverable = balances[input_vat].debits - balances[input_vat].credits
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "output_vat": output,
        "input_vat": recoverable,
        "payable": output - recoverable,
    }


__all__ = [
    "SalesError",
    "ISRAELI_VAT_HISTORY",
    "vat_rate",
    "DocumentState",
    "SalesLine",
    "SalesOrder",
    "DeliveryNote",
    "SalesInvoice",
    "credit_note",
    "post_delivery",
    "post_sales_invoice",
    "receive_payment",
    "AgedRow",
    "aged_debtors",
    "vat_return",
]
