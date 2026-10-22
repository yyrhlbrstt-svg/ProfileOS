"""The job file: everything the shop keeps about one piece of work.

A fabricator does not think in "sessions" — they think in jobs. A job is
opened when the customer calls, priced, won or lost, released to the floor,
and finally installed. Every one of those states carries a date somebody will
be asked about later, so the job file records them rather than leaving the
history in whoever's memory.

The job file holds the *schedule* of openings, not the built elements: the
build is derived from the schedule and the system rules, and re-deriving it on
open means a job saved before a rule was corrected picks up the correction.
Storing the derived cut list instead would quietly preserve the old mistake.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..elements.model import ElementSchedule
from ..models.base import RoundTrips


class JobStatus(StrEnum):
    """Where a job stands commercially, in the order it normally moves."""

    ENQUIRY = "enquiry"
    QUOTED = "quoted"
    WON = "won"
    IN_PRODUCTION = "in_production"
    INSTALLED = "installed"
    LOST = "lost"

    @property
    def hebrew(self) -> str:
        return {
            JobStatus.ENQUIRY: "פנייה",
            JobStatus.QUOTED: "הצעה נשלחה",
            JobStatus.WON: "הוזמן",
            JobStatus.IN_PRODUCTION: "בייצור",
            JobStatus.INSTALLED: "הותקן",
            JobStatus.LOST: "לא נסגר",
        }[self]

    @property
    def is_open(self) -> bool:
        """Whether the job is still live work — the pipeline, not the archive."""
        return self not in (JobStatus.INSTALLED, JobStatus.LOST)

    @property
    def badge(self) -> str:
        """Which status colour the interface paints this with."""
        return {
            JobStatus.ENQUIRY: "muted",
            JobStatus.QUOTED: "info",
            JobStatus.WON: "accent",
            JobStatus.IN_PRODUCTION: "warning",
            JobStatus.INSTALLED: "success",
            JobStatus.LOST: "danger",
        }[self]


class Trade(StrEnum):
    """Which of the shop's two trades a job belongs to."""

    ALUMINIUM = "aluminium"
    PLUMBING = "plumbing"
    BOTH = "both"

    @property
    def hebrew(self) -> str:
        return {
            Trade.ALUMINIUM: "אלומיניום",
            Trade.PLUMBING: "אינסטלציה",
            Trade.BOTH: "אלומיניום ואינסטלציה",
        }[self]

    def includes(self, other: "Trade") -> bool:
        return self is Trade.BOTH or self is other


class LineKind(StrEnum):
    """Why a priced line is on the job."""

    #: Part of what was quoted: removal of the old windows, a crane, a boiler.
    QUOTED = "quoted"
    #: Asked for during the work — "another window", "move the toilet".
    EXTRA = "extra"

    @property
    def hebrew(self) -> str:
        return {LineKind.QUOTED: "בהצעה", LineKind.EXTRA: "תוספת"}[self]


#: Where work comes from, in the words a shop uses when it asks "how did you
#: hear about us?". Free text is allowed too; these are the usual answers.
LEAD_SOURCES: tuple[str, ...] = (
    "המלצה של לקוח", "לקוח חוזר", "קבלן / אדריכל", "גוגל", "פייסבוק / אינסטגרם",
    "מדרג", "שלט / רכב", "עלון / מודעה", "אחר",
)


#: Why a quotation was lost — asked when a job is marked lost, so that over a
#: year the shop knows whether it loses on price, on time, or on silence.
LOST_REASONS: tuple[str, ...] = (
    "מחיר", "זמן אספקה", "לא חזרנו אליו בזמן", "בחר מתחרה", "הפרויקט בוטל / נדחה",
    "לא ענה", "אחר",
)


class JobLine(RoundTrips):
    """A priced line on the job that is not a designed opening.

    Work items from the shop's price book, or written freehand. An extra
    records who approved it and how, because the argument at the end of a job
    is always about the extras nobody wrote down.
    """

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(default_factory=lambda: f"L-{_short_id()}")
    kind: LineKind = LineKind.QUOTED
    description: str
    quantity: float = 1.0
    unit: str = "יח׳"
    #: Net of VAT, in shekels.
    unit_price: float = 0.0
    #: The price-book item it came from, if any.
    item_code: str = ""
    added_on: str = Field(default_factory=lambda: date.today().isoformat())
    #: "אושר בוואטסאפ 12/10", "אושר בטלפון" — empty for an extra not yet agreed.
    approved: str = ""
    #: The quotation whose total already includes this line. Empty means the
    #: line is on top of any quotation and is counted on its own.
    in_quote: str = ""
    #: What put it here, e.g. "plumbing-fixtures", so a repeat replaces it.
    origin: str = ""

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    @property
    def counts(self) -> bool:
        """Whether it is owed: quoted lines always, extras once agreed."""
        return self.kind is LineKind.QUOTED or bool(self.approved)


