"""Turning a series from "we can price it" into "we can cut it".

This is the one gap that decides whether the software is production software
this week or next month, and it is not a gap that code can close: the numbers
are in the supplier's catalogue and nowhere else. Inventing them would be
worse than leaving them empty, because a bar cut to a guessed deduction is a
bar in the skip and nobody would know why.

What code *can* do is make entering them take twenty minutes instead of a
week. There are eleven numbers. They are the ones every catalogue in this
trade prints, they are asked for in the order the catalogue prints them, each
one is checked against what is physically possible, and the source is
recorded with them — because a confirmed figure with no record of where it
came from is not confirmed, it is an assertion.

Once they are in, the series is cuttable, the "not for production" banner
comes off its cut sheets, and the entry survives a restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("systems.confirmation")


@dataclass(frozen=True)
class Figure:
    """One number to read out of the catalogue."""

    key: str
    hebrew: str
    english: str
    #: Where in a catalogue this is ordinarily found, in the shop's words.
    where: str
    unit: str = "מ״מ"
    minimum: float = 0.0
    maximum: float = 500.0
    default: float | None = None
    #: Whether the series simply cannot be cut without it.
    required: bool = True

    def check(self, value: float) -> str:
        """What is wrong with this value, in the fabricator's own terms."""
        if value < self.minimum or value > self.maximum:
            return (
                f"{self.hebrew}: ⁦{value:g}⁩ {self.unit} מחוץ לתחום הסביר "
                f"(⁦{self.minimum:g}–{self.maximum:g}⁩)"
            )
        return ""


#: Everything needed to cut a series, in catalogue order. Kept short on
#: purpose: a form with forty fields is a form nobody finishes, and these
#: eleven are what the cut list is actually derived from.
FIGURES: tuple[Figure, ...] = (
    Figure(
        "frame_face", "רוחב פנים המשקוף", "Frame face width",
        "בחתך המשקוף — הרוחב הנראה שקובע את פתח האור",
        minimum=20.0, maximum=200.0, default=45.0,
    ),
    Figure(
        "mullion_face", "רוחב פנים הזקף", "Mullion face width",
        "בחתך הזקף או הקורה",
        minimum=20.0, maximum=250.0, default=50.0,
    ),
    Figure(
        "mullion_end_deduction", "קיזוז קצה זקף", "Mullion end deduction",
        "בטבלת החיתוך — כמה מקצרים כל זקף בכל קצה",
        minimum=-100.0, maximum=100.0, default=0.0, required=False,
    ),
    Figure(
        "sash_overlap", "חפיפת כנף על משקוף", "Sash overlap on frame",
        "בחתך המורכב — כמה הכנף מכסה את המשקוף בכל צד",
        minimum=0.0, maximum=60.0, default=8.0,
    ),
    Figure(
        "sash_clearance", "מרווח כנף-משקוף", "Sash to frame clearance",
        "בטבלת החיתוך של הכנף",
        minimum=0.0, maximum=20.0, default=2.0,
    ),
    Figure(
        "sash_bottom_clearance", "מרווח תחתון", "Bottom clearance",
        "בסף — המרווח לניקוז",
        minimum=0.0, maximum=40.0, default=3.0, required=False,
    ),
    Figure(
        "sash_face", "רוחב פנים הכנף", "Sash face width",
        "בחתך הכנף",
        minimum=20.0, maximum=200.0, default=32.0,
    ),
    Figure(
        "glass_edge_cover", "כיסוי קצה זכוכית", "Glass edge cover",
        "בטבלת הזיגוג — כמה הסרגל מכסה מהשמשה בכל צד",
        minimum=5.0, maximum=40.0, default=15.0,
    ),
    Figure(
        "glass_clearance", "מרווח זכוכית", "Glass edge clearance",
        "בטבלת הזיגוג — המרווח סביב השמשה",
        minimum=0.0, maximum=15.0, default=3.0,
    ),
    Figure(
        "max_glass_thickness", "עובי זיגוג מרבי", "Maximum glass thickness",
        "בטבלת הזיגוג — העובי הגדול ביותר שהמערכת מקבלת",
        minimum=4.0, maximum=80.0, default=52.0,
    ),
    Figure(
        "installation_clearance", "מרווח התקנה", "Installation clearance",
        "בהוראות ההרכבה — המרווח בין המשקוף לפתח הבנייה",
        minimum=0.0, maximum=40.0, default=10.0, required=False,
    ),
)

FIGURE_BY_KEY: dict[str, Figure] = {figure.key: figure for figure in FIGURES}


