"""Catalogue ingestion: build an owned profile library from what suppliers publish.

Typical use::

    from profileos.catalogue import ingest, to_plugin

    report = ingest(table="klil-4300.pdf", drawings="dxf/", system_series="4300")
    print(report.summary())
    for entry in report.conflicts:
        print(entry.profile_id, [c.describe() for c in entry.disagreements])
"""

from __future__ import annotations

from .ingest import (
    DrawingResult,
    analyse_drawing,
    code_candidates,
    cross_check,
    ingest,
    measured_values,
    normalise_code,
    scan_drawings,
    to_plugin,
)
from .model import (
    DEFAULT_TOLERANCES,
    CatalogueEntry,
    CheckStatus,
    IngestionReport,
    PropertyCheck,
    SourceKind,
)
from .tables import (
    DEFAULT_CODE_PATTERN,
    STANDARD_COLUMNS,
    UNIT_SCALE,
    CatalogueError,
    Column,
    TableRow,
    TableSpec,
    detect_decimal,
    numbers_in,
    parse_lines,
    parse_number,
    pdf_pages,
    pypdf_available,
    read_table,
    rows_from_csv,
    rows_from_pdf,
    scale_for,
)

__all__ = [
    # tables
    "CatalogueError",
    "Column",
    "STANDARD_COLUMNS",
    "DEFAULT_CODE_PATTERN",
    "UNIT_SCALE",
    "scale_for",
    "TableSpec",
    "TableRow",
    "detect_decimal",
    "parse_number",
    "numbers_in",
    "parse_lines",
    "pypdf_available",
    "pdf_pages",
    "rows_from_pdf",
    "rows_from_csv",
    "read_table",
    # model
    "SourceKind",
    "CheckStatus",
    "DEFAULT_TOLERANCES",
    "PropertyCheck",
    "CatalogueEntry",
    "IngestionReport",
    # ingest
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
