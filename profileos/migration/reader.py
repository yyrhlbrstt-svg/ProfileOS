"""Reading the spreadsheet a shop already has.

Nobody moves off their old software by typing four hundred customers in
again. They export to Excel, and the file that lands on the desk has three
properties that defeat most importers:

*It is not UTF-8.* Excel on a Hebrew Windows saves CSV in windows-1255, and
reading that as UTF-8 turns every name into mojibake — which looks like the
import worked until somebody opens the customer list.

*The headers are in Hebrew, and not the same Hebrew twice.* One export says
"שם לקוח", the next says "שם", the one from the accountant says "לקוח".

*There is rubbish above the header row.* A title, a date, an empty line, and
then the real columns.

All three are handled here, and none of them is guessed at silently: the
reader reports which encoding it settled on and which column it matched to
which field, so somebody can look at that before four hundred rows are
written anywhere.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("migration.reader")

#: Tried in order. ``utf-8-sig`` first because a modern export is usually
#: that; ``cp1255`` is what Excel on a Hebrew Windows actually writes.
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1255", "cp1252", "iso-8859-8")

#: Hebrew letters, used to tell a good decoding from a plausible one.
_HEBREW = set("אבגדהוזחטיכךלמםנןסעפףצץקרשת")


def _hebrew_share(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch in _HEBREW) / len(letters)


def sniff_encoding(path: Path) -> tuple[str, str]:
    """Decode the file, and say which encoding was used.

    Every legacy encoding decodes almost any byte, so "it did not raise" is
    not evidence. The tie is broken by which decoding produces the most
    Hebrew letters, because a Hebrew export decoded wrongly produces accented
    Latin instead — plausible bytes, nonsense words.
    """
    raw = Path(path).read_bytes()

    # A byte-order mark settles it: the file says what it is, and guessing
    # past that can turn a real UTF-8 export into plausible-looking nonsense.
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"),
                          (b"\xff\xfe", "utf-16"),
                          (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            text = raw.decode(encoding)
            if not text.strip():
                raise ProfileOSError(f"הקובץ {Path(path).name} ריק")
            _log.info("Read %s as %s (byte-order mark)", Path(path).name, encoding)
            return text, encoding

    if not raw.strip():
        raise ProfileOSError(f"הקובץ {Path(path).name} ריק")

    best: tuple[float, str, str] | None = None
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "�" in text:
            continue
        score = _hebrew_share(text)
        # A clean UTF-8 decode of a file with no Hebrew at all is still right.
        if encoding in ("utf-8-sig", "utf-8") and score == 0:
            score = 0.01
        if best is None or score > best[0]:
            best = (score, encoding, text)

    if best is None:
        raise ProfileOSError(
            f"לא ניתן לקרוא את {Path(path).name} באף קידוד מוכר. "
            "שמרו אותו מ-Excel כ-CSV UTF-8"
        )
    _log.info("Read %s as %s", Path(path).name, best[1])
    return best[2], best[1]


def _split_rows(text: str) -> list[list[str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(StringIO(text), dialect) if any(cell.strip() for cell in row)]


def _looks_like_header(row: list[str], expected: Iterable[str]) -> bool:
    """Whether this row names columns rather than holding data."""
    filled = [cell.strip() for cell in row if cell.strip()]
    if len(filled) < 2:
        return False
    if any(_is_number(cell) for cell in filled) and len(filled) < 4:
        return False
    wanted = {alias for alias in expected}
    return any(normalise(cell) in wanted for cell in filled)


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", "").replace("₪", "").strip())
    except ValueError:
        return False
    return True


def normalise(header: str) -> str:
    """One spelling of a column name, so aliases can be matched."""
    return (
        header.replace("\ufeff", "").strip()
        .replace("״", '"')
        .replace("׳", "'")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", "")
        .replace("  ", " ")
        .casefold()
    )


@dataclass
class Table:
    """A spreadsheet as read, and what had to be decided to read it."""

    rows: list[dict[str, str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    encoding: str = ""
    #: Rows above the header that were skipped — a title, a date, a blank.
    skipped_preamble: int = 0
    source: str = ""

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def describe(self) -> str:
        return (
            f"{self.source}: ⁦{len(self.rows)}⁩ שורות, ⁦{len(self.headers)}⁩ עמודות, "
            f"קידוד {self.encoding}"
        )


#: Refuse a worksheet that unpacks to more than any price list could be.
MAX_SHEET_BYTES = 64 * 1024 * 1024

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _column_index(reference: str) -> int:
    """'C7' → 2."""
    index = 0
    for char in reference:
        if not char.isalpha():
            break
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
    return index - 1


def _number_text(raw: str) -> str:
    """A stored number as somebody would type it: 12.5, not 12.499999999999998."""
    try:
        value = float(raw)
    except ValueError:
        return raw
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return format(value, ".10g")


def xlsx_rows(path: Path) -> list[list[str]]:
    """The first worksheet of an Excel file, as rows of text.

    Suppliers send price lists as Excel, and a shop should not have to open
    each one and save it as CSV first. An .xlsx file is a zip of XML; the
    first sheet, its shared strings and its numbers are all that is read —
    no formulas are run, no macros, nothing but the values Excel saved.
    """
    import zipfile
    from xml.etree import ElementTree

    try:
        with zipfile.ZipFile(path) as book:
            names = set(book.namelist())

            def read(name: str) -> bytes:
                info = book.getinfo(name)
                if info.file_size > MAX_SHEET_BYTES:
                    raise ProfileOSError(f"{Path(path).name}: הגיליון גדול מדי לקריאה")
                return book.read(name)

            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(read("xl/sharedStrings.xml"))
                for item in root.findall("m:si", _NS):
                    shared.append("".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t")))

            sheet = "xl/worksheets/sheet1.xml"
            if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
                workbook = ElementTree.fromstring(read("xl/workbook.xml"))
                first = workbook.find("m:sheets/m:sheet", _NS)
                rels = ElementTree.fromstring(read("xl/_rels/workbook.xml.rels"))
                if first is not None:
                    wanted = first.get(f"{{{_NS['r']}}}id")
                    for rel in rels.findall("rel:Relationship", _NS):
                        if rel.get("Id") == wanted:
                            target = rel.get("Target", "").lstrip("/")
                            sheet = target if target.startswith("xl/") else f"xl/{target}"
            if sheet not in names:
                raise ProfileOSError(f"{Path(path).name}: לא נמצא גיליון בקובץ")

            rows: list[list[str]] = []
            for row in ElementTree.fromstring(read(sheet)).iter(f"{{{_NS['m']}}}row"):
                cells: dict[int, str] = {}
                for position, cell in enumerate(row.findall("m:c", _NS)):
                    reference = cell.get("r", "")
                    column = _column_index(reference) if reference else position
                    kind = cell.get("t", "n")
                    value = cell.find("m:v", _NS)
                    raw = value.text if value is not None and value.text else ""
                    if kind == "s" and raw:
                        text = shared[int(raw)] if int(raw) < len(shared) else ""
                    elif kind == "inlineStr":
                        text = "".join(t.text or "" for t in cell.iter(f"{{{_NS['m']}}}t"))
                    elif kind == "b":
                        text = "TRUE" if raw == "1" else "FALSE"
                    elif kind in ("str", "e"):
                        text = raw
                    else:
                        text = _number_text(raw) if raw else ""
                    cells[column] = text.strip()
                if cells and any(cells.values()):
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            return rows
    except zipfile.BadZipFile as exc:
        raise ProfileOSError(f"{Path(path).name} אינו קובץ Excel תקין") from exc
    except ElementTree.ParseError as exc:
        raise ProfileOSError(f"{Path(path).name}: הקובץ פגום ולא נקרא") from exc


def read_table(path: Path, expected: Iterable[str] = ()) -> Table:
    """Read a delimited export or an Excel sheet, finding the header row wherever it is."""
    source = Path(path)
    if not source.is_file():
        raise ProfileOSError(f"אין קובץ בשם {source}")

    suffix = source.suffix.lower()
    if suffix == ".xls":
        raise ProfileOSError(
            f"{source.name} שמור בפורמט Excel ישן. פתחו אותו ושמרו בשם כ־xlsx או CSV."
        )
    if suffix in (".xlsx", ".xlsm"):
        rows, encoding = xlsx_rows(source), "Excel"
    else:
        text, encoding = sniff_encoding(source)
        rows = _split_rows(text)
    if not rows:
        raise ProfileOSError(f"{source.name} אינו מכיל שורות")

    expected = set(expected)
    header_index = 0
    if expected:
        # Look a little way down for the real header: exports often start
        # with a title and a date.
        for index, row in enumerate(rows[:10]):
            if _looks_like_header(row, expected):
                header_index = index
                break

    headers = [cell.strip() for cell in rows[header_index]]
    # Unnamed or repeated header cells would lose a column's values.
    seen: dict[str, int] = {}
    for index, header in enumerate(headers):
        name = header or f"עמודה {index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers[index] = name
    table = Table(
        headers=headers,
        encoding=encoding,
        skipped_preamble=header_index,
        source=source.name,
    )
    for row in rows[header_index + 1:]:
        entry = {
            headers[index]: (row[index].strip() if index < len(row) else "")
            for index in range(len(headers))
        }
        if any(entry.values()):
            table.rows.append(entry)
    return table


def match_columns(
    headers: Iterable[str], aliases: dict[str, tuple[str, ...]]
) -> dict[str, str]:
    """Which column feeds which field, by any of its known spellings.

    Exact matches first, then a contains check, so "שם הלקוח" finds "שם" but
    an exact "שם" column is never lost to a longer one.
    """
    found: dict[str, str] = {}
    normalised = {header: normalise(header) for header in headers}

    for field_name, spellings in aliases.items():
        wanted = {normalise(spelling) for spelling in spellings}
        for header, plain in normalised.items():
            if header in found.values():
                continue
            if plain in wanted:
                found[field_name] = header
                break

    for field_name, spellings in aliases.items():
        if field_name in found:
            continue
        wanted = {normalise(spelling) for spelling in spellings}
        for header, plain in normalised.items():
            if header in found.values():
                continue
            if any(spelling and spelling in plain for spelling in wanted):
                found[field_name] = header
                break
    return found


def to_number(text: Any) -> float | None:
    """A number as a spreadsheet writes it: commas, shekels, spaces."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = (
        str(text).replace(",", "").replace("₪", "").replace("%", "")
        .replace("⁦", "").replace("⁩", "").strip()
    )
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


__all__ = [
    "ENCODINGS",
    "Table",
    "match_columns",
    "normalise",
    "read_table",
    "sniff_encoding",
    "xlsx_rows",
    "to_number",
]
