"""The library the shop searches: profiles to open, and openings to build.

Two screens in this software start empty on a fresh machine — the profile
screen, which waits for a drawing, and the element screen, which waits for
somebody to type twelve numbers. Both are correct and both are useless at
eight in the morning, because the fabricator does not have a DXF to hand and
does not want to type a window they have made four hundred times.

This module is the answer to that: a searchable list of the profiles this
installation can actually open right now, and a list of the openings an
Israeli aluminium shop actually builds, each one ready to be made with a
single click. It is engine code rather than screen code on purpose — the
command palette, the desktop screens and the command line all search the same
list, and a list that only the screen knows about is a list nobody can test.

Nothing here is a claim about a manufacturer's figures. A preset is a shape
and a size, which is exactly the part a shop is happy to have guessed for
them; the cut deductions still come from the system rules, with their own
provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core.config import get_settings, samples_dir
from .core.logging_setup import get_logger

_log = get_logger("library")


def _folds(text: str) -> str:
    return text.casefold().replace("־", "-").replace("״", '"').strip()


def _matches(needle: str, terms: tuple[str, ...]) -> bool:
    """A search is a match when every word typed appears somewhere.

    Words rather than the whole string, because people search the way they
    speak: "הזזה 3" should find a three-leaf slider whichever order the two
    parts were typed in.
    """
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
#: they are rather than by file name. They are drawings, not catalogue data:
#: they measure correctly and they are honest about being examples.
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
    into one folder and every one of them is findable from then on, without
    an import step and without this software copying anybody's drawing
    anywhere.
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
# Openings
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LibraryOpening:
    """A ready-made opening: pick it, and it is built.

    The numbers are the sizes these units are ordinarily made at in Israel —
    a starting point that is right often enough to save the typing and easy
    enough to change when it is not. ``sash`` names which cell opens, so a
    two-leaf slider arrives with the correct leaf moving.
    """

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

    def search_terms(self) -> tuple[str, ...]:
        return (self.preset_id, self.hebrew, self.kind, self.note, *self.tags,
                f"{self.width:.0f}", f"{self.height:.0f}")

    def describe(self) -> str:
        return f"⁦{self.width:.0f} × {self.height:.0f}⁩ מ״מ"


#: The openings an Israeli aluminium shop builds week in, week out. Sizes are
#: the common ones; the opening type and the divisions are what make each one
#: the thing it is called.
OPENINGS: tuple[LibraryOpening, ...] = (
    LibraryOpening(
        "sliding_2", "חלון הזזה ⁦2⁩ כנפיים", "sliding_unit", 1800, 1400,
        columns=2, sash_type="sliding", sash_column=0, sill=900,
        note="הזזה רגילה לחדר מגורים", tags=("הזזה", "בלגי", "sliding"),
    ),
    LibraryOpening(
        "sliding_3", "חלון הזזה ⁦3⁩ כנפיים", "sliding_unit", 2700, 1400,
        columns=3, sash_type="sliding", sash_column=1, sill=900,
        note="הזזה רחבה, כנף אמצעית נעה", tags=("הזזה", "sliding"),
    ),
    LibraryOpening(
        "sliding_4", "חלון הזזה ⁦4⁩ כנפיים", "sliding_unit", 3600, 1400,
        columns=4, sash_type="sliding", sash_column=1, sill=900,
        note="הזזה על שתי מסילות", tags=("הזזה", "sliding"),
    ),
    LibraryOpening(
        "sliding_door", "דלת הזזה למרפסת", "sliding_unit", 3000, 2200,
        columns=3, sash_type="sliding", sash_column=1, sill=0,
        note="יציאה למרפסת, סף נמוך", tags=("מרפסת", "דלת", "הזזה"),
    ),
    LibraryOpening(
        "tilt_turn", "חלון נטוי-נפתח", "window", 1000, 1400,
        sash_type="tilt_turn", sill=900,
        note="כנף אחת, פתיחה כפולה", tags=("קיפ", "נטוי", "tilt"),
    ),
    LibraryOpening(
        "casement_2", "חלון ציר ⁦2⁩ כנפיים", "window", 1400, 1400,
        columns=2, sash_type="casement", sash_column=0, sill=900,
        note="בלגי, שתי כנפיים על ציר", tags=("בלגי", "ציר", "casement"),
    ),
    LibraryOpening(
        "top_hung", "חלון מתהפך עליון", "window", 900, 600,
        sash_type="top_hung", sill=1700,
        note="חלון שירותים או ממ״ד", tags=("אמבטיה", "שירותים", "מתהפך"),
    ),
    LibraryOpening(
        "fixed_light", "חלון קבוע", "window", 1200, 1400,
        sash_type="fixed", sill=900,
        note="זיגוג קבוע ללא כנף", tags=("קבוע", "fixed"),
    ),
    LibraryOpening(
        "kitchen_window", "חלון מטבח", "window", 1600, 1000,
        columns=2, sash_type="sliding", sash_column=0, sill=1400,
        note="מעל שיש המטבח", tags=("מטבח",),
    ),
    LibraryOpening(
        "entry_door", "דלת כניסה", "door", 1000, 2100,
        sash_type="door", sill=0,
        note="כנף אחת, פתיחה החוצה", tags=("כניסה", "דלת", "door"),
    ),
    LibraryOpening(
        "double_door", "דלת דו-כנפית", "door", 1800, 2100,
        columns=2, sash_type="door", sash_column=0, sill=0,
        note="כניסה ראשית רחבה", tags=("דלת", "כניסה"),
    ),
    LibraryOpening(
        "shopfront", "חזית מסחרית", "shopfront", 4000, 2700,
        columns=4, sash_type="door", sash_column=1, sill=0,
        note="ויטרינה עם דלת משולבת", tags=("חנות", "ויטרינה", "shopfront"),
    ),
    LibraryOpening(
        "curtain_wall", "קיר מסך", "curtain_wall", 6000, 3600,
        columns=4, rows=3, sash_type="fixed", sill=0,
        note="רשת זקפים וקורות", tags=("מסך", "curtain", "פסאדה"),
    ),
    LibraryOpening(
        "office_partition", "מחיצת משרד", "curtain_wall", 3000, 2700,
        columns=3, rows=1, sash_type="door", sash_column=1, sill=0,
        note="מחיצה פנימית עם דלת", tags=("משרד", "מחיצה", "אופיס"),
    ),
    LibraryOpening(
        "mamad_window", "חלון ממ״ד", "window", 1000, 1000,
        sash_type="casement", sill=1100,
        note="פתח ממ״ד — הזיגוג והפרזול לפי דרישת פיקוד העורף",
        tags=("ממד", "ביטחון", "מיגון"),
    ),
    LibraryOpening(
        "bathroom_window", "חלון אמבטיה", "window", 700, 600,
        sash_type="top_hung", sill=1800,
        note="פתח קטן גבוה", tags=("אמבטיה", "שירותים"),
    ),
)


def opening_library() -> list[LibraryOpening]:
    return list(OPENINGS)


def search_openings(text: str) -> list[LibraryOpening]:
    return [o for o in OPENINGS if _matches(text, o.search_terms())]


def opening(preset_id: str) -> LibraryOpening | None:
    return next((o for o in OPENINGS if o.preset_id == preset_id), None)


__all__ = [
    "DRAWING_SUFFIXES",
    "LibraryOpening",
    "LibraryProfile",
    "OPENINGS",
    "SAMPLE_PROFILES",
    "folder_profiles",
    "opening",
    "opening_library",
    "profile_library",
    "sample_profiles",
    "search_openings",
    "search_profiles",
]
