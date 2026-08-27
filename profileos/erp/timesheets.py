"""Hours actually worked, against the job they were worked on.

Job costing has been reading estimates: the labour that *should* have gone
into a window, from a standard time somebody set once. That is the right
number for quoting and the wrong one for knowing. The difference between them
is where a shop's margin actually goes — the job that took two extra days
because the glass came back wrong looks identical, on paper, to the one that
went perfectly.

Standard times are still what quotes are built on. This is the other half:
what really happened, booked by the person who did it, against the job and the
operation. With both, the estimate can be corrected by measurement rather than
by argument, which is the only way standard times ever become true.

Nothing here is a clocking-in system for paying wages. It is a record of where
the shop's hours went, and it says so, because the two have different legal
weight and confusing them helps nobody.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.timesheets")


@dataclass
class Entry:
    """One stretch of work, by one person, on one job."""

    entry_id: str = field(default_factory=lambda: f"T-{uuid4().hex[:6].upper()}")
    person: str = ""
    job_id: str = ""
    #: Which operation, using the scheduler's own names where it maps.
    operation: str = ""
    on: date = field(default_factory=date.today)
    minutes: int = 0
    #: What they were doing, in their words, when the operation does not say.
    note: str = ""
    #: Set when the time was spent redoing something, so rework is visible.
    rework: bool = False
    #: The hourly cost of this person to the shop, if the shop tracks it.
    rate: float = 0.0

    def __post_init__(self) -> None:
        if not self.person.strip():
            raise ProfileOSError("רישום שעות בלי שם עובד אינו רישום")
        if self.minutes <= 0:
            raise ProfileOSError("רישום שעות חייב זמן חיובי")
        if self.minutes > 16 * 60:
            raise ProfileOSError(
                f"⁦{self.minutes / 60:.1f}⁩ שעות ביום אחד — כנראה טעות הקלדה"
            )

    @property
    def hours(self) -> float:
        return round(self.minutes / 60.0, 2)

    @property
    def cost(self) -> float:
        return round(self.hours * self.rate, 2)

    def describe(self) -> str:
        where = self.job_id or "ללא עבודה"
        what = self.operation or self.note or "עבודה"
        tail = " · תיקון חוזר" if self.rework else ""
        return f"{self.person} · {where} · {what} · ⁦{self.hours:.2f}⁩ שעות{tail}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id, "person": self.person,
            "job_id": self.job_id, "operation": self.operation,
            "on": self.on.isoformat(), "minutes": self.minutes,
            "note": self.note, "rework": self.rework, "rate": self.rate,
        }


def minutes_between(start: str, end: str) -> int:
    """Minutes between two clock times, as somebody writes them on a sheet.

    A shift that ends before it starts has crossed midnight, which happens on
    a late installation and is not an error.
    """
    def parse(text: str) -> time:
        text = text.strip().replace(".", ":")
        parts = text.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError) as exc:
            raise ProfileOSError(f"לא ניתן לקרוא שעה מ״{text}״") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ProfileOSError(f"שעה לא אפשרית: {text}")
        return time(hour, minute)

    first, second = parse(start), parse(end)
    delta = (
        datetime.combine(date.today(), second)
        - datetime.combine(date.today(), first)
    )
    if delta.total_seconds() <= 0:
        delta += timedelta(days=1)
    return int(delta.total_seconds() // 60)


class TimeBook:
    """Where the shop's hours went."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, Entry] = {}
        if self.path is not None:
            self.load()

    # -- persistence --------------------------------------------------------- #
    def load(self) -> "TimeBook":
        if self.path is None or not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _log.exception("Time book at %s unreadable", self.path)
            return self
        for item in raw.get("entries", []):
            try:
                entry = Entry(
                    entry_id=item.get("entry_id", ""),
                    person=item["person"], job_id=item.get("job_id", ""),
                    operation=item.get("operation", ""),
                    on=date.fromisoformat(item["on"]),
                    minutes=int(item["minutes"]), note=item.get("note", ""),
                    rework=bool(item.get("rework", False)),
                    rate=float(item.get("rate", 0.0)),
                )
            except Exception:  # noqa: BLE001 - one bad row, not the book
                _log.warning("Skipping unreadable time entry: %s", item.get("entry_id"))
                continue
            self._entries[entry.entry_id] = entry
        return self

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [entry.as_dict() for entry in self._entries.values()]}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    # -- writing ------------------------------------------------------------- #
    def add(self, entry: Entry) -> Entry:
        self._entries[entry.entry_id] = entry
        self.save()
        return entry

    def book(
        self, person: str, job_id: str, minutes: int, *,
        operation: str = "", on: date | None = None, rework: bool = False,
        rate: float = 0.0, note: str = "",
    ) -> Entry:
        return self.add(Entry(
            person=person, job_id=job_id, minutes=minutes, operation=operation,
            on=on or date.today(), rework=rework, rate=rate, note=note,
        ))

    def remove(self, entry_id: str) -> None:
        if entry_id in self._entries:
            del self._entries[entry_id]
            self.save()

    # -- reading ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(sorted(self._entries.values(), key=lambda e: e.on, reverse=True))

    def for_job(self, job_id: str) -> list[Entry]:
        return [entry for entry in self if entry.job_id == job_id]

    def for_person(self, person: str) -> list[Entry]:
        needle = person.strip().casefold()
        return [entry for entry in self if entry.person.casefold() == needle]

    def between(self, start: date, end: date) -> list[Entry]:
        return [entry for entry in self if start <= entry.on <= end]

    def hours_on_job(self, job_id: str) -> float:
        return round(sum(entry.hours for entry in self.for_job(job_id)), 2)

    def cost_of_job(self, job_id: str, *, default_rate: float = 0.0) -> float:
        """What the hours on this job cost, at the rates recorded."""
        total = 0.0
        for entry in self.for_job(job_id):
            total += entry.hours * (entry.rate or default_rate)
        return round(total, 2)

    def rework_share(self, job_id: str = "") -> float:
        """How much of the time went into doing something twice."""
        entries = self.for_job(job_id) if job_id else list(self)
        total = sum(entry.minutes for entry in entries)
        if not total:
            return 0.0
        again = sum(entry.minutes for entry in entries if entry.rework)
        return round(again / total * 100.0, 1)

    def by_operation(self, job_id: str = "") -> dict[str, float]:
        entries = self.for_job(job_id) if job_id else list(self)
        totals: dict[str, float] = defaultdict(float)
        for entry in entries:
            totals[entry.operation or "אחר"] += entry.hours
        return {key: round(value, 2) for key, value in sorted(totals.items())}

    def by_person(self, start: date | None = None, end: date | None = None) -> dict[str, float]:
        entries = (
            self.between(start, end) if start and end else list(self)
        )
        totals: dict[str, float] = defaultdict(float)
        for entry in entries:
            totals[entry.person] += entry.hours
        return {key: round(value, 2) for key, value in sorted(totals.items())}

    # -- the point of keeping it ---------------------------------------------- #
    def against_estimate(self, job_id: str, estimated_hours: float) -> dict[str, Any]:
        """Booked hours against the estimate the job was quoted on.

        This is how a standard time stops being a guess somebody set once:
        after a few real jobs the difference is a measurement, and the estimate
        can be corrected rather than argued about.
        """
        actual = self.hours_on_job(job_id)
        difference = round(actual - estimated_hours, 2)
        share = (difference / estimated_hours * 100.0) if estimated_hours else 0.0
        verdict = "כמתוכנן"
        if estimated_hours and share > 15:
            verdict = f"חריגה של ⁦{share:.0f}%⁩ מהזמן שתומחר"
        elif estimated_hours and share < -15:
            verdict = f"⁦{abs(share):.0f}%⁩ מתחת לזמן שתומחר — אולי התקן גבוה מדי"
        return {
            "estimated_hours": round(estimated_hours, 2),
            "actual_hours": actual,
            "difference_hours": difference,
            "difference_pct": round(share, 1),
            "rework_pct": self.rework_share(job_id),
            "by_operation": self.by_operation(job_id),
            "verdict": verdict,
        }


def default_timebook() -> TimeBook:
    from ..core.config import get_settings

    return TimeBook(get_settings().data_dir / "timesheets.json")


__all__ = [
    "Entry",
    "TimeBook",
    "default_timebook",
    "minutes_between",
]
