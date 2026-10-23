"""The final measurement against the price the customer accepted.

A job is quoted from the architect's drawing or a quick look on site. The
fitter measures properly once the plaster is on, and the openings come out
bigger — 1500 on the drawing is 1540 in the wall. The shop makes 1540 and
bills the 1500 it quoted, because nobody went back to the quotation with the
new numbers. On a house of windows that is a few thousand shekels a job.

This puts the two side by side, opening by opening: the size quoted, the frame
the measurement calls for, and what the difference costs at the rate that
opening was sold at — the accepted version's own price per square metre, not
a list price and not a guess. Growth becomes a suggested extra the shop can
send for approval in one click; a smaller opening is shown as well, because
the customer will ask about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: Below this an opening "grew" by measuring tolerance, not by being bigger.
GROWTH_SHARE = 0.01
#: Below this an extra is not worth the customer's goodwill [₪ per job].
WORTH_ASKING = 50.0

ORIGIN = "measure:"


@dataclass(frozen=True)
class SizeChange:
    """One opening: what was sold, what is being made, what it is worth."""

    element_id: str
    reference: str
    quantity: int
    quoted: tuple[float, float]
    measured: tuple[float, float]
    #: The accepted version's price for one piece of this opening.
    quoted_price: float

    @property
    def quoted_m2(self) -> float:
        return self.quoted[0] * self.quoted[1] / 1e6

    @property
    def measured_m2(self) -> float:
        return self.measured[0] * self.measured[1] / 1e6

    @property
    def rate_m2(self) -> float:
        """What this opening was sold at, per square metre."""
        return self.quoted_price / self.quoted_m2 if self.quoted_m2 > 0 else 0.0

    @property
    def per_piece(self) -> float:
        return round(self.rate_m2 * (self.measured_m2 - self.quoted_m2), 2)

    @property
    def amount(self) -> float:
        return round(self.per_piece * self.quantity, 2)

    @property
    def grew(self) -> bool:
        return self.measured_m2 > self.quoted_m2 * (1 + GROWTH_SHARE)

    @property
    def shrank(self) -> bool:
        return self.measured_m2 < self.quoted_m2 * (1 - GROWTH_SHARE)

    def describe(self) -> str:
        qw, qh = self.quoted
        mw, mh = self.measured
        sign = "+" if self.amount >= 0 else "−"
        return (
            f"⁦{self.reference}⁩: הוצע ⁦{qw:.0f}×{qh:.0f}⁩, נמדד ⁦{mw:.0f}×{mh:.0f}⁩ מ״מ"
            + (f" × ⁦{self.quantity}⁩" if self.quantity > 1 else "")
            + f" — ⁦{sign}{abs(self.amount):,.0f} ₪⁩ לפי מחיר ההצעה"
        )

    def extra_line(self) -> dict[str, Any]:
        """The extra to send for approval: priced per piece, at the sold rate."""
        qw, qh = self.quoted
        mw, mh = self.measured
        return {
            "description": (
                f"הגדלת {self.reference} לפי מדידה סופית: "
                f"⁦{qw:.0f}×{qh:.0f}⁩ ← ⁦{mw:.0f}×{mh:.0f}⁩ מ״מ"
            ),
            "quantity": float(self.quantity),
            "unit": "יח׳",
            "unit_price": self.per_piece,
            "origin": ORIGIN + self.element_id,
        }


def accepted_revision(job: Any, history: Any) -> Any:
    """The version the customer agreed to, or the last one they were sent."""
    number = getattr(job, "accepted_revision", 0) or 0
    if number:
        try:
            return history.get(number)
        except Exception:  # noqa: BLE001 - fall back to what they hold
            pass
    return history.last_sent or history.current


def compare(job: Any, survey: Any, revision: Any) -> list[SizeChange]:
    """Each measured opening whose frame differs from what was quoted.

    Only openings the sheet says may be made (measured, square, with the
    system's clearance) and that the accepted version priced on its own line.
    """
    if revision is None or survey is None or getattr(job, "schedule", None) is None:
        return []
    priced = {line.key: line for line in revision.lines if line.key}
    by_marking = {}
    for opening in job.schedule.openings:
        by_marking[str(opening.reference or opening.name)] = opening
    changes: list[SizeChange] = []
    for entry in survey.openings:
        opening = by_marking.get(entry.reference)
        if opening is None or not entry.may_be_made:
            continue
        line = priced.get(opening.element_id)
        if line is None or line.unit_price <= 0:
            continue
        frame = entry.frame_size()
        if frame is None:
            continue
        # The size the customer agreed to, as the version recorded it; an
        # older version without sizes falls back to the job's opening.
        quoted = ((line.width, line.height) if line.width and line.height
                  else (opening.width, opening.height))
        change = SizeChange(
            element_id=opening.element_id, reference=entry.reference,
            quantity=int(opening.quantity), quoted=quoted,
            measured=(round(frame[0], 1), round(frame[1], 1)),
            quoted_price=line.unit_price,
        )
        if change.grew or change.shrank:
            changes.append(change)
    return changes


def not_compared(job: Any, survey: Any, revision: Any) -> list[tuple[str, str]]:
    """``(marking, why)`` for every measured opening ``compare`` had to leave
    out, by the same rules — "all match" must never be said about an opening
    that was not looked at."""
    if survey is None:
        return []
    priced = {line.key: line for line in getattr(revision, "lines", []) if line.key}
    schedule = getattr(job, "schedule", None)
    by_marking = {str(opening.reference or opening.name): opening
                  for opening in getattr(schedule, "openings", []) or []}
    left: list[tuple[str, str]] = []
    for entry in survey.openings:
        if not entry.is_measured:
            continue
        problems = entry.problems()
        opening = by_marking.get(entry.reference)
        line = priced.get(opening.element_id) if opening is not None else None
        if problems:
            left.append((entry.reference, problems[0]))
        elif entry.frame_size() is None:
            left.append((entry.reference, "אין מרווח התקנה מהשיטה — מידת המשקוף לא ידועה"))
        elif opening is None:
            left.append((entry.reference, "אין פתח בסימון הזה בפרויקט"))
        elif line is None:
            left.append((entry.reference, "לא תומחר בשורה משלו בגרסה שאושרה"))
        elif line.unit_price <= 0:
            left.append((entry.reference, "המחיר שלו בגרסה שאושרה הוא אפס"))
    return left


def unbilled(job: Any, changes: Iterable[SizeChange]) -> list[SizeChange]:
    """Openings that grew with no extra for them on the job yet."""
    billed = {line.origin for line in getattr(job, "lines", []) if line.origin}
    return [
        change for change in changes
        if change.grew and ORIGIN + change.element_id not in billed
    ]


def worth_asking(changes: Iterable[SizeChange]) -> bool:
    return sum(change.amount for change in changes if change.grew) >= WORTH_ASKING


__all__ = [
    "GROWTH_SHARE",
    "ORIGIN",
    "SizeChange",
    "WORTH_ASKING",
    "accepted_revision",
    "compare",
    "not_compared",
    "unbilled",
    "worth_asking",
]
