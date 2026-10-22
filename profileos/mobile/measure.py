"""Measurements taken on site, typed once.

The usual sequence is: measure the opening, write it on a scrap of paper,
drive back, type it into the office computer. Two of those steps are where the
mistakes come from, and they are both transcription. So the phone writes
straight into the same store the office reads, and nobody types a number twice.

Two decisions worth stating:

**Measurements are not sizes.** What a fitter measures is the *structural
opening* — the hole in the wall. What gets made is smaller by the installation
clearance on each side. Storing the measurement and deriving the element size
means the clearance can be changed later without anybody having to remember
which numbers already had it taken off.

**Three widths and three heights, not one each.** Openings are not square. A
single measurement hides a 15 mm taper that will not be found until the frame
is offered up, so the phone asks for top, middle and bottom, and the record
knows how far out of square it is.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("mobile.measure")

#: Out-of-square beyond this needs somebody to look before anything is cut [mm].
SQUARENESS_LIMIT = 10.0
#: What a window or door opening in a building can be [mm]. Outside this the
#: number was mistyped — 1200 typed as 12000, or centimetres typed as mm.
SMALLEST_MM = 150.0
LARGEST_MM = 8000.0
#: Three widths of one hole further apart than this are a typing slip, not a
#: crooked wall [mm].
TYPO_SPREAD_MM = 150.0


def impossible(widths: Iterable[float], heights: Iterable[float]) -> str:
    """Why these sizes cannot be a real opening, or ``""``."""
    for label, values in (("רוחב", list(widths)), ("גובה", list(heights))):
        given = [v for v in values if v]
        for value in given:
            if value < 0 or value < SMALLEST_MM or value > LARGEST_MM:
                return (
                    f"{label} ⁦{value:g}⁩ מ״מ לא נראה כמו מידת פתח — "
                    f"מידות במילימטרים, בין ⁦{SMALLEST_MM:g}⁩ ל־⁦{LARGEST_MM:g}⁩"
                )
        if len(given) > 1 and max(given) - min(given) > TYPO_SPREAD_MM:
            return (
                f"ה{label} נמדד ⁦{min(given):g}⁩ וגם ⁦{max(given):g}⁩ — "
                "כנראה טעות הקלדה. בדקו ושלחו שוב"
            )
    return ""


@dataclass
class SiteMeasurement:
    """One opening, measured on site."""

    reference: str
    project_id: str = ""
    #: Structural opening widths at the top, middle and bottom [mm].
    widths: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Structural opening heights at the left, middle and right [mm].
    heights: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: Diagonals, when they were taken; the honest test for square [mm].
    diagonals: tuple[float, float] | None = None
    floor_level: float | None = None
    storey: str = ""
    note: str = ""
    measured_by: str = ""
    measured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    device: str = ""
    #: The phone's own name for this measurement: a measurement sent again
    #: (the answer was lost on a bad connection) is recognised, not doubled.
    client_id: str = ""

    @property
    def width(self) -> float:
        """The width to build to: the narrowest, because the frame has to fit."""
        values = [w for w in self.widths if w > 0]
        return min(values) if values else 0.0

    @property
    def height(self) -> float:
        values = [h for h in self.heights if h > 0]
        return min(values) if values else 0.0

    @property
    def width_range(self) -> float:
        values = [w for w in self.widths if w > 0]
        return max(values) - min(values) if len(values) > 1 else 0.0

    @property
    def height_range(self) -> float:
        values = [h for h in self.heights if h > 0]
        return max(values) - min(values) if len(values) > 1 else 0.0

    @property
    def diagonal_difference(self) -> float | None:
        if not self.diagonals:
            return None
        return abs(self.diagonals[0] - self.diagonals[1])

    def problems(self) -> list[str]:
        """What is wrong with this measurement, in the fitter's own terms."""
        found: list[str] = []
        if self.width <= 0 or self.height <= 0:
            found.append("חסרה מידה — צריך גם רוחב וגם גובה")
        if self.width_range > SQUARENESS_LIMIT:
            found.append(
                f"הרוחב משתנה לאורך הגובה ב־⁦{self.width_range:.0f}⁩ מ״מ — "
                "לבדוק לפני חיתוך"
            )
        if self.height_range > SQUARENESS_LIMIT:
            found.append(
                f"הגובה משתנה לאורך הרוחב ב־⁦{self.height_range:.0f}⁩ מ״מ — "
                "לבדוק לפני חיתוך"
            )
        difference = self.diagonal_difference
        if difference is not None and difference > SQUARENESS_LIMIT:
            found.append(
                f"האלכסונים נבדלים ב־⁦{difference:.0f}⁩ מ״מ — הפתח לא בזווית"
            )
        return found

    def element_size(self, clearance: float = 10.0) -> tuple[float, float]:
        """The frame size to make, once the perimeter joint is taken off."""
        return (
            max(self.width - 2.0 * clearance, 0.0),
            max(self.height - 2.0 * clearance, 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "project_id": self.project_id,
            "widths": list(self.widths),
            "heights": list(self.heights),
            "diagonals": list(self.diagonals) if self.diagonals else None,
            "floor_level": self.floor_level,
            "storey": self.storey,
            "note": self.note,
            "measured_by": self.measured_by,
            "measured_at": self.measured_at.isoformat(),
            "device": self.device,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SiteMeasurement":
        diagonals = data.get("diagonals")
        return cls(
            reference=str(data["reference"]),
            project_id=str(data.get("project_id", "")),
            widths=tuple(float(v) for v in data.get("widths", (0, 0, 0))),  # type: ignore[arg-type]
            heights=tuple(float(v) for v in data.get("heights", (0, 0, 0))),  # type: ignore[arg-type]
            diagonals=tuple(float(v) for v in diagonals) if diagonals else None,  # type: ignore[arg-type]
            floor_level=data.get("floor_level"),
            storey=str(data.get("storey", "")),
            note=str(data.get("note", "")),
            measured_by=str(data.get("measured_by", "")),
            measured_at=datetime.fromisoformat(data["measured_at"]),
            device=str(data.get("device", "")),
            client_id=str(data.get("client_id", "")),
        )


@dataclass
class MeasurementStore:
    """Every measurement, newest first, kept as one file per installation.

    A later measurement of the same opening does not overwrite the earlier one.
    Somebody re-measuring usually means the first figure was wrong, and knowing
    that it changed — and when, and who by — is exactly what is wanted when a
    frame turns up 20 mm out.
    """

    path: Path
    records: list[SiteMeasurement] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "MeasurementStore":
        target = Path(path)
        store = cls(path=target)
        from ..core.storage import stored as _stored

        if not _stored(target):
            return store
        from ..core.storage import read_json

        try:
            data = read_json(target, default={})
        except Exception as exc:  # noqa: BLE001
            raise ProfileOSError(f"קובץ המדידות מהטלפון פגום: {exc}") from exc
        store.records = [SiteMeasurement.from_dict(entry) for entry in data.get("records", [])]
        return store

    def save(self) -> None:
        from ..core.storage import write_json

        write_json(self.path, {"version": 1, "records": [r.to_dict() for r in self.records]})

    def add(self, measurement: SiteMeasurement) -> SiteMeasurement:
        self.records.insert(0, measurement)
        self.save()
        _log.info(
            "Measured %s: %.0f x %.0f mm by %s",
            measurement.reference,
            measurement.width,
            measurement.height,
            measurement.measured_by or measurement.device or "?",
        )
        return measurement

    def by_client_id(self, client_id: str) -> SiteMeasurement | None:
        if not client_id:
            return None
        return next((r for r in self.records if r.client_id == client_id), None)

    def latest(self, reference: str) -> SiteMeasurement | None:
        return next((r for r in self.records if r.reference == reference), None)

    def history(self, reference: str) -> list[SiteMeasurement]:
        return [r for r in self.records if r.reference == reference]

    def for_project(self, project_id: str) -> list[SiteMeasurement]:
        return [r for r in self.records if r.project_id == project_id]

    def references(self) -> list[str]:
        """Every opening measured, most recently touched first."""
        seen: list[str] = []
        for record in self.records:
            if record.reference not in seen:
                seen.append(record.reference)
        return seen

    def changed(self) -> list[tuple[str, float, float]]:
        """Openings measured more than once, with how much the size moved."""
        moves: list[tuple[str, float, float]] = []
        for reference in self.references():
            history = self.history(reference)
            if len(history) < 2:
                continue
            newest, previous = history[0], history[1]
            moves.append(
                (reference, newest.width - previous.width, newest.height - previous.height)
            )
        return [m for m in moves if abs(m[1]) > 0.5 or abs(m[2]) > 0.5]


def default_store_path() -> Path:
    from ..core.config import get_settings

    settings = get_settings()
    base = getattr(settings, "data_dir", None) or Path.home() / ".config" / "ProfileOS"
    return Path(base) / "site-measurements.json"


__all__ = [
    "LARGEST_MM",
    "MeasurementStore",
    "SMALLEST_MM",
    "impossible",
    "SQUARENESS_LIMIT",
    "SiteMeasurement",
    "default_store_path",
]
