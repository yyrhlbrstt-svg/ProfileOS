"""What changed between one price and the next, and what it cost.

A customer asks for a quotation. They ask for the north elevation in a heavier
series. They ask to drop the two toilet windows. They ask for the shutters back
in. Four weeks later somebody rings and asks why the price went up by ⁦18,000⁩
₪, and the shop has four PDFs with the same filename and no answer.

This keeps every version and answers that question. A revision is never edited
— it is superseded, with a reason written at the moment it happened, by the
person who made it. Comparing two revisions gives the lines that were added,
removed, re-priced and re-counted, each with what it did to the total, and the
whole difference reconciles: the sum of the changes equals the change in the
price. If it did not, the comparison would be decoration.

One thing this refuses to do: guess. When two revisions have lines that cannot
be matched — no code, and the wording was rewritten — they are reported as one
line removed and one added, not silently paired because they cost about the
same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("quoting.revisions")


@dataclass(frozen=True)
class Line:
    """One priced line, frozen at the moment a revision was taken."""

    key: str
    description: str
    quantity: float
    unit: str
    unit_price: float
    category: str = "material"

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "description": self.description,
            "quantity": self.quantity, "unit": self.unit,
            "unit_price": self.unit_price, "category": self.category,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Line":
        return cls(
            key=str(raw.get("key", "")),
            description=str(raw.get("description", "")),
            quantity=float(raw.get("quantity", 0.0)),
            unit=str(raw.get("unit", "")),
            unit_price=float(raw.get("unit_price", 0.0)),
            category=str(raw.get("category", "material")),
        )


@dataclass
class Revision:
    """One version of a quotation, and why it exists."""

    number: int
    #: Why this version was made, in the words somebody would say on the phone.
    reason: str = ""
    by: str = ""
    on: date = field(default_factory=date.today)
    lines: list[Line] = field(default_factory=list)
    net_price: float = 0.0
    currency: str = "ILS"
    #: Set the moment it goes to the customer. A sent revision is never edited.
    sent_on: date | None = None
    note: str = ""

    @property
    def is_sent(self) -> bool:
        return self.sent_on is not None

    @property
    def line_total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def label(self) -> str:
        return f"גרסה ⁦{self.number}⁩"

    def describe(self) -> str:
        state = (
            f"נשלחה ⁦{self.sent_on.strftime('%d/%m/%Y')}⁩"
            if self.sent_on
            else "טיוטה"
        )
        return (
            f"{self.label} · ⁦{self.net_price:,.0f}⁩ {self.currency} · "
            f"{state}" + (f" · {self.reason}" if self.reason else "")
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number, "reason": self.reason, "by": self.by,
            "on": self.on.isoformat(),
            "lines": [line.as_dict() for line in self.lines],
            "net_price": self.net_price, "currency": self.currency,
            "sent_on": self.sent_on.isoformat() if self.sent_on else None,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Revision":
        sent = raw.get("sent_on")
        return cls(
            number=int(raw.get("number", 1)),
            reason=str(raw.get("reason", "")),
            by=str(raw.get("by", "")),
            on=date.fromisoformat(str(raw.get("on", date.today().isoformat()))),
            lines=[Line.from_dict(item) for item in raw.get("lines", [])],
            net_price=float(raw.get("net_price", 0.0)),
            currency=str(raw.get("currency", "ILS")),
            sent_on=date.fromisoformat(str(sent)) if sent else None,
            note=str(raw.get("note", "")),
        )


# --------------------------------------------------------------------------- #
# Comparing two revisions
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Change:
    """One difference between two revisions, priced."""

    kind: str            # added / removed / repriced / requantified / renamed
    key: str
    description: str
    before: float = 0.0  # the line's total in the earlier revision
    after: float = 0.0   # the line's total in the later one
    detail: str = ""

    @property
    def effect(self) -> float:
        """What this change did to the price."""
        return round(self.after - self.before, 2)

    @property
    def hebrew_kind(self) -> str:
        return {
            "added": "נוסף",
            "removed": "הוסר",
            "repriced": "שינוי מחיר",
            "requantified": "שינוי כמות",
        }.get(self.kind, self.kind)

    def describe(self) -> str:
        sign = "+" if self.effect > 0 else ""
        body = f"{self.hebrew_kind}: {self.description}"
        if self.detail:
            body += f" ({self.detail})"
        return f"{body} · ⁦{sign}{self.effect:,.0f}⁩"


@dataclass
class Comparison:
    """Everything that moved between two revisions, and the sum of it."""

    earlier: Revision
    later: Revision
    changes: list[Change] = field(default_factory=list)

    @property
    def price_difference(self) -> float:
        return round(self.later.net_price - self.earlier.net_price, 2)

    @property
    def line_difference(self) -> float:
        return round(sum(change.effect for change in self.changes), 2)

    @property
    def reconciles(self) -> bool:
        """Whether the changes account for the whole move in the price.

        They do not when margin, overhead or a fixed charge moved as well —
        which is worth saying out loud rather than hiding, because "the price
        went up and none of the lines changed" is a real and awkward answer a
        salesperson needs before the customer finds it.
        """
        return abs(self.line_difference - self.price_difference) < 1.0

    @property
    def unexplained(self) -> float:
        return round(self.price_difference - self.line_difference, 2)

    def of_kind(self, kind: str) -> list[Change]:
        return [change for change in self.changes if change.kind == kind]

    @property
    def biggest(self) -> list[Change]:
        return sorted(self.changes, key=lambda c: abs(c.effect), reverse=True)

    def summary(self) -> dict[str, Any]:
        return {
            "from": self.earlier.number,
            "to": self.later.number,
            "added": len(self.of_kind("added")),
            "removed": len(self.of_kind("removed")),
            "repriced": len(self.of_kind("repriced")),
            "requantified": len(self.of_kind("requantified")),
            "price_difference": self.price_difference,
            "explained": self.line_difference,
            "unexplained": self.unexplained,
            "reconciles": self.reconciles,
        }

    def describe(self) -> str:
        """The sentence somebody says on the phone."""
        if not self.changes and not self.price_difference:
            return (
                f"בין גרסה ⁦{self.earlier.number}⁩ לגרסה ⁦{self.later.number}⁩ "
                "לא השתנה דבר"
            )
        sign = "+" if self.price_difference > 0 else ""
        head = (
            f"מגרסה ⁦{self.earlier.number}⁩ לגרסה ⁦{self.later.number}⁩: "
            f"⁦{sign}{self.price_difference:,.0f}⁩ {self.later.currency}"
        )
        parts = []
        for kind, word in (
            ("added", "נוספו"), ("removed", "הוסרו"),
            ("repriced", "עודכנו במחיר"), ("requantified", "שונו בכמות"),
        ):
            count = len(self.of_kind(kind))
            if count:
                parts.append(f"{word} ⁦{count}⁩")
        if parts:
            head += " · " + ", ".join(parts)
        if not self.reconciles:
            head += (
                f" · ⁦{abs(self.unexplained):,.0f}⁩ לא מוסברים בשורות "
                "(רווח, תקורה או חיוב קבוע)"
            )
        return head


def compare(earlier: Revision, later: Revision) -> Comparison:
    """What moved between two revisions, line by line, and by how much."""
    result = Comparison(earlier=earlier, later=later)

    before = {line.key: line for line in earlier.lines if line.key}
    after = {line.key: line for line in later.lines if line.key}

    # Lines without a key cannot be matched across revisions at all. They are
    # reported whole rather than paired on a resemblance.
    unkeyed_before = [line for line in earlier.lines if not line.key]
    unkeyed_after = [line for line in later.lines if not line.key]

    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None and new is not None:
            result.changes.append(Change(
                "added", key, new.description, 0.0, new.total,
                detail=f"⁦{new.quantity:g}⁩ {new.unit}",
            ))
        elif new is None and old is not None:
            result.changes.append(Change(
                "removed", key, old.description, old.total, 0.0,
                detail=f"⁦{old.quantity:g}⁩ {old.unit}",
            ))
        elif old is not None and new is not None:
            if abs(old.quantity - new.quantity) > 1e-9:
                result.changes.append(Change(
                    "requantified", key, new.description,
                    old.total, round(new.quantity * old.unit_price, 2),
                    detail=f"⁦{old.quantity:g}⁩ → ⁦{new.quantity:g}⁩ {new.unit}",
                ))
            if abs(old.unit_price - new.unit_price) > 1e-9:
                result.changes.append(Change(
                    "repriced", key, new.description,
                    round(new.quantity * old.unit_price, 2), new.total,
                    detail=(
                        f"⁦{old.unit_price:,.2f}⁩ → ⁦{new.unit_price:,.2f}⁩ "
                        f"ל{new.unit}"
                    ),
                ))

    for line in unkeyed_before:
        result.changes.append(Change(
            "removed", "", line.description, line.total, 0.0,
            detail="שורה בלי קוד — לא ניתן להתאים בין גרסאות",
        ))
    for line in unkeyed_after:
        result.changes.append(Change(
            "added", "", line.description, 0.0, line.total,
            detail="שורה בלי קוד — לא ניתן להתאים בין גרסאות",
        ))
    return result


# --------------------------------------------------------------------------- #
# The history of one quotation
# --------------------------------------------------------------------------- #
@dataclass
class QuoteHistory:
    """Every version of one job's price, in the order they were made."""

    job_id: str
    revisions: list[Revision] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.revisions)

    def __iter__(self):
        return iter(self.revisions)

    @property
    def current(self) -> Revision | None:
        return self.revisions[-1] if self.revisions else None

    @property
    def last_sent(self) -> Revision | None:
        """The version the customer is actually holding."""
        for revision in reversed(self.revisions):
            if revision.is_sent:
                return revision
        return None

    def get(self, number: int) -> Revision:
        for revision in self.revisions:
            if revision.number == number:
                return revision
        raise ProfileOSError(f"אין גרסה ⁦{number}⁩ להצעה של {self.job_id}")

    def take(
        self, quotation: Any, *, reason: str = "", by: str = "",
        on: date | None = None,
    ) -> Revision:
        """Freeze the quotation as it stands right now, as the next version."""
        number = (self.current.number + 1) if self.current else 1
        if number > 1 and not reason.strip():
            raise ProfileOSError(
                "גרסה חדשה בלי סיבה היא גרסה שאיש לא יזכור למה נוצרה"
            )
        revision = Revision(
            number=number, reason=reason.strip(), by=by,
            on=on or date.today(),
            lines=list(_lines_of(quotation)),
            net_price=round(float(getattr(quotation, "net_price", 0.0)), 2),
            currency=str(getattr(quotation, "currency", "ILS")),
        )
        self.revisions.append(revision)

        from ..core.audit import Action, try_record

        previous = self.revisions[-2] if len(self.revisions) > 1 else None
        try_record(
            Action.CREATED, f"quote:{self.job_id}",
            field_name=f"revision {number}",
            before=previous.net_price if previous else None,
            after=revision.net_price,
            person=by, note=reason,
        )
        _log.info(
            "Quote %s revision %d taken: %.2f %s",
            self.job_id, number, revision.net_price, revision.currency,
        )
        return revision

    def mark_sent(self, number: int, *, on: date | None = None) -> Revision:
        revision = self.get(number)
        if revision.is_sent:
            raise ProfileOSError(f"גרסה ⁦{number}⁩ כבר נשלחה")
        revision.sent_on = on or date.today()

        from ..core.audit import Action, try_record

        try_record(
            Action.ISSUED, f"quote:{self.job_id}",
            field_name=f"revision {number}", after=revision.net_price,
            note="נשלחה ללקוח",
        )
        return revision

    def compare(self, earlier: int, later: int) -> Comparison:
        return compare(self.get(earlier), self.get(later))

    def since_sent(self) -> Comparison | None:
        """What has changed since the customer last saw a price.

        The check to run before answering "so is that still the price?".
        """
        sent = self.last_sent
        if sent is None or self.current is None or sent is self.current:
            return None
        return compare(sent, self.current)

    def trail(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        previous: Revision | None = None
        for revision in self.revisions:
            move = (
                round(revision.net_price - previous.net_price, 2)
                if previous else 0.0
            )
            rows.append({
                "number": revision.number,
                "on": revision.on.isoformat(),
                "by": revision.by,
                "reason": revision.reason,
                "net_price": revision.net_price,
                "change": move,
                "sent": revision.sent_on.isoformat() if revision.sent_on else "",
            })
            previous = revision
        return rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "revisions": [revision.as_dict() for revision in self.revisions],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "QuoteHistory":
        return cls(
            job_id=str(raw.get("job_id", "")),
            revisions=[
                Revision.from_dict(item) for item in raw.get("revisions", [])
            ],
        )


def _lines_of(quotation: Any) -> Iterable[Line]:
    """Freeze whatever the pricing engine produced, in a shape that lasts.

    The key is the line's own code where it has one. A line without a code
    cannot be followed across revisions, and this does not pretend otherwise.
    """
    for line in getattr(quotation, "lines", []) or []:
        yield Line(
            key=str(getattr(line, "code", "") or ""),
            description=str(getattr(line, "description", "")),
            quantity=float(getattr(line, "quantity", 0.0)),
            unit=str(getattr(line, "unit", "")),
            unit_price=float(getattr(line, "unit_price", 0.0)),
            category=str(getattr(line, "category", "material")),
        )


class RevisionBook:
    """Every job's quotation history, kept on disk."""

    def __init__(self, path: Path | None = None) -> None:
        from ..core.config import get_settings

        self.path = (
            Path(path) if path else get_settings().data_dir / "quote_revisions.json"
        )
        self._histories: dict[str, QuoteHistory] = {}

    def load(self) -> "RevisionBook":
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return self
        for item in raw.get("histories", []):
            try:
                history = QuoteHistory.from_dict(item)
            except (ValueError, KeyError) as exc:
                _log.warning("Skipped an unreadable quote history: %s", exc)
                continue
            self._histories[history.job_id] = history
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written": datetime.now().isoformat(timespec="seconds"),
            "histories": [h.as_dict() for h in self._histories.values()],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def for_job(self, job_id: str) -> QuoteHistory:
        if job_id not in self._histories:
            self._histories[job_id] = QuoteHistory(job_id=job_id)
        return self._histories[job_id]

    def __len__(self) -> int:
        return len(self._histories)

    def __iter__(self):
        return iter(self._histories.values())


def default_revisions() -> RevisionBook:
    return RevisionBook().load()


__all__ = [
    "Change",
    "Comparison",
    "Line",
    "QuoteHistory",
    "Revision",
    "RevisionBook",
    "compare",
    "default_revisions",
]
