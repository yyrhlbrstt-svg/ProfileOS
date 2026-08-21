"""The type library: every opening this shop can make, at any size it asks for.

A list of ready-made windows is a demo, not a library. What a fabricator needs
is what the German packages give them — a tree of types, a leaf count, a
series, and a size box — except that typing into three boxes and a tree is
slower than saying the thing out loud. So this library is generated rather
than stored: it holds the *types* an Israeli aluminium shop builds, and the
search turns a sentence into a real, buildable opening.

    הזזה 4 כנפיים 6000/2200 קליל 9000

is a family, a leaf count, a size and a series, and every one of those parts
is optional. Nothing is enumerated in advance, so there is no size that is
"missing from the list": a width nobody has ever ordered is found the same way
as the one every third job uses.

Numbers are read the way the trade says them:

* ``2.4``  — a decimal point means metres.
* ``3``    — a small whole number is a leaf count, not a size.
* ``240``  — two or three digits are centimetres, because that is how a
  customer says a window over the phone.
* ``6000`` — four digits are millimetres, which is how it is cut.

The series comes from the systems directory, so an opening picked here
carries the same provenance as everything else: quotable on typical figures,
cuttable only once the supplier's catalogue has been ingested.

Profiles work the same way — the drawings this installation can actually open,
the shop's own folder first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .core.config import get_settings, samples_dir
from .core.logging_setup import get_logger

_log = get_logger("library")


def _folds(text: str) -> str:
    return (
        text.casefold()
        .replace("־", "-")
        .replace("״", '"')
        .replace("’", "'")
        .strip()
    )


def _matches(needle: str, terms: tuple[str, ...]) -> bool:
    """Every word typed has to appear somewhere, in any order."""
    words = [word for word in _folds(needle).split() if word]
    if not words:
        return True
    haystack = " ".join(_folds(term) for term in terms)
    return all(word in haystack for word in words)


# --------------------------------------------------------------------------- #
# Profiles
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LibraryProfile:
    """One cross-section the shop can open, and where it came from."""

    profile_id: str
    hebrew: str
    path: Path
    origin: str
    note: str = ""

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def search_terms(self) -> tuple[str, ...]:
        return (self.profile_id, self.hebrew, self.origin, self.note, self.path.name)


#: The sample sections that ship with the software, named in Hebrew for what
#: they are rather than by file name.
SAMPLE_PROFILES: tuple[tuple[str, str, str], ...] = (
    ("mullion_mb70", "זקף תרמי ⁦70⁩ מ״מ", "זקף עם פס פוליאמיד, לחלון ולקיר מסך"),
    ("frame_thermal", "משקוף תרמי", "משקוף היקפי עם שבירה תרמית"),
    ("glazing_bead", "סרגל זיגוג", "סרגל הצמדה לזכוכית בידודית"),
    ("gapped_box", "חתך קופסה", "חתך בדיקה — קופסה עם רווח"),
)

#: Extensions a profile drawing arrives in.
DRAWING_SUFFIXES = (".dxf", ".dwg")


def sample_profiles() -> list[LibraryProfile]:
    """The examples shipped with the software, whichever ones are present."""
    folder = samples_dir()
    found = []
    for stem, hebrew, note in SAMPLE_PROFILES:
        path = folder / f"{stem}.dxf"
        if path.is_file():
            found.append(LibraryProfile(stem, hebrew, path, "דוגמאות", note))
    return found


def folder_profiles(folder: Path | None = None) -> list[LibraryProfile]:
    """Every drawing in the shop's own profile folder.

    This is how a shop's library grows: they drop the supplier's DXF files
    into one folder and every one of them is findable from then on, without an
    import step and without this software copying anybody's drawing anywhere.
    """
    if folder is None:
        try:
            folder = get_settings().profiles_dir
        except Exception:  # noqa: BLE001 - a search must not fail on settings
            return []
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() not in DRAWING_SUFFIXES or not path.is_file():
            continue
        found.append(LibraryProfile(path.stem, path.stem, path, "התיקייה שלך"))
    return found


def profile_library(folder: Path | None = None) -> list[LibraryProfile]:
    """Everything openable, the shop's own drawings first."""
    return folder_profiles(folder) + sample_profiles()


