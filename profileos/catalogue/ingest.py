"""Turning a supplier's catalogue and drawing pack into an owned profile library.

The run has three parts, and the third is the one that matters:

1. Every DXF in the drawing folder goes through the geometry and structural
   engines, which produce a profile definition and a full set of measured
   section properties.
2. The supplier's table is parsed for the same articles.
3. The two are set against each other, property by property, and every
   disagreement is reported.

Step three is the reason to do this at all. A catalogue figure and a drawing
are independent statements about one extrusion; agreement is evidence, and
disagreement is a finding the fabricator has to see before somebody sizes a
mullion on the wrong number. Nothing here silently prefers one source over the
other — the measured value is what the library stores, because it is the one
that was derived rather than transcribed, and the published value is kept
beside it so the conflict stays visible.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable, Sequence

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from ..models.profile import ProfileDefinition, ProfileRole
from ..models.results import SectionProperties
from .model import (
    DEFAULT_TOLERANCES,
    CatalogueEntry,
    IngestionReport,
    PropertyCheck,
)
from .tables import CatalogueError, TableRow, TableSpec, read_table

_log = get_logger(__name__)

#: Units used when a check is described in a report.
_UNITS = {
    "area": " mm²",
    "perimeter": " mm",
    "ixx": " mm⁴",
    "iyy": " mm⁴",
    "sx": " mm³",
    "sy": " mm³",
    "j": " mm⁴",
    "mass_per_metre": " kg/m",
    "width": " mm",
    "height": " mm",
}


def normalise_code(code: str) -> str:
    """Reduce an article code to what two spellings of it have in common.

    ``MB-70.1234``, ``MB70 1234`` and ``mb70/1234`` are one extrusion written
    three ways — by the catalogue, by the drawing office, and by whoever named
    the file. Comparing the stripped, upper-cased forms matches them without
    matching things that are genuinely different.
    """
    return re.sub(r"[^A-Za-z0-9]+", "", code).upper()


def code_candidates(path: Path) -> list[str]:
    """Article codes a drawing filename might be announcing, best first.

    ``4301_outer_frame.dxf`` is claiming to be article 4301; so is
    ``KLIL-4301.dxf`` and ``4301.dxf``. The whole stem is offered first, then
    progressively shorter leading fragments, so an exact catalogue match wins
    over a prefix match.
    """
    stem = path.stem
    candidates = [stem]
    parts = re.split(r"[\s_\-.]+", stem)
    for count in range(len(parts), 0, -1):
        joined = "".join(parts[:count])
        if joined and joined not in candidates:
            candidates.append(joined)
    for part in parts:
        if part and part not in candidates:
            candidates.append(part)
    return candidates


# --------------------------------------------------------------------------- #
# Drawings
# --------------------------------------------------------------------------- #
class DrawingResult:
    """One analysed DXF, or the reason it could not be analysed."""

    __slots__ = ("path", "definition", "properties", "error", "warnings")

    def __init__(
        self,
        path: Path,
        definition: ProfileDefinition | None = None,
        properties: SectionProperties | None = None,
        error: str | None = None,
        warnings: Sequence[str] = (),
    ) -> None:
        self.path = path
        self.definition = definition
        self.properties = properties
        self.error = error
        self.warnings = list(warnings)

    @property
    def ok(self) -> bool:
        return self.definition is not None and self.properties is not None


def analyse_drawing(
    path: str | Path,
    *,
    profile_id: str | None = None,
    system_series: str = "unknown",
    material_id: str | None = None,
    role: ProfileRole = ProfileRole.OTHER,
    torsion: bool = True,
) -> DrawingResult:
    """Run one DXF through the full geometry and structural pipeline.

    A failure here is recorded rather than raised: a drawing pack of four
    hundred files always contains a few that are empty, are a title block, or
    were exported from something that does not close its polylines. One of them
    must not abort the ingestion of the other three hundred and ninety.
    """
    source = Path(path)
    try:
        from ..geometry import profile_from_dxf
        from ..structural import analyse_section

        definition, section = profile_from_dxf(
            source,
            profile_id=profile_id or source.stem,
            system_series=system_series,
            material_id=material_id,
            role=role,
        )
        properties = analyse_section(
            section.polygon,
            topology=section.topology,
            material=definition.material_id,
            profile_id=definition.profile_id,
            compute_torsion_constants=torsion,
        )
        warnings = list(getattr(section.report, "warnings", []) or [])
        warnings.extend(properties.warnings)
        return DrawingResult(source, definition, properties, warnings=warnings)
    except ProfileOSError as exc:
        return DrawingResult(source, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - third-party DXF parsing
        return DrawingResult(source, error=f"{type(exc).__name__}: {exc}")


def scan_drawings(
    folder: str | Path,
    *,
    pattern: str = "*.dxf",
    recursive: bool = True,
) -> list[Path]:
    """Every drawing file under ``folder``, in a stable order."""
    root = Path(folder)
    if not root.is_dir():
        raise CatalogueError(f"Drawing folder not found: {root}", path=str(root))
    files = root.rglob(pattern) if recursive else root.glob(pattern)
    return sorted(files)


# --------------------------------------------------------------------------- #
# Cross-checking
# --------------------------------------------------------------------------- #
def measured_values(properties: SectionProperties) -> dict[str, float]:
    """The measured figures that a catalogue table can be checked against."""
    values: dict[str, float] = {
        "area": properties.area,
        "perimeter": properties.perimeter,
        "ixx": properties.ixx,
        "iyy": properties.iyy,
        "sx": properties.sx,
        "sy": properties.sy,
        "width": properties.width,
        "height": properties.height,
    }
    if properties.j is not None:
        values["j"] = properties.j
    if properties.mass_per_metre is not None:
        values["mass_per_metre"] = properties.mass_per_metre
    return {key: value for key, value in values.items() if value}


def cross_check(
    published: dict[str, float],
    properties: SectionProperties,
    *,
    tolerances: dict[str, float] | None = None,
) -> list[PropertyCheck]:
    """Set every published figure against the measured one."""
    limits = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    measured = measured_values(properties)
    checks: list[PropertyCheck] = []
    for name in sorted(set(published) | set(measured)):
        if name not in limits:
            continue
        checks.append(
            PropertyCheck(
                name=name,
                published=published.get(name),
                measured=measured.get(name),
                tolerance=limits[name],
                unit=_UNITS.get(name, ""),
            )
        )
    return checks


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
def ingest(
    *,
    table: str | Path | None = None,
    drawings: str | Path | None = None,
    system_series: str = "unknown",
    material_id: str | None = None,
    spec: TableSpec | None = None,
    tolerances: dict[str, float] | None = None,
    torsion: bool = True,
    limit: int | None = None,
) -> IngestionReport:
    """Ingest a supplier catalogue, a drawing pack, or both together.

    Either input may be given alone. A table with no drawings yields entries
    that carry published figures and no geometry — useful for pricing, useless
    for machining. Drawings with no table yield measured geometry that nothing
    has corroborated. Given both, every article present in both is checked, and
    the ones present in only one are listed rather than quietly dropped.
    """
    started = time.perf_counter()
    source_name = " + ".join(str(s) for s in (table, drawings) if s) or "empty"
    report = IngestionReport(source=source_name)

    rows: list[TableRow] = []
    if table is not None:
        try:
            rows = read_table(table, spec)
        except CatalogueError as exc:
            report.errors.append(str(exc))
    by_code: dict[str, TableRow] = {}
    for row in rows:
        key = normalise_code(row.code)
        if key in by_code:
            report.warnings.append(f"catalogue lists {row.code} more than once")
            continue
        by_code[key] = row

    files: list[Path] = []
    if drawings is not None:
        try:
            files = scan_drawings(drawings)
        except CatalogueError as exc:
            report.errors.append(str(exc))
    if limit is not None:
        files = files[:limit]

    matched_rows: set[str] = set()

    for path in files:
        row: TableRow | None = None
        for candidate in code_candidates(path):
            row = by_code.get(normalise_code(candidate))
            if row is not None:
                break

        profile_id = row.code if row is not None else path.stem
        result = analyse_drawing(
            path,
            profile_id=profile_id,
            system_series=system_series,
            material_id=material_id,
            torsion=torsion,
        )
        if not result.ok:
            report.errors.append(f"{path.name}: {result.error}")
            if row is None:
                report.unmatched_drawings.append(str(path))
            continue

        entry = CatalogueEntry(
            profile_id=profile_id,
            system_series=system_series,
            name=(row.description if row is not None else None) or result.definition.name,
            definition=result.definition,
            measured=result.properties,
            published=dict(row.values) if row is not None else {},
            dxf_path=str(path),
            pdf_page=row.page if row is not None else None,
            warnings=list(result.warnings),
        )
        if row is not None:
            matched_rows.add(normalise_code(row.code))
            if row.partial:
                entry.warnings.append(
                    "the catalogue row had fewer numbers than the table has "
                    "columns, so the published figures past the gap may be "
                    "attributed to the wrong property"
                )
            entry.checks = cross_check(
                entry.published, result.properties, tolerances=tolerances
            )
        else:
            report.unmatched_drawings.append(str(path))
        report.entries.append(entry)

    # Rows nothing was drawn for still belong in the library: they carry the
    # weight and the price, which is enough to quote from even when there is
    # no geometry to machine from.
    for key, row in by_code.items():
        if key in matched_rows:
            continue
        report.unmatched_rows.append(row.code)
        report.entries.append(
            CatalogueEntry(
                profile_id=row.code,
                system_series=system_series,
                name=row.description,
                published=dict(row.values),
                pdf_page=row.page,
                warnings=(
                    ["no drawing found; figures are the supplier's, unchecked"]
                    + (["the catalogue row was short of columns"] if row.partial else [])
                ),
            )
        )

    report.entries.sort(key=lambda entry: entry.profile_id)
    _log.info(
        "ingested %d entries (%d with geometry, %d verified, %d conflicts) in %.2f s",
        len(report.entries),
        len(report.with_geometry),
        len(report.verified),
        len(report.conflicts),
        time.perf_counter() - started,
    )
    return report


def to_plugin(
    report: IngestionReport,
    *,
    plugin_id: str,
    name: str,
    version: str = "1.0.0",
    include_conflicts: bool = False,
) -> dict[str, object]:
    """Package the ingested profiles as a ProfileOS data plugin.

    Entries whose published and measured figures disagree are left out by
    default. Shipping them would put a number into the library that two sources
    contradict each other about, and the fabricator would have no way to see
    that from inside a cutting list. ``include_conflicts`` overrides it for the
    case where the supplier has confirmed which figure is right.
    """
    profiles: list[dict[str, object]] = []
    skipped: list[str] = []
    for entry in report.entries:
        if entry.definition is None:
            continue
        if entry.disagreements and not include_conflicts:
            skipped.append(entry.profile_id)
            continue
        payload = entry.definition.model_dump(mode="json")
        payload["metadata"] = {
            **(payload.get("metadata") or {}),
            "ingested_from": entry.dxf_path,
            "catalogue_page": entry.pdf_page,
            "verification": entry.status,
            "published": entry.published,
        }
        profiles.append(payload)

    return {
        "plugin_id": plugin_id,
        "name": name,
        "version": version,
        "kind": "profile_library",
        "source": report.source,
        "generated_at": report.started_at.isoformat(),
        "profiles": profiles,
        "excluded_for_conflict": skipped,
    }


__all__ = [
    "normalise_code",
    "code_candidates",
    "DrawingResult",
    "analyse_drawing",
    "scan_drawings",
    "measured_values",
    "cross_check",
    "ingest",
    "to_plugin",
]
