"""Remakes: a piece cut to the wrong size, a scratched profile, a pane that
broke — recorded in two taps, back on the floor, and counted.

A re-cut nobody wrote down becomes a missing piece on installation day, and
the owner never sees which mistake keeps coming back. Here a scrapped piece
gets a reason; a new piece takes its place at the start of the floor (the
next cut list, the next glass order); and the month's remakes are added up
by reason — with their cost where somebody knew it, never a guessed one.

The record travels on the new piece itself (``metadata["remake"]``), so it
is kept in the job's work order through every merge between screens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from .tracking import ProductionItem, Stage

#: (key, Hebrew) — the reasons a shop actually writes on the scrap pile.
REASONS: tuple[tuple[str, str], ...] = (
    ("wrong_size", "נחתך במידה שגויה"),
    ("damaged", "שריטה או מכה בייצור"),
    ("broken", "נשבר בייצור"),
    ("supplier", "פגם מהספק"),
    ("site", "נפגע בהובלה או באתר"),
    ("other", "אחר"),
)
REASON_HEBREW = dict(REASONS)
KEY = "remake"


def remake(order: Any, item_id: str, *, reason: str, note: str = "", by: str = "",
           cost: float = 0.0, on: date | None = None) -> ProductionItem:
    """Scrap ``item_id`` and put a new piece for it at the start of the floor.

    ``cost`` is what the scrapped piece cost, if the person knows it; ``0``
    leaves it unknown and it is not counted as zero.
    """
    if reason not in REASON_HEBREW:
        raise ProfileOSError("בחרו למה הפריט נפסל")
    if reason == "other" and not note.strip():
        raise ProfileOSError("כתבו בכמה מילים מה קרה לפריט")
    item = order.find(item_id)
    if item is None:
        raise ProfileOSError(f"אין בפקודת העבודה פריט ⁦{item_id}⁩")
    ok, why = item.advance(Stage.SCRAPPED, operator=by or None, station="פסילה",
                           note=f"{REASON_HEBREW[reason]}{' — ' + note.strip() if note.strip() else ''}")
    if not ok:
        raise ProfileOSError(f"אי אפשר לפסול את ⁦{item_id}⁩: {why}")
    stamp = uuid4().hex[:4].upper()
    fresh = ProductionItem(
        item_id=f"{item.item_id}-R{stamp}", kind=item.kind, description=item.description,
        project_id=item.project_id, element_ref=item.element_ref,
        barcode=f"{item.barcode}-R{stamp}" if item.barcode else None,
        quantity=item.quantity,
        metadata={**{k: v for k, v in item.metadata.items() if k != KEY},
                  KEY: {"of": item.item_id, "reason": reason, "note": note.strip(),
                        "by": by, "cost": round(float(cost or 0), 2),
                        "on": (on or datetime.now(timezone.utc).date()).isoformat()}},
    )
    order.add(fresh)
    return fresh


@dataclass
class RemakeLine:
    job_id: str
    item_id: str
    description: str
    reason: str
    note: str
    on: date
    cost: float

    @property
    def reason_hebrew(self) -> str:
        return REASON_HEBREW.get(self.reason, self.reason)


@dataclass
class RemakeReport:
    lines: list[RemakeLine] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.lines)

    @property
    def known_cost(self) -> float:
        return round(sum(line.cost for line in self.lines), 2)

    @property
    def unpriced(self) -> int:
        return sum(1 for line in self.lines if line.cost <= 0)

    def by_reason(self) -> list[tuple[str, int, float]]:
        totals: dict[str, list[float]] = {}
        for line in self.lines:
            totals.setdefault(line.reason_hebrew, []).append(line.cost)
        return sorted(((reason, len(costs), round(sum(costs), 2))
                       for reason, costs in totals.items()), key=lambda row: -row[1])

    def describe(self) -> str:
        if not self.lines:
            return "אין פריטים שנפסלו"
        top, count, _cost = self.by_reason()[0]
        said = (f"⁦{self.count}⁩ פריטים נפסלו ויוצרו מחדש — הכי הרבה: {top} (⁦{count}⁩)")
        if self.known_cost:
            said += f" · עלות ידועה ⁦{self.known_cost:,.0f} ₪⁩"
        if self.unpriced:
            said += f" · ל־⁦{self.unpriced}⁩ לא נרשמה עלות"
        return said


def remakes(orders: Iterable[tuple[str, Any]], *, start: date | None = None,
            end: date | None = None) -> RemakeReport:
    """The remakes recorded in ``(job_id, work order)`` pairs, between two dates."""
    report = RemakeReport()
    for job_id, order in orders:
        for item in getattr(order, "items", []) or []:
            record = (item.metadata or {}).get(KEY)
            if not isinstance(record, dict):
                continue
            try:
                on = date.fromisoformat(str(record.get("on", ""))[:10])
            except ValueError:
                continue
            if (start and on < start) or (end and on > end):
                continue
            report.lines.append(RemakeLine(
                job_id, str(record.get("of", item.item_id)), item.description,
                str(record.get("reason", "other")), str(record.get("note", "")), on,
                float(record.get("cost", 0) or 0)))
    return report


__all__ = ["KEY", "REASONS", "REASON_HEBREW", "RemakeLine", "RemakeReport", "remake", "remakes"]