def search_profiles(text: str, folder: Path | None = None) -> list[LibraryProfile]:
    return [p for p in profile_library(folder) if _matches(text, p.search_terms())]


# --------------------------------------------------------------------------- #
# Opening types
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OpeningFamily:
    """A kind of opening, before anybody has said how big it is.

    ``leaves`` is every leaf count the type is made in; ``widths`` and
    ``heights`` are the sizes offered when nobody has asked for one in
    particular. They are a browsing convenience, never a limit — any size the
    search is given is built, whether it appears here or not.
    """

    family_id: str
    hebrew: str
    kind: str
    sash_type: str
    leaves: tuple[int, ...] = (1,)
    rows: int = 1
    #: Which column carries the opening leaf: the outer one, the middle one,
    #: or none at all for fixed glazing.
    sash_position: str = "first"
    widths: tuple[float, ...] = (1200, 1800, 2400)
    heights: tuple[float, ...] = (1200, 1400)
    sill: float = 900.0
    glass: str = "dgu-6-16-6"
    note: str = ""
    tags: tuple[str, ...] = ()
    #: Outside these the type is the wrong choice, not merely a large one.
    min_width: float = 300.0
    max_width: float = 12000.0
    min_height: float = 300.0
    max_height: float = 6000.0

    def search_terms(self) -> tuple[str, ...]:
        return (self.family_id, self.hebrew, self.kind, self.sash_type,
                self.note, *self.tags)

    def leaf_word(self, leaves: int) -> str:
        if leaves <= 1 or self.sash_type == "fixed":
            return ""
        return f"⁦{leaves}⁩ כנפיים"


#: Widths and heights offered for browsing. Everything an Israeli shop cuts
#: routinely; the search will make any other size on request.
_SLIDING_W = (1500, 1800, 2100, 2400, 2700, 3000, 3600, 4200, 4800, 5400, 6000)
_SLIDING_H = (1000, 1200, 1400, 1600, 1800, 2100, 2200, 2400)
_CASEMENT_W = (600, 800, 1000, 1200, 1400, 1600, 1800, 2000)
_CASEMENT_H = (600, 900, 1100, 1400, 1600, 1800)
_DOOR_W = (800, 900, 1000, 1100, 1200)
_DOOR_H = (2000, 2100, 2200, 2400)
_WALL_W = (3000, 4000, 5000, 6000, 8000, 10000, 12000)
_WALL_H = (2700, 3000, 3600, 4200, 5000, 6000)


