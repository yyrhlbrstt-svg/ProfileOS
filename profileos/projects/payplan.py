"""Payment in stages: so much at signing, so much before installation, the
rest at handover.

Most Israeli window jobs are paid this way, and the money most often lost is
the middle payment: the crew goes out, the windows go in, and the payment
that should have come before is now a balance to chase. The shop sets its
plan once (System ← Operator ← payment terms); every job's stages are then
worked out from its own records — the agreed total with approved extras,
what came in, the cheques that name it — never stored, so they cannot
drift from the account.

A stage is *reached* when the job gets there: signing when it is ordered,
"before installation" once an installation is booked (or done), handover
once it is installed. What is due now is everything reached, less what is
paid or promised by a cheque.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: (key, Hebrew name), in the order the money comes.
STAGES: tuple[tuple[str, str], ...] = (
    ("signing", "בחתימה"),
    ("before_install", "לפני התקנה"),
    ("handover", "במסירה"),
)

_ORDERED = ("won", "in_production", "installed")


def shop_plan(settings: Any = None) -> dict[str, float]:
    """The shop's plan in percent, or ``{}`` when it has none (or it does not
    add up to a hundred — a plan that asks for 90% is not a plan)."""
    if settings is None:
        from ..core.config import get_settings

        settings = get_settings()
    plan = {key: float(value) for key, value in
            dict(getattr(settings, "payment_plan", {}) or {}).items()
            if key in dict(STAGES) and float(value or 0) > 0}
    return plan if plan and abs(sum(plan.values()) - 100.0) < 0.01 else {}


@dataclass(frozen=True)
class StageDue:
    key: str
    hebrew: str
    pct: float
    amount: float          # gross, with VAT
    until: float           # this stage and those before it, gross
    reached: bool
    covered: float         # of this stage, paid or promised by a cheque

    @property
    def open(self) -> float:
        return round(max(0.0, self.amount - self.covered), 2)


@dataclass(frozen=True)
class PlanView:
    stages: tuple[StageDue, ...]
    due_now: float          # reached and not paid or promised

    @property
    def current(self) -> StageDue | None:
        """The stage the money is waiting on now, if any."""
        for stage in self.stages:
            if stage.reached and stage.open > 0.5:
                return stage
        return None

    def describe(self) -> str:
        parts = []
        for stage in self.stages:
            mark = "✓" if stage.open <= 0.5 else ("לגבות" if stage.reached else "")
            parts.append(f"{stage.hebrew} ⁦{stage.pct:g}%⁩ {mark}".strip())
        line = " · ".join(parts)
        if self.due_now > 0.5:
            line += f" — לגבות עכשיו ⁦{self.due_now:,.0f} ₪⁩"
        return line


def reached(job: Any, key: str, *, install_booked: bool = False) -> bool:
    status = getattr(job.status, "value", job.status)
    if key == "signing":
        return status in _ORDERED
    if key == "before_install":
        return status == "installed" or (status in _ORDERED and install_booked)
    return status == "installed"


def view(job: Any, cheques: Iterable[Any] = (), *, plan: dict[str, float] | None = None,
         install_booked: bool = False, account: Any = None) -> PlanView | None:
    """The job's stages under the shop's plan; ``None`` without a plan or a sum."""
    from .account import job_account

    plan = shop_plan() if plan is None else plan
    if not plan:
        return None
    account = account or job_account(job, list(cheques))
    total = account.total_gross
    if total <= 0:
        return None
    # Money in hand or promised by a cheque pays the stages in order.
    pool = account.received + account.in_cheques
    stages = []
    until = 0.0
    for key, hebrew in STAGES:
        pct = plan.get(key, 0.0)
        if pct <= 0:
            continue
        amount = round(total * pct / 100.0, 2)
        until = round(until + amount, 2)
        covered = round(min(amount, max(0.0, pool)), 2)
        pool -= covered
        stages.append(StageDue(key, hebrew, pct, amount, until,
                               reached(job, key, install_booked=install_booked), covered))
    due = round(sum(stage.open for stage in stages if stage.reached), 2)
    return PlanView(tuple(stages), due)


def install_booked(job_id: str, agenda: Any) -> bool:
    """Whether an installation visit for this job is on the agenda, not done."""
    if agenda is None:
        return False
    try:
        from .agenda import VisitKind

        return any(visit.job_id == job_id and not visit.done
                   and visit.kind in (VisitKind.INSTALLATION, VisitKind.DELIVERY)
                   for visit in agenda)
    except Exception:  # noqa: BLE001 - an unreadable agenda books nothing
        return False


__all__ = ["PlanView", "STAGES", "StageDue", "install_booked", "reached", "shop_plan", "view"]