class PaymentMethod(StrEnum):
    TRANSFER = "transfer"
    CASH = "cash"
    CREDIT = "credit"
    BIT = "bit"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            PaymentMethod.TRANSFER: "העברה בנקאית", PaymentMethod.CASH: "מזומן",
            PaymentMethod.CREDIT: "אשראי", PaymentMethod.BIT: "ביט / פייבוקס",
            PaymentMethod.OTHER: "אחר",
        }[self]


class Payment(RoundTrips):
    """Money received for the job other than a cheque (those live in the drawer).

    ``withheld`` is income tax a contractor deducted at source: it is not owed
    by them any more, it is a credit with the tax authority, and a balance
    that ignored it would chase the customer for money they already paid.
    """

    model_config = ConfigDict(extra="forbid")

    payment_id: str = Field(default_factory=lambda: f"P-{_short_id()}")
    on: str = Field(default_factory=lambda: date.today().isoformat())
    amount: float
    method: PaymentMethod = PaymentMethod.TRANSFER
    withheld: float = 0.0
    reference: str = ""
    note: str = ""


class InvoiceRecord(RoundTrips):
    """A tax invoice issued for the job — recorded here, issued elsewhere.

    The shop issues invoices in its invoicing service (Morning, iCount); this
    keeps the number and, for a business customer above the threshold, the
    Tax Authority allocation number, so a missing one is noticed before the
    customer's bookkeeper refuses the invoice.
    """

    model_config = ConfigDict(extra="forbid")

    number: str
    issued_on: str = Field(default_factory=lambda: date.today().isoformat())
    net: float = 0.0
    allocation_number: str = ""
    #: The customer is a business that will claim the VAT.
    business_customer: bool = False
    issuer: str = ""


class ProgressBill(RoundTrips):
    """One interim bill to a main contractor (חשבון חלקי מצטבר).

    The contractor approves a cumulative percentage of the work; this bill is
    what that adds over the last one. They hold back a retention (עיכבון)
    until the end, and deduct income tax at source when the shop's
    certificate says so.
    """

    model_config = ConfigDict(extra="forbid")

    number: int
    on: str = Field(default_factory=lambda: date.today().isoformat())
    #: Approved so far, as a percentage of the whole job (0–100).
    cumulative_pct: float
    retention_pct: float = 5.0
    withholding_pct: float = 0.0
    #: The job's value (net) when this bill was made. Kept, so a bill already
    #: sent never changes when an extra is approved later.
    contract_net: float = 0.0
    approved_by: str = ""
    note: str = ""


class PlumbingSpec(RoundTrips):
    """The plumbing side of a job: what fixtures, for how many dwellings."""

    model_config = ConfigDict(extra="forbid")

    #: Fixture id → count, as on the plumbing page.
    fixtures: dict[str, int] = Field(default_factory=dict)
    dwellings: int = 1
    notes: str = ""


def _short_id() -> str:
    from uuid import uuid4

    return uuid4().hex[:6].upper()


#: Where a job may go from where it is. Anything else is a mistake worth
#: catching: a job cannot be installed before it is ordered, and a lost job
#: does not quietly become a live one — it is reopened as an enquiry.
TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.ENQUIRY: {JobStatus.QUOTED, JobStatus.LOST},
    JobStatus.QUOTED: {JobStatus.WON, JobStatus.LOST, JobStatus.ENQUIRY},
    JobStatus.WON: {JobStatus.IN_PRODUCTION, JobStatus.LOST},
    JobStatus.IN_PRODUCTION: {JobStatus.INSTALLED, JobStatus.WON},
    JobStatus.INSTALLED: set(),
    JobStatus.LOST: {JobStatus.ENQUIRY},
}


class Customer(RoundTrips):
    """One customer, as the shop files them."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    name: str
    contact: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    #: Company registration / VAT number, printed on statutory documents.
    tax_id: str = ""
    notes: str = ""

    def describe(self) -> str:
        parts = [self.name]
        if self.city:
            parts.append(self.city)
        if self.phone:
            parts.append(self.phone)
        return " · ".join(parts)


class StatusEvent(RoundTrips):
    """One move from one status to another, and when."""

    model_config = ConfigDict(extra="forbid")

    status: JobStatus
    at: str
    note: str = ""

    @classmethod
    def now(cls, status: JobStatus, note: str = "") -> "StatusEvent":
        return cls(status=status, at=datetime.now(timezone.utc).isoformat(timespec="seconds"), note=note)


class JobError(ValueError):
    """A job was asked to do something the shop's own rules forbid."""


class MaterialOrderState(StrEnum):
    """Where an order of material for the job stands with the supplier."""

    SENT = "sent"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    CANCELLED = "cancelled"

    @property
    def hebrew(self) -> str:
        return {
            "sent": "נשלחה", "confirmed": "אושרה על ידי הספק",
            "received": "התקבלה", "cancelled": "בוטלה",
        }[self.value]


class MaterialOrderLine(RoundTrips):
    """One thing ordered: so many bars of a profile, metres of gasket, units."""

    model_config = ConfigDict(extra="forbid")

    code: str
    description: str = ""
    quantity: float
    unit: str = "יח׳"
    #: For bars: the stock length ordered, in millimetres.
    length_mm: float = 0.0
    #: Net of VAT and of the shop's discount, per unit as ordered. Zero when
    #: the supplier's list did not price it — never a guess.
    unit_price: float = 0.0
    #: Which list the price came from.
    price_source: str = ""

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


