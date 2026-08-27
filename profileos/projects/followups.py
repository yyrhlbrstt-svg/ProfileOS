"""Chasing the quotation, which is where most of the money is left.

A shop sends a quotation on Sunday. The customer says they will think about
it. Nobody rings. Three weeks later the job is somebody else's, and the shop
concludes the price was too high — which it very often was not.

Every commercial package calls this CRM and most of them make it a second
system nobody opens. This is deliberately smaller: a list of things somebody
has to do, each attached to a job or a customer, each with a date, and a
standing rule that sending a quotation creates the chase for it. Nothing here
sends anything or decides anything; it only makes sure that the day a decision
was supposed to be made, somebody is looking at it.

Two rules give it its shape.

A follow-up date is a **working** day. A reminder that falls on a Saturday or
in the middle of Tishrei is a reminder that arrives when nobody is there, gets
scrolled past on the Sunday, and trains people to ignore the list.

A task is closed with an outcome, not ticked. "Rang, they are still waiting on
their architect" is worth keeping; a tick is not. A task closed with nothing
said is accepted, because forcing prose is how a list becomes a nuisance — but
the ones closed silently are counted, so a shop can see how much of its own
history it is throwing away.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("projects.followups")


class Kind(StrEnum):
    """What sort of thing has to be done."""

    CHASE_QUOTE = "chase_quote"
    CHASE_PAYMENT = "chase_payment"
    MEASURE = "measure"
    CALL_BACK = "call_back"
    SEND_DOCUMENT = "send_document"
    SITE_VISIT = "site_visit"
    ORDER_MATERIAL = "order_material"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            "chase_quote": "מעקב הצעת מחיר",
            "chase_payment": "גבייה",
            "measure": "מדידה",
            "call_back": "לחזור ללקוח",
            "send_document": "לשלוח מסמך",
            "site_visit": "ביקור באתר",
            "order_material": "הזמנת חומר",
            "other": "אחר",
        }[self.value]


class Outcome(StrEnum):
    """How a task ended. ``OPEN`` until it has."""

    OPEN = "open"
    DONE = "done"
    NO_ANSWER = "no_answer"
    POSTPONED = "postponed"
    LOST = "lost"
    CANCELLED = "cancelled"

    @property
    def hebrew(self) -> str:
        return {
            "open": "פתוחה",
            "done": "בוצע",
            "no_answer": "אין מענה",
            "postponed": "נדחה",
            "lost": "לא נסגר",
            "cancelled": "בוטל",
        }[self.value]

    @property
    def is_closed(self) -> bool:
        return self is not Outcome.OPEN


#: When to chase a quotation, in working days after it was sent. Three touches
#: and then stop: a fourth call is not persistence, it is a nuisance, and the
#: shop's own time is worth something.
CHASE_SCHEDULE: tuple[tuple[int, str], ...] = (
    (2, "לוודא שההצעה התקבלה ושהיא מובנת"),
    (7, "לשאול אם יש שאלות ומתי מתכננים להחליט"),
    (21, "בירור אחרון — אם אין החלטה, לסמן את התיק ולהפסיק לרדוף"),
)


@dataclass
class Task:
    """One thing somebody has to do, on a day somebody is at work."""

    task_id: str = field(default_factory=lambda: f"TK-{uuid4().hex[:6].upper()}")
    kind: Kind = Kind.OTHER
    what: str = ""
    #: What it is about: "job:2026-114" or "customer:C-0012".
    about: str = ""
    subject_name: str = ""
    due: date = field(default_factory=date.today)
    assigned_to: str = ""
    created_on: date = field(default_factory=date.today)
    created_by: str = ""
    outcome: Outcome = Outcome.OPEN
    closed_on: date | None = None
    #: What actually happened. Worth more than the tick.
    result: str = ""

    @property
    def is_open(self) -> bool:
        return not self.outcome.is_closed

    def is_overdue(self, on: date | None = None) -> bool:
        return self.is_open and self.due < (on or date.today())

    def days_late(self, on: date | None = None) -> int:
        return max(0, ((on or date.today()) - self.due).days) if self.is_open else 0

    @property
    def closed_silently(self) -> bool:
        """Closed with nothing said — accepted, but counted."""
        return self.outcome.is_closed and not self.result.strip()

    def close(
        self, outcome: Outcome = Outcome.DONE, *,
        result: str = "", on: date | None = None,
    ) -> "Task":
        if self.outcome.is_closed:
            raise ProfileOSError(f"משימה {self.task_id} כבר {self.outcome.hebrew}")
        self.outcome = outcome
        self.result = result
        self.closed_on = on or date.today()
        return self

    def postpone(self, to: date, *, reason: str = "") -> "Task":
        """Move the date without losing that it was moved."""
        if self.outcome.is_closed:
            raise ProfileOSError("אי אפשר לדחות משימה סגורה")
        if to <= self.due:
            raise ProfileOSError("דחייה היא לתאריך מאוחר יותר")
        note = reason or "נדחה"
        self.result = (
            f"{self.result} · " if self.result else ""
        ) + f"{note} מ-⁦{self.due.strftime('%d/%m')}⁩"
        self.due = to
        return self

    def describe(self) -> str:
        state = (
            f"⁦{self.days_late()}⁩ ימי איחור" if self.is_overdue()
            else (self.outcome.hebrew if not self.is_open else "פתוחה")
        )
        who = f" · {self.assigned_to}" if self.assigned_to else ""
        return (
            f"⁦{self.due.strftime('%d/%m/%Y')}⁩ · {self.kind.hebrew} · "
            f"{self.subject_name or self.about} · {self.what} · {state}{who}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "kind": self.kind.value,
            "what": self.what, "about": self.about,
            "subject_name": self.subject_name,
            "due": self.due.isoformat(), "assigned_to": self.assigned_to,
            "created_on": self.created_on.isoformat(),
            "created_by": self.created_by, "outcome": self.outcome.value,
            "closed_on": self.closed_on.isoformat() if self.closed_on else None,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        closed = raw.get("closed_on")
        return cls(
            task_id=str(raw.get("task_id", "")),
            kind=Kind(str(raw.get("kind", "other"))),
            what=str(raw.get("what", "")), about=str(raw.get("about", "")),
            subject_name=str(raw.get("subject_name", "")),
            due=date.fromisoformat(str(raw.get("due", date.today().isoformat()))),
            assigned_to=str(raw.get("assigned_to", "")),
            created_on=date.fromisoformat(
                str(raw.get("created_on", date.today().isoformat()))
            ),
            created_by=str(raw.get("created_by", "")),
            outcome=Outcome(str(raw.get("outcome", "open"))),
            closed_on=date.fromisoformat(str(closed)) if closed else None,
            result=str(raw.get("result", "")),
        )


def _working_day(when: date, calendar: Any = None) -> date:
    """Move a date onto the next day somebody is actually at work."""
    if calendar is None:
        from ..erp.scheduling import Calendar

        calendar = Calendar.israeli()
    try:
        return calendar.next_working_day(when)
    except Exception:  # noqa: BLE001 - a broken calendar must not lose the task
        _log.warning("Could not resolve a working day for %s", when)
        return when


def working_days_after(start: date, count: int, calendar: Any = None) -> date:
    """``count`` working days after ``start``, on the shop's own calendar."""
    if calendar is None:
        from ..erp.scheduling import Calendar

        calendar = Calendar.israeli()
    if count <= 0:
        return _working_day(start, calendar)
    return calendar.working_days(start + timedelta(days=1), count)[-1]


