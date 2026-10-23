"""Purchasing: what to order, what arrived, and whether to pay for it.

The requirements run
--------------------
What a job needs is not what a job must buy. Net requirement is gross
requirement less what is on the rack and uncommitted, less what is already on
order. Ordering the gross is how a shop ends up with four hundred metres of a
profile it will not touch again this year.

The three-way match
-------------------
This is the control that makes a purchase ledger worth having. Three documents
describe one delivery and are produced by three different parties: the order
says what was agreed, the goods receipt says what physically arrived, and the
supplier's invoice says what is being charged. Paying an invoice without
setting it against the other two is how a shop pays for a delivery it never
had, or pays last quarter's price increase it never agreed to.

Matching here is explicit about *which* leg failed — quantity or price, over or
under — because "mismatch" tells a buyer nothing and "invoiced 640 m against a
receipt of 600 m" tells them exactly who to ring.

Tolerances exist because deliveries are not exact. Aluminium is cut to stock
lengths, so an order for 590 m arrives as 600 m; a tolerance that refuses that
would block every delivery, and one that ignores a 12% overcharge is not a
control at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Iterable

from ..core.errors import ProfileOSError
from .ledger import JournalEntry, Ledger, Posting, money
from .stock import MovementKind, StockLedger


class PurchasingError(ProfileOSError):
    """A purchasing document cannot be produced or accepted."""


class OrderState(StrEnum):
    DRAFT = "draft"
    PLACED = "placed"
    PART_RECEIVED = "part_received"
    RECEIVED = "received"
    INVOICED = "invoiced"
    CANCELLED = "cancelled"


class MatchResult(StrEnum):
    MATCHED = "matched"
    QUANTITY_OVER = "quantity_over"
    QUANTITY_SHORT = "quantity_short"
    PRICE_OVER = "price_over"
    PRICE_UNDER = "price_under"
    NO_RECEIPT = "no_receipt"
    NO_ORDER = "no_order"


@dataclass
class Requirement:
    """One line of a requirements run, and the arithmetic behind it."""

    item: str
    gross: float
    on_hand: float
    allocated: float
    on_order: float
    unit: str = "m"
    needed_by: date | None = None

    @property
    def free(self) -> float:
        """On the rack and not already promised to something else."""
        return max(0.0, self.on_hand - self.allocated)

    @property
    def net(self) -> float:
        """What actually has to be bought."""
        return max(0.0, self.gross - self.free - self.on_order)

    @property
    def must_order(self) -> bool:
        return self.net > 1e-6

    def explain(self) -> str:
        return (
            f"{self.item}: need {self.gross:g}{self.unit}, "
            f"{self.free:g} free on the rack, {self.on_order:g} already on order "
            f"→ buy {self.net:g}{self.unit}"
        )


@dataclass
class OrderLine:
    item: str
    quantity: float
    unit_price: float
    """Price of one unit, in minor currency units."""
    unit: str = "m"
    received: float = 0.0
    invoiced: float = 0.0
    description: str | None = None

    @property
    def value(self) -> int:
        return money(self.quantity * self.unit_price, minor_units=1)

    @property
    def outstanding(self) -> float:
        return max(0.0, self.quantity - self.received)


@dataclass
class PurchaseOrder:
    """What was agreed with a supplier."""

    order_id: str
    supplier_id: str
    raised: date
    lines: list[OrderLine] = field(default_factory=list)
    promised: date | None = None
    state: OrderState = OrderState.DRAFT
    project_id: str | None = None
    note: str | None = None
    currency: str = "ILS"

    def __post_init__(self) -> None:
        if not self.lines:
            raise PurchasingError("A purchase order needs at least one line",
                                  order=self.order_id)
        codes = [line.item for line in self.lines]
        if len(codes) != len(set(codes)):
            raise PurchasingError(
                "An item appears twice on one order; merge the lines so the "
                "receipt and the invoice have one line to match against",
                order=self.order_id,
            )

    @property
    def net(self) -> int:
        return sum(line.value for line in self.lines)

    def line(self, item: str) -> OrderLine:
        for line in self.lines:
            if line.item == item:
                return line
        raise PurchasingError(
            f"Item {item!r} is not on order {self.order_id}", order=self.order_id
        )

    def refresh_state(self) -> OrderState:
        if self.state in (OrderState.DRAFT, OrderState.CANCELLED):
            return self.state
        received = sum(line.received for line in self.lines)
        ordered = sum(line.quantity for line in self.lines)
        if received <= 1e-9:
            self.state = OrderState.PLACED
        elif received + 1e-9 < ordered:
            self.state = OrderState.PART_RECEIVED
        else:
            self.state = OrderState.RECEIVED
        if all(line.invoiced >= line.received - 1e-9 for line in self.lines) and received:
            self.state = OrderState.INVOICED
        return self.state


@dataclass
class ReceiptLine:
    item: str
    quantity: float
    unit_price: float | None = None
    """Price actually charged on the delivery note, when it carries one."""


@dataclass
class GoodsReceipt:
    """What physically arrived."""

    receipt_id: str
    order_id: str
    on: date
    lines: list[ReceiptLine] = field(default_factory=list)
    delivery_note: str | None = None


@dataclass
class InvoiceLine:
    item: str
    quantity: float
    unit_price: float


@dataclass
class PurchaseInvoice:
    """What the supplier is charging."""

    invoice_id: str
    supplier_id: str
    order_id: str | None
    on: date
    lines: list[InvoiceLine] = field(default_factory=list)
    vat_rate: float = 0.18
    supplier_reference: str | None = None

    @property
    def net(self) -> int:
        return sum(money(line.quantity * line.unit_price, minor_units=1)
                   for line in self.lines)

    @property
    def vat(self) -> int:
        return money(self.net * self.vat_rate, minor_units=1)

    @property
    def gross(self) -> int:
        return self.net + self.vat


@dataclass
class MatchLine:
    """One item, seen from all three documents."""

    item: str
    ordered: float
    received: float
    invoiced: float
    order_price: float
    invoice_price: float
    result: MatchResult

    @property
    def ok(self) -> bool:
        return self.result is MatchResult.MATCHED

    def explain(self) -> str:
        if self.result is MatchResult.MATCHED:
            return f"{self.item}: matched"
        if self.result is MatchResult.NO_ORDER:
            return f"{self.item}: invoiced but never ordered"
        if self.result is MatchResult.NO_RECEIPT:
            return f"{self.item}: invoiced but nothing was received"
        if self.result in (MatchResult.QUANTITY_OVER, MatchResult.QUANTITY_SHORT):
            return (
                f"{self.item}: invoiced {self.invoiced:g} against a receipt of "
                f"{self.received:g}"
            )
        return (
            f"{self.item}: invoiced at {self.invoice_price / 100:.2f} against an "
            f"agreed {self.order_price / 100:.2f}"
        )


@dataclass
class Match:
    """The outcome of setting an invoice against its order and receipts."""

    invoice_id: str
    lines: list[MatchLine] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(line.ok for line in self.lines)

    @property
    def failures(self) -> list[MatchLine]:
        return [line for line in self.lines if not line.ok]

    def explain(self) -> str:
        if self.ok:
            return f"Invoice {self.invoice_id} matches its order and receipts."
        return "\n".join(line.explain() for line in self.failures)


#: What counts as an acceptable difference. Quantity tolerance is generous
#: because material comes in stock lengths; price tolerance is tight because a
#: price difference is a decision somebody made, not a physical constraint.
DEFAULT_QUANTITY_TOLERANCE = 0.05
DEFAULT_PRICE_TOLERANCE = 0.01


def requirements(
    demand: dict[str, float],
    stock: StockLedger,
    *,
    needed_by: date | None = None,
) -> list[Requirement]:
    """Turn gross demand into net requirements against the current stock."""
    rows: list[Requirement] = []
    for item, gross in sorted(demand.items()):
        try:
            state = stock.state(item)
        except Exception:  # noqa: BLE001 - an unknown item must all be bought
            rows.append(Requirement(item, gross, 0.0, 0.0, 0.0, needed_by=needed_by))
            continue
        rows.append(
            Requirement(
                item=item,
                gross=gross,
                on_hand=state.on_hand,
                allocated=state.allocated,
                on_order=state.on_order,
                unit=state.item.unit,
                needed_by=needed_by,
            )
        )
    return rows


def orders_from_requirements(
    rows: Iterable[Requirement],
    stock: StockLedger,
    prices: dict[str, float],
    *,
    raised: date | None = None,
    project_id: str | None = None,
    prefix: str = "PO",
    start_number: int = 1,
) -> list[PurchaseOrder]:
    """Group net requirements into one order per supplier.

    One order per supplier rather than one per line: a supplier who receives
    six orders for six profiles on the same morning will ring up and ask why,
    and delivery is charged per drop.
    """
    by_supplier: dict[str, list[OrderLine]] = {}
    lead_times: dict[str, int] = {}
    when = raised or date.today()

    for row in rows:
        if not row.must_order:
            continue
        state = stock.state(row.item)
        supplier = state.item.supplier_id or "unassigned"
        price = prices.get(row.item)
        if price is None:
            raise PurchasingError(
                "No price for an item that has to be bought; add it to the "
                "supplier's price list before raising the order",
                item=row.item,
                supplier=supplier,
            )
        # Round up to the reorder quantity when one is set, so an order for
        # 12 m of a profile sold in 6 m bars asks for 12, not 11.4.
        quantity = row.net
        step = state.item.reorder_quantity
        if step > 0:
            multiples = int(quantity / step)
            if quantity - multiples * step > 1e-9:
                multiples += 1
            quantity = multiples * step
        by_supplier.setdefault(supplier, []).append(
            OrderLine(
                item=row.item,
                quantity=round(quantity, 3),
                unit_price=price,
                unit=state.item.unit,
                description=state.item.name,
            )
        )
        lead_times[supplier] = max(
            lead_times.get(supplier, 0), state.item.lead_time_days
        )

    orders: list[PurchaseOrder] = []
    for index, (supplier, lines) in enumerate(sorted(by_supplier.items()), start=start_number):
        orders.append(
            PurchaseOrder(
                order_id=f"{prefix}-{index:04d}",
                supplier_id=supplier,
                raised=when,
                lines=lines,
                promised=when + timedelta(days=lead_times.get(supplier, 14)),
                project_id=project_id,
            )
        )
    return orders


def place(order: PurchaseOrder, stock: StockLedger) -> PurchaseOrder:
    """Commit an order: the quantities become on-order against the stock book."""
    if order.state is not OrderState.DRAFT:
        raise PurchasingError(
            "Only a draft order can be placed", order=order.order_id,
            state=str(order.state),
        )
    for line in order.lines:
        stock.order(line.item, line.quantity)
    order.state = OrderState.PLACED
    return order


def receive(
    order: PurchaseOrder,
    receipt: GoodsReceipt,
    stock: StockLedger,
    ledger: Ledger | None = None,
) -> list:
    """Book a delivery in.

    The value goes to stock and to goods-received-not-invoiced, not to the
    supplier's account: at this moment the shop owes for the goods but has no
    invoice to pay. That liability is what the three-way match later clears.
    """
    if order.state in (OrderState.DRAFT, OrderState.CANCELLED):
        raise PurchasingError(
            "Cannot receive against an order that was never placed",
            order=order.order_id, state=str(order.state),
        )
    movements = []
    for line in receipt.lines:
        ordered = order.line(line.item)
        price = line.unit_price if line.unit_price is not None else ordered.unit_price
        movement = stock.receive(
            line.item, line.quantity, price,
            on=receipt.on, reference=receipt.receipt_id,
            kind=MovementKind.RECEIPT,
        )
        ordered.received += line.quantity
        movements.append(movement)

        if ledger is not None:
            item = stock.state(line.item).item
            ledger.post(
                JournalEntry(
                    entry_id=f"GR-{receipt.receipt_id}-{line.item}",
                    date=receipt.on,
                    narrative=f"Goods received {line.item} {line.quantity:g}{item.unit}",
                    postings=(
                        Posting(item.account, movement.value),
                        Posting("2150", -movement.value),
                    ),
                    source="goods_receipt",
                    reference=order.order_id,
                )
            )
    order.refresh_state()
    return movements


def three_way_match(
    invoice: PurchaseInvoice,
    order: PurchaseOrder | None,
    receipts: Iterable[GoodsReceipt] = (),
    *,
    quantity_tolerance: float = DEFAULT_QUANTITY_TOLERANCE,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
) -> Match:
    """Set an invoice against its order and the goods actually received."""
    received: dict[str, float] = {}
    for receipt in receipts:
        for line in receipt.lines:
            received[line.item] = received.get(line.item, 0.0) + line.quantity

    match = Match(invoice_id=invoice.invoice_id)
    for line in invoice.lines:
        ordered_line = None
        if order is not None:
            try:
                ordered_line = order.line(line.item)
            except PurchasingError:
                ordered_line = None

        got = received.get(line.item, 0.0)
        order_price = ordered_line.unit_price if ordered_line else 0.0
        ordered_quantity = ordered_line.quantity if ordered_line else 0.0

        if ordered_line is None:
            result = MatchResult.NO_ORDER
        elif got <= 1e-9:
            result = MatchResult.NO_RECEIPT
        elif line.quantity > got * (1 + quantity_tolerance) + 1e-9:
            result = MatchResult.QUANTITY_OVER
        elif line.quantity < got * (1 - quantity_tolerance) - 1e-9:
            result = MatchResult.QUANTITY_SHORT
        elif order_price > 0 and line.unit_price > order_price * (1 + price_tolerance):
            result = MatchResult.PRICE_OVER
        elif order_price > 0 and line.unit_price < order_price * (1 - price_tolerance):
            result = MatchResult.PRICE_UNDER
        else:
            result = MatchResult.MATCHED

        match.lines.append(
            MatchLine(
                item=line.item,
                ordered=ordered_quantity,
                received=got,
                invoiced=line.quantity,
                order_price=order_price,
                invoice_price=line.unit_price,
                result=result,
            )
        )
    return match


def post_purchase_invoice(
    invoice: PurchaseInvoice,
    ledger: Ledger,
    *,
    match: Match | None = None,
    creditors: str = "2100",
    accrual: str = "2150",
    input_vat: str = "1400",
) -> JournalEntry:
    """Post a supplier invoice, clearing the accrual raised at goods receipt.

    An unmatched invoice is refused. Posting it would put a liability on the
    books that nobody has agreed to, and the whole point of the match is that
    it happens *before* the money moves.
    """
    if match is not None and not match.ok:
        raise PurchasingError(
            "This invoice does not match its order and receipts, so it is not "
            "posted: " + match.explain(),
            invoice=invoice.invoice_id,
        )
    net, vat, gross = invoice.net, invoice.vat, invoice.gross
    postings = [
        Posting(accrual, net, "clearing goods received not invoiced"),
        Posting(input_vat, vat, "recoverable VAT"),
        Posting(creditors, -gross, invoice.supplier_id),
    ]
    return ledger.post(
        JournalEntry(
            entry_id=f"PI-{invoice.invoice_id}",
            date=invoice.on,
            narrative=f"Purchase invoice {invoice.invoice_id} from {invoice.supplier_id}",
            postings=tuple(postings),
            source="purchase_invoice",
            reference=invoice.order_id,
        )
    )


def pay_supplier(
    ledger: Ledger,
    supplier_id: str,
    amount: int,
    on: date,
    *,
    entry_id: str,
    bank: str = "1100",
    creditors: str = "2100",
) -> JournalEntry:
    return ledger.post_simple(
        entry_id, on, f"Payment to {supplier_id}", creditors, bank, amount,
        source="payment", reference=supplier_id,
    )


__all__ = [
    "PurchasingError",
    "OrderState",
    "MatchResult",
    "Requirement",
    "OrderLine",
    "PurchaseOrder",
    "ReceiptLine",
    "GoodsReceipt",
    "InvoiceLine",
    "PurchaseInvoice",
    "MatchLine",
    "Match",
    "DEFAULT_QUANTITY_TOLERANCE",
    "DEFAULT_PRICE_TOLERANCE",
    "requirements",
    "orders_from_requirements",
    "place",
    "receive",
    "three_way_match",
    "post_purchase_invoice",
    "pay_supplier",
]
