"""Interim bills to a main contractor, worked out the way the contractor does.

A contractor never pays the job; they pay a cumulative percentage approved
each month, hold back a retention until handover, and deduct tax at source.
Each bill is therefore the difference between this month's approved total and
last month's — get one month wrong and every later bill inherits it, which is
why the figures are recomputed from the list every time rather than stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.errors import ProfileOSError
from .account import VAT_RATE, job_account


@dataclass(frozen=True)
class BillFigures:
    number: int
    on: str
    cumulative_pct: float
    #: Approved to date, net of VAT.
    cumulative_net: float
    #: This bill: what the new percentage adds, net of VAT.
    this_net: float
    vat: float
    retention: float
    withholding: float

    @property
    def to_pay(self) -> float:
        """What the contractor should transfer for this bill."""
        return round(self.this_net + self.vat - self.retention - self.withholding, 2)


def bill_figures(job: Any, *, vat_rate: float = VAT_RATE) -> list[BillFigures]:
    """Every interim bill on the job, in order, with its money worked out."""
    contract = job_account(job, vat_rate=vat_rate).total_net
    figures: list[BillFigures] = []
    previous = 0.0
    for bill in sorted(job.progress_bills, key=lambda b: b.number):
        cumulative = round(contract * bill.cumulative_pct / 100.0, 2)
        this = round(cumulative - previous, 2)
        figures.append(BillFigures(
            number=bill.number, on=bill.on, cumulative_pct=bill.cumulative_pct,
            cumulative_net=cumulative, this_net=this,
            vat=round(this * vat_rate, 2),
            retention=round(this * bill.retention_pct / 100.0, 2),
            withholding=round(this * bill.withholding_pct / 100.0, 2),
        ))
        previous = cumulative
    return figures


def retention_held(job: Any) -> float:
    """Retention held back so far — owed at handover, and easy to forget."""
    return round(sum(figure.retention for figure in bill_figures(job)), 2)


def next_bill(job: Any, cumulative_pct: float, *, retention_pct: float = 5.0,
              withholding_pct: float = 0.0, approved_by: str = "") -> Any:
    """The next bill, refused if the percentage does not move forward."""
    from .model import ProgressBill

    last = max((bill.cumulative_pct for bill in job.progress_bills), default=0.0)
    if not 0 < cumulative_pct <= 100:
        raise ProfileOSError("האחוז המצטבר חייב להיות בין ⁦0⁩ ל־⁦100⁩")
    if cumulative_pct <= last:
        raise ProfileOSError(
            f"החשבון הקודם כבר אושר על ⁦{last:g}%⁩ — חשבון מצטבר חייב להיות גבוה ממנו"
        )
    if not 0 <= retention_pct <= 20 or not 0 <= withholding_pct <= 50:
        raise ProfileOSError("אחוז עיכבון או ניכוי לא סביר — בדקו שוב")
    number = max((bill.number for bill in job.progress_bills), default=0) + 1
    return ProgressBill(
        number=number, cumulative_pct=cumulative_pct, retention_pct=retention_pct,
        withholding_pct=withholding_pct, approved_by=approved_by,
    )


def bill_text(job: Any, figure: BillFigures, *, brand_name: str) -> str:
    """The bill as a message the contractor's office can check line by line."""
    return "\n".join([
        f"חשבון חלקי מס׳ ⁦{figure.number}⁩ — {job.name}",
        f"מאת: {brand_name}",
        "",
        f"אושר מצטבר: ⁦{figure.cumulative_pct:g}%⁩ — ⁦{figure.cumulative_net:,.2f} ₪⁩",
        f"חשבון זה לפני מע״מ: ⁦{figure.this_net:,.2f} ₪⁩",
        f"מע״מ: ⁦{figure.vat:,.2f} ₪⁩",
        f"עיכבון: ⁦{figure.retention:,.2f} ₪⁩",
        f"ניכוי במקור: ⁦{figure.withholding:,.2f} ₪⁩",
        f"לתשלום: ⁦{figure.to_pay:,.2f} ₪⁩",
    ])


__all__ = ["BillFigures", "bill_figures", "bill_text", "next_bill", "retention_held"]
