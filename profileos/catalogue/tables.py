"""Reading supplier profile tables out of PDFs, CSVs and spreadsheets.

Why this exists
---------------
A profile library is the thing an aluminium fabricator cannot buy their way out
of. The established packages sell it as a subscription: the catalogues live
inside the software, the supplier's updates arrive through the vendor, and the
fabricator pays for access to numbers that describe extrusions they are already
buying. This module reads those numbers straight from what the supplier already
publishes, so the library belongs to the shop.

What is hard about it
---------------------
A PDF has no table structure. It has glyphs at coordinates, and a text
extractor turns them back into lines by guessing. So the parser here does not
try to be clever about layout; it works on lines, finds the ones that begin
with something shaped like an article code, and reads the numbers that follow.
Which number means what comes from a header row when one can be found, and from
an explicit column list when it cannot.

Units
-----
Catalogues publish in centimetre units — cm² for area, cm⁴ for second moments,
cm³ for section moduli — while everything inside ProfileOS is millimetres.
Getting that conversion wrong is a factor of ten thousand on a second moment,
which is exactly the kind of error that looks plausible in a table and collapses
a mullion, so the unit is part of the column definition and never inferred.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger(__name__)


class CatalogueError(ProfileOSError):
    """A supplier catalogue could not be read."""


#: Multiplier from a catalogue unit to the canonical millimetre system.
UNIT_SCALE: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "mm2": 1.0,
    "cm2": 100.0,
    "mm3": 1.0,
    "cm3": 1_000.0,
    "mm4": 1.0,
    "cm4": 10_000.0,
    "mm6": 1.0,
    "cm6": 1_000_000.0,
    "kg/m": 1.0,
    "g/m": 0.001,
    "kg/cm": 100.0,
    "": 1.0,
}


def scale_for(unit: str) -> float:
    key = unit.strip().lower().replace("^", "").replace("²", "2").replace("³", "3")
    key = key.replace("⁴", "4").replace("⁶", "6")
    if key not in UNIT_SCALE:
        raise CatalogueError(f"Unknown catalogue unit {unit!r}", unit=unit)
    return UNIT_SCALE[key]


@dataclass(frozen=True)
class Column:
    """One numeric column of a supplier table, and what it means."""

    #: Canonical property name: area, ixx, iyy, mass_per_metre, width, ...
    property: str
    unit: str = ""
    #: Header words that identify this column, lower-cased. Order is irrelevant.
    aliases: tuple[str, ...] = ()

    @property
    def scale(self) -> float:
        return scale_for(self.unit)


#: The columns an aluminium profile table almost always carries, with the
#: header words used for them in English, German, Italian and Hebrew — the four
#: languages an Israeli fabricator's supplier catalogues actually arrive in.
STANDARD_COLUMNS: tuple[Column, ...] = (
    Column("mass_per_metre", "kg/m", ("kg/m", "weight", "gewicht", "peso", "משקל")),
    Column("area", "cm2", ("area", "a", "querschnitt", "sezione", "שטח", "שטח חתך")),
    Column("perimeter", "cm", ("perimeter", "umfang", "perimetro", "היקף")),
    Column("ixx", "cm4", ("ix", "ixx", "jx", "i-x", "מומנט x")),
    Column("iyy", "cm4", ("iy", "iyy", "jy", "i-y", "מומנט y")),
    Column("sx", "cm3", ("wx", "sx", "zx", "w-x")),
    Column("sy", "cm3", ("wy", "sy", "zy", "w-y")),
    Column("width", "mm", ("width", "b", "breite", "larghezza", "רוחב")),
    Column("height", "mm", ("height", "h", "höhe", "hohe", "altezza", "גובה")),
    Column("wall_thickness", "mm", ("t", "s", "wall", "wandstärke", "עובי")),
    Column("j", "cm4", ("it", "j", "torsion", "torsionsträgheit")),
)

#: Anything that looks like a supplier article number: letters, digits, dots,
#: dashes and slashes, from two characters up. Two-character codes are real —
#: cover caps and gaskets often have them — and the guard against a stray word
#: being read as a code is the number of data columns that follow it, not the
#: code's length.
DEFAULT_CODE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._\-/]{1,23})\b")

#: A number, with either separator, optionally signed, optionally with
#: thousands separators. Catalogues mix "1.234,5" and "1,234.5" freely.
#:
#: The lookarounds matter more than the body. Without the leading one, the "2"
#: of a "cm2" column header reads as data and the header stops being
#: recognisable as a header; without the trailing one, the grouped alternative
#: bites off "430" from "4300" and invents a number that was never printed.
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])[-+]?"
    r"(?:\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![0-9])"
)


#: A separator followed by anything other than exactly three digits can only be
#: a decimal point — no thousands group is ever one, two or four digits long.
_DECIMAL_EVIDENCE = {
    ".": re.compile(r"\d[.](?:\d{1,2}|\d{4,})(?![\d])"),
    ",": re.compile(r"\d[,](?:\d{1,2}|\d{4,})(?![\d])"),
}
#: Both separators in one number settles it outright: the last one is decimal.
_BOTH_COMMA_LAST = re.compile(r"\d[.]\d{3}[,]\d")
_BOTH_DOT_LAST = re.compile(r"\d[,]\d{3}[.]\d")


def detect_decimal(text: str) -> str:
    """Work out whether a document writes decimals with ``.`` or ``,``.

    ``1,842`` is one thousand eight hundred and forty two in London and one
    point eight four two in Milan, and nothing inside the token can tell them
    apart. The document as a whole can: a single ``6,82`` elsewhere on the page
    settles every comma in the file, because no catalogue mixes conventions.

    Guessing per token is what produces a profile weighing 1842 kg/m, which is
    the kind of number that passes a schema and fails a building.
    """
    if _BOTH_COMMA_LAST.search(text):
        return ","
    if _BOTH_DOT_LAST.search(text):
        return "."
    dots = len(_DECIMAL_EVIDENCE["."].findall(text))
    commas = len(_DECIMAL_EVIDENCE[","].findall(text))
    if commas > dots:
        return ","
    return "."


def parse_number(token: str, decimal: str = ".") -> float | None:
    """Read a catalogue number written with ``decimal`` as its decimal point."""
    text = token.strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    grouping = "," if decimal == "." else "."
    text = text.replace(grouping, "").replace(decimal, ".")
    try:
        return float(text)
    except ValueError:
        return None


def numbers_in(line: str, decimal: str = ".") -> list[float]:
    """Every number on a line, in order."""
    values: list[float] = []
    for match in _NUMBER.finditer(line):
        value = parse_number(match.group(), decimal)
        if value is not None:
            values.append(value)
    return values


@dataclass
class TableSpec:
    """How to read one supplier's table."""

    #: Column order after the article code. Used when no header is found, and
    #: as the set of columns a header may map to.
    columns: Sequence[Column] = field(default=STANDARD_COLUMNS)
    code_pattern: re.Pattern[str] = DEFAULT_CODE_PATTERN
    #: Rows must carry at least this many numbers after the code to count as
    #: data. Two is what separates a real row from a heading that happens to
    #: carry a series number — and article codes are themselves often numeric,
    #: so the code being a number is no evidence either way.
    min_numbers: int = 2
    #: Series name, when the whole catalogue is one system.
    system_series: str | None = None
    #: When true the column order is taken from ``columns`` verbatim rather
    #: than being matched against a header row.
    fixed_order: bool = False
    #: ``"."``, ``","`` or ``"auto"`` to work it out from the document.
    decimal: str = "auto"

    def match_header(self, line: str, decimal: str = ".") -> list[Column] | None:
        """Map a header line onto columns, or ``None`` if it is not a header.

        A header is recognised by carrying at least two known column names and
        essentially no data — a row of numbers with a stray word in it is a
        data row, not a header.
        """
        lowered = line.lower()
        if len(numbers_in(line, decimal)) > 2:
            return None
        tokens = [t for t in re.split(r"[\s|;,\t]+", lowered) if t]
        if len(tokens) < 3:
            return None

        order: list[Column] = []
        for token in tokens:
            cleaned = token.strip("[]():.").replace("²", "2").replace("³", "3")
            cleaned = cleaned.replace("⁴", "4")
            for column in self.columns:
                if cleaned in column.aliases or cleaned.split("[")[0] in column.aliases:
                    if column not in order:
                        order.append(column)
                    break
        return order if len(order) >= 2 else None