#: Every type this shop makes. Sizes and leaf counts multiply out from here;
#: the search never has to have seen a combination before to build it.
FAMILIES: tuple[OpeningFamily, ...] = (
    OpeningFamily(
        "sliding", "חלון הזזה", "sliding_unit", "sliding",
        leaves=(2, 3, 4, 6), sash_position="first",
        widths=_SLIDING_W, heights=_SLIDING_H, sill=900,
        note="הזזה על מסילות — הפתח הנפוץ ביותר בארץ",
        tags=("הזזה", "sliding", "מסילה", "בלגי הזזה"),
        min_width=900, max_width=12000,
    ),
    OpeningFamily(
        "sliding_fixed", "הזזה עם קבוע", "sliding_unit", "sliding",
        leaves=(2, 3, 4), sash_position="first",
        widths=_SLIDING_W, heights=_SLIDING_H, sill=900,
        note="כנף נעה אחת מול זיגוג קבוע",
        tags=("הזזה", "קבוע", "משולב"),
        min_width=1200, max_width=12000,
    ),
    OpeningFamily(
        "lift_slide", "הרמה והזזה", "sliding_unit", "lift_slide",
        leaves=(2, 3, 4), sash_position="first",
        widths=(2400, 3000, 3600, 4200, 4800, 5400, 6000, 7200),
        heights=(2100, 2200, 2400, 2700), sill=0,
        note="כנף כבדה עם מנגנון הרמה — מפתחים גדולים",
        tags=("הרמה", "לפט", "lift", "כבד", "מרפסת"),
        min_width=1800, max_width=12000, min_height=1500,
    ),
    OpeningFamily(
        "sliding_door", "דלת הזזה למרפסת", "sliding_unit", "sliding",
        leaves=(2, 3, 4), sash_position="middle",
        widths=(2400, 2700, 3000, 3600, 4200, 4800, 6000),
        heights=(2100, 2200, 2400), sill=0,
        note="יציאה למרפסת, סף נמוך",
        tags=("מרפסת", "דלת", "הזזה", "יציאה"),
        min_width=1400, max_width=12000, min_height=1600,
    ),
    OpeningFamily(
        "casement", "חלון ציר (בלגי)", "window", "casement",
        leaves=(1, 2, 3), sash_position="first",
        widths=_CASEMENT_W, heights=_CASEMENT_H, sill=900,
        note="פתיחה על ציר צד",
        tags=("בלגי", "ציר", "casement", "כנף"),
        min_width=400, max_width=3000,
    ),
    OpeningFamily(
        "tilt_turn", "חלון נטוי-נפתח", "window", "tilt_turn",
        leaves=(1, 2), sash_position="first",
        widths=_CASEMENT_W, heights=_CASEMENT_H, sill=900,
        note="פתיחה כפולה — ציר צד ונטייה",
        tags=("נטוי", "קיפ", "tilt", "turn", "אירופאי"),
        min_width=500, max_width=1600, min_height=600,
    ),
    OpeningFamily(
        "top_hung", "חלון מתהפך עליון", "window", "top_hung",
        leaves=(1, 2), sash_position="first",
        widths=(600, 800, 900, 1000, 1200), heights=(400, 500, 600, 800),
        sill=1700, note="ציר עליון — שירותים, אמבטיה, אוורור",
        tags=("מתהפך", "אוורור", "אמבטיה", "שירותים"),
        min_width=300, max_width=2000, max_height=1200,
    ),
    OpeningFamily(
        "bottom_hung", "חלון מתהפך תחתון", "window", "bottom_hung",
        leaves=(1,), widths=(600, 800, 1000, 1200), heights=(400, 500, 600),
        sill=1700, note="ציר תחתון, נפתח פנימה",
        tags=("קיפ", "מתהפך", "אוורור"),
        min_width=300, max_width=1600, max_height=1000,
    ),
    OpeningFamily(
        "pivot", "חלון ציר מרכזי", "window", "pivot",
        leaves=(1,), widths=(800, 1000, 1200, 1400), heights=(800, 1000, 1200),
        sill=900, note="מסתובב סביב ציר אמצעי",
        tags=("ציר", "פיבוט", "pivot", "מסתובב"),
        min_width=600, max_width=2000,
    ),
    OpeningFamily(
        "fixed", "חלון קבוע", "window", "fixed",
        leaves=(1, 2, 3), sash_position="none",
        widths=(800, 1000, 1200, 1500, 1800, 2400, 3000),
        heights=(600, 900, 1200, 1400, 1800, 2200), sill=900,
        note="זיגוג קבוע ללא כנף",
        tags=("קבוע", "fixed", "אטום", "ויטרינה"),
        max_width=4000,
    ),
    OpeningFamily(
        "fanlight", "חלון עם אור עליון", "window", "casement",
        leaves=(1, 2), rows=2, sash_position="first",
        widths=(1000, 1200, 1400, 1800), heights=(1800, 2100, 2400),
        sill=600, note="כנף למטה, קבוע מעליה",
        tags=("אור עליון", "פנלייט", "fanlight"),
        min_height=1200,
    ),
    OpeningFamily(
        "door_single", "דלת כניסה", "door", "door",
        leaves=(1,), widths=_DOOR_W, heights=_DOOR_H, sill=0,
        note="כנף אחת",
        tags=("דלת", "כניסה", "door", "פתח"),
        min_width=600, max_width=1400, min_height=1800,
    ),
    OpeningFamily(
        "door_double", "דלת דו-כנפית", "door", "door",
        leaves=(2,), sash_position="first",
        widths=(1400, 1600, 1800, 2000, 2400), heights=_DOOR_H, sill=0,
        note="שתי כנפיים על ציר",
        tags=("דלת", "כניסה", "כפולה", "ראשית"),
        min_width=1200, max_width=3000, min_height=1800,
    ),
    OpeningFamily(
        "door_fanlight", "דלת עם אור עליון", "door", "door",
        leaves=(1, 2), rows=2, sash_position="first",
        widths=(1000, 1200, 1600, 1800), heights=(2400, 2700, 3000), sill=0,
        note="דלת עם זיגוג קבוע מעליה",
        tags=("דלת", "אור עליון", "כניסה"),
        min_height=2000,
    ),
    OpeningFamily(
        "shopfront", "חזית מסחרית", "shopfront", "door",
        leaves=(2, 3, 4, 5), sash_position="middle",
        widths=(3000, 4000, 5000, 6000, 8000), heights=(2400, 2700, 3000, 3600),
        sill=0, note="ויטרינה עם דלת משולבת",
        tags=("חנות", "ויטרינה", "מסחרי", "shopfront"),
        min_width=2000, max_width=12000, min_height=2000,
    ),
    OpeningFamily(
        "curtain_wall", "קיר מסך", "curtain_wall", "fixed",
        leaves=(2, 3, 4, 5, 6), rows=3, sash_position="none",
        widths=_WALL_W, heights=_WALL_H, sill=0,
        note="רשת זקפים וקורות",
        tags=("מסך", "curtain", "פסאדה", "חזית", "רשת"),
        min_width=1500, max_width=12000, min_height=1500, max_height=6000,
    ),
    OpeningFamily(
        "curtain_wall_vent", "קיר מסך עם פתח אוורור", "curtain_wall", "top_hung",
        leaves=(3, 4, 5), rows=3, sash_position="middle",
        widths=_WALL_W, heights=_WALL_H, sill=0,
        note="רשת עם כנף מתהפכת אחת",
        tags=("מסך", "אוורור", "curtain"),
        min_width=2000, max_width=12000, min_height=1800, max_height=6000,
    ),
    OpeningFamily(
        "partition", "מחיצת משרד", "curtain_wall", "door",
        leaves=(2, 3, 4), sash_position="middle",
        widths=(2400, 3000, 3600, 4200, 6000), heights=(2400, 2700, 3000),
        sill=0, note="מחיצה פנימית עם דלת",
        tags=("משרד", "מחיצה", "אופיס", "פנימי", "partition"),
        min_width=1200, max_width=12000, min_height=1800,
    ),
    OpeningFamily(
        "storefront_fixed", "ויטרינה קבועה", "shopfront", "fixed",
        leaves=(1, 2, 3, 4), sash_position="none",
        widths=(2000, 3000, 4000, 5000, 6000), heights=(2400, 2700, 3000),
        sill=0, note="זיגוג חנות ללא דלת",
        tags=("ויטרינה", "חנות", "קבוע", "מסחרי"),
        min_width=1000, max_width=12000, min_height=1500,
    ),
    OpeningFamily(
        "mamad", "חלון ממ״ד", "window", "casement",
        leaves=(1,), widths=(800, 1000, 1200), heights=(800, 1000, 1200),
        sill=1100,
        note="הזיגוג והפרזול לפי דרישת פיקוד העורף — לא נגזר כאן",
        tags=("ממד", "מיגון", "ביטחון", "הדף"),
        min_width=600, max_width=1600, max_height=1600,
    ),
    OpeningFamily(
        "louvre", "רפפה", "window", "fixed",
        leaves=(1, 2), sash_position="none",
        widths=(600, 800, 1000, 1200, 1600), heights=(600, 900, 1200),
        sill=900, note="אוורור קבוע — הלהבים אינם נגזרים כאן",
        tags=("רפפה", "louvre", "אוורור", "מיזוג"),
        max_width=3000,
    ),
    OpeningFamily(
        "skylight", "חלון גג", "window", "top_hung",
        leaves=(1,), widths=(800, 1000, 1200, 1500), heights=(800, 1000, 1200),
        sill=0, note="פתח בגג או ברביד — נדרשת אטימה נפרדת",
        tags=("גג", "רביד", "skylight", "אור"),
        min_width=500, max_width=3000,
    ),
)


