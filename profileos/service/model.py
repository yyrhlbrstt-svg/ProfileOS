"""After the window is in the wall.

Every package in this trade stops at delivery, and every fabricator's actual
week is half made of what happens afterwards: a sash that drops in March, a
seal that whistles, a slider that a customer says "was never like that", a
handle a builder broke and would like called a warranty claim.

That work is where the shop's reputation and a surprising amount of its money
go, and it is kept on paper — so nobody knows how many calls a system attracts,
which fitter's jobs come back, or whether a claim is still under warranty
until somebody digs out the delivery note.

This is that register. A call names the job and, where it is known, the exact
element, so a recurring fault is visible as a pattern rather than as a run of
unrelated annoyances. Warranty is computed from the delivery date and the
component, not decided in the moment. And the fault, once diagnosed, is
recorded as a cause — because the fifth call about the same cause is a message
to the workshop, not to the fitter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4


class Severity(StrEnum):
    """How fast somebody has to go out."""

    BLOCKING = "blocking"
    URGENT = "urgent"
    NORMAL = "normal"
    COSMETIC = "cosmetic"

    @property
    def hebrew(self) -> str:
        return {
            "blocking": "הדירה לא סגורה",
            "urgent": "דחוף",
            "normal": "רגיל",
            "cosmetic": "אסתטי",
        }[self.value]

    @property
    def response_days(self) -> int:
        """Working days to be there, which is what the shop is judged on."""
        return {"blocking": 1, "urgent": 2, "normal": 7, "cosmetic": 21}[self.value]


class Symptom(StrEnum):
    """What the customer says on the phone, in their words."""

    WATER = "water"
    DRAUGHT = "draught"
    NOISE = "noise"
    STIFF = "stiff"
    DROPPED = "dropped"
    BROKEN_GLASS = "broken_glass"
    MISTED_UNIT = "misted_unit"
    HANDLE = "handle"
    SHUTTER = "shutter"
    SCREEN = "screen"
    FINISH = "finish"
    CONDENSATION = "condensation"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            "water": "חדירת מים",
            "draught": "רוח נכנסת",
            "noise": "רעש",
            "stiff": "קשה לפתוח",
            "dropped": "כנף צנחה",
            "broken_glass": "זכוכית שבורה",
            "misted_unit": "אדים בין השמשות",
            "handle": "ידית או פרזול",
            "shutter": "תריס",
            "screen": "רשת",
            "finish": "גימור, צבע או שריטה",
            "condensation": "עיבוי על הזכוכית",
            "other": "אחר",
        }[self.value]

    @property
    def likely_severity(self) -> Severity:
        return {
            "water": Severity.URGENT,
            "draught": Severity.NORMAL,
            "noise": Severity.NORMAL,
            "stiff": Severity.NORMAL,
            "dropped": Severity.URGENT,
            "broken_glass": Severity.BLOCKING,
            "misted_unit": Severity.NORMAL,
            "handle": Severity.NORMAL,
            "shutter": Severity.NORMAL,
            "screen": Severity.COSMETIC,
            "finish": Severity.COSMETIC,
            "condensation": Severity.COSMETIC,
            "other": Severity.NORMAL,
        }[self.value]


class Cause(StrEnum):
    """What it turned out to be — the field that makes the register worth keeping."""

    UNKNOWN = "unknown"
    MANUFACTURE = "manufacture"
    INSTALLATION = "installation"
    DESIGN = "design"
    COMPONENT = "component"
    BUILDING = "building"
    CUSTOMER = "customer"
    WEAR = "wear"
    NO_FAULT = "no_fault"

    @property
    def hebrew(self) -> str:
        return {
            "unknown": "טרם אובחן",
            "manufacture": "ייצור",
            "installation": "הרכבה",
            "design": "תכנון",
            "component": "רכיב מספק",
            "building": "הבניין — טיח, איטום, שיפועים",
            "customer": "שימוש או נזק של הלקוח",
            "wear": "בלאי סביר",
            "no_fault": "אין תקלה",
        }[self.value]

    @property
    def is_ours(self) -> bool:
        """Whether the shop pays for it, which is the only question that matters."""
        return self in (Cause.MANUFACTURE, Cause.INSTALLATION, Cause.DESIGN)

    @property
    def is_chargeable(self) -> bool:
        """Whether the customer can be invoiced for the visit."""
        return self in (Cause.CUSTOMER, Cause.BUILDING, Cause.NO_FAULT)


class CallState(StrEnum):
    OPEN = "open"
    SCHEDULED = "scheduled"
    WAITING_PARTS = "waiting_parts"
    DONE = "done"
    REJECTED = "rejected"

    @property
    def hebrew(self) -> str:
        return {
            "open": "פתוחה",
            "scheduled": "נקבע ביקור",
            "waiting_parts": "ממתין לחלק",
            "done": "טופלה",
            "rejected": "נדחתה",
        }[self.value]

    @property
    def is_open(self) -> bool:
        return self in (CallState.OPEN, CallState.SCHEDULED, CallState.WAITING_PARTS)


#: How long each part of the work is warranted for, in months. These are the
#: shop's own terms, not a statute: they are what goes in writing on the
#: quotation, and they are here so that the same numbers answer the phone.
WARRANTY_MONTHS: dict[str, int] = {
    "fabrication": 24,
    "installation": 24,
    "sealed_unit": 60,
    "hardware": 24,
    "motor": 24,
    "shutter": 24,
    "screen": 12,
    "finish": 60,
    "glass_breakage": 0,
}

WARRANTY_HEBREW: dict[str, str] = {
    "fabrication": "ייצור",
    "installation": "הרכבה",
    "sealed_unit": "זכוכית בידודית — אטימות",
    "hardware": "פרזול",
    "motor": "מנוע תריס",
    "shutter": "תריס",
    "screen": "רשת",
    "finish": "גימור וצבע",
    "glass_breakage": "שבר זכוכית — אינו באחריות",
}

#: Which warranty each symptom is claimed against.
SYMPTOM_WARRANTY: dict[str, str] = {
    "water": "installation",
    "draught": "fabrication",
    "noise": "fabrication",
    "stiff": "hardware",
    "dropped": "hardware",
    "broken_glass": "glass_breakage",
    "misted_unit": "sealed_unit",
    "handle": "hardware",
    "shutter": "shutter",
    "screen": "screen",
    "finish": "finish",
    "condensation": "fabrication",
    "other": "fabrication",
}


def warranty_expires(delivered: date, component: str) -> date | None:
    """When cover for this component runs out. ``None`` means never covered."""
    months = WARRANTY_MONTHS.get(component, 24)
    if months <= 0:
        return None
    year = delivered.year + (delivered.month - 1 + months) // 12
    month = (delivered.month - 1 + months) % 12 + 1
    day = min(delivered.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30,
                              31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


@dataclass
class Visit:
    """One trip to the site."""

    on: date
    engineer: str = ""
    minutes: int = 0
    note: str = ""
    resolved: bool = False


@dataclass
class ServiceCall:
    """One thing a customer rang about."""

    call_id: str = field(default_factory=lambda: f"SC-{uuid4().hex[:6].upper()}")
    job_id: str = ""
    customer_name: str = ""
    element_id: str = ""
    element_name: str = ""
    symptom: Symptom = Symptom.OTHER
    description: str = ""
    severity: Severity | None = None
    opened: date = field(default_factory=date.today)
    #: When the job was handed over — what warranty is counted from.
    delivered: date | None = None
    state: CallState = CallState.OPEN
    cause: Cause = Cause.UNKNOWN
    visits: list[Visit] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    closed: date | None = None
    #: What the visit was invoiced at, when it was chargeable.
    charged: float = 0.0
    site: str = ""
    phone: str = ""

    def __post_init__(self) -> None:
        if self.severity is None:
            self.severity = self.symptom.likely_severity

    @property
    def component(self) -> str:
        return SYMPTOM_WARRANTY.get(self.symptom.value, "fabrication")

    @property
    def warranty_component_hebrew(self) -> str:
        return WARRANTY_HEBREW.get(self.component, self.component)

    def warranty_until(self) -> date | None:
        if self.delivered is None:
            return None
        return warranty_expires(self.delivered, self.component)

    @property
    def under_warranty(self) -> bool | None:
        """``None`` when nobody has said when the job was handed over.

        Deliberately three-valued: "we do not know" is a different answer from
        "no", and the shop that treats them the same either gives work away or
        argues with a customer who was right.
        """
        if self.delivered is None:
            return None
        expires = self.warranty_until()
        if expires is None:
            return False
        return self.opened <= expires

    def due_by(self, calendar: Any = None) -> date:
        """When somebody has to have been there."""
        days = self.severity.response_days
        if calendar is None:
            return self.opened + timedelta(days=days)
        return calendar.working_days(self.opened, days)[-1]

    def is_overdue(self, today: date | None = None, calendar: Any = None) -> bool:
        if not self.state.is_open:
            return False
        return (today or date.today()) > self.due_by(calendar)

    @property
    def minutes_spent(self) -> int:
        return sum(visit.minutes for visit in self.visits)

    def schedule(self, on: date, engineer: str = "") -> "ServiceCall":
        self.visits.append(Visit(on=on, engineer=engineer))
        self.state = CallState.SCHEDULED
        return self

    def close(
        self,
        on: date,
        cause: Cause,
        *,
        minutes: int = 0,
        engineer: str = "",
        note: str = "",
        charged: float = 0.0,
    ) -> "ServiceCall":
        """Record what it was and shut it, which is what makes the data worth having."""
        if self.state is CallState.DONE:
            from ..core.errors import ProfileOSError

            raise ProfileOSError(
                f"הקריאה כבר נסגרה ב-{self.closed}. פתח קריאה חדשה אם יש בעיה נוספת."
            )
        self.visits.append(
            Visit(on=on, engineer=engineer, minutes=minutes, note=note, resolved=True)
        )
        self.cause = cause
        self.state = CallState.DONE
        self.closed = on
        self.charged = charged
        return self

    def describe(self) -> str:
        where = self.element_name or self.job_id or self.customer_name
        return f"{self.symptom.hebrew} · {where} · {self.state.hebrew}"


__all__ = [
    "CallState",
    "Cause",
    "SYMPTOM_WARRANTY",
    "ServiceCall",
    "Severity",
    "Symptom",
    "Visit",
    "WARRANTY_HEBREW",
    "WARRANTY_MONTHS",
    "warranty_expires",
]
