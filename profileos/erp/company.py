"""The books of one fabricator, in one object.

Stock, purchasing, sales and the ledger are separate modules because they are
separate concerns, but a shop does not experience them separately: receiving a
delivery is a stock movement *and* a ledger posting *and* a change to an order's
state, and if any of the three is skipped the books stop agreeing with the
racks.

:class:`Company` is where those are bound together, so a caller says "receive
this delivery" once and every consequence follows. Each method leaves the
system consistent or raises and changes nothing.

:meth:`Company.audit` is the point of the whole design: it re-derives the stock
value from the movement history, re-derives the trial balance from the
postings, and checks that the stock accounts in the ledger agree with the stock
book. Three independent records of the same facts, cross-checked — which is
what makes the figures worth quoting from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .ledger import STANDARD_CHART, Account, Ledger, format_money, money
from .purchasing import (
    GoodsReceipt,
    Match,
    PurchaseInvoice,
    PurchaseOrder,
    PurchasingError,
    Requirement,
    orders_from_requirements,
    pay_supplier,
    place,
    post_purchase_invoice,
    receive,
    requirements,
    three_way_match,
)
from .sales import (
    DeliveryNote,
    SalesInvoice,
    SalesOrder,
    aged_debtors,
    credit_note,
    post_delivery,
    post_sales_invoice,
    receive_payment,
    vat_return,
)
from .scheduling import (
    DEFAULT_WORK_CENTRES,
    Calendar,
    JobDemand,
    Schedule,
    Scheduler,
    StandardTimes,
)
from .stock import MovementKind, StockItem, StockLedger, post_movement

_log = get_logger(__name__)


class CompanyError(ProfileOSError):
    """An operation would leave the company's records inconsistent."""


#: Accounts that are supposed to hold nothing but stock value.
#:
#: The audit checks these whether or not any item currently points at them. A
#: balance on a stock account that the stock book knows nothing about is
#: precisely the discrepancy worth finding — and deriving the list from the
#: items alone makes that case invisible, because an account with no items has
#: nothing to compare against and so is never looked at.
STOCK_ACCOUNT_CODES: tuple[str, ...] = ("1300", "1310", "1320")


