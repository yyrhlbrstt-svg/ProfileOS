"""What the shop is confirming it will make, and what it is still waiting for.

The gap that costs a fabricator most of its lost weeks is not in the drawing
office. It is between the customer saying yes and the first bar being cut: the
final measurement was never taken, the colour was never chosen, the deposit
never arrived, and nobody wrote down that the promised date assumed all three.
Four weeks later the customer asks why nothing has been made and the shop finds
it has been waiting on an answer it never asked for in writing.

An order confirmation here is therefore two documents in one. The first half is
what everybody's software prints: what was ordered, at what price, with VAT.
The second half is the half that matters — the list of things the shop needs
from the customer, each with a date, and a promised delivery that is computed
from the shop's own working calendar starting from **when the last of them
arrives**, not from today.

That is the rule this module will not bend: a promised date that assumes
something the shop does not have is marked provisional, in the document, in
the customer's copy. A firm date is only printed when there is nothing left to
wait for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.order_confirmation")


class Need(StrEnum):
    """The things a shop routinely waits on, named the way it says them."""

    MEASUREMENT = "measurement"
    COLOUR = "colour"
    GLASS_CHOICE = "glass_choice"
    DEPOSIT = "deposit"
    SITE_ACCESS = "site_access"
    OPENINGS_READY = "openings_ready"
    DRAWING_APPROVAL = "drawing_approval"
    PERMIT = "permit"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            "measurement": "מדידה סופית באתר",
            "colour": "בחירת גוון",
            "glass_choice": "בחירת זכוכית",
            "deposit": "מקדמה",
            "site_access": "גישה לאתר",
            "openings_ready": "פתחים מוכנים במבנה",
            "drawing_approval": "אישור שרטוטים",
            "permit": "היתר / אישור",
            "other": "אחר",
        }[self.value]

    @property
    def blocks_production(self) -> bool:
        """Whether the shop physically cannot start without it.

        Site access does not stop a bar being cut; a final measurement does,
        and a colour does the moment the profile goes to the coater. The
        distinction decides which items make a date provisional and which
        merely make it uncomfortable.
        """
        return self in (
            Need.MEASUREMENT, Need.COLOUR, Need.GLASS_CHOICE,
            Need.DEPOSIT, Need.DRAWING_APPROVAL,
        )


@dataclass
class Prerequisite:
    """One thing the shop needs from somebody else before it can proceed."""

    need: Need = Need.OTHER
    detail: str = ""
    #: Whose it is: usually the customer, sometimes the architect or builder.
    owed_by: str = "הלקוח"
    requested_on: date = field(default_factory=date.today)
    due: date | None = None
    received_on: date | None = None
    note: str = ""

    @property
    def is_received(self) -> bool:
        return self.received_on is not None

    @property
    def is_overdue(self) -> bool:
        return (
            not self.is_received
            and self.due is not None
            and self.due < date.today()
        )

    @property
    def label(self) -> str:
        return self.detail or self.need.hebrew

    def describe(self) -> str:
        if self.is_received:
            return (
                f"{self.label} · התקבל ⁦{self.received_on.strftime('%d/%m/%Y')}⁩"
            )
        when = (
            f" · עד ⁦{self.due.strftime('%d/%m/%Y')}⁩" if self.due else ""
        )
        late = " · באיחור" if self.is_overdue else ""
        return f"{self.label} · באחריות {self.owed_by}{when}{late}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "need": self.need.value, "detail": self.detail,
            "owed_by": self.owed_by,
            "requested_on": self.requested_on.isoformat(),
            "due": self.due.isoformat() if self.due else None,
            "received_on": (
                self.received_on.isoformat() if self.received_on else None
            ),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Prerequisite":
        def _day(key: str) -> date | None:
            value = raw.get(key)
            return date.fromisoformat(str(value)) if value else None

        return cls(
            need=Need(str(raw.get("need", "other"))),
            detail=str(raw.get("detail", "")),
            owed_by=str(raw.get("owed_by", "הלקוח")),
            requested_on=_day("requested_on") or date.today(),
            due=_day("due"),
            received_on=_day("received_on"),
            note=str(raw.get("note", "")),
        )


@dataclass
class OrderConfirmation:
    """What was ordered, what it costs, and what the shop is still waiting for."""

    confirmation_id: str = field(
        default_factory=lambda: f"OC-{uuid4().hex[:6].upper()}"
    )
    job_id: str = ""
    job_name: str = ""
    customer_name: str = ""
    customer_reference: str = ""
    site_address: str = ""
    issued: date = field(default_factory=date.today)
    #: The quotation revision this confirms, so the price has a provenance.
    quote_revision: int = 0
    lines: list[dict[str, Any]] = field(default_factory=list)
    net: float = 0.0
    vat_rate: float = 0.18
    #: The deposit the shop is asking for, as a share of the gross.
    deposit_pct: float = 0.0
    deposit_received: float = 0.0
    #: Working days of production once everything is in hand.
    lead_working_days: int = 0
    prerequisites: list[Prerequisite] = field(default_factory=list)
    accepted_on: date | None = None
    accepted_by: str = ""
    note: str = ""

    # -- money ---------------------------------------------------------------- #
    @property
    def vat(self) -> float:
        return round(self.net * self.vat_rate, 2)

    @property
    def gross(self) -> float:
        return round(self.net + self.vat, 2)

    @property
    def deposit_due(self) -> float:
        return round(self.gross * self.deposit_pct / 100.0, 2)

    @property
    def deposit_outstanding(self) -> float:
        return round(max(0.0, self.deposit_due - self.deposit_received), 2)

    # -- what is outstanding --------------------------------------------------- #
    @property
    def outstanding(self) -> list[Prerequisite]:
        return [item for item in self.prerequisites if not item.is_received]

    @property
    def blocking(self) -> list[Prerequisite]:
        """The outstanding items that actually stop production starting."""
        blockers = []
        for item in self.outstanding:
            if not item.need.blocks_production:
                continue
            # The money answers for itself: once the deposit is in, the line
            # asking for it is satisfied whether or not anybody ticked it.
            if item.need is Need.DEPOSIT and self.deposit_outstanding <= 0:
                continue
            blockers.append(item)
        if self.deposit_outstanding > 0 and not any(
            item.need is Need.DEPOSIT for item in blockers
        ):
            blockers.append(Prerequisite(
                need=Need.DEPOSIT,
                detail=f"מקדמה ⁦{self.deposit_outstanding:,.0f}⁩ ₪",
            ))
        return blockers

    @property
    def is_clear_to_start(self) -> bool:
        return not self.blocking

    def receive(self, need: Need, *, on: date | None = None) -> Prerequisite:
        """Mark one of the things the shop was waiting for as arrived."""
        for item in self.prerequisites:
            if item.need is need and not item.is_received:
                item.received_on = on or date.today()
                return item
        raise ProfileOSError(
            f"אין פריט פתוח מסוג {need.hebrew} באישור {self.confirmation_id}"
        )

    # -- the promised date ----------------------------------------------------- #
    def start_date(self, *, on: date | None = None) -> date:
        """The earliest day work could begin: when the last blocker clears.

        With nothing outstanding this is today. With a measurement due next
        Thursday it is next Thursday, and the promise is counted from there —
        which is the arithmetic every shop does in its head and none of them
        writes down.
        """
        today = on or date.today()
        dates = [today]
        for item in self.blocking:
            dates.append(item.due or today)
        return max(dates)

    def promised_date(
        self, *, calendar: Any = None, on: date | None = None
    ) -> date:
        """The delivery date, counted in the shop's own working days.

        Not calendar days: a fortnight over Tishrei is not a fortnight, and a
        date promised on a Saturday is a date nobody meant.
        """
        if calendar is None:
            from .scheduling import Calendar

            calendar = Calendar.israeli()

        start = calendar.next_working_day(self.start_date(on=on))
        if self.lead_working_days <= 0:
            return start
        return calendar.working_days(start, self.lead_working_days)[-1]

    @property
    def date_is_firm(self) -> bool:
        """A date is firm only when nothing is still being waited on."""
        return self.is_clear_to_start

    def date_line(self, *, calendar: Any = None, on: date | None = None) -> str:
        """The sentence that goes on the customer's copy."""
        when = self.promised_date(calendar=calendar, on=on)
        stamp = f"⁦{when.strftime('%d/%m/%Y')}⁩"
        if self.date_is_firm:
            return f"מועד אספקה: {stamp}"
        waiting = ", ".join(item.label for item in self.blocking[:3])
        return (
            f"מועד אספקה משוער: {stamp} — מותנה בקבלת {waiting}. "
            "המועד ייקבע סופית עם קבלתם."
        )

    # -- checking -------------------------------------------------------------- #
    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.customer_name.strip():
            found.append("חסר שם הלקוח")
        if not self.lines:
            found.append("אין שורות באישור ההזמנה")
        if self.net <= 0:
            found.append("אין סכום באישור ההזמנה")
        if not self.quote_revision:
            found.append(
                "האישור אינו מפנה לגרסת הצעת מחיר — לא יהיה אפשר להוכיח "
                "על מה סוכם"
            )
        if self.lead_working_days <= 0:
            found.append("לא נקבע זמן ייצור — אי אפשר לחשב מועד אספקה")
        if self.deposit_pct and not self.deposit_due:
            found.append("נקבע אחוז מקדמה אבל הסכום יוצא אפס")
        for item in self.outstanding:
            if item.need.blocks_production and item.due is None:
                found.append(
                    f"{item.label} חוסם ייצור ואין לו תאריך יעד — "
                    "מועד האספקה נשען על אוויר"
                )
        return found

    @property
    def may_be_issued(self) -> bool:
        return not self.problems()

    def _deposit_line(self) -> str:
        if not self.deposit_pct:
            return "—"
        state = (
            f"טרם התקבלו ⁦{self.deposit_outstanding:,.2f}⁩ ₪"
            if self.deposit_outstanding
            else "התקבלה"
        )
        return f"⁦{self.deposit_due:,.2f}⁩ ₪ · {state}"

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("אישור הזמנה", self.confirmation_id),
            ("תיק", f"{self.job_id} · {self.job_name}"),
            ("לקוח", self.customer_name),
            ("הזמנת הלקוח", self.customer_reference or "—"),
            ("על בסיס", f"הצעת מחיר גרסה ⁦{self.quote_revision}⁩"),
            ("לפני מע״מ", f"⁦{self.net:,.2f}⁩ ₪"),
            ("מע״מ ⁦{:.0%}⁩".format(self.vat_rate), f"⁦{self.vat:,.2f}⁩ ₪"),
            ("סה״כ", f"⁦{self.gross:,.2f}⁩ ₪"),
            ("מקדמה", self._deposit_line()),
            ("זמן ייצור", f"⁦{self.lead_working_days}⁩ ימי עבודה"),
        ]

    def describe(self) -> str:
        return (
            f"{self.confirmation_id} · {self.customer_name} · "
            f"⁦{self.gross:,.0f}⁩ ₪ · "
            + (
                "מוכן לייצור"
                if self.is_clear_to_start
                else f"ממתין ל-⁦{len(self.blocking)}⁩ פריטים"
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "job_id": self.job_id, "job_name": self.job_name,
            "customer_name": self.customer_name,
            "customer_reference": self.customer_reference,
            "site_address": self.site_address,
            "issued": self.issued.isoformat(),
            "quote_revision": self.quote_revision,
            "lines": self.lines, "net": self.net, "vat_rate": self.vat_rate,
            "deposit_pct": self.deposit_pct,
            "deposit_received": self.deposit_received,
            "lead_working_days": self.lead_working_days,
            "prerequisites": [item.as_dict() for item in self.prerequisites],
            "accepted_on": (
                self.accepted_on.isoformat() if self.accepted_on else None
            ),
            "accepted_by": self.accepted_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OrderConfirmation":
        accepted = raw.get("accepted_on")
        return cls(
            confirmation_id=str(raw.get("confirmation_id", "")),
            job_id=str(raw.get("job_id", "")),
            job_name=str(raw.get("job_name", "")),
            customer_name=str(raw.get("customer_name", "")),
            customer_reference=str(raw.get("customer_reference", "")),
            site_address=str(raw.get("site_address", "")),
            issued=date.fromisoformat(
                str(raw.get("issued", date.today().isoformat()))
            ),
            quote_revision=int(raw.get("quote_revision", 0)),
            lines=list(raw.get("lines", [])),
            net=float(raw.get("net", 0.0)),
            vat_rate=float(raw.get("vat_rate", 0.18)),
            deposit_pct=float(raw.get("deposit_pct", 0.0)),
            deposit_received=float(raw.get("deposit_received", 0.0)),
            lead_working_days=int(raw.get("lead_working_days", 0)),
            prerequisites=[
                Prerequisite.from_dict(item)
                for item in raw.get("prerequisites", [])
            ],
            accepted_on=date.fromisoformat(str(accepted)) if accepted else None,
            accepted_by=str(raw.get("accepted_by", "")),
            note=str(raw.get("note", "")),
        )


#: What a dwelling job normally waits on, so nobody has to remember the list.
STANDARD_PREREQUISITES: tuple[tuple[Need, int], ...] = (
    (Need.DEPOSIT, 7),
    (Need.MEASUREMENT, 10),
    (Need.COLOUR, 10),
    (Need.GLASS_CHOICE, 14),
)


def confirm_order(
    job: Any, *, revision: Any = None, net: float = 0.0,
    lead_working_days: int = 0, deposit_pct: float = 0.0,
    needs: Iterable[Need] | None = None, vat_rate: float = 0.18,
    on: date | None = None,
) -> OrderConfirmation:
    """Build an order confirmation from a job and the revision it rests on.

    ``needs`` defaults to the standard list rather than to nothing: a shop
    that has to remember to ask for the deposit is a shop that sometimes does
    not.
    """
    today = on or date.today()
    confirmation = OrderConfirmation(
        job_id=str(getattr(job, "job_id", "")),
        job_name=str(getattr(job, "name", "")),
        customer_name=str(getattr(job, "customer_name", "")),
        customer_reference=str(getattr(job, "reference", "")),
        site_address=str(getattr(job, "site_address", "")),
        issued=today,
        quote_revision=int(getattr(revision, "number", 0) or 0),
        net=round(
            float(net or getattr(revision, "net_price", 0.0)
                  or getattr(job, "quote_total", 0.0)),
            2,
        ),
        vat_rate=vat_rate,
        deposit_pct=deposit_pct,
        lead_working_days=lead_working_days,
    )

    if revision is not None and getattr(revision, "lines", None):
        confirmation.lines = [
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": line.unit_price,
                "total": line.total,
            }
            for line in revision.lines
        ]

    wanted = list(needs) if needs is not None else [
        need for need, _days in STANDARD_PREREQUISITES
    ]
    default_days = dict(STANDARD_PREREQUISITES)
    for need in wanted:
        if need is Need.DEPOSIT and not deposit_pct:
            continue
        confirmation.prerequisites.append(Prerequisite(
            need=need,
            requested_on=today,
            due=today + timedelta(days=default_days.get(need, 14)),
        ))

    _log.info("Order confirmation %s built for %s",
              confirmation.confirmation_id, confirmation.job_id)
    return confirmation


__all__ = [
    "STANDARD_PREREQUISITES",
    "Need",
    "OrderConfirmation",
    "Prerequisite",
    "confirm_order",
]
