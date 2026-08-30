"""Measuring the hole in the wall, and turning it into a frame size.

Everything upstream of this is arithmetic on numbers somebody typed. This is
the one place the software meets a building, and it is where the expensive
mistakes are made: a window an aluminium fabricator cannot make smaller once
it is welded, fitted into an opening that turned out to be ⁦14⁩ mm narrower at
the bottom than at the top.

The trade's own method is the method here, and it is not "measure the
opening". It is:

* three widths — at the head, the middle and the sill — and the **smallest**
  is the one the frame has to pass through;
* three heights — at the left, the middle and the right, likewise;
* both diagonals, because a rectangle is only a rectangle if they match, and
  an opening out of square by more than the packers can hide shows as a taper
  down the side of a finished frame;
* the finished floor level, because a sill measured from a screed that has not
  been poured is a sill in the wrong place.

The frame size is then the smallest structural dimension less the fitting
clearance on both sides — a figure that belongs to the system, not to this
module, so it is passed in or it stays empty. Guessing it is exactly the kind
of invention that puts a window in a skip.

An opening that has not been measured is not measured. It is never filled in
from the drawing "for now", because a frame size on a drawing and a frame size
on a measurement sheet look identical the morning the saw runs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("delivery.survey")

#: Out-of-square beyond this, in millimetres, shows on a finished frame however
#: carefully it is packed. It is a workmanship figure, not a standard, and it
#: is kept here as one number a shop can argue with rather than buried.
SQUARE_TOLERANCE_MM = 10.0

#: A spread between the three widths (or heights) beyond this means the opening
#: is not parallel, and a single "opening width" is a fiction.
SPREAD_TOLERANCE_MM = 8.0

#: Fitting clearance per side when the system does not state one. It is
#: deliberately not applied: it is what the warning quotes as typical, so the
#: number a person types has something to be judged against.
TYPICAL_CLEARANCE_MM = 10.0


def _smallest(values: Iterable[float]) -> float | None:
    kept = [float(value) for value in values if value is not None and value > 0]
    return min(kept) if kept else None


def _spread(values: Iterable[float]) -> float:
    kept = [float(value) for value in values if value is not None and value > 0]
    return round(max(kept) - min(kept), 1) if len(kept) > 1 else 0.0


@dataclass
class OpeningSurvey:
    """One hole in a wall, measured the way it has to be measured."""

    reference: str = ""
    room: str = ""
    floor: str = ""
    #: Widths at head, middle and sill [mm].
    width_head: float | None = None
    width_middle: float | None = None
    width_sill: float | None = None
    #: Heights at left, middle and right [mm].
    height_left: float | None = None
    height_middle: float | None = None
    height_right: float | None = None
    #: Both diagonals [mm]. Equal means square.
    diagonal_a: float | None = None
    diagonal_b: float | None = None
    #: Height of the sill above the finished floor [mm].
    sill_above_floor: float | None = None
    #: Whether that floor is the finished one or a slab awaiting screed.
    floor_is_finished: bool = False
    #: Depth of the reveal the frame sits in [mm].
    reveal_depth: float | None = None
    wall_type: str = ""
    #: Fitting clearance per side, from the system. Never invented here.
    clearance_per_side: float | None = None
    measured_by: str = ""
    measured_on: date | None = None
    note: str = ""
    photographs: list[str] = field(default_factory=list)

    # -- what was found -------------------------------------------------------- #
    @property
    def is_measured(self) -> bool:
        """Whether anybody has actually been to the building."""
        return self.measured_on is not None and self.smallest_width is not None

    @property
    def widths(self) -> list[float]:
        return [
            value for value in
            (self.width_head, self.width_middle, self.width_sill)
            if value is not None and value > 0
        ]

    @property
    def heights(self) -> list[float]:
        return [
            value for value in
            (self.height_left, self.height_middle, self.height_right)
            if value is not None and value > 0
        ]

    @property
    def smallest_width(self) -> float | None:
        return _smallest(self.widths)

    @property
    def smallest_height(self) -> float | None:
        return _smallest(self.heights)

    @property
    def width_spread(self) -> float:
        return _spread(self.widths)

    @property
    def height_spread(self) -> float:
        return _spread(self.heights)

    @property
    def out_of_square(self) -> float | None:
        """Difference between the diagonals [mm]. ``None`` if not both taken."""
        if self.diagonal_a is None or self.diagonal_b is None:
            return None
        return round(abs(self.diagonal_a - self.diagonal_b), 1)

    @property
    def implied_lean(self) -> float | None:
        """Roughly how far out of plumb the opening is, from the diagonals.

        A diagonal difference is easier to measure than an angle and harder to
        picture. For an opening of width ``w`` and height ``h``, a difference
        ``d`` between the diagonals corresponds to the top being displaced
        sideways by about ``d·√(w²+h²) / (2w)`` — near enough for deciding
        whether the packers will cope.
        """
        difference = self.out_of_square
        width, height = self.smallest_width, self.smallest_height
        if difference is None or not width or not height:
            return None
        diagonal = math.hypot(width, height)
        return round(difference * diagonal / (2.0 * width), 1)

    # -- what to make ----------------------------------------------------------- #
    def frame_size(self) -> tuple[float, float] | None:
        """Outer frame width and height, or ``None`` when it cannot be known.

        It cannot be known without a clearance from the system. Returning a
        plausible pair here would be the single most expensive invention this
        software could make.
        """
        width, height = self.smallest_width, self.smallest_height
        if width is None or height is None or self.clearance_per_side is None:
            return None
        return (
            round(width - 2 * self.clearance_per_side, 1),
            round(height - 2 * self.clearance_per_side, 1),
        )

    def describe(self) -> str:
        # The reference is Latin text at the head of a Hebrew line, so it is
        # isolated like a number: without that, "D1 · פתח …" comes out with
        # the marking somewhere in the middle of the sentence.
        marking = f"⁦{self.reference}⁩" if self.reference else "—"
        if not self.is_measured:
            return f"{marking} · לא נמדד"
        frame = self.frame_size()
        body = (
            f"{marking} · פתח ⁦{self.smallest_width:g}×"
            f"{self.smallest_height:g}⁩"
        )
        if frame:
            body += f" · מסגרת ⁦{frame[0]:g}×{frame[1]:g}⁩"
        else:
            body += " · מסגרת: חסר מרווח התקנה"
        square = self.out_of_square
        if square is not None:
            body += f" · אלכסונים ⁦{square:g}⁩"
        return body

    def problems(self) -> list[str]:
        """What would make a frame cut to this measurement not fit."""
        found: list[str] = []
        if not self.widths:
            found.append("לא נמדד אף רוחב")
        elif len(self.widths) < 3:
            found.append(
                "נמדדו פחות משלושה רוחבים — פתח שאינו מקביל ייראה תקין "
                "במדידה אחת"
            )
        if not self.heights:
            found.append("לא נמדד אף גובה")
        elif len(self.heights) < 3:
            found.append("נמדדו פחות משלושה גבהים")

        if self.width_spread > SPREAD_TOLERANCE_MM:
            found.append(
                f"הפרש ⁦{self.width_spread:g}⁩ מ״מ בין הרוחבים — הפתח אינו "
                "מקביל, מסגרת אחידה תשאיר מרווח משתנה"
            )
        if self.height_spread > SPREAD_TOLERANCE_MM:
            found.append(
                f"הפרש ⁦{self.height_spread:g}⁩ מ״מ בין הגבהים — הפתח אינו מקביל"
            )

        square = self.out_of_square
        if square is None:
            found.append(
                "לא נמדדו אלכסונים — בלעדיהם אי אפשר לדעת אם הפתח מלבני"
            )
        elif square > SQUARE_TOLERANCE_MM:
            lean = self.implied_lean
            detail = f" (סטייה של כ-⁦{lean:g}⁩ מ״מ)" if lean else ""
            found.append(
                f"הפתח מחוץ לזווית ב-⁦{square:g}⁩ מ״מ{detail} — מעל "
                f"⁦{SQUARE_TOLERANCE_MM:g}⁩ מ״מ זה נראה על המסגרת הגמורה"
            )

        if self.sill_above_floor is not None and not self.floor_is_finished:
            found.append(
                "גובה הסף נמדד מרצפה שאינה גמורה — אחרי הריצוף הסף יהיה "
                "במקום אחר"
            )
        if self.clearance_per_side is None:
            found.append(
                "לא נקבע מרווח התקנה לסדרה — אי אפשר לגזור מידת מסגרת. "
                f"מקובל כ-⁦{TYPICAL_CLEARANCE_MM:g}⁩ מ״מ לצד, אך זה נתון "
                "של היצרן ולא ניחוש שלנו"
            )
        elif self.clearance_per_side <= 0:
            found.append("מרווח התקנה אפס — מסגרת לא תיכנס לפתח")
        if not self.measured_by.strip():
            found.append("לא נרשם מי מדד — שאלה מחר לא תהיה למי להפנות")
        return found

    @property
    def may_be_made(self) -> bool:
        """Whether a frame may be cut to this. Not the same as "measured"."""
        return self.is_measured and not self.problems()

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference, "room": self.room, "floor": self.floor,
            "width_head": self.width_head, "width_middle": self.width_middle,
            "width_sill": self.width_sill, "height_left": self.height_left,
            "height_middle": self.height_middle, "height_right": self.height_right,
            "diagonal_a": self.diagonal_a, "diagonal_b": self.diagonal_b,
            "sill_above_floor": self.sill_above_floor,
            "floor_is_finished": self.floor_is_finished,
            "reveal_depth": self.reveal_depth, "wall_type": self.wall_type,
            "clearance_per_side": self.clearance_per_side,
            "measured_by": self.measured_by,
            "measured_on": self.measured_on.isoformat() if self.measured_on else None,
            "note": self.note, "photographs": list(self.photographs),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OpeningSurvey":
        when = raw.get("measured_on")

        def number(key: str) -> float | None:
            value = raw.get(key)
            return None if value is None else float(value)

        return cls(
            reference=str(raw.get("reference", "")),
            room=str(raw.get("room", "")), floor=str(raw.get("floor", "")),
            width_head=number("width_head"), width_middle=number("width_middle"),
            width_sill=number("width_sill"), height_left=number("height_left"),
            height_middle=number("height_middle"),
            height_right=number("height_right"),
            diagonal_a=number("diagonal_a"), diagonal_b=number("diagonal_b"),
            sill_above_floor=number("sill_above_floor"),
            floor_is_finished=bool(raw.get("floor_is_finished", False)),
            reveal_depth=number("reveal_depth"),
            wall_type=str(raw.get("wall_type", "")),
            clearance_per_side=number("clearance_per_side"),
            measured_by=str(raw.get("measured_by", "")),
            measured_on=date.fromisoformat(str(when)) if when else None,
            note=str(raw.get("note", "")),
            photographs=list(raw.get("photographs", [])),
        )


@dataclass
class Survey:
    """A job's measurements: one sheet, carried to the site and back."""

    survey_id: str = field(default_factory=lambda: f"SV-{uuid4().hex[:6].upper()}")
    job_id: str = ""
    job_name: str = ""
    site_address: str = ""
    opened: date = field(default_factory=date.today)
    openings: list[OpeningSurvey] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.openings)

    def __iter__(self):
        return iter(self.openings)

    def opening(self, reference: str) -> OpeningSurvey:
        for entry in self.openings:
            if entry.reference == reference:
                return entry
        raise ProfileOSError(f"אין פתח {reference} בגיליון המדידה")

    @property
    def measured(self) -> list[OpeningSurvey]:
        return [entry for entry in self.openings if entry.is_measured]

    @property
    def unmeasured(self) -> list[OpeningSurvey]:
        return [entry for entry in self.openings if not entry.is_measured]

    @property
    def makeable(self) -> list[OpeningSurvey]:
        return [entry for entry in self.openings if entry.may_be_made]

    @property
    def progress(self) -> float:
        return (
            len(self.measured) / len(self.openings) * 100.0
            if self.openings else 0.0
        )

    def problems(self) -> list[str]:
        """Everything standing between this sheet and a saw, opening by opening."""
        found: list[str] = []
        if not self.openings:
            return ["אין פתחים בגיליון"]
        if self.unmeasured:
            found.append(
                f"⁦{len(self.unmeasured)}⁩ פתחים לא נמדדו — הם לא ייוצרו "
                "לפי מידות מהתכנית"
            )
        for entry in self.measured:
            for problem in entry.problems():
                marking = f"⁦{entry.reference}⁩" if entry.reference else "—"
                found.append(f"{marking}: {problem}")
        return found

    @property
    def may_be_made(self) -> bool:
        return bool(self.openings) and not self.problems()

    def describe(self) -> str:
        return (
            f"⁦{self.survey_id}⁩ · {self.job_name or self.job_id} · "
            f"נמדדו ⁦{len(self.measured)}⁩ מתוך ⁦{len(self.openings)}⁩ · "
            f"מוכנים לייצור ⁦{len(self.makeable)}⁩"
        )

    # -- the sheet that goes to the site ---------------------------------------- #
    SHEET_HEADERS = (
        "סימון", "חדר", "רוחב עליון", "רוחב אמצע", "רוחב תחתון",
        "גובה שמאל", "גובה אמצע", "גובה ימין", "אלכסון א", "אלכסון ב",
        "סף מעל רצפה", "עומק גליף", "הערות",
    )

    def sheet_rows(self) -> list[list[str]]:
        """Blank boxes, in the order somebody standing at the opening fills them."""
        return [
            [entry.reference, entry.room] + [""] * (len(self.SHEET_HEADERS) - 2)
            for entry in self.openings
        ]

    # -- what it becomes --------------------------------------------------------- #
    def to_openings(self, template: Any = None) -> list[Any]:
        """Turn the measured sizes into elements ready to be built.

        Only the openings that may be made. An opening that was measured but
        is out of square, or has no clearance from its system, is left out and
        named — it is not quietly rounded into the batch.
        """
        from ..elements.model import Opening

        made: list[Any] = []
        for entry in self.makeable:
            size = entry.frame_size()
            if size is None:  # pragma: no cover - may_be_made covers this
                continue
            width, height = size
            values: dict[str, Any] = {
                "name": entry.reference or "פתח",
                "width": width,
                "height": height,
                "reference": entry.reference or None,
            }
            if template is not None:
                base = template.model_dump()
                base.pop("element_id", None)
                base.update(values)
                made.append(Opening(**base))
            else:
                made.append(Opening(**values))
        return made

    def as_dict(self) -> dict[str, Any]:
        return {
            "survey_id": self.survey_id, "job_id": self.job_id,
            "job_name": self.job_name, "site_address": self.site_address,
            "opened": self.opened.isoformat(),
            "openings": [entry.as_dict() for entry in self.openings],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Survey":
        return cls(
            survey_id=str(raw.get("survey_id", "")),
            job_id=str(raw.get("job_id", "")),
            job_name=str(raw.get("job_name", "")),
            site_address=str(raw.get("site_address", "")),
            opened=date.fromisoformat(
                str(raw.get("opened", date.today().isoformat()))
            ),
            openings=[
                OpeningSurvey.from_dict(item) for item in raw.get("openings", [])
            ],
        )


def survey_for_job(job: Any, *, clearance_per_side: float | None = None) -> Survey:
    """Open a measurement sheet with a line per opening the job already has.

    The schedule says what to expect; it does not say what is there. Every
    line comes back blank.
    """
    survey = Survey(
        job_id=str(getattr(job, "job_id", "")),
        job_name=str(getattr(job, "name", "")),
        site_address=str(getattr(job, "site_address", "")),
    )
    schedule = getattr(job, "schedule", None)
    for opening in getattr(schedule, "openings", []) or []:
        survey.openings.append(OpeningSurvey(
            reference=str(getattr(opening, "reference", "") or opening.name),
            clearance_per_side=clearance_per_side,
        ))
    _log.info(
        "Survey %s opened for %s with %d openings",
        survey.survey_id, survey.job_id, len(survey),
    )
    return survey


class SurveyBook:
    """Every measurement sheet the shop has taken, kept on disk."""

    def __init__(self, path: Path | None = None) -> None:
        from ..core.config import get_settings

        self.path = Path(path) if path else get_settings().data_dir / "surveys.json"
        self._surveys: dict[str, Survey] = {}

    def load(self) -> "SurveyBook":
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return self
        for item in raw.get("surveys", []):
            try:
                survey = Survey.from_dict(item)
            except (ValueError, KeyError) as exc:
                _log.warning("Skipped an unreadable survey: %s", exc)
                continue
            self._surveys[survey.survey_id] = survey
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written": datetime.now().isoformat(timespec="seconds"),
            "surveys": [survey.as_dict() for survey in self._surveys.values()],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def add(self, survey: Survey) -> Survey:
        self._surveys[survey.survey_id] = survey
        self.save()
        return survey

    def get(self, survey_id: str) -> Survey:
        if survey_id not in self._surveys:
            raise ProfileOSError(f"אין גיליון מדידה {survey_id}")
        return self._surveys[survey_id]

    def for_job(self, job_id: str) -> list[Survey]:
        return [survey for survey in self if survey.job_id == job_id]

    def __len__(self) -> int:
        return len(self._surveys)

    def __iter__(self):
        return iter(
            sorted(self._surveys.values(), key=lambda s: s.opened, reverse=True)
        )


def default_surveys() -> SurveyBook:
    return SurveyBook().load()


__all__ = [
    "SPREAD_TOLERANCE_MM",
    "SQUARE_TOLERANCE_MM",
    "TYPICAL_CLEARANCE_MM",
    "OpeningSurvey",
    "Survey",
    "SurveyBook",
    "default_surveys",
    "survey_for_job",
]
