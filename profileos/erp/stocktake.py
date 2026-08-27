"""Counting the racks, and what to do with the difference.

The stock book says there are ⁦14⁩ bars of a series on the rack. Somebody walks
to the rack and there are ⁦11⁩. Every fabricator lives with that gap, and the
question is never whether it exists but whether anybody writes it down.

A stocktake here is a sheet, not a button. It freezes what the book claims,
gets carried to the racks on paper or a tablet, comes back with numbers written
against some of the lines, and only then is posted — and posting reports the
value of the difference, because a shop that counts without seeing the money is
counting for nothing.

The one rule this module will not bend: **a line nobody counted is not a line
with zero on it.** Posting blanks as zero writes off the rack, and the ledger
would then agree with a count that never happened. Uncounted lines are listed,
named, and left alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.stocktake")


class Status(StrEnum):
    OPEN = "open"
    POSTED = "posted"
    ABANDONED = "abandoned"

    @property
    def hebrew(self) -> str:
        return {"open": "פתוחה", "posted": "נרשמה", "abandoned": "בוטלה"}[self.value]


@dataclass
class CountLine:
    """One item on the sheet: what the book said, and what somebody found."""

    code: str
    name: str = ""
    unit: str = ""
    #: What the book claimed at the moment the sheet was opened.
    book: float = 0.0
    #: The book's own cost per unit **in shekels**, frozen with it, so the
    #: value of a difference is priced at what the shop paid rather than at
    #: today's price. The stock ledger keeps costs in agorot; they are
    #: converted here, because this sheet is read by people.
    unit_cost: float = 0.0
    location: str = ""
    #: ``None`` means nobody has counted this line. It is not zero.
    counted: float | None = None
    counted_by: str = ""
    note: str = ""

    @property
    def is_counted(self) -> bool:
        return self.counted is not None

    @property
    def difference(self) -> float:
        """Found minus book. Positive means the rack had more than the book."""
        if self.counted is None:
            return 0.0
        return round(self.counted - self.book, 4)

    @property
    def value_difference(self) -> float:
        return round(self.difference * self.unit_cost, 2)

    @property
    def agrees(self) -> bool:
        return self.is_counted and abs(self.difference) < 1e-6

    def describe(self) -> str:
        if not self.is_counted:
            return f"{self.code} · לא נספר"
        sign = "+" if self.difference > 0 else ""
        return (
            f"{self.code} · בספר ⁦{self.book:g}⁩ · נספר ⁦{self.counted:g}⁩ · "
            f"הפרש ⁦{sign}{self.difference:g}⁩"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "unit": self.unit,
            "book": self.book, "unit_cost": self.unit_cost,
            "location": self.location, "counted": self.counted,
            "counted_by": self.counted_by, "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CountLine":
        return cls(
            code=str(raw.get("code", "")), name=str(raw.get("name", "")),
            unit=str(raw.get("unit", "")), book=float(raw.get("book", 0.0)),
            unit_cost=float(raw.get("unit_cost", 0.0)),
            location=str(raw.get("location", "")),
            counted=(
                None if raw.get("counted") is None else float(raw["counted"])
            ),
            counted_by=str(raw.get("counted_by", "")),
            note=str(raw.get("note", "")),
        )


@dataclass
class Stocktake:
    """A count sheet: frozen book quantities, and the numbers found."""

    sheet_id: str = field(default_factory=lambda: f"ST-{uuid4().hex[:6].upper()}")
    opened: date = field(default_factory=date.today)
    opened_by: str = ""
    #: What this count covers, in the shop's words: "מחסן פרופילים", "פרזול".
    scope: str = ""
    status: Status = Status.OPEN
    lines: list[CountLine] = field(default_factory=list)
    posted_on: date | None = None
    posted_by: str = ""

    # -- reading ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self.lines)

    def line(self, code: str) -> CountLine:
        for line in self.lines:
            if line.code == code:
                return line
        raise ProfileOSError(f"אין שורה לפריט {code} בגיליון הספירה")

    @property
    def counted(self) -> list[CountLine]:
        return [line for line in self.lines if line.is_counted]

    @property
    def uncounted(self) -> list[CountLine]:
        """Lines nobody reached. These are never posted as zero."""
        return [line for line in self.lines if not line.is_counted]

    @property
    def differences(self) -> list[CountLine]:
        """Counted lines where the rack disagreed with the book, worst first."""
        return sorted(
            (line for line in self.counted if not line.agrees),
            key=lambda line: abs(line.value_difference),
            reverse=True,
        )

    @property
    def progress(self) -> float:
        return (len(self.counted) / len(self.lines) * 100.0) if self.lines else 0.0

    @property
    def accuracy(self) -> float:
        """Share of counted lines where the book was right.

        The number that says whether the stock book may be trusted between
        counts. A shop at ⁦98%⁩ can quote from the book; a shop at ⁦60%⁩ is
        guessing every time it promises a delivery date.
        """
        if not self.counted:
            return 0.0
        agreed = sum(1 for line in self.counted if line.agrees)
        return round(agreed / len(self.counted) * 100.0, 1)

    @property
    def net_value(self) -> float:
        """What posting this sheet would do to the value of the stock."""
        return round(sum(line.value_difference for line in self.counted), 2)

    @property
    def shrinkage(self) -> float:
        """Value of what the racks were short. Reported apart from surplus.

        A sheet that is ⁦3,000⁩ ₪ short in one place and ⁦3,000⁩ ₪ over in
        another nets to nothing and is not nothing: it is two mistakes.
        """
        return round(
            sum(
                line.value_difference
                for line in self.counted
                if line.value_difference < 0
            ),
            2,
        )

    @property
    def surplus(self) -> float:
        return round(
            sum(
                line.value_difference
                for line in self.counted
                if line.value_difference > 0
            ),
            2,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "status": self.status.value,
            "lines": len(self.lines),
            "counted": len(self.counted),
            "uncounted": len(self.uncounted),
            "differences": len(self.differences),
            "progress_pct": round(self.progress, 1),
            "accuracy_pct": self.accuracy,
            "net_value": self.net_value,
            "shrinkage": self.shrinkage,
            "surplus": self.surplus,
        }

    def describe(self) -> str:
        return (
            f"{self.sheet_id} · {self.scope or 'כל המלאי'} · "
            f"נספרו ⁦{len(self.counted)}⁩ מתוך ⁦{len(self.lines)}⁩ · "
            f"הפרשים ⁦{len(self.differences)}⁩ · "
            f"שווי ⁦{self.net_value:,.0f}⁩ ₪"
        )

    def warnings(self) -> list[str]:
        """What somebody should read before posting."""
        found: list[str] = []
        if self.status is Status.POSTED:
            found.append("הגיליון כבר נרשם — רישום נוסף אינו אפשרי")
            return found
        if not self.lines:
            found.append("אין שורות בגיליון")
            return found
        if self.uncounted:
            found.append(
                f"⁦{len(self.uncounted)}⁩ שורות לא נספרו — הן יישארו כפי "
                "שהן בספר ולא יירשמו כאפס"
            )
        if not self.counted:
            found.append("לא נספרה אף שורה — אין מה לרשום")
        if self.shrinkage:
            found.append(
                f"חוסר של ⁦{abs(self.shrinkage):,.0f}⁩ ₪ ברצפה מול הספר"
            )
        if self.surplus:
            found.append(
                f"עודף של ⁦{self.surplus:,.0f}⁩ ₪ ברצפה מול הספר — "
                "בדקו קליטות שלא נרשמו"
            )
        for line in self.counted:
            if line.book and abs(line.difference) > abs(line.book) * 0.5:
                found.append(
                    f"{line.code}: ההפרש גדול מחצי מהיתרה — ודאו שנספרה "
                    "היחידה הנכונה"
                )
        return found

    # -- writing ------------------------------------------------------------- #
    def enter(
        self, code: str, counted: float, *, by: str = "", note: str = ""
    ) -> CountLine:
        """Write a counted quantity against one line."""
        if self.status is not Status.OPEN:
            raise ProfileOSError(
                f"גיליון {self.sheet_id} {self.status.hebrew} ואי אפשר לשנות אותו"
            )
        if counted < 0:
            raise ProfileOSError("כמות שנספרה אינה יכולה להיות שלילית")
        line = self.line(code)
        line.counted = float(counted)
        line.counted_by = by or line.counted_by
        if note:
            line.note = note
        return line

    def clear(self, code: str) -> CountLine:
        """Undo a count on one line, back to uncounted rather than to zero."""
        line = self.line(code)
        line.counted = None
        line.counted_by = ""
        return line

    def post(self, ledger: Any, *, by: str = "", on: date | None = None) -> list[Any]:
        """Bring the book into line with what was counted. Only counted lines.

        Returns the stock movements written, which is fewer than the counted
        lines: a line that agreed with the book produces no movement.
        """
        if self.status is not Status.OPEN:
            raise ProfileOSError(
                f"גיליון {self.sheet_id} {self.status.hebrew} ואי אפשר לרשום אותו שוב"
            )
        if not self.counted:
            raise ProfileOSError("לא נספרה אף שורה — אין מה לרשום")

        when = on or date.today()
        movements = []
        for line in self.counted:
            movement = ledger.adjust(
                line.code, line.counted, on=when,
                reference=f"{self.sheet_id}",
            )
            if movement is not None:
                movements.append(movement)

        self.status = Status.POSTED
        self.posted_on = when
        self.posted_by = by

        from ..core.audit import Action, try_record

        try_record(
            Action.POSTED, f"stocktake:{self.sheet_id}",
            field_name="stock_value", before=0.0, after=self.net_value,
            person=by, note=(
                f"{len(movements)} תנועות · חוסר {self.shrinkage:,.2f} · "
                f"עודף {self.surplus:,.2f}"
            ),
        )
        _log.info(
            "Stocktake %s posted: %d movements, %.2f value change",
            self.sheet_id, len(movements), self.net_value,
        )
        return movements

    def abandon(self) -> None:
        """Close a sheet that was never finished, without touching the book."""
        if self.status is Status.POSTED:
            raise ProfileOSError("גיליון שנרשם אינו ניתן לביטול")
        self.status = Status.ABANDONED

    # -- the sheet itself ----------------------------------------------------- #
    def count_sheet_rows(self) -> list[list[str]]:
        """Rows for the sheet that gets carried to the racks.

        The book quantity is deliberately **not** on it. A counter who can see
        what the answer is supposed to be writes that number down, and the
        count becomes a copy of the book instead of a check on it.
        """
        return [
            [line.code, line.name, line.location or "—", line.unit or "יח׳", ""]
            for line in self.lines
        ]

    COUNT_SHEET_HEADERS = ("קוד", "פריט", "מיקום", "יחידה", "נספר")

    def as_dict(self) -> dict[str, Any]:
        return {
            "sheet_id": self.sheet_id,
            "opened": self.opened.isoformat(),
            "opened_by": self.opened_by,
            "scope": self.scope,
            "status": self.status.value,
            "posted_on": self.posted_on.isoformat() if self.posted_on else None,
            "posted_by": self.posted_by,
            "lines": [line.as_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Stocktake":
        posted = raw.get("posted_on")
        return cls(
            sheet_id=str(raw.get("sheet_id", "")),
            opened=date.fromisoformat(
                str(raw.get("opened", date.today().isoformat()))
            ),
            opened_by=str(raw.get("opened_by", "")),
            scope=str(raw.get("scope", "")),
            status=Status(str(raw.get("status", "open"))),
            posted_on=date.fromisoformat(str(posted)) if posted else None,
            posted_by=str(raw.get("posted_by", "")),
            lines=[CountLine.from_dict(item) for item in raw.get("lines", [])],
        )


def open_stocktake(
    ledger: Any, *, scope: str = "", by: str = "",
    codes: Iterable[str] | None = None, include_zero: bool = True,
) -> Stocktake:
    """Freeze what the book claims right now, as a sheet to go and check.

    ``include_zero`` matters more than it looks: an item the book says there
    are none of is exactly the item most likely to be sitting on the rack
    unrecorded, so it is on the sheet by default.
    """
    sheet = Stocktake(scope=scope, opened_by=by)
    wanted = set(codes) if codes is not None else None

    for state in _states(ledger):
        item = state.item
        if wanted is not None and item.code not in wanted:
            continue
        if not include_zero and abs(state.on_hand) < 1e-9:
            continue
        sheet.lines.append(CountLine(
            code=item.code,
            name=getattr(item, "name", "") or getattr(item, "description", ""),
            unit=getattr(item, "unit", "") or "",
            book=round(state.on_hand, 4),
            # The ledger holds costs in minor units; the sheet is in shekels.
            unit_cost=round(state.average_cost / 100.0, 4),
            location=(
                getattr(item, "location", "")
                or getattr(item, "category", "")
                or ""
            ),
        ))

    sheet.lines.sort(key=lambda line: (line.location, line.code))
    _log.info("Stocktake %s opened with %d lines", sheet.sheet_id, len(sheet.lines))
    return sheet


def _states(ledger: Any) -> list[Any]:
    """Every item state in a stock ledger, however it exposes them."""
    for attribute in ("states", "_states", "items"):
        found = getattr(ledger, attribute, None)
        if isinstance(found, dict):
            return list(found.values())
        if isinstance(found, (list, tuple)):
            return list(found)
    raise ProfileOSError("לא נמצאו פריטי מלאי בספר המלאי")


class StocktakeBook:
    """Every count sheet the shop has opened, kept on disk."""

    def __init__(self, path: Path | None = None) -> None:
        from ..core.config import get_settings

        self.path = Path(path) if path else get_settings().data_dir / "stocktakes.json"
        self._sheets: dict[str, Stocktake] = {}

    def load(self) -> "StocktakeBook":
        if not self.path.exists():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Could not read %s: %s", self.path, exc)
            return self
        for item in raw.get("stocktakes", []):
            try:
                sheet = Stocktake.from_dict(item)
            except (ValueError, KeyError) as exc:
                _log.warning("Skipped an unreadable stocktake: %s", exc)
                continue
            self._sheets[sheet.sheet_id] = sheet
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "written": datetime.now().isoformat(timespec="seconds"),
            "stocktakes": [sheet.as_dict() for sheet in self._sheets.values()],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def add(self, sheet: Stocktake) -> Stocktake:
        self._sheets[sheet.sheet_id] = sheet
        self.save()
        return sheet

    def get(self, sheet_id: str) -> Stocktake:
        if sheet_id not in self._sheets:
            raise ProfileOSError(f"אין גיליון ספירה {sheet_id}")
        return self._sheets[sheet_id]

    def __len__(self) -> int:
        return len(self._sheets)

    def __iter__(self):
        return iter(
            sorted(self._sheets.values(), key=lambda s: s.opened, reverse=True)
        )

    @property
    def open_sheets(self) -> list[Stocktake]:
        return [sheet for sheet in self if sheet.status is Status.OPEN]

    def history(self) -> list[dict[str, Any]]:
        """Accuracy over time — whether counting is fixing anything."""
        return [
            {
                "sheet_id": sheet.sheet_id,
                "on": (sheet.posted_on or sheet.opened).isoformat(),
                "scope": sheet.scope,
                "accuracy_pct": sheet.accuracy,
                "shrinkage": sheet.shrinkage,
                "status": sheet.status.value,
            }
            for sheet in self
            if sheet.status is Status.POSTED
        ]


def default_stocktakes() -> StocktakeBook:
    return StocktakeBook().load()


__all__ = [
    "CountLine",
    "Status",
    "Stocktake",
    "StocktakeBook",
    "default_stocktakes",
    "open_stocktake",
]
