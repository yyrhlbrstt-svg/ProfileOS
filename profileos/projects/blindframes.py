"""Two-stage installation: blind frames first, the windows months later.

On a new build the shop sets a blind frame (משקוף עיוור) in each opening while
the walls are still bare block — plumb, level, to the contractor's level line
— and the plaster is brought up to it. The windows are measured from the
blind frames once the plaster is done, made, and fitted after tiling. Weeks to
months pass between the two visits, and that gap is where the money goes:

* a blind frame knocked crooked by another trade, or plaster in its rebate,
  found only on the day the windows arrive;
* windows made to the drawing, not to the blind frame as it was built;
* a job "waiting for the contractor" that nobody chases.

So each opening carries its blind frame's own record — installed (when, by
whom, to which level mark), checked after the plaster (square and clean, or
what is wrong) — and the job knows which stage it is at. Nothing here sets a
size: the deduction from a blind frame to a window frame is the series' own
figure, confirmed from its catalogue, or it is typed by the person.

The record lives in each opening's ``metadata["blind_frame"]``, so it travels
with the job file and its backups.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date
from enum import StrEnum
from typing import Any, Iterable

from ..core.errors import ProfileOSError

KEY = "blind_frame"

#: Blind frames in this long and not yet checked after plastering: worth a
#: call to the contractor — the windows cannot be measured before.
CHASE_AFTER_DAYS = 30


@dataclass
class BlindFrame:
    """One opening's blind frame, as the shop recorded it."""

    installed_on: str = ""
    installed_by: str = ""
    #: The contractor's mark it was set to ("קו 1.00+ בקומה"): when the
    #: mark was wrong, the contractor carries it; when the setting was, the shop.
    level_mark: str = ""
    checked_on: str = ""
    #: After the plaster: True square and clean, False something to fix,
    #: None not checked yet.
    check_ok: bool | None = None
    check_note: str = ""

    @property
    def installed(self) -> bool:
        return bool(self.installed_on)

    @property
    def checked(self) -> bool:
        return self.check_ok is not None

    @classmethod
    def of(cls, opening: Any) -> "BlindFrame | None":
        raw = (getattr(opening, "metadata", None) or {}).get(KEY)
        if not isinstance(raw, dict):
            return None
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def put(self, opening: Any) -> None:
        opening.metadata[KEY] = asdict(self)


class Stage(StrEnum):
    TO_INSTALL = "to_install"
    PARTLY = "partly"
    WAITING_PLASTER = "waiting_plaster"
    PROBLEMS = "problems"
    READY_TO_MEASURE = "ready_to_measure"

    @property
    def hebrew(self) -> str:
        return {
            "to_install": "משקופים עיוורים להתקנה",
            "partly": "חלק מהמשקופים העיוורים הותקנו",
            "waiting_plaster": "משקופים עיוורים הותקנו — מחכים לטיח",
            "problems": "משקופים עיוורים לתיקון אחרי הטיח",
            "ready_to_measure": "אפשר למדוד חלונות מהמשקופים העיוורים",
        }[self.value]


def _openings(job: Any) -> list[Any]:
    return list(getattr(getattr(job, "schedule", None), "openings", None) or [])


def frames(job: Any) -> list[tuple[Any, BlindFrame]]:
    """``(opening, blind frame)`` for every opening that has one."""
    found = []
    for opening in _openings(job):
        frame = BlindFrame.of(opening)
        if frame is not None:
            found.append((opening, frame))
    return found


def is_two_stage(job: Any) -> bool:
    return bool(frames(job))


def marking(opening: Any) -> str:
    return str(getattr(opening, "reference", "") or opening.name)


def set_two_stage(job: Any, on: bool) -> None:
    """Every opening gets a blind frame, or none does. A blind frame already
    recorded as installed is not taken off: that would erase a site visit."""
    if on:
        for opening in _openings(job):
            if BlindFrame.of(opening) is None:
                BlindFrame().put(opening)
        return
    installed = [f"⁦{marking(opening)}⁩" for opening, frame in frames(job) if frame.installed]
    if installed:
        raise ProfileOSError(
            "יש משקופים עיוורים שכבר נרשמו כמותקנים (" + ", ".join(installed[:5])
            + ") — אי אפשר לבטל את ההתקנה בשני שלבים")
    for opening in _openings(job):
        opening.metadata.pop(KEY, None)


def _chosen(job: Any, markings: Iterable[str]) -> list[tuple[Any, BlindFrame]]:
    """The openings picked — by element id, or by marking when it is unique
    (two openings both called W1 are not both marked by picking one)."""
    wanted = set(markings)
    every = frames(job)
    counts: dict[str, int] = {}
    for opening, _frame in every:
        counts[marking(opening)] = counts.get(marking(opening), 0) + 1
    chosen = [(opening, frame) for opening, frame in every
              if opening.element_id in wanted
              or (marking(opening) in wanted and counts[marking(opening)] == 1)]
    if not chosen:
        raise ProfileOSError("לא נבחרו פתחים עם משקוף עיוור")
    return chosen