@dataclass(frozen=True)
class LibraryOpening:
    """One buildable opening: a family, a leaf count, a size and a series."""

    preset_id: str
    hebrew: str
    kind: str
    width: float
    height: float
    columns: int = 1
    rows: int = 1
    sash_type: str = "fixed"
    sash_column: int = 0
    sash_row: int = 0
    sill: float = 900.0
    glass: str = "dgu-6-16-6"
    quantity: int = 1
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    family_id: str = ""
    #: The series this is to be made in, when one was asked for. ``generic``
    #: keeps the family's typical rules and says so.
    system_id: str = "generic"
    system_hebrew: str = ""

    def search_terms(self) -> tuple[str, ...]:
        return (self.preset_id, self.hebrew, self.kind, self.note,
                self.system_hebrew, *self.tags,
                f"{self.width:.0f}", f"{self.height:.0f}")

    def describe(self) -> str:
        return f"⁦{self.width:.0f} × {self.height:.0f}⁩ מ״מ"

    @property
    def title(self) -> str:
        """The full name: type, leaves and series, as it would be said."""
        parts = [self.hebrew]
        if self.system_hebrew:
            parts.append(self.system_hebrew)
        return " · ".join(parts)


def _make(
    family: OpeningFamily,
    leaves: int,
    width: float,
    height: float,
    *,
    system_id: str = "generic",
    system_hebrew: str = "",
) -> LibraryOpening:
    """One concrete opening from a family, a leaf count and a size."""
    leaves = max(1, min(leaves, 12))
    if family.sash_position == "none" or family.sash_type == "fixed":
        sash_type = "fixed"
        sash_column = 0
    else:
        sash_type = family.sash_type
        sash_column = (leaves // 2) if family.sash_position == "middle" else 0
        sash_column = min(sash_column, leaves - 1)

    leaf_word = family.leaf_word(leaves)
    hebrew = f"{family.hebrew} {leaf_word}".strip()
    return LibraryOpening(
        preset_id=f"{family.family_id}-{leaves}-{width:.0f}x{height:.0f}",
        hebrew=hebrew,
        kind=family.kind,
        width=float(width),
        height=float(height),
        columns=leaves,
        rows=family.rows,
        sash_type=sash_type,
        sash_column=sash_column,
        # An opening leaf in a multi-row type sits in the bottom row, where a
        # person can reach the handle.
        sash_row=0,
        sill=family.sill,
        glass=family.glass,
        note=family.note,
        tags=family.tags,
        family_id=family.family_id,
        system_id=system_id or "generic",
        system_hebrew=system_hebrew,
    )


def family(family_id: str) -> OpeningFamily | None:
    return next((f for f in FAMILIES if f.family_id == family_id), None)


def families() -> list[OpeningFamily]:
    return list(FAMILIES)


# --------------------------------------------------------------------------- #
# Reading what was typed
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Query:
    """What a typed line asked for. Every part is optional."""

    words: tuple[str, ...] = ()
    width: float | None = None
    height: float | None = None
    leaves: int | None = None
    system_id: str = ""
    system_hebrew: str = ""
    quantity: int | None = None

    @property
    def has_size(self) -> bool:
        return self.width is not None or self.height is not None


_PAIR = re.compile(r"(\d+(?:[.,]\d+)?)\s*[x×*/על]+\s*(\d+(?:[.,]\d+)?)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LEAF_WORDS = ("כנפיים", "כנף", "כנפים", "leaf", "leaves", "כנפי")
_METRE_WORDS = ("מטר", "מ'", "מ׳", "m")
_CM_WORDS = ("סמ", 'ס"מ', "cm")
_COUNT_WORDS = ("יח", "יחידות", "יחידה", "כמות", "pcs", "×", "x")


def to_millimetres(text: str, unit: str = "") -> float:
    """Read a dimension the way the trade says it.

    A decimal point means metres; two or three digits mean centimetres,
    because that is how a customer says a window over the phone; four digits
    are already millimetres. An explicit unit always wins.
    """
    value = float(text.replace(",", "."))
    if unit in _METRE_WORDS:
        return value * 1000.0
    if unit in _CM_WORDS:
        return value * 10.0
    if "." in text or "," in text:
        return value * 1000.0
    if value < 20:
        return value * 1000.0
    if value < 400:
        return value * 10.0
    return value


def _take_series(words: list[str]) -> tuple[list[str], str, str]:
    """Pull a series out of the typed words, before any number is read.

    This has to happen first, because series are named by number — קליל 9000,
    אקסטל E45 — and a search that reads 9000 as a width finds nothing.

    Only a series *designation* counts: the token has to be the series' own
    name. A family word will not do, however strongly it points at one series
    today, because "הזזה" means the kind of window, and the day somebody
    classifies one more series as sliding it would silently start meaning a
    manufacturer instead. Where two makers share a number, the maker's name
    beside it decides.
    """
    from .systems import DIRECTORY

    entries = list(DIRECTORY)
    for index, word in enumerate(words):
        matches = [
            entry for entry in entries
            if _folds(entry.series) == word
            or any(_folds(alias) == word for alias in entry.aliases)
        ]
        if not matches:
            continue
        if len(matches) > 1:
            neighbours = {words[i] for i in (index - 1, index + 1) if 0 <= i < len(words)}
            narrowed = [
                entry for entry in matches
                if any(
                    neighbour in _folds(entry.manufacturer)
                    or neighbour in _folds(entry.hebrew)
                    for neighbour in neighbours
                )
            ]
            if len(narrowed) != 1:
                continue
            matches = narrowed
        entry = matches[0]
        terms = {_folds(term) for term in entry.search_terms()}
        terms.add(_folds(entry.display))
        rest = [
            candidate for position, candidate in enumerate(words)
            if position != index
            and not any(candidate in term for term in terms if len(term) > 2)
        ]
        return rest, entry.id, entry.display
    return words, "", ""


def parse_query(text: str) -> Query:
    """Turn a typed line into a family filter, a size, a leaf count, a series."""
    lowered = _folds(text)
    width = height = None
    leaves = quantity = None

    # A pair — 6000x2200, 240/140, "3 על 2.2" — is a size, both halves.
    pair = _PAIR.search(lowered)
    if pair:
        width = to_millimetres(pair.group(1))
        height = to_millimetres(pair.group(2))
        lowered = lowered[: pair.start()] + " " + lowered[pair.end():]

    words = [word for word in lowered.split() if word]
    words, system_id, system_hebrew = _take_series(words)
    remaining: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        following = words[index + 1] if index + 1 < len(words) else ""
        number = _NUMBER.fullmatch(word)
        if not number:
            remaining.append(word)
            index += 1
            continue

        # "4 כנפיים" is a leaf count however large the number reads.
        if any(following.startswith(leaf) for leaf in _LEAF_WORDS):
            leaves = int(float(word.replace(",", ".")))
            index += 2
            continue
        plain = float(word.replace(",", "."))

        # "12 יחידות" is how many, never how big.
        if any(following.startswith(unit) for unit in _COUNT_WORDS):
            quantity = max(1, int(plain))
            index += 2
            continue

        if following in _METRE_WORDS or following in _CM_WORDS:
            size = to_millimetres(word, following)
            index += 2
        else:
            # A bare small whole number is a leaf count. Nobody orders a
            # three-millimetre window, and everybody orders a three-leaf one.
            if plain.is_integer() and 1 <= plain <= 6 and "." not in word:
                leaves = int(plain)
                index += 1
                continue
            size = to_millimetres(word)
            index += 1
        if width is None:
            width = size
        elif height is None:
            height = size
        else:
            # Both dimensions are already given, so a third number is a count
            # — read as it was typed, not as a dimension.
            quantity = max(1, int(plain))

    return Query(
        words=tuple(remaining),
        width=width,
        height=height,
        leaves=leaves,
        system_id=system_id,
        system_hebrew=system_hebrew,
        quantity=quantity,
    )


def _nearest(value: float, options: tuple[float, ...]) -> float:
    return min(options, key=lambda option: abs(option - value))


def _fits(fam: OpeningFamily, width: float | None, height: float | None) -> bool:
    """Whether this type is the right choice at this size, not merely possible."""
    if width is not None and not (fam.min_width <= width <= fam.max_width):
        return False
    if height is not None and not (fam.min_height <= height <= fam.max_height):
        return False
    return True


def search_openings(text: str, *, limit: int = 80) -> list[LibraryOpening]:
    """Everything that answers what was typed — including sizes nobody stored.

    With a size, the answer is every type that is made at that size, built at
    exactly it. Without one, it is the types themselves at the sizes they are
    ordinarily made in, so the list can be browsed by somebody who does not
    yet know what they want.
    """
    query = parse_query(text)
    per_family: list[list[LibraryOpening]] = []

    matching = [
        fam for fam in FAMILIES
        if _matches(" ".join(query.words), fam.search_terms())
        and (query.leaves is None or query.leaves in fam.leaves
             or (query.leaves == 1 and fam.sash_type == "fixed"))
    ]

    for fam in matching:
        leaf_options = (
            [query.leaves] if query.leaves is not None and query.leaves in fam.leaves
            else list(fam.leaves)
        )
        found: list[LibraryOpening] = []
        if query.has_size:
            width = query.width if query.width is not None else _nearest(
                fam.widths[len(fam.widths) // 2], fam.widths
            )
            height = query.height if query.height is not None else _nearest(
                width / 1.7, fam.heights
            )
            if not _fits(fam, width, height):
                continue
            for leaves in leaf_options:
                found.append(_make(
                    fam, leaves, width, height,
                    system_id=query.system_id, system_hebrew=query.system_hebrew,
                ))
        else:
            # Browsing: a few sizes per leaf count, so the list reads as a
            # catalogue rather than as a spreadsheet.
            for leaves in leaf_options:
                for width in fam.widths[:3]:
                    height = fam.heights[min(1, len(fam.heights) - 1)]
                    found.append(_make(
                        fam, leaves, width, height,
                        system_id=query.system_id,
                        system_hebrew=query.system_hebrew,
                    ))
        if found:
            per_family.append(found)

    # One from each type before a second from any of them. A list that opens
    # with eleven sliders looks like a library of sliders.
    results: list[LibraryOpening] = []
    depth = 0
    while len(results) < limit and any(len(f) > depth for f in per_family):
        for found in per_family:
            if depth < len(found):
                results.append(found[depth])
                if len(results) >= limit:
                    break
        depth += 1

    if query.quantity:
        results = [replace(item, quantity=query.quantity) for item in results]
    return results


def opening(preset_id: str) -> LibraryOpening | None:
    """Rebuild an opening from the identifier a search gave it."""
    parts = preset_id.rsplit("-", 2)
    if len(parts) != 3:
        return None
    family_id, leaves, size = parts
    fam = family(family_id)
    if fam is None or "x" not in size:
        return None
    try:
        width, height = (float(value) for value in size.split("x", 1))
        return _make(fam, int(leaves), width, height)
    except ValueError:
        return None


def opening_library() -> list[LibraryOpening]:
    """The catalogue as it reads with nothing typed."""
    return search_openings("")


def catalogue_size() -> int:
    """How many openings the library can make, counting sizes in 50mm steps.

    Said out loud rather than left implied, because the honest answer to "is
    that all the windows" is a number, and the number is not sixteen.
    """
    total = 0
    for fam in FAMILIES:
        widths = int((fam.max_width - fam.min_width) // 50) + 1
        heights = int((fam.max_height - fam.min_height) // 50) + 1
        total += len(fam.leaves) * widths * heights
    return total


__all__ = [
    "DRAWING_SUFFIXES",
    "FAMILIES",
    "LibraryOpening",
    "LibraryProfile",
    "OpeningFamily",
    "Query",
    "SAMPLE_PROFILES",
    "catalogue_size",
    "families",
    "family",
    "folder_profiles",
    "opening",
    "opening_library",
    "parse_query",
    "profile_library",
    "sample_profiles",
    "search_openings",
    "search_profiles",
    "to_millimetres",
]