class TaskBook:
    """Everything somebody still has to do, kept on disk."""

    def __init__(self, path: Path | None = None) -> None:
        from ..core.config import get_settings

        self.path = Path(path) if path else get_settings().data_dir / "tasks.json"
        self._tasks: dict[str, Task] = {}

    def load(self) -> "TaskBook":
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return self
        for item in raw.get("tasks", []):
            try:
                task = Task.from_dict(item)
            except (ValueError, KeyError) as exc:
                _log.warning("Skipped an unreadable task: %s", exc)
                continue
            self._tasks[task.task_id] = task
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written": datetime.now().isoformat(timespec="seconds"),
            "tasks": [task.as_dict() for task in self._tasks.values()],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    # -- writing ------------------------------------------------------------- #
    def add(self, task: Task, *, save: bool = True) -> Task:
        self._tasks[task.task_id] = task
        if save:
            self.save()
        return task

    def create(
        self, kind: Kind, what: str, *, about: str = "", subject_name: str = "",
        due: date | None = None, assigned_to: str = "", created_by: str = "",
        calendar: Any = None,
    ) -> Task:
        """Add one task, with its date moved onto a working day."""
        return self.add(Task(
            kind=kind, what=what, about=about, subject_name=subject_name,
            due=_working_day(due or date.today(), calendar),
            assigned_to=assigned_to, created_by=created_by,
        ))

    def get(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise ProfileOSError(f"אין משימה {task_id}")
        return self._tasks[task_id]

    def close(self, task_id: str, outcome: Outcome = Outcome.DONE, **kwargs: Any) -> Task:
        task = self.get(task_id).close(outcome, **kwargs)
        self.save()
        return task

    def remove(self, task_id: str) -> None:
        if task_id in self._tasks:
            del self._tasks[task_id]
            self.save()

    # -- reading ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._tasks)

    def __iter__(self):
        return iter(sorted(self._tasks.values(), key=lambda task: task.due))

    @property
    def open_tasks(self) -> list[Task]:
        return [task for task in self if task.is_open]

    def due_by(self, when: date | None = None) -> list[Task]:
        """Everything that should have been done by a day, oldest first."""
        limit = when or date.today()
        return [task for task in self.open_tasks if task.due <= limit]

    def overdue(self, on: date | None = None) -> list[Task]:
        return [task for task in self.open_tasks if task.is_overdue(on)]

    def this_week(self, *, from_day: date | None = None) -> list[Task]:
        start = from_day or date.today()
        end = start + timedelta(days=7)
        return [task for task in self.open_tasks if start <= task.due <= end]

    def about(self, subject: str) -> list[Task]:
        return [task for task in self if task.about == subject]

    def for_person(self, person: str) -> list[Task]:
        needle = person.strip().casefold()
        return [
            task for task in self.open_tasks
            if task.assigned_to.casefold() == needle
        ]

    def summary(self, *, on: date | None = None) -> dict[str, Any]:
        today = on or date.today()
        closed = [task for task in self if not task.is_open]
        return {
            "open": len(self.open_tasks),
            "due_today": len([
                task for task in self.open_tasks if task.due == today
            ]),
            "overdue": len(self.overdue(today)),
            "closed": len(closed),
            "closed_silently": len([t for t in closed if t.closed_silently]),
        }

    # -- the standing rule ---------------------------------------------------- #
    def chase_quote(
        self, job: Any, *, sent_on: date | None = None,
        assigned_to: str = "", calendar: Any = None,
    ) -> list[Task]:
        """Create the follow-up schedule for a quotation that has gone out.

        Three touches on working days, then stop. A shop that never sets these
        concludes its prices are too high, when what it has is a habit of not
        ringing back.
        """
        when = sent_on or date.today()
        job_id = str(getattr(job, "job_id", "") or "")
        subject = f"job:{job_id}" if job_id else ""
        name = str(getattr(job, "customer_name", "") or getattr(job, "name", ""))

        existing = {
            task.what for task in self.about(subject)
            if task.kind is Kind.CHASE_QUOTE and task.is_open
        }
        made: list[Task] = []
        for days, what in CHASE_SCHEDULE:
            if what in existing:
                continue
            made.append(self.add(
                Task(
                    kind=Kind.CHASE_QUOTE, what=what, about=subject,
                    subject_name=name,
                    due=working_days_after(when, days, calendar),
                    assigned_to=assigned_to,
                ),
                save=False,
            ))
        if made:
            self.save()
        _log.info("Scheduled %d follow-ups for %s", len(made), subject or "-")
        return made

    def unchased_quotes(self, jobs: Iterable[Any]) -> list[Any]:
        """Quotations sitting with a customer that nobody is chasing.

        The report worth running on a Sunday morning: not what was quoted, but
        what was quoted and then forgotten.
        """
        from .model import JobStatus

        chased = {
            task.about for task in self.open_tasks
            if task.kind is Kind.CHASE_QUOTE
        }
        return [
            job for job in jobs
            if getattr(job, "status", None) is JobStatus.QUOTED
            and f"job:{getattr(job, 'job_id', '')}" not in chased
        ]


def default_tasks() -> TaskBook:
    return TaskBook().load()


__all__ = [
    "CHASE_SCHEDULE",
    "Kind",
    "Outcome",
    "Task",
    "TaskBook",
    "default_tasks",
    "working_days_after",
]
