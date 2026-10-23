"""The customer said yes: what changes on the job, in one place.

Said at the desk (the status set to won) or on the phone at the customer's
table (the quotation signed there), the same things follow: the version the
customer agreed to is recorded, the job asks for what a window cannot be made
without, and the quotation's chase calls are closed.
"""

from __future__ import annotations

from typing import Any


def win(job: Any, *, revision: int = 0, note: str = "") -> Any:
    """Move the job to won, with the version agreed (0 when unknown)."""
    from .model import CustomerNeed, JobStatus

    job.advance(JobStatus.WON, note)
    if revision:
        job.accepted_revision = revision
    # What a window cannot be made without: asked for now, in one list,
    # not discovered when the bars are on the saw.
    if not job.waiting_for and job.trade.value == "aluminium":
        job.waiting_for = [CustomerNeed(need=kind)
                           for kind in ("measurement", "colour", "glass_choice")]
    return job


def settle_chases(job_id: str, *, won: bool, reason: str = "") -> None:
    """The customer decided: the quotation's chase calls are over."""
    from .followups import Outcome, default_tasks

    default_tasks().settle_quote(
        job_id, Outcome.DONE if won else Outcome.LOST,
        result="נסגר" if won else f"לא נסגר: {reason}".strip(": "),
    )


__all__ = ["settle_chases", "win"]
