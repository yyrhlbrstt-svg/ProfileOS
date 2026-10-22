"""What a job is worth, what has come in, and what is still owed.

Every number a fabricator needs at the end of a job already exists somewhere
in this software — the quoted figure on the job, the extras agreed on site,
the cheques in the drawer with the job's number on them, a transfer, the tax
a contractor withheld — and none of them were added up in one place. So the
last ten or twenty percent after installation was the money nobody chased.

The account is computed, never stored: it is read from the records each time,
so it cannot drift from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: Israeli VAT, the rate the quotations are printed with.
VAT_RATE = 0.18


@dataclass(frozen=True)
class JobAccount:
    """One job's money, in shekels. Net figures are before VAT."""

    job_id: str
    #: What was agreed: the quotation, or the quoted work lines if no
    #: quotation was issued.
    contract_net: float
    extras_net: float
    #: Extras written down but not agreed — not owed until they are.
    pending_extras_net: float
    vat: float
    #: Received: transfers, cash, card.
    paid: float
    #: Deducted at source by the customer — credited, not owed.
    withheld: float
    #: Cheques banked and cleared.
    cleared: float
    #: Cheques in the drawer, not yet money.
    in_cheques: float
    #: Cheques that came back. Still owed.
    bounced: float
    invoiced_net: float

    @property
    def total_net(self) -> float:
        return round(self.contract_net + self.extras_net, 2)

    @property
    def total_gross(self) -> float:
        return round(self.total_net + self.vat, 2)

    @property
    def received(self) -> float:
        """Money and credits actually in hand."""
        return round(self.paid + self.withheld + self.cleared, 2)

    @property
    def balance(self) -> float:
        """Still to come in, counting post-dated cheques as on their way."""
        return round(self.total_gross - self.received - self.in_cheques, 2)

    @property
    def uncovered(self) -> float:
        """Owed and not even promised by a cheque — the figure to chase."""
        return round(max(0.0, self.balance), 2)

    @property
    def not_invoiced_net(self) -> float:
        return round(max(0.0, self.total_net - self.invoiced_net), 2)

    def describe(self) -> str:
        if self.total_gross <= 0:
            return "אין עדיין סכום לפרויקט"
        if self.uncovered <= 0.5:
            return f"שולם במלואו · ⁦{self.total_gross:,.0f} ₪⁩"
        return (
            f"נותר לגבות ⁦{self.uncovered:,.0f} ₪⁩ מתוך ⁦{self.total_gross:,.0f} ₪⁩"
        )


def job_account(job: Any, cheques: Iterable[Any] = (), *, vat_rate: float = VAT_RATE) -> JobAccount:
    """Add up one job from its own records and the cheques that name it."""
    from .model import LineKind

    lines = list(getattr(job, "lines", []) or [])
    quoted_lines = sum(line.total for line in lines if line.kind is LineKind.QUOTED)
    # The quotation already includes the quoted work lines when one was issued
    # from this job; with no quotation the lines are the agreement.
    contract = float(getattr(job, "quote_total", 0.0) or 0.0) or quoted_lines
    extras = sum(line.total for line in lines if line.kind is LineKind.EXTRA and line.approved)
    pending = sum(line.total for line in lines if line.kind is LineKind.EXTRA and not line.approved)
    net = contract + extras

    payments = list(getattr(job, "payments", []) or [])
    paid = sum(payment.amount for payment in payments)
    withheld = sum(payment.withheld for payment in payments)

    cleared = in_cheques = bounced = 0.0
    for cheque in cheques:
        if getattr(cheque, "job_id", "") != job.job_id:
            continue
        state = getattr(cheque.state, "value", str(cheque.state))
        if state == "cleared":
            cleared += cheque.amount
        elif state == "bounced":
            bounced += cheque.amount
        elif state in ("held", "deposited"):
            in_cheques += cheque.amount

    invoiced = sum(invoice.net for invoice in getattr(job, "invoices", []) or [])
    return JobAccount(
        job_id=job.job_id,
        contract_net=round(contract, 2),
        extras_net=round(extras, 2),
        pending_extras_net=round(pending, 2),
        vat=round(net * vat_rate, 2),
        paid=round(paid, 2),
        withheld=round(withheld, 2),
        cleared=round(cleared, 2),
        in_cheques=round(in_cheques, 2),
        bounced=round(bounced, 2),
        invoiced_net=round(invoiced, 2),
    )


def invoices_missing_allocation(job: Any) -> list[Any]:
    """Business invoices over the allocation threshold with no allocation number.

    The threshold is the one kept in :mod:`profileos.erp.israel`, which the
    bookkeeper confirms — not a second copy of it here.
    """
    from ..erp.israel import DEFAULT_ALLOCATION_RULE

    threshold = DEFAULT_ALLOCATION_RULE.threshold
    return [
        invoice for invoice in getattr(job, "invoices", []) or []
        if invoice.business_customer and invoice.net > threshold
        and not invoice.allocation_number.strip()
    ]


__all__ = [
    "JobAccount",
    "VAT_RATE",
    "invoices_missing_allocation",
    "job_account",
]
