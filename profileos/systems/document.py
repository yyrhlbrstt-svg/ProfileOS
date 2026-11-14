"""Adding series without touching the code.

The directory that ships covers the market as the operator described it. No
list of this kind stays complete: a supplier adds a series, a shop starts
working with an importer nobody else in the country uses. So a catalogue is
also a document — ``kind: "system_catalogue"`` — that the plugin loader picks
up from the data directory and merges into the directory at start-up.

The document carries names and classifications, deliberately not deductions.
Deductions come from :mod:`profileos.catalogue`, which reads them out of the
supplier's own drawings and records the file they came from. Letting a
hand-written JSON file assert a cut deduction would put the one number that
must be traceable back into the one place nobody can trace.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.hotreload import DataSchema
from ..core.registry import SYSTEM_CATALOGUES
from .model import Manufacturer, Provenance, SystemEntry, SystemFamily


class ManufacturerDocument(BaseModel):
    """A system house, as written in a catalogue document."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    hebrew: str = ""
    country: str = "IL"
    local_stock: bool = True
    website: str | None = None

    def to_manufacturer(self) -> Manufacturer:
        return Manufacturer(
            id=self.id.strip().lower(),
            name=self.name.strip(),
            hebrew=self.hebrew.strip() or self.name.strip(),
            country=self.country.strip().upper(),
            local_stock=self.local_stock,
            website=self.website,
        )


class SeriesDocument(BaseModel):
    """One series, as written in a catalogue document."""

    model_config = ConfigDict(extra="forbid")

    manufacturer: str
    series: str
    hebrew: str = ""
    family: SystemFamily | None = None
    thermally_broken: bool = False
    depth: float | None = Field(default=None, gt=0)
    notes: str = ""
    aliases: list[str] = Field(default_factory=list)

    @field_validator("manufacturer", "series")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    def to_entry(self, source: str) -> SystemEntry:
        return SystemEntry(
            manufacturer=self.manufacturer.lower(),
            series=self.series,
            hebrew=self.hebrew.strip(),
            family=self.family,
            thermally_broken=self.thermally_broken,
            depth=self.depth,
            # A document may name and classify a series. It may not declare its
            # figures confirmed — only a loaded catalogue does that.
            provenance=Provenance.UNKNOWN,
            source=source,
            notes=self.notes,
            aliases=tuple(alias for alias in self.aliases if alias.strip()),
        )


class SystemCatalogue(BaseModel):
    """A set of series and the houses that make them."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str = "1.0"
    kind: str = "system_catalogue"
    #: Where this list came from, recorded on every series it contributes.
    source: str = "system catalogue document"
    manufacturers: list[ManufacturerDocument] = Field(default_factory=list)
    series: list[SeriesDocument] = Field(default_factory=list)

    @field_validator("id", "name")
    @classmethod
    def _required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    def entries(self) -> list[SystemEntry]:
        return [item.to_entry(self.source) for item in self.series]

    def makers(self) -> list[Manufacturer]:
        return [item.to_manufacturer() for item in self.manufacturers]

    def merge_into(self, directory: Any) -> int:
        """Add everything in this document to a directory. Returns the count.

        Manufacturers are added first so a series never lands pointing at a
        house the directory has never heard of.
        """
        for maker in self.makers():
            directory.add_manufacturer(maker)
        added = 0
        for entry in self.entries():
            directory.add(entry)
            added += 1
        return added


def _validate_catalogue(document: dict[str, Any]) -> SystemCatalogue:
    return SystemCatalogue.model_validate(document)


#: Registers ``kind: "system_catalogue"`` files as hot-reloadable plugins.
SYSTEM_CATALOGUE_SCHEMA = DataSchema(
    kind="system_catalogue",
    model=_validate_catalogue,
    registry=SYSTEM_CATALOGUES,
    key_field="id",
    document_model=SystemCatalogue,
)


__all__ = [
    "ManufacturerDocument",
    "SeriesDocument",
    "SYSTEM_CATALOGUE_SCHEMA",
    "SystemCatalogue",
]