@dataclass
class TableRow:
    """One parsed catalogue row: an article code and canonical property values."""

    code: str
    values: dict[str, float]
    #: Whatever text followed the code before the numbers started.
    description: str | None = None
    #: True when the row had fewer numbers than the table had columns, so the
    #: column mapping past the gap cannot be trusted.
    partial: bool = False
    page: int | None = None
    raw: str = ""

    def summary(self) -> dict[str, object]:
        return {"code": self.code, "description": self.description, **self.values}


def parse_lines(
    lines: Iterable[str], spec: TableSpec | None = None, *, page: int | None = None
) -> list[TableRow]:
    """Read data rows out of already-extracted text lines.

    The column order is re-learned every time a header line appears, which is
    what makes a multi-section catalogue work: one PDF often carries a table of
    frames, then a table of sashes with different columns, and treating the
    first header as global would silently mislabel the second table.
    """
    rules = spec or TableSpec()
    body = list(lines)
    decimal = rules.decimal
    if decimal not in {".", ","}:
        decimal = detect_decimal("\n".join(body))
    order: list[Column] = list(rules.columns) if rules.fixed_order else []
    rows: list[TableRow] = []

    for line in body:
        text = line.strip()
        if not text:
            continue

        if not rules.fixed_order:
            header = rules.match_header(text, decimal)
            if header is not None:
                order = header
                continue
            if not order:
                order = list(rules.columns)

        code_match = rules.code_pattern.match(text)
        if not code_match:
            continue
        code = code_match.group(1)
        remainder = text[code_match.end() :]
        matches = list(_NUMBER.finditer(remainder))
        if len(matches) < rules.min_numbers:
            continue

        # Descriptions carry numbers of their own — "Mullion 70/100", "6060-T6",
        # "1.5 mm wall" — and taking the first numbers on the line as the first
        # columns silently shifts every value one place left. The data columns
        # are the run at the *end* of the line, so the trailing block the right
        # size wins, provided no words are mixed into it.
        if len(matches) > len(order) and order:
            tail = matches[-len(order) :]
            interleaved = any(
                re.search(r"[A-Za-z]", remainder[before.end() : after.start()])
                for before, after in zip(tail, tail[1:])
            )
            if not interleaved:
                matches = tail

        values = [parse_number(m.group(), decimal) for m in matches]
        values = [v for v in values if v is not None]
        description = remainder[: matches[0].start()].strip(" \t|;:-,") or None

        mapped: dict[str, float] = {}
        for column, value in zip(order, values):
            mapped[column.property] = value * column.scale
        if mapped:
            rows.append(
                TableRow(
                    code=code,
                    values=mapped,
                    description=description,
                    # A row with fewer numbers than the header has columns has a
                    # blank cell somewhere, and nothing in the extracted text
                    # says where. The values are still offered, flagged, because
                    # a flagged figure the cross-check can catch beats no figure.
                    partial=len(values) < len(order),
                    page=page,
                    raw=text,
                )
            )
    return rows


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def pypdf_available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def pdf_pages(path: str | Path) -> Iterator[tuple[int, str]]:
    """Yield ``(page number, text)`` for each page of a PDF."""
    if not pypdf_available():
        raise CatalogueError(
            "Reading PDF catalogues needs pypdf; install profileos[catalogue]"
        )
    from pypdf import PdfReader

    source = Path(path)
    if not source.is_file():
        raise CatalogueError(f"Catalogue PDF not found: {source}", path=str(source))
    try:
        reader = PdfReader(str(source))
    except Exception as exc:  # noqa: BLE001 - third-party parser
        raise CatalogueError(f"Could not open {source.name}: {exc}") from exc

    for number, page in enumerate(reader.pages, start=1):
        try:
            yield number, page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the run
            _log.warning("page %d of %s could not be read: %s", number, source.name, exc)
            yield number, ""