def record_installed(job: Any, markings: Iterable[str], *, by: str, level_mark: str = "",
                     on: date | None = None) -> int:
    if not by.strip():
        raise ProfileOSError("מי התקין? רשמו את שם המתקין")
    chosen = _chosen(job, markings)
    for opening, frame in chosen:
        frame.installed_on = (on or date.today()).isoformat()
        frame.installed_by = by.strip()
        frame.level_mark = level_mark.strip()
        frame.put(opening)
    return len(chosen)


def record_check(job: Any, markings: Iterable[str], *, ok: bool, note: str = "",
                 on: date | None = None) -> int:
    """After the plaster: the blind frames looked at again."""
    chosen = _chosen(job, markings)
    not_in = [f"⁦{marking(opening)}⁩" for opening, frame in chosen if not frame.installed]
    if not_in:
        raise ProfileOSError("אי אפשר לבדוק משקוף שעוד לא הותקן: " + ", ".join(not_in[:5]))
    if not ok and not note.strip():
        raise ProfileOSError("מה צריך לתקן? כתבו בכמה מילים (טיח בתוך המשקוף, לא בפלס…)")
    for opening, frame in chosen:
        frame.checked_on = (on or date.today()).isoformat()
        frame.check_ok = ok
        frame.check_note = note.strip() if not ok else ""
        frame.put(opening)
    return len(chosen)


def stage(job: Any) -> Stage | None:
    """Where the blind frames stand; ``None`` for a one-stage job."""
    found = [frame for _opening, frame in frames(job)]
    if not found:
        return None
    installed = [frame for frame in found if frame.installed]
    if not installed:
        return Stage.TO_INSTALL
    if len(installed) < len(found):
        return Stage.PARTLY
    if any(frame.check_ok is False for frame in found):
        return Stage.PROBLEMS
    if all(frame.check_ok for frame in found):
        return Stage.READY_TO_MEASURE
    return Stage.WAITING_PLASTER


def describe(job: Any) -> str:
    """The stage in a line, with the counts that make it true."""
    current = stage(job)
    if current is None:
        return "התקנה בשלב אחד"
    found = frames(job)
    installed = sum(1 for _o, frame in found if frame.installed)
    checked = sum(1 for _o, frame in found if frame.check_ok)
    return (f"{current.hebrew} · הותקנו ⁦{installed}⁩ מתוך ⁦{len(found)}⁩"
            + (f" · נבדקו אחרי הטיח ⁦{checked}⁩" if installed else ""))


def to_fix(job: Any) -> list[tuple[str, str]]:
    """``(marking, what)`` for each blind frame found wrong after the plaster."""
    return [(marking(opening), frame.check_note) for opening, frame in frames(job)
            if frame.check_ok is False]


def waiting_since(job: Any) -> date | None:
    """The day the last blind frame went in, while any is unchecked."""
    found = frames(job)
    if not found or any(not frame.installed for _o, frame in found):
        return None
    if all(frame.checked for _o, frame in found):
        return None
    return max(date.fromisoformat(frame.installed_on) for _o, frame in found)


def phone_records_for(job: Any, sheet_stage: str, records: Iterable[Any]) -> list[Any]:
    """The phone's measurements that belong on this sheet. The phone does
    not say whether it measured the wall or the blind frame — the dates do:
    the wall before the first blind frame went in, the blind frames after
    they were set (and checked, once they were)."""
    records = list(records)
    found = frames(job)
    if not found:
        return records if not sheet_stage else []
    installed = [date.fromisoformat(f.installed_on) for _o, f in found if f.installed]
    if not sheet_stage:
        if not installed:
            return records
        first = min(installed)
        return [r for r in records if r.measured_at.astimezone().date() < first]
    if not installed:
        return []
    checked = [date.fromisoformat(f.checked_on) for _o, f in found if f.checked_on]
    start = max(checked) if checked else max(installed)
    return [r for r in records if r.measured_at.astimezone().date() >= start]


def windows_not_ready(job: Any) -> list[str]:
    """What stands between a two-stage job and making or fitting its windows."""
    current = stage(job)
    if current is None or current is Stage.READY_TO_MEASURE:
        return []
    if current in (Stage.TO_INSTALL, Stage.PARTLY):
        missing = [f"⁦{marking(o)}⁩" for o, frame in frames(job) if not frame.installed]
        return [f"משקופים עיוורים עוד לא הותקנו: {', '.join(missing[:6])}"]
    if current is Stage.PROBLEMS:
        return ["משקופים עיוורים לתיקון: " + "; ".join(
            f"⁦{mark}⁩ {what}" for mark, what in to_fix(job)[:4])]
    return ["המשקופים העיוורים עוד לא נבדקו אחרי הטיח"]


__all__ = [
    "BlindFrame",
    "CHASE_AFTER_DAYS",
    "KEY",
    "Stage",
    "describe",
    "frames",
    "is_two_stage",
    "marking",
    "phone_records_for",
    "record_check",
    "record_installed",
    "set_two_stage",
    "stage",
    "to_fix",
    "waiting_since",
    "windows_not_ready",
]
