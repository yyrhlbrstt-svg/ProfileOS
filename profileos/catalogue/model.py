"""Records the catalogue ingestion engine produces.

The point of ingestion is not to copy numbers out of a supplier's PDF. It is
to end up with a profile library the fabricator owns, whose every entry has
geometry that has been *measured* from the drawing rather than transcribed —
and whose published figures have been checked against that measurement.

That check is the whole value. A catalogue table and a DXF drawing of the same
extrusion are two independent statements about one piece of aluminium. When
they agree, the entry is trustworthy. When they disagree by more than the
tolerance a rolled section is allowed, something is wrong — the wrong drawing
is filed under the code, the table has a typo, or the drawing is an old
revision — and the fabricator needs to know before a mullion is sized on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from ..models.profile import ProfileDefinition
from ..models.results import SectionProperties


class SourceKind(StrEnum):
    """Where a piece of catalogue data came from."""

    #: Measured from the supplier's DXF by the geometry and structural engines.
    MEASURED = "measured"
    #: Read out of the supplier's printed or PDF table.
    PUBLISHED = "published"
    #: Typed in by a person.
    MANUAL = "manual"


class CheckStatus(StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    #: Only one of the two sources had the value, so nothing was compared.
    UNCHECKED = "unchecked"


#: Relative tolerance per property, as a fraction.
#:
#: These are not arbitrary. EN 12020-2 allows a mass deviation of a few percent
#: on a precision extrusion, and a catalogue's second moments are themselves
#: usually computed from a nominal drawing rather than a measured one, so a
#: small spread between a published figure and one integrated from the DXF is
#: normal. A spread beyond these is not, and it is what the report is for.
DEFAULT_TOLERANCES: dict[str, float] = {
    "area": 0.03,
    "mass_per_metre": 0.03,
    "ixx": 0.05,
    "iyy": 0.05,
    "width": 0.01,
    "height": 0.01,
    "perimeter": 0.05,
    "sx": 0.05,
    "sy": 0.05,
    "j": 0.15,  # torsion is the most sensitive to how fillets were drawn
}


@dataclass
class PropertyCheck:
    """One published figure set against the same figure measured from the DXF."""

    name: str
    published: float | None
    measured: float | None
    tolerance: float
    unit: str = ""

    @property
    def status(self) -> CheckStatus:
        if self.published is None or self.measured is None:
            return CheckStatus.UNCHECKED
        if self.measured == 0.0 and self.published == 0.0:
            return CheckStatus.AGREE
        return (
            CheckStatus.AGREE
            if abs(self.deviation or 0.0) <= self.tolerance
            else CheckStatus.DISAGREE
        )

    @property
    def deviation(self) -> float | None:
        """Signed relative difference, measured against published."""
        if self.published is None or self.measured is None or self.published == 0.0:
            return None
        return (self.measured - self.published) / self.published

    def describe(self) -> str:
        if self.status is CheckStatus.UNCHECKED:
            return f"{self.name}: not compared"
        assert self.deviation is not None
        return (
            f"{self.name}: published {self.published:,.4g}{self.unit}, "
            f"measured {self.measured:,.4g}{self.unit} "
            f"({self.deviation * 100:+.2f}%)"
        )


@dataclass
class CatalogueEntry:
    """One extrusion, as far as ingestion could establish it."""

    profile_id: str
    system_series: str
    name: str | None = None
    #: Present when a DXF was found and successfully analysed.
    definition: ProfileDefinition | None = None
    measured: SectionProperties | None = None
    #: Figures lifted from the supplier's table, keyed by property name.
    published: dict[str, float] = field(default_factory=dict)
    checks: list[PropertyCheck] = field(default_factory=list)
    dxf_path: str | None = None
    pdf_page: int | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_geometry(self) -> bool:
        return self.definition is not None

    @property
    def disagreements(self) -> list[PropertyCheck]:
        return [check for check in self.checks if check.status is CheckStatus.DISAGREE]

    @property
    def verified(self) -> bool:
        """True when at least one figure was compared and none disagreed."""
        compared = [c for c in self.checks if c.status is not CheckStatus.UNCHECKED]
        return bool(compared) and not self.disagreements

    @property
    def status(self) -> str:
        if not self.has_geometry:
            return "table only"
        if not any(c.status is not CheckStatus.UNCHECKED for c in self.checks):
            return "unverified"
        return "verified" if self.verified else "conflict"

    def summary(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "series": self.system_series,
            "name": self.name,
            "status": self.status,
            "geometry": self.has_geometry,
            "checked": sum(
                1 for c in self.checks if c.status is not CheckStatus.UNCHECKED
            ),
            "conflicts": len(self.disagreements),
            "warnings": len(self.warnings),
        }


@dataclass
class IngestionReport:
    """Everything one ingestion run established, and everything it could not."""

    source: str
    entries: list[CatalogueEntry] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: DXF files that were read but matched no catalogue row.
    unmatched_drawings: list[str] = field(default_factory=list)
    #: Table rows with no drawing to check them against.
    unmatched_rows: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def verified(self) -> list[CatalogueEntry]:
        return [entry for entry in self.entries if entry.verified]

    @property
    def conflicts(self) -> list[CatalogueEntry]:
        return [entry for entry in self.entries if entry.disagreements]

    @property
    def with_geometry(self) -> list[CatalogueEntry]:
        return [entry for entry in self.entries if entry.has_geometry]

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "entries": len(self.entries),
            "with_geometry": len(self.with_geometry),
            "verified": len(self.verified),
            "conflicts": len(self.conflicts),
            "unmatched_drawings": len(self.unmatched_drawings),
            "unmatched_rows": len(self.unmatched_rows),
            "errors": len(self.errors),
        }


__all__ = [
    "SourceKind",
    "CheckStatus",
    "DEFAULT_TOLERANCES",
    "PropertyCheck",
    "CatalogueEntry",
    "IngestionReport",
]
