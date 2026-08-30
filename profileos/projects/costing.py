"""Did this job make money, while there is still time to do something about it.

A quotation says what the job was expected to cost. A ledger says, months
later, what it did. Between those two is where a fabricator actually lives:
the aluminium went up after the price was given, the glass came back wrong
once, the fitter went back three times, and none of that is visible until the
year is closed and it is far too late to have priced the next one differently.

So this reads the job from all four sides at once — quoted, committed,
consumed and returned — and puts them beside each other while the job is
still open. The margin it reports is not a forecast: every part of it is a
figure somebody already entered somewhere else.

Nothing here invents a cost. Where a side of the job has no data, it says so
and stays out of the arithmetic, because a margin that quietly assumes zero
for the labour nobody booked is worse than no margin at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class CostLine:
    """One contribution to what the job cost, and where the figure came from."""

    category: str
    hebrew: str
    amount: float
    source: str
    #: False when the figure is an estimate standing in for something unrecorded.
    is_actual: bool = True


@dataclass
class JobCosting:
    """The four views of one job's money."""

    job_id: str
    job_name: str = ""
    #: What the customer was quoted, net of VAT.
    quoted: float = 0.0
    #: What has been invoiced to the customer so far.
    invoiced: float = 0.0
    #: What the customer has actually paid.
    received: float = 0.0
    #: What was expected to be spent, from the priced bill of materials.
    estimated_cost: float = 0.0
    #: What has been committed on purchase orders but not yet consumed.
    committed: float = 0.0
    lines: list[CostLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    currency: str = "ILS"
    as_at: date = field(default_factory=date.today)
    #: Costs quoted in somebody else's money, by currency.
    foreign_costs: dict[str, float] = field(default_factory=dict)
    #: How much of the cost moves if the exchange rate does.
    foreign_exposure: float = 0.0

    # -- what it cost --------------------------------------------------------- #
    @property
    def actual_cost(self) -> float:
        return round(sum(line.amount for line in self.lines), 2)

    @property
    def has_estimates_standing_in(self) -> bool:
        return any(not line.is_actual for line in self.lines)

    def cost_by_category(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for line in self.lines:
            totals[line.hebrew] = round(totals.get(line.hebrew, 0.0) + line.amount, 2)
        return totals

    # -- what it earned ------------------------------------------------------- #
    @property
    def margin(self) -> float:
        """Money left on what has been invoiced, after what it cost."""
        return round(self.invoiced - self.actual_cost, 2)

    @property
    def margin_pct(self) -> float:
        if not self.invoiced:
            return 0.0
        return round(self.margin / self.invoiced * 100.0, 1)

    @property
    def quoted_margin(self) -> float:
        return round(self.quoted - self.estimated_cost, 2)

    @property
    def quoted_margin_pct(self) -> float:
        if not self.quoted:
            return 0.0
        return round(self.quoted_margin / self.quoted * 100.0, 1)

    @property
    def drift(self) -> float:
        """How far the margin has moved from the one that was quoted."""
        return round(self.margin_pct - self.quoted_margin_pct, 1)

    @property
    def unbilled(self) -> float:
        return round(self.quoted - self.invoiced, 2)

    @property
    def outstanding(self) -> float:
        return round(self.invoiced - self.received, 2)

    @property
    def is_losing(self) -> bool:
        return self.invoiced > 0 and self.margin < 0

    def verdict(self) -> str:
        if not self.invoiced and not self.actual_cost:
            return "עדיין אין תנועה בעבודה הזאת"
        if self.is_losing:
            return f"העבודה בהפסד של ⁦{abs(self.margin):,.0f}⁩ ₪"
        if self.drift < -5:
            return (
                f"הרווח ירד ב-⁦{abs(self.drift):.1f}⁩ נקודות מהמתוכנן "
                f"(⁦{self.margin_pct:.1f}%⁩ מול ⁦{self.quoted_margin_pct:.1f}%⁩)"
            )
        return f"רווח ⁦{self.margin_pct:.1f}%⁩ — כמתוכנן"

    def summary_rows(self) -> list[tuple[str, str]]:
        rows = [
            ("הוצע ללקוח", f"⁦{self.quoted:,.0f}⁩ ₪"),
            ("חויב עד כה", f"⁦{self.invoiced:,.0f}⁩ ₪"),
            ("התקבל", f"⁦{self.received:,.0f}⁩ ₪"),
            ("טרם חויב", f"⁦{self.unbilled:,.0f}⁩ ₪"),
            ("פתוח לגבייה", f"⁦{self.outstanding:,.0f}⁩ ₪"),
            ("עלות מתוכננת", f"⁦{self.estimated_cost:,.0f}⁩ ₪"),
            ("עלות בפועל", f"⁦{self.actual_cost:,.0f}⁩ ₪"),
            ("התחייבויות פתוחות", f"⁦{self.committed:,.0f}⁩ ₪"),
            ("רווח מתוכנן", f"⁦{self.quoted_margin:,.0f}⁩ ₪ · ⁦{self.quoted_margin_pct:.1f}%⁩"),
            ("רווח בפועל", f"⁦{self.margin:,.0f}⁩ ₪ · ⁦{self.margin_pct:.1f}%⁩"),
        ]
        return rows


def cost_job(
    job: Any,
    *,
    quotation: Any = None,
    company: Any = None,
    service: Any = None,
    timesheets: Any = None,
    rates: Any = None,
    labour_rate: float = 120.0,
) -> JobCosting:
    """Read one job's money from every side that has an entry for it.

    ``quotation`` supplies what was expected; ``company`` the ledger's own
    record of what was bought, invoiced and received; ``service`` the visits
    made after handover, which are the cost most often left out of a margin
    because nobody thinks of them as belonging to the job.
    """
    costing = JobCosting(
        job_id=getattr(job, "job_id", ""),
        job_name=getattr(job, "name", ""),
    )

    # -- what was promised --------------------------------------------------- #
    quoted_value = getattr(job, "quoted_value", None)
    if quoted_value:
        costing.quoted = float(quoted_value)
    if quotation is not None:
        costing.quoted = float(getattr(quotation, "net_price", costing.quoted) or 0.0)
        costing.estimated_cost = float(getattr(quotation, "cost", 0.0) or 0.0)
        material = float(getattr(quotation, "material_cost", 0.0) or 0.0)
        labour = float(getattr(quotation, "labour_cost", 0.0) or 0.0)
        if material:
            costing.lines.append(CostLine(
                "material", "חומרים — לפי ההצעה", material,
                "הצעת המחיר", is_actual=False,
            ))
        if labour:
            costing.lines.append(CostLine(
                "labour", "עבודה — לפי ההצעה", labour,
                "הצעת המחיר", is_actual=False,
            ))

    # -- what the ledger knows ------------------------------------------------ #
    if company is not None:
        actual_material = 0.0
        for invoice in getattr(company, "purchase_invoices", {}).values():
            if getattr(invoice, "project_id", None) == costing.job_id:
                actual_material += float(getattr(invoice, "net", 0)) / 100.0
        if actual_material:
            # A real figure replaces the estimate rather than joining it.
            costing.lines = [
                line for line in costing.lines if line.category != "material"
            ]
            costing.lines.append(CostLine(
                "material", "חומרים — חשבוניות ספקים", round(actual_material, 2),
                "ספר הרכש",
            ))

        for order in getattr(company, "purchase_orders", {}).values():
            if getattr(order, "project_id", None) != costing.job_id:
                continue
            state = str(getattr(order, "state", ""))
            if "invoiced" not in state and "cancelled" not in state:
                costing.committed += float(getattr(order, "net", 0)) / 100.0

        for invoice in getattr(company, "sales_invoices", {}).values():
            if getattr(invoice, "order_id", None) and costing.job_id and (
                costing.job_id not in str(invoice.order_id)
            ):
                continue
            costing.invoiced += float(getattr(invoice, "net", 0)) / 100.0
            costing.received += float(
                getattr(company, "payments_in", {}).get(invoice.invoice_id, 0)
            ) / 100.0

    # -- the hours somebody actually worked ----------------------------------- #
    # A booked hour replaces the estimate rather than joining it: once the
    # shop knows what the job really took, the figure it was quoted on stops
    # being the best answer available.
    if timesheets is not None:
        booked = timesheets.hours_on_job(costing.job_id)
        if booked:
            cost = timesheets.cost_of_job(
                costing.job_id, default_rate=labour_rate
            )
            costing.lines = [
                line for line in costing.lines if line.category != "labour"
            ]
            costing.lines.append(CostLine(
                "labour", "עבודה — שעות שנרשמו", round(cost, 2),
                f"⁦{booked:.1f}⁩ שעות בספר השעות",
            ))
            rework = timesheets.rework_share(costing.job_id)
            if rework > 10:
                costing.warnings.append(
                    f"⁦{rework:.0f}%⁩ מהשעות בעבודה הזאת היו תיקון חוזר"
                )

    # -- what came back ------------------------------------------------------- #
    if service is not None:
        calls = service.for_job(costing.job_id)
        ours = [call for call in calls if call.cause.is_ours]
        minutes = sum(call.minutes_spent for call in ours)
        if minutes:
            costing.lines.append(CostLine(
                "service", "חזרות לאתר על חשבוננו",
                round(minutes / 60.0 * labour_rate, 2),
                f"⁦{len(ours)}⁩ קריאות שירות",
            ))
        recovered = sum(call.charged for call in calls)
        if recovered:
            costing.invoiced += recovered

    # -- say what is missing rather than assuming it is nothing --------------- #
    if not costing.quoted:
        costing.warnings.append("לא נרשמה הצעת מחיר לעבודה הזאת")
    if costing.has_estimates_standing_in:
        costing.warnings.append(
            "חלק מהעלויות עדיין לפי ההצעה ולא לפי חשבוניות — הרווח הוא אומדן"
        )
    if costing.committed:
        costing.warnings.append(
            f"⁦{costing.committed:,.0f}⁩ ₪ בהזמנות רכש שטרם התקבלה עליהן חשבונית"
        )

    # -- money that is not in shekels ----------------------------------------- #
    # A job bought in euros and sold in shekels has a margin that moves with
    # the rate. Saying so is cheap; finding out from the supplier's invoice is
    # not.
    if rates is not None and costing.foreign_costs:
        exposure = rates.exposure(costing.foreign_costs)
        costing.foreign_exposure = exposure["foreign"]
        for warning in exposure["warnings"]:
            costing.warnings.append(warning)
        if exposure["share_pct"] > 25:
            costing.warnings.append(
                f"⁦{exposure['share_pct']:.0f}%⁩ מהעלות נקובה במטבע זר — "
                f"תזוזה של ⁦5%⁩ בשער שווה ⁦{exposure['if_rate_moves_5pct']:,.0f}⁩ ₪"
            )
    return costing


def portfolio(
    jobs: Any,
    *,
    company: Any = None,
    service: Any = None,
) -> list[JobCosting]:
    """Every open job's money, worst margin first — the list to read on Sunday."""
    costings = [cost_job(job, company=company, service=service) for job in jobs]
    return sorted(costings, key=lambda costing: costing.margin_pct)


__all__ = ["CostLine", "JobCosting", "cost_job", "portfolio"]