@dataclass
class Company:
    """One fabricator's stock, orders, invoices and accounts."""

    name: str
    currency: str = "ILS"
    ledger: Ledger = field(default_factory=lambda: Ledger(STANDARD_CHART))
    stock: StockLedger = field(default_factory=StockLedger)
    purchase_orders: dict[str, PurchaseOrder] = field(default_factory=dict)
    receipts: dict[str, list[GoodsReceipt]] = field(default_factory=dict)
    purchase_invoices: dict[str, PurchaseInvoice] = field(default_factory=dict)
    sales_orders: dict[str, SalesOrder] = field(default_factory=dict)
    sales_invoices: dict[str, SalesInvoice] = field(default_factory=dict)
    payments_in: dict[str, int] = field(default_factory=dict)
    scheduler: Scheduler = field(default_factory=Scheduler)
    _sequence: dict[str, int] = field(default_factory=dict, repr=False)

    # -- numbering ------------------------------------------------------------ #
    def next_number(self, series: str, *, year: int | None = None) -> str:
        """Sequential document numbers per series and year.

        A tax invoice needs an unbroken sequence, so the counter lives with the
        company rather than being handed in by whoever happens to be calling.
        """
        stamp = year or date.today().year
        key = f"{series}:{stamp}"
        self._sequence[key] = self._sequence.get(key, 0) + 1
        return f"{series}-{stamp}-{self._sequence[key]:04d}"

    # -- stock ---------------------------------------------------------------- #
    def add_item(self, item: StockItem) -> StockItem:
        self.stock.add_item(item)
        return item

    def receive_stock(
        self, code: str, quantity: float, unit_cost: float, *,
        on: date | None = None, reference: str | None = None,
    ):
        """Take material in outside a purchase order — a cash purchase, a return."""
        movement = self.stock.receive(code, quantity, unit_cost, on=on, reference=reference)
        post_movement(self.ledger, movement, self.stock.state(code).item,
                      counter_account="2100")
        return movement

    def issue_to_job(
        self, code: str, quantity: float, job: str, *, on: date | None = None,
    ):
        """Consume material into a job: out of stock, into work in progress."""
        movement = self.stock.issue(code, quantity, on=on, reference=job)
        post_movement(self.ledger, movement, self.stock.state(code).item,
                      counter_account="1500")
        return movement

    def stocktake(self, counts: dict[str, float], *, on: date | None = None):
        """Correct the book to a physical count; differences hit the variance account."""
        results = []
        for code, counted in sorted(counts.items()):
            movement = self.stock.adjust(code, counted, on=on, reference="stocktake")
            if movement is None:
                continue
            post_movement(self.ledger, movement, self.stock.state(code).item,
                          counter_account="5900")
            results.append(movement)
        return results

    # -- purchasing ------------------------------------------------------------ #
    def plan_purchases(
        self, demand: dict[str, float], prices: dict[str, float], *,
        on: date | None = None, project_id: str | None = None,
    ) -> tuple[list[Requirement], list[PurchaseOrder]]:
        """Work out what has to be bought, and draft the orders for it."""
        rows = requirements(demand, self.stock)
        start = self._sequence.get(f"PO:{(on or date.today()).year}", 0) + 1
        orders = orders_from_requirements(
            rows, self.stock, prices, raised=on, project_id=project_id,
            start_number=start,
        )
        return rows, orders

    def place_order(self, order: PurchaseOrder) -> PurchaseOrder:
        if order.order_id in self.purchase_orders:
            raise CompanyError(f"Order {order.order_id} already exists")
        place(order, self.stock)
        self.purchase_orders[order.order_id] = order
        return order

    def receive_delivery(self, receipt: GoodsReceipt) -> list:
        order = self.purchase_orders.get(receipt.order_id)
        if order is None:
            raise CompanyError(
                "No such purchase order; a delivery without an order cannot be "
                "matched and must not be booked in",
                order=receipt.order_id,
            )
        movements = receive(order, receipt, self.stock, self.ledger)
        self.receipts.setdefault(receipt.order_id, []).append(receipt)
        return movements

    def match_invoice(self, invoice: PurchaseInvoice) -> Match:
        order = self.purchase_orders.get(invoice.order_id or "")
        return three_way_match(invoice, order, self.receipts.get(invoice.order_id or "", []))

    def book_purchase_invoice(self, invoice: PurchaseInvoice, *, force: bool = False):
        """Match, then post. An unmatched invoice is refused unless forced.

        ``force`` exists because a buyer sometimes accepts a difference — a
        price rise they agreed on the phone. It is deliberately explicit, so
        accepting a mismatch is a decision somebody made rather than something
        that happened.
        """
        match = self.match_invoice(invoice)
        entry = post_purchase_invoice(
            invoice, self.ledger, match=None if force else match
        )
        self.purchase_invoices[invoice.invoice_id] = invoice
        order = self.purchase_orders.get(invoice.order_id or "")
        if order is not None:
            for line in invoice.lines:
                try:
                    order.line(line.item).invoiced += line.quantity
                except PurchasingError:
                    continue
            order.refresh_state()
        return entry, match

    def pay(self, supplier_id: str, amount: int, on: date) -> None:
        pay_supplier(
            self.ledger, supplier_id, amount, on,
            entry_id=self.next_number("PAY", year=on.year),
        )

    # -- sales ------------------------------------------------------------------ #
    def confirm_sales_order(self, order: SalesOrder) -> SalesOrder:
        self.sales_orders[order.order_id] = order
        return order

    def deliver(self, note: DeliveryNote) -> None:
        post_delivery(note, self.ledger)

    def invoice(self, invoice: SalesInvoice) -> SalesInvoice:
        post_sales_invoice(invoice, self.ledger)
        self.sales_invoices[invoice.invoice_id] = invoice
        return invoice

    def credit(self, invoice_id: str, on: date) -> SalesInvoice:
        original = self.sales_invoices.get(invoice_id)
        if original is None:
            raise CompanyError(f"No invoice {invoice_id} to credit", invoice=invoice_id)
        note = credit_note(original, self.next_number("CN", year=on.year), on)
        return self.invoice(note)

    def collect(self, customer: str, amount: int, on: date) -> None:
        receive_payment(
            self.ledger, customer, amount, on,
            entry_id=self.next_number("RCT", year=on.year),
        )
        self.payments_in[customer] = self.payments_in.get(customer, 0) + amount

    # -- planning ---------------------------------------------------------------- #
    def schedule(self, jobs: Iterable[JobDemand], *, start: date | None = None) -> Schedule:
        return self.scheduler.schedule(jobs, start=start)

    # -- reporting ---------------------------------------------------------------- #
    def aged_debtors(self, as_at: date | None = None):
        return aged_debtors(
            self.sales_invoices.values(), self.payments_in, as_at or date.today()
        )

    def vat_return(self, start: date, end: date) -> dict[str, object]:
        return vat_return(self.ledger, start, end)

    def audit(self) -> dict[str, object]:
        """Cross-check three independent records of the same facts.

        Raises on any disagreement, with the amount it is out by. Returning a
        vague "ok" would let a discrepancy of a few agorot pass unnoticed, and
        a few agorot is exactly how a real error first shows itself.
        """
        self.ledger.check()
        self.stock.check()

        accounts = {state.item.account for state in self.stock.items.values()}
        accounts.update(
            code
            for code in STOCK_ACCOUNT_CODES
            if code in self.ledger.accounts and self.ledger.balance(code) != 0
        )
        book_value = {account: 0 for account in accounts}
        for state in self.stock.items.values():
            book_value[state.item.account] = (
                book_value.get(state.item.account, 0) + state.value
            )

        discrepancies: list[dict[str, object]] = []
        for account in sorted(accounts):
            ledger_value = self.ledger.balance(account)
            if ledger_value != book_value[account]:
                discrepancies.append(
                    {
                        "account": account,
                        "ledger": ledger_value,
                        "stock_book": book_value[account],
                        "out_by": ledger_value - book_value[account],
                    }
                )
        if discrepancies:
            raise CompanyError(
                "The stock accounts disagree with the stock book",
                detail="; ".join(
                    f"{d['account']}: ledger {d['ledger']} vs book {d['stock_book']} "
                    f"(out by {d['out_by']})"
                    for d in discrepancies
                ),
            )
        return {
            "ledger_balanced": True,
            "stock_reconciled": True,
            "stock_accounts_agree": True,
            "stock_value": self.stock.total_value,
            "entries": len(self.ledger.entries),
            "movements": len(self.stock.movements),
        }

    def summary(self) -> dict[str, object]:
        balance_sheet = self.ledger.balance_sheet()
        profit = self.ledger.profit_and_loss()
        return {
            "company": self.name,
            "currency": self.currency,
            "stock_value": self.stock.total_value,
            "debtors": self.ledger.balance("1200"),
            "creditors": self.ledger.balance("2100"),
            "bank": self.ledger.balance("1100"),
            "income": profit["income"],
            "expense": profit["expense"],
            "result": profit["result"],
            "balance_sheet_difference": balance_sheet["difference"],
            "purchase_orders": len(self.purchase_orders),
            "sales_invoices": len(self.sales_invoices),
        }

    def describe(self) -> str:
        s = self.summary()
        return (
            f"{self.name}: stock {format_money(s['stock_value'], self.currency)}, "
            f"debtors {format_money(s['debtors'], self.currency)}, "
            f"creditors {format_money(s['creditors'], self.currency)}, "
            f"result {format_money(s['result'], self.currency)}"
        )


def company_for_brand(brand_id: str | None = None) -> Company:
    """A company set up for the operator this installation belongs to."""
    from ..branding import get_brand

    brand = get_brand(brand_id)
    return Company(
        name=brand.document_name,
        currency="ILS" if (brand.country or "").strip() in ("ישראל", "Israel", "IL") else "EUR",
    )


__all__ = ["CompanyError", "STOCK_ACCOUNT_CODES", "Company", "company_for_brand"]
