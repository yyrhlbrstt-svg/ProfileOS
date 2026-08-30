"""Who changed the price, and when — in a file that cannot quietly lose a line.

A shop with three people in the office and a shared folder eventually has the
conversation: the quotation went out at ⁦96,000⁩ and the file now says
⁦86,000⁩, and nobody changed it. Somebody did. Without a record, the argument
is about memory, and the person with the best memory wins rather than the
person who is right.

This records the change: what was altered, from what to what, by whom, when.
Two decisions make it worth having rather than another log nobody reads.

The first is that it is append-only and **chained**. Each line carries a hash
of the line before it, so removing or editing an entry breaks the chain from
that point on and :func:`AuditLog.verify` says exactly where. A log that can be
edited by the person it incriminates is decoration.

The second is that it records the value, not the event. "Somebody edited the
quotation" answers nothing. "⁦96,000⁩ → ⁦86,000⁩, by דנה, on Tuesday at 14:12"
ends the conversation.

What it does not claim: this is a record, not a permission system. It says what
happened; it does not stop anything happening, and anybody with the folder can
delete the whole file. What they cannot do is remove one line from the middle
and have it still look intact.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import ProfileOSError
from .logging_setup import get_logger

_log = get_logger("core.audit")

LOG_NAME = "audit.jsonl"

#: The first link. A fixed value rather than an empty string, so a file whose
#: first line was removed does not verify by accident.
GENESIS = "profileos-audit-v1"


class Action(StrEnum):
    """What happened, in the few kinds that matter."""

    CREATED = "created"
    CHANGED = "changed"
    DELETED = "deleted"
    ISSUED = "issued"       # a document went to a customer or the tax authority
    POSTED = "posted"       # something was written to the books
    CONFIRMED = "confirmed"  # somebody vouched for a supplier figure
    RESTORED = "restored"

    @property
    def hebrew(self) -> str:
        return {
            "created": "נוצר",
            "changed": "שונה",
            "deleted": "נמחק",
            "issued": "הונפק",
            "posted": "נרשם",
            "confirmed": "אושר",
            "restored": "שוחזר",
        }[self.value]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_person() -> str:
    """Who is at the keyboard, as far as this installation can tell.

    There is no login here, so this is the operating system's user name — and
    it is recorded as what it is rather than dressed up as an identity. On a
    shared machine it will be the same for everybody, which is worth knowing
    when reading the log rather than discovering later.
    """
    for variable in ("PROFILEOS_USER", "USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(variable, "").strip()
        if value:
            return value
    return "לא ידוע"


@dataclass(frozen=True)
class Entry:
    """One recorded change."""

    at: str = field(default_factory=_now)
    person: str = field(default_factory=current_person)
    action: Action = Action.CHANGED
    #: What was touched: "quote:2026-114", "customer:C-0012", "system:7300".
    subject: str = ""
    #: Which field, when the change is to one value rather than a whole record.
    field_name: str = ""
    before: Any = None
    after: Any = None
    note: str = ""
    #: Hash of the previous entry, making the file a chain.
    previous: str = ""

    @property
    def when(self) -> datetime:
        try:
            return datetime.fromisoformat(self.at)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    def payload(self) -> dict[str, Any]:
        """Everything that is hashed. The digest itself is not in here."""
        return {
            "at": self.at, "person": self.person, "action": self.action.value,
            "subject": self.subject, "field": self.field_name,
            "before": self.before, "after": self.after, "note": self.note,
            "previous": self.previous,
        }

    def digest(self) -> str:
        body = json.dumps(
            self.payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def as_line(self) -> str:
        record = self.payload()
        record["digest"] = self.digest()
        return json.dumps(record, ensure_ascii=False, sort_keys=True)

    def describe(self) -> str:
        stamp = self.at[:16].replace("T", " ")
        head = f"⁦{stamp}⁩ · {self.person} · {self.action.hebrew} · {self.subject}"
        if self.field_name:
            head += f" · {self.field_name}"
        if self.before is not None or self.after is not None:
            head += f": {_show(self.before)} ← {_show(self.after)}"
        if self.note:
            head += f" · {self.note}"
        return head

    @classmethod
    def from_line(cls, line: str) -> tuple["Entry", str]:
        """Read one line back, with the digest it was written with."""
        raw = json.loads(line)
        entry = cls(
            at=str(raw.get("at", "")),
            person=str(raw.get("person", "")),
            action=Action(str(raw.get("action", "changed"))),
            subject=str(raw.get("subject", "")),
            field_name=str(raw.get("field", "")),
            before=raw.get("before"),
            after=raw.get("after"),
            note=str(raw.get("note", "")),
            previous=str(raw.get("previous", "")),
        )
        return entry, str(raw.get("digest", ""))


def _show(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"⁦{value:,.2f}⁩"
    if isinstance(value, (int, bool)):
        return f"⁦{value}⁩"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


@dataclass
class Verification:
    """Whether the chain holds, and where it stops if it does not."""

    entries: int = 0
    ok: bool = True
    broken_at: int | None = None
    reason: str = ""

    def describe(self) -> str:
        if self.ok:
            return f"⁦{self.entries}⁩ רשומות, השרשרת שלמה"
        return (
            f"השרשרת נשברה בשורה ⁦{self.broken_at}⁩ מתוך ⁦{self.entries}⁩ — "
            f"{self.reason}"
        )


class AuditLog:
    """An append-only, hash-chained record of what changed."""

    def __init__(self, path: Path | None = None) -> None:
        from .config import get_settings

        self.path = Path(path) if path else get_settings().data_dir / LOG_NAME

    # -- writing ------------------------------------------------------------- #
    def _last_digest(self) -> str:
        """The digest of the final line, which the next line will point at."""
        if not self.path.exists():
            return GENESIS
        last = ""
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last = line
        except OSError as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return GENESIS
        if not last:
            return GENESIS
        try:
            return str(json.loads(last).get("digest", GENESIS))
        except json.JSONDecodeError:
            return GENESIS

    def record(
        self,
        action: Action | str,
        subject: str,
        *,
        field_name: str = "",
        before: Any = None,
        after: Any = None,
        note: str = "",
        person: str = "",
    ) -> Entry:
        """Append one entry. Never rewrites what is already there."""
        entry = Entry(
            action=Action(action) if not isinstance(action, Action) else action,
            subject=subject,
            field_name=field_name,
            before=before,
            after=after,
            note=note,
            person=person or current_person(),
            previous=self._last_digest(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Opened in append mode for every write: two people on a shared folder
        # both appending short lines is the one case the operating system
        # handles for us, and holding the file open would not be.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.as_line() + "\n")
        return entry

    def record_changes(
        self,
        subject: str,
        before: Any,
        after: Any,
        *,
        fields: Iterable[str] = (),
        note: str = "",
        person: str = "",
    ) -> list[Entry]:
        """Record every field that actually moved between two versions.

        A record with twenty fields where one changed produces one entry, not
        twenty — a log that repeats the unchanged is a log nobody reads.
        """
        names = list(fields) or sorted(
            set(_fields_of(before)) | set(_fields_of(after))
        )
        written: list[Entry] = []
        for name in names:
            old = _value_of(before, name)
            new = _value_of(after, name)
            if old == new:
                continue
            written.append(self.record(
                Action.CHANGED, subject, field_name=name,
                before=_plain(old), after=_plain(new), note=note, person=person,
            ))
        return written

    # -- reading ------------------------------------------------------------- #
    def __iter__(self) -> Iterator[Entry]:
        if not self.path.exists():
            return iter(())
        found: list[Entry] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry, _digest = Entry.from_line(line)
                except (json.JSONDecodeError, ValueError) as exc:
                    _log.warning("Unreadable audit line %d: %s", number, exc)
                    continue
                found.append(entry)
        return iter(found)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def all(self) -> list[Entry]:
        return list(self)

    def recent(self, limit: int = 50) -> list[Entry]:
        return list(self)[-limit:][::-1]

    def for_subject(self, subject: str) -> list[Entry]:
        """Every change to one thing, oldest first — the story of that record."""
        return [entry for entry in self if entry.subject == subject]

    def by_person(self, person: str) -> list[Entry]:
        needle = person.strip().casefold()
        return [entry for entry in self if entry.person.casefold() == needle]

    def between(self, start: datetime, end: datetime) -> list[Entry]:
        return [entry for entry in self if start <= entry.when <= end]

    # -- checking ------------------------------------------------------------- #
    def verify(self) -> Verification:
        """Walk the chain and say whether it still holds.

        A line removed from the middle, or a figure quietly edited in place,
        breaks every link after it — and this reports the first break, which
        is where the tampering was.
        """
        result = Verification()
        if not self.path.exists():
            return result

        expected = GENESIS
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                result.entries += 1
                try:
                    entry, stored = Entry.from_line(line)
                except (json.JSONDecodeError, ValueError):
                    return _broken(result, number, "השורה אינה קריאה")

                if entry.previous != expected:
                    return _broken(
                        result, number, "השורה אינה מצביעה על קודמתה — "
                        "כנראה נמחקה שורה"
                    )
                recomputed = entry.digest()
                if recomputed != stored:
                    return _broken(
                        result, number, "תוכן השורה שונה ממה שנחתם — "
                        "כנראה נערך בדיעבד"
                    )
                expected = recomputed
        return result


def _broken(result: Verification, at: int, reason: str) -> Verification:
    result.ok = False
    result.broken_at = at
    result.reason = reason
    _log.warning("Audit chain broken at line %d: %s", at, reason)
    return result


def _fields_of(record: Any) -> list[str]:
    if record is None:
        return []
    if isinstance(record, dict):
        return list(record)
    for attribute in ("model_fields", "__dataclass_fields__"):
        found = getattr(type(record), attribute, None)
        if found:
            return list(found)
    return [
        name for name in vars(record)
        if not name.startswith("_")
    ] if hasattr(record, "__dict__") else []


def _value_of(record: Any, name: str) -> Any:
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _plain(value: Any) -> Any:
    """Reduce a value to something JSON can hold without losing the point."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return str(value)


