"""The diary of the field: who goes where, on which day, to do what.

Installations, measuring visits, service calls and deliveries were each
planned in their own place, so the evening question — "who is going where
tomorrow, and has the customer been told?" — had no single answer. A wasted
trip to a customer who is not home costs half a day of two people.

One appointment per visit, whatever it is for, with the job or the service
call it belongs to. The day's list is read straight from it, and the
reminder the evening before is one WhatsApp message per customer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..core.storage import MergingBook

_log = get_logger("projects.agenda")


class VisitKind(StrEnum):
    INSTALLATION = "installation"
    SURVEY = "survey"
    SERVICE = "service"
    DELIVERY = "delivery"
    PLUMBING = "plumbing"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            "installation": "התקנה",
            "survey": "מדידה",
            "service": "שירות",
            "delivery": "אספקה",
            "plumbing": "עבודת אינסטלציה",
            "other": "אחר",
        }[self.value]

    @property
    def phrase(self) -> str:
        """How the reminder names it: "מחר <phrase>"."""
        return {
            "installation": "מגיעים להתקין את העבודה",
            "survey": "מגיעים למדידה",
            "service": "מגיע טכנאי שירות",
            "delivery": "מגיעה אספקה",
            "plumbing": "מגיעים לעבודת האינסטלציה",
            "other": "אנחנו מגיעים אליך",
        }[self.value]


@dataclass
class Appointment:
    """One visit, on one day."""

    on: date
    kind: VisitKind = VisitKind.OTHER
    #: "08:00" or "08:00–10:00"; empty for "during the day".
    time: str = ""
    customer_name: str = ""
    phone: str = ""
    address: str = ""
    job_id: str = ""
    call_id: str = ""
    #: Who goes: "צוות א׳", "יוסי".
    crew: str = ""
    note: str = ""
    #: When the customer was sent the reminder, if they were.
    reminded_on: str = ""
    done: bool = False
    appointment_id: str = field(default_factory=lambda: f"AP-{uuid4().hex[:6].upper()}")

    def __post_init__(self) -> None:
        if isinstance(self.on, str):
            self.on = date.fromisoformat(self.on)
        if isinstance(self.kind, str):
            self.kind = VisitKind(self.kind)

    def describe(self) -> str:
        when = f"⁦{self.time}⁩ · " if self.time else ""
        who = self.customer_name or self.job_id or "—"
        where = f" · {self.address}" if self.address else ""
        return f"{when}{self.kind.hebrew} · {who}{where}"

    def as_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["on"] = self.on.isoformat()
        raw["kind"] = self.kind.value
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Appointment":
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in raw.items() if key in known})


class Agenda(MergingBook):
    """Every visit planned, kept on disk, merged on save."""

    _list_key = "appointments"
    _id_field = "appointment_id"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._items: dict[str, Appointment] = {}

    def _records(self) -> dict[str, Any]:
        return self._items

    def _to_raw(self, record: Any) -> dict[str, Any]:
        return record.as_dict()

    def _from_raw(self, raw: dict[str, Any]) -> Any:
        return Appointment.from_dict(raw)

    def load(self) -> "Agenda":
        from ..core.storage import stored

        if self.path is None or not stored(self.path):
            return self
        raw = self._read_or_mark(self.path)
        for entry in raw.get("appointments", []):
            try:
                item = Appointment.from_dict(entry)
            except Exception as exc:  # noqa: BLE001 - kept on disk by the merge
                _log.warning("Skipped an unreadable appointment: %s", exc)
                continue
            self._items[item.appointment_id] = item
        self._remember_loaded()
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._save_merged(self.path)

    # -- writing ------------------------------------------------------------ #
    def add(self, appointment: Appointment) -> Appointment:
        if appointment.on < date.today() - timedelta(days=365):
            raise ProfileOSError("תאריך הביקור רחוק מדי בעבר — בדקו את השנה")
        self._items[appointment.appointment_id] = appointment
        self.save()
        return appointment

    def mark_reminded(self, appointment_id: str) -> None:
        item = self._items.get(appointment_id)
        if item is not None:
            item.reminded_on = date.today().isoformat()
            self.save()

    def mark_done(self, appointment_id: str, done: bool = True) -> None:
        item = self._items.get(appointment_id)
        if item is not None:
            item.done = done
            self.save()

    def move(self, appointment_id: str, to: date, time: str | None = None) -> None:
        item = self._items.get(appointment_id)
        if item is None:
            raise ProfileOSError("הביקור לא נמצא")
        item.on = to
        if time is not None:
            item.time = time
        item.reminded_on = ""  # a new day needs a new reminder
        self.save()

    def remove(self, appointment_id: str) -> None:
        if appointment_id in self._items:
            del self._items[appointment_id]
            self.save()

    # -- reading ------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(sorted(self._items.values(), key=lambda a: (a.on, a.time or "99")))

    def on_day(self, day: date) -> list[Appointment]:
        return [item for item in self if item.on == day]

    def tomorrow(self, today: date | None = None) -> list[Appointment]:
        """The next working day's visits — Sunday after a Friday."""
        return self.on_day(next_working_day(today or date.today()))

    def unreminded(self, day: date) -> list[Appointment]:
        return [item for item in self.on_day(day) if not item.reminded_on and not item.done]

    def for_job(self, job_id: str) -> list[Appointment]:
        return [item for item in self if item.job_id == job_id]


def next_working_day(today: date) -> date:
    """The next day the shop goes out: Friday and Saturday are skipped."""
    day = today + timedelta(days=1)
    while day.weekday() in (4, 5):  # Friday, Saturday
        day += timedelta(days=1)
    return day


_WEEKDAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")


def day_word(day: date, today: date | None = None) -> str:
    """"מחר" for tomorrow, "ביום ראשון" across a weekend, a date further out."""
    today = today or date.today()
    if day == today:
        return "היום"
    if day == today + timedelta(days=1):
        return "מחר"
    if (day - today).days < 7:
        return f"ביום {_WEEKDAYS[day.weekday()]}"
    return f"ב־⁦{day:%d/%m}⁩"


def reminder_text(appointment: Appointment, *, brand_name: str, phone: str = "",
                  today: date | None = None, job: Any = None) -> str:
    """The reminder; for an installation with its job, the site questions
    the job's own openings raise."""
    from ..sharing import visit_reminder_message

    questions: list[str] = []
    if job is not None and appointment.kind is VisitKind.INSTALLATION:
        from .install_check import site_questions

        questions = site_questions(job)
    return visit_reminder_message(
        brand_name=brand_name, customer_name=appointment.customer_name,
        when=appointment.time, what=appointment.kind.phrase, phone=phone,
        day=day_word(appointment.on, today), questions=questions,
    )


def default_agenda() -> Agenda:
    from ..core.config import get_settings

    return Agenda(get_settings().data_dir / "agenda.json").load()


__all__ = [
    "Agenda",
    "Appointment",
    "VisitKind",
    "day_word",
    "default_agenda",
    "next_working_day",
    "reminder_text",
]
