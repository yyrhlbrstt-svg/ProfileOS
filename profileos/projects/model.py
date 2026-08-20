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
    schedule: ElementSchedule | None = None

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