_DEFAULT: AuditLog | None = None


def audit_path() -> Path:
    """Where the chain lives.

    Beside the data by default. ``PROFILEOS_AUDIT_LOG`` moves it, which is how
    a shop puts the record somewhere the people it records cannot casually
    reach — and how the test suite keeps its own noise out of a real log.
    """
    from .config import get_settings

    override = os.environ.get("PROFILEOS_AUDIT_LOG", "").strip()
    if override:
        return Path(override)
    return get_settings().data_dir / LOG_NAME


def audit() -> AuditLog:
    """The installation's log. One object, so the chain stays in order."""
    global _DEFAULT

    wanted = audit_path()
    if _DEFAULT is None or _DEFAULT.path != wanted:
        _DEFAULT = AuditLog(wanted)
    return _DEFAULT


def record(action: Action | str, subject: str, **kwargs: Any) -> Entry:
    """Shorthand for the installation's log."""
    return audit().record(action, subject, **kwargs)


def try_record(action: Action | str, subject: str, **kwargs: Any) -> Entry | None:
    """Record, but never let the recording break the thing being recorded.

    A read-only folder, a full disk, a lock somebody left behind — none of
    these are reasons to refuse a stocktake or lose a confirmed supplier
    figure. The failure is logged and the work goes through. An audit log that
    can stop a shop working would be turned off by the end of the week, and a
    log that is turned off records nothing at all.
    """
    try:
        return audit().record(action, subject, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Could not write an audit entry for %s: %s", subject, exc)
        return None


__all__ = [
    "GENESIS",
    "LOG_NAME",
    "Action",
    "AuditLog",
    "Entry",
    "Verification",
    "audit",
    "audit_path",
    "current_person",
    "record",
    "try_record",
]
