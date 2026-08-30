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


def read_table(path: Path, expected: Iterable[str] = ()) -> Table:
    """Read a delimited export, finding the header row wherever it is."""
    source = Path(path)
    if not source.is_file():
        raise ProfileOSError(f"אין קובץ בשם {source}")

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
    "to_number",
]
