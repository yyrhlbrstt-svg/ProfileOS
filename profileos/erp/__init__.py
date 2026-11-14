"""ERP: stock, purchasing, sales, the general ledger and capacity planning.

Typical use::

    from profileos.erp import Company, StockItem, money

    shop = Company(name='דאדי בע"מ')
    shop.add_item(StockItem("4301", "Outer frame", supplier_id="extal"))
    rows, orders = shop.plan_purchases({"4301": 480.0}, {"4301": money(41.50)})
    for order in orders:
        shop.place_order(order)
    ...
    shop.audit()   # raises if the books and the racks disagree
"""

from __future__ import annotations

from .company import (
    STOCK_ACCOUNT_CODES,
    Company,
    CompanyError,
    company_for_brand,
)
from .ledger import (
    STANDARD_CHART,
    Account,
    AccountBalance,
    AccountType,
    JournalEntry,
    Ledger,
    LedgerError,
    Posting,
    format_money,
    money,
)
from .purchasing import (
    DEFAULT_PRICE_TOLERANCE,
    DEFAULT_QUANTITY_TOLERANCE,
    GoodsReceipt,
    InvoiceLine,
    Match,
    MatchLine,
    MatchResult,
    OrderLine,
    OrderState,
    PurchaseInvoice,
    PurchaseOrder,
    PurchasingError,
    ReceiptLine,
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
    ISRAELI_VAT_HISTORY,
    AgedRow,
    DeliveryNote,
    DocumentState,
    SalesError,
    SalesInvoice,
    SalesLine,
    SalesOrder,
    aged_debtors,
    credit_note,
    post_delivery,
    post_sales_invoice,
    receive_payment,
    vat_rate,
    vat_return,
)
from .scheduling import (
    DEFAULT_WORK_CENTRES,
    OPERATION_SEQUENCE,
    Calendar,
    JobDemand,
    Operation,
    Schedule,
    ScheduledOperation,
    Scheduler,
    SchedulingError,
    StandardTimes,
    WorkCentre,
    demand_from_builds,
)
from .stock import (
    ItemState,
    Layer,
    Movement,
    MovementKind,
    StockError,
    StockItem,
    StockLedger,
    Valuation,
    post_movement,
)

__all__ = [
    # ledger
    "LedgerError", "AccountType", "Account", "STANDARD_CHART", "Posting",
    "JournalEntry", "AccountBalance", "Ledger", "money", "format_money",
    # stock
    "StockError", "Valuation", "MovementKind", "StockItem", "Layer",
    "Movement", "ItemState", "StockLedger", "post_movement",
    # purchasing
    "PurchasingError", "OrderState", "MatchResult", "Requirement", "OrderLine",
    "PurchaseOrder", "ReceiptLine", "GoodsReceipt", "InvoiceLine",
    "PurchaseInvoice", "MatchLine", "Match", "DEFAULT_QUANTITY_TOLERANCE",
    "DEFAULT_PRICE_TOLERANCE", "requirements", "orders_from_requirements",
    "place", "receive", "three_way_match", "post_purchase_invoice",
    "pay_supplier",
    # sales
    "SalesError", "ISRAELI_VAT_HISTORY", "vat_rate", "DocumentState",
    "SalesLine", "SalesOrder", "DeliveryNote", "SalesInvoice", "credit_note",
    "post_delivery", "post_sales_invoice", "receive_payment", "AgedRow",
    "aged_debtors", "vat_return",
    # scheduling
    "SchedulingError", "Operation", "OPERATION_SEQUENCE", "Calendar",
    "WorkCentre", "DEFAULT_WORK_CENTRES", "StandardTimes", "JobDemand",
    "ScheduledOperation", "Schedule", "Scheduler", "demand_from_builds",
    # company
    "CompanyError", "STOCK_ACCOUNT_CODES", "Company", "company_for_brand",
]