def rows_from_pdf(path: str | Path, spec: TableSpec | None = None) -> list[TableRow]:
    """Parse every data row in a PDF catalogue.

    The decimal convention is settled once across the whole document before any
    page is parsed. Deciding it page by page would let a page of round numbers
    disagree with the page before it, and the two pages would then disagree
    about what a comma means in the same catalogue.
    """
    pages = list(pdf_pages(path))
    rules = spec or TableSpec()
    if rules.decimal not in {".", ","}:
        rules = replace(rules, decimal=detect_decimal("\n".join(t for _, t in pages)))

    rows: list[TableRow] = []
    for number, text in pages:
        rows.extend(parse_lines(text.splitlines(), rules, page=number))
    _log.info("read %d catalogue rows from %s", len(rows), Path(path).name)
    return rows


def rows_from_csv(
    path: str | Path,
    *,
    columns: dict[str, str] | None = None,
    code_field: str = "code",
) -> list[TableRow]:
    """Parse a delimited export, which is what a supplier sends when asked.

    ``columns`` maps a CSV header to ``"property:unit"``, e.g.
    ``{"Ix": "ixx:cm4"}``. Without it the header names are matched against the
    standard aliases, exactly as for a PDF.
    """
    source = Path(path)
    if not source.is_file():
        raise CatalogueError(f"Catalogue file not found: {source}", path=str(source))

    text = source.read_text(encoding="utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    decimal = detect_decimal(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise CatalogueError(f"{source.name} has no header row")

    mapping: dict[str, tuple[str, float]] = {}
    for name in reader.fieldnames:
        key = (name or "").strip()
        if columns and key in columns:
            prop, _, unit = columns[key].partition(":")
            mapping[key] = (prop, scale_for(unit))
            continue
        lowered = key.lower().strip("[]() ").replace("²", "2").replace("³", "3")
        lowered = lowered.replace("⁴", "4")
        for column in STANDARD_COLUMNS:
            if lowered in column.aliases:
                mapping[key] = (column.property, column.scale)
                break

    code_key = next(
        (n for n in reader.fieldnames if (n or "").strip().lower() == code_field.lower()),
        reader.fieldnames[0],
    )

    rows: list[TableRow] = []
    for record in reader:
        code = (record.get(code_key) or "").strip()
        if not code:
            continue
        values: dict[str, float] = {}
        for name, (prop, scale) in mapping.items():
            if name == code_key:
                continue
            number = parse_number(record.get(name) or "", decimal)
            if number is not None:
                values[prop] = number * scale
        description = None
        for candidate in ("description", "name", "designation", "תיאור"):
            for name in reader.fieldnames:
                if (name or "").strip().lower() == candidate:
                    description = (record.get(name) or "").strip() or None
                    break
            if description:
                break
        rows.append(TableRow(code=code, values=values, description=description))
    _log.info("read %d catalogue rows from %s", len(rows), source.name)
    return rows


def read_table(path: str | Path, spec: TableSpec | None = None) -> list[TableRow]:
    """Read a catalogue table from whatever format it arrived in."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return rows_from_pdf(path, spec)
    if suffix in {".csv", ".tsv", ".txt"}:
        return rows_from_csv(path)
    raise CatalogueError(
        f"Unsupported catalogue format {suffix!r}; expected .pdf, .csv or .tsv",
        path=str(path),
    )


__all__ = [
    "CatalogueError",
    "UNIT_SCALE",
    "scale_for",
    "Column",
    "STANDARD_COLUMNS",
    "DEFAULT_CODE_PATTERN",
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
]