class MaterialOrder(RoundTrips):
    """Material ordered for the job from one supplier."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(default_factory=lambda: f"MO-{_short_id()}")
    supplier: str
    ordered_on: str = Field(default_factory=lambda: date.today().isoformat())
    needed_by: str = ""
    state: MaterialOrderState = MaterialOrderState.SENT
    lines: list[MaterialOrderLine] = Field(default_factory=list)
    received_on: str = ""
    note: str = ""

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def unpriced(self) -> list[MaterialOrderLine]:
        return [line for line in self.lines if line.unit_price <= 0]

    @property
    def open(self) -> bool:
        return self.state in (MaterialOrderState.SENT, MaterialOrderState.CONFIRMED)


class JobFile(RoundTrips):
    """One job: who it is for, what is in it, and where it stands."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    name: str
    customer_id: str = ""
    customer_name: str = ""
    status: JobStatus = JobStatus.ENQUIRY
    #: The customer's own reference — their order number, their drawing number.
    reference: str = ""
    site_address: str = ""
    system_id: str = "generic"
    trade: Trade = Trade.ALUMINIUM
    #: How the customer found the shop — the answer that says where to spend
    #: on advertising, once there are enough jobs to read it from.
    source: str = ""
    #: When the customer was asked for a review after handover.
    review_requested: str = ""
    #: Why the job was lost, when it was.
    lost_reason: str = ""
    #: When the yearly check was offered to the customer.
    checkup_offered: str = ""
    schedule: ElementSchedule | None = None
    plumbing: PlumbingSpec | None = None
    #: Priced work beyond the designed openings, quoted or added on site.
    lines: list[JobLine] = Field(default_factory=list)
    payments: list[Payment] = Field(default_factory=list)
    invoices: list[InvoiceRecord] = Field(default_factory=list)
    progress_bills: list[ProgressBill] = Field(default_factory=list)
    #: Material ordered from suppliers for this job.
    material_orders: list[MaterialOrder] = Field(default_factory=list)

    #: A snapshot of the last quotation, so the job list can show money
    #: without repricing every job it displays.
    quote_total: float = 0.0
    currency: str = "ILS"
    quoted_on: str = ""

    created: str = Field(default_factory=lambda: date.today().isoformat())
    updated: str = Field(default_factory=lambda: date.today().isoformat())
    due_date: str = ""
    notes: str = ""
    history: list[StatusEvent] = Field(default_factory=list)

    # -- derived ---------------------------------------------------------- #
    @property
    def opening_count(self) -> int:
        """Distinct openings in the schedule — how many *drawings* there are."""
        if self.schedule is None:
            return 0
        return len(self.schedule.openings)

    @property
    def unit_count(self) -> int:
        """Units to be made: the same opening ordered four times is four windows."""
        if self.schedule is None:
            return 0
        return sum(max(1, opening.quantity) for opening in self.schedule.openings)

    @property
    def total_area(self) -> float:
        """Total elevation area [m²] across the schedule, quantities included."""
        if self.schedule is None:
            return 0.0
        return sum(o.area * max(1, o.quantity) for o in self.schedule.openings)

    @property
    def material_ordered(self) -> bool:
        """Whether material was ordered for it (and not every order cancelled)."""
        return any(order.state != MaterialOrderState.CANCELLED for order in self.material_orders)

    def touch(self) -> None:
        self.updated = date.today().isoformat()

    # -- transitions ------------------------------------------------------- #
    def can_advance(self, to: JobStatus) -> tuple[bool, str]:
        if to is self.status:
            return False, f"הפרויקט כבר בסטטוס {to.hebrew}"
        if to not in TRANSITIONS[self.status]:
            allowed = ", ".join(sorted(s.hebrew for s in TRANSITIONS[self.status])) or "—"
            return False, (
                f"אי אפשר לעבור מ{self.status.hebrew} ל{to.hebrew} (מותר: {allowed})"
            )
        return True, ""

    def advance(self, to: JobStatus, note: str = "") -> "JobFile":
        ok, reason = self.can_advance(to)
        if not ok:
            raise JobError(reason)
        self.status = to
        self.history.append(StatusEvent.now(to, note))
        self.touch()
        return self

    def record_quote(self, total: float, currency: str = "ILS") -> None:
        """Remember what was quoted, so the list can show it."""
        self.quote_total = round(float(total), 2)
        self.currency = currency
        self.quoted_on = date.today().isoformat()
        self.touch()

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "customer": self.customer_name,
            "status": self.status.value,
            "openings": self.opening_count,
            "units": self.unit_count,
            "area_m2": round(self.total_area, 2),
            "quote_total": self.quote_total,
            "updated": self.updated,
        }


__all__ = [
    "Customer",
    "JobError",
    "JobFile",
    "JobStatus",
    "StatusEvent",
    "TRANSITIONS",
]