@dataclass
class Confirmation:
    """One series' figures, and where they were read from."""

    entry_id: str
    #: Which catalogue, which edition, which page. Required.
    source: str = ""
    #: Who read them, so a question later has somebody to ask.
    entered_by: str = ""
    entered_on: str = field(default_factory=lambda: date.today().isoformat())
    values: dict[str, float] = field(default_factory=dict)
    #: Article numbers by role, when the catalogue names them.
    profiles: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def missing(self) -> list[Figure]:
        return [
            figure for figure in FIGURES
            if figure.required and figure.key not in self.values
        ]

    def problems(self) -> list[str]:
        """Everything that would stop this being trusted with a saw."""
        found: list[str] = []
        if not self.source.strip():
            found.append(
                "חסר מקור — איזה קטלוג, איזו מהדורה. בלי זה זו לא אמת מאומתת"
            )
        for figure in self.missing():
            found.append(f"חסר: {figure.hebrew} ({figure.where})")
        for key, value in self.values.items():
            figure = FIGURE_BY_KEY.get(key)
            if figure is None:
                found.append(f"נתון לא מוכר: {key}")
                continue
            problem = figure.check(value)
            if problem:
                found.append(problem)

        # Two cross-checks that catch a transposed pair, which is the mistake
        # somebody typing eleven numbers off a page actually makes.
        cover = self.values.get("glass_edge_cover")
        clearance = self.values.get("glass_clearance")
        if cover is not None and clearance is not None and clearance >= cover:
            found.append(
                "מרווח הזכוכית גדול מהכיסוי — כנראה הוחלפו ביניהם, "
                "השמשה תיפול מהסרגל"
            )
        overlap = self.values.get("sash_overlap")
        sash_clearance = self.values.get("sash_clearance")
        if overlap is not None and sash_clearance is not None and sash_clearance >= overlap:
            found.append(
                "מרווח הכנף גדול מהחפיפה — כנראה הוחלפו ביניהם, הכנף לא תיסגר"
            )
        return found

    @property
    def is_complete(self) -> bool:
        return not self.problems()

    def to_rules(self, name: str = "") -> Any:
        """Build the rule set the cut list is derived from."""
        from ..elements.rules import (
            FrameRules, GlassRules, MullionRules, SashRules, SystemRules,
        )

        problems = self.problems()
        if problems:
            raise ProfileOSError(
                "אי אפשר לאשר את הסדרה: " + " · ".join(problems)
            )
        values = self.values
        return SystemRules(
            id=self.entry_id,
            name=name or self.entry_id,
            supplier=self.source,
            frame=FrameRules(
                face_width=values["frame_face"],
                installation_clearance=values.get("installation_clearance", 10.0),
            ),
            sash=SashRules(
                frame_overlap=values["sash_overlap"],
                rebate_clearance=values["sash_clearance"],
                bottom_clearance=values.get("sash_bottom_clearance", 3.0),
                sash_face_width=values["sash_face"],
            ),
            glass=GlassRules(
                edge_cover=values["glass_edge_cover"],
                edge_clearance=values["glass_clearance"],
                max_glass_thickness=values["max_glass_thickness"],
            ),
            mullion=MullionRules(
                face_width=values["mullion_face"],
                end_deduction=values.get("mullion_end_deduction", 0.0),
            ),
            profiles=dict(self.profiles),
            notes=self.notes or None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "source": self.source,
            "entered_by": self.entered_by,
            "entered_on": self.entered_on,
            "values": dict(self.values),
            "profiles": dict(self.profiles),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Confirmation":
        return cls(
            entry_id=data["entry_id"],
            source=data.get("source", ""),
            entered_by=data.get("entered_by", ""),
            entered_on=data.get("entered_on", date.today().isoformat()),
            values={k: float(v) for k, v in (data.get("values") or {}).items()},
            profiles=dict(data.get("profiles") or {}),
            notes=data.get("notes", ""),
        )


def template(entry_id: str) -> dict[str, Any]:
    """A blank form to fill in from the catalogue, with the hints in it.

    Handed to a shop as a file they can fill in at the bench with the
    catalogue open, rather than clicking through a screen.
    """
    return {
        "entry_id": entry_id,
        "source": "",
        "entered_by": "",
        "_כיצד": (
            "מלאו את הערכים מהקטלוג של הספק. ״source״ חייב לומר איזה קטלוג "
            "ואיזו מהדורה. שדות שאינם חובה אפשר להשאיר ריקים."
        ),
        "values": {figure.key: figure.default for figure in FIGURES},
        "_הסבר": {
            figure.key: f"{figure.hebrew} [{figure.unit}] — {figure.where}"
            for figure in FIGURES
        },
        "profiles": {
            "frame": "", "sash": "", "mullion": "", "transom": "", "bead": "",
        },
        "notes": "",
    }


def write_template(entry_id: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(template(entry_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_confirmation(path: Path) -> Confirmation:
    """Read a filled-in template, ignoring the explanatory keys."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    values = {
        key: float(value)
        for key, value in (data.get("values") or {}).items()
        if value is not None and str(value).strip() != ""
    }
    return Confirmation(
        entry_id=data["entry_id"],
        source=data.get("source", ""),
        entered_by=data.get("entered_by", ""),
        values=values,
        profiles={k: v for k, v in (data.get("profiles") or {}).items() if v},
        notes=data.get("notes", ""),
    )


class ConfirmationBook:
    """Every series the shop has entered figures for, kept between sessions.

    Without this the work of typing eleven numbers is lost on the next
    restart, which is the difference between a demonstration and a tool.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, Confirmation] = {}
        self.load()

    def load(self) -> "ConfirmationBook":
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file must not lose the rest
            _log.exception("Confirmations at %s unreadable", self.path)
            return self
        for entry in raw.get("confirmations", []):
            try:
                confirmation = Confirmation.from_dict(entry)
            except Exception:  # noqa: BLE001
                _log.warning("Skipping unreadable confirmation: %s", entry)
                continue
            self._entries[confirmation.entry_id] = confirmation
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "confirmations": [item.as_dict() for item in self._entries.values()]
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries.values())

    def get(self, entry_id: str) -> Confirmation | None:
        return self._entries.get(entry_id)

    def record(self, confirmation: Confirmation) -> Confirmation:
        """Keep a confirmation and apply it, or refuse it with the reason."""
        problems = confirmation.problems()
        if problems:
            raise ProfileOSError("אי אפשר לאשר: " + " · ".join(problems))
        previous = self._entries.get(confirmation.entry_id)
        self._entries[confirmation.entry_id] = confirmation
        self.save()
        self.apply_one(confirmation)

        # Confirming a series is the act that lets a saw cut to these figures.
        # If a bar comes out wrong six months from now, the first question is
        # who vouched for the deduction and out of which catalogue.
        from ..core.audit import Action, try_record

        try_record(
            Action.CONFIRMED, f"system:{confirmation.entry_id}",
            field_name="values",
            before=dict(previous.values) if previous else None,
            after=dict(confirmation.values),
            person=confirmation.entered_by,
            note=confirmation.source,
        )
        return confirmation

    def forget(self, entry_id: str) -> None:
        """Delete a confirmation, and stop cutting to it in the same breath.

        Removing the record without revoking the rules would leave the series
        cuttable for the rest of the session on figures nobody stands behind
        any more, which is the worst of both.
        """
        if entry_id not in self._entries:
            return
        forgotten = self._entries.pop(entry_id)
        self.save()

        from ..core.audit import Action, try_record

        try_record(
            Action.DELETED, f"system:{entry_id}", field_name="values",
            before=dict(forgotten.values), after=None,
            note="הסדרה חזרה למצב ״לא לייצור״",
        )
        from . import DIRECTORY

        try:
            DIRECTORY.revoke(entry_id)
        except KeyError:  # pragma: no cover - a series that is no longer listed
            _log.warning("Revoked a confirmation for unknown series %s", entry_id)

    def apply_one(self, confirmation: Confirmation) -> bool:
        """Attach one confirmation's rules to the directory."""
        from . import DIRECTORY

        entry = DIRECTORY.get(confirmation.entry_id)
        if entry is None:
            _log.warning("Confirmation for unknown series %s", confirmation.entry_id)
            return False
        try:
            rules = confirmation.to_rules(name=entry.display)
            DIRECTORY.confirm(entry.id, rules, source=confirmation.source)
        except Exception as exc:  # noqa: BLE001 - one bad entry, not the book
            _log.warning("Could not apply %s: %s", confirmation.entry_id, exc)
            return False
        return True

    def apply(self) -> int:
        """Re-attach every stored confirmation. Returns how many took."""
        return sum(1 for item in self if self.apply_one(item))


def default_confirmations() -> ConfirmationBook:
    from ..core.config import get_settings

    return ConfirmationBook(get_settings().data_dir / "system_confirmations.json")


def load_confirmations() -> int:
    """Restore the shop's confirmed series at start-up."""
    try:
        return default_confirmations().apply()
    except Exception:  # noqa: BLE001 - never stop the application starting
        _log.exception("Could not load system confirmations")
        return 0


__all__ = [
    "FIGURES",
    "FIGURE_BY_KEY",
    "Confirmation",
    "ConfirmationBook",
    "Figure",
    "default_confirmations",
    "load_confirmations",
    "read_confirmation",
    "template",
    "write_template",
]
