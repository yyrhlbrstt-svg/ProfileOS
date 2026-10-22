"""A list of openings, pasted, becomes the job's openings.

The architect sends a door-and-window schedule as a table; the contractor
sends a WhatsApp with "סלון הזזה 240/210, 3 חלונות ציר 120×140 לחדרים";
the customer reads sizes off a tape over the phone. Typing each into the form
is twenty minutes and twenty chances for a transposed digit.

Each line is read the way the opening search already reads a typed line —
type, leaves, size in mm, cm or metres, count, series — with what a schedule
adds in front: the opening's mark ("W1", "ח-3", "D02") and the room. A line
that cannot be read is shown as such and left out. Nothing is guessed: no
size means no opening, and the preview says so before anything is added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: A mark at the start of a line: W1, W-01, D02, ח1, ח-3, CW-1 — or a
#: numbered list ("1.", "2)") which is not a mark at all.
_MARK = re.compile(r"^\s*([A-Za-zא-ת]{1,3}-?\d{1,3}[A-Za-z]?)(?=[\s\t,:;.)]|$)[\s,:;.)]*")
_NUMBERED = re.compile(r"^\s*\d{1,3}\s*[.)]\s+")
_TIMES = re.compile(r"(?<=\d)\s*[x×X*]\s*(?=\d)")

#: Rooms as people write them, so the room is kept on the opening and is
#: not read as a word that has to match an opening type.
ROOMS = (
    "חדר שינה", "חדר הורים", "חדר ילדים", "חדר רחצה", "חדר אמבטיה", "חדר כביסה",
    "חדר עבודה", "סלון", "מטבח", "פינת אוכל", "מקלחת", "שירותים", "אמבטיה",
    "מרפסת", "ממ״ד", "ממ\"ד", "ממד", "מבואה", "מסדרון", "מחסן",
    "חדר", "לובי", "משרד", "גג",
)


@dataclass
class IntakeLine:
    """One line of what was pasted, and what it was read as."""

    text: str
    mark: str = ""
    room: str = ""
    opening: Any = None
    problem: str = ""
    title: str = ""

    @property
    def ok(self) -> bool:
        return self.opening is not None and not self.problem

    def describe(self) -> str:
        if not self.ok:
            return f"לא נקלט: {self.problem}"
        opening = self.opening
        parts = [f"⁦{self.mark}⁩" if self.mark else "", self.title,
                 f"⁦{opening.width:.0f}×{opening.height:.0f}⁩ מ״מ"]
        if opening.quantity > 1:
            parts.append(f"⁦{opening.quantity}⁩ יח׳")
        if self.room:
            parts.append(self.room)
        return " · ".join(part for part in parts if part)


@dataclass
class Intake:
    lines: list[IntakeLine] = field(default_factory=list)

    @property
    def read(self) -> list[IntakeLine]:
        return [line for line in self.lines if line.ok]

    @property
    def unread(self) -> list[IntakeLine]:
        return [line for line in self.lines if not line.ok]

    def openings(self) -> list[Any]:
        return [line.opening for line in self.read]

    def describe(self) -> str:
        units = sum(line.opening.quantity for line in self.read)
        text = f"נקלטו ⁦{len(self.read)}⁩ פתחים (⁦{units}⁩ יחידות)"
        if self.unread:
            text += f" · ⁦{len(self.unread)}⁩ שורות לא נקלטו — הן לא יתווספו"
        return text


def _split(text: str) -> list[str]:
    """Lines of a table, a list or a message — one opening each."""
    parts: list[str] = []
    for raw in text.replace("\r", "\n").split("\n"):
        for piece in raw.split(";"):
            if piece.strip():
                parts.append(piece.strip())
    return parts


#: Plurals people write in a list; the opening types are named in the singular.
_PLURALS = {"חלונות": "חלון", "דלתות": "דלת", "ויטרינות": "ויטרינה", "תריסים": "תריס"}
#: "4 חלונות ..." — a count in front of the type.
_LEADING_COUNT = re.compile(r"^\s*(\d{1,3})\s+(?=[א-ת])")
#: "120 140 2", "120x140 2", "240/210 2" — after a width and a height, a
#: table's next column is a count (unless it says leaves).
_TRAILING_COUNT = re.compile(
    r"(\d+(?:\.\d+)?)(\s+|x|/)(\d+(?:\.\d+)?)\s+(\d{1,4})(?!\s*כנ)(?=\s|$)")
#: "2 יח׳", "3 יחידות", "כמות 2" — a count said in words.
_SAID_COUNT = re.compile(
    r"(?:(\d{1,3})\s*יח(?:ידות|ידה)?[׳'\"]?|כמות\s*:?\s*(\d{1,3}))(?=\s|$)")
#: More than this "of the same" in one schedule line is a sill height or a
#: code in the next column, not a count — the line is shown, not guessed.
MOST_ALIKE = 50
#: What is fitted to an opening, never an opening by itself: "תריס 120x140"
#: does not say whether the window under it slides or turns.
_FITTED = {"תריס", "רשת", "סורג", "אדן", "ארגז"}
#: Words that are about size or count, not about what kind of opening it is.
_NOT_A_TYPE = {"מטר", "מטרים", "מ", "ס\"מ", "סמ", "ס״מ", "מ\"מ", "ממ", "מ״מ", "על", "x",
               "כנפיים", "כנף", "כנפות", "cm", "mm", "m"}


def _vocabulary() -> str:
    from ..library import FAMILIES, _folds

    return " ".join(_folds(term) for family in FAMILIES for term in family.search_terms())


def _known_words(text: str) -> str:
    """Words that name nothing the shop makes ("לחדרים", "בערך") are dropped,
    so they do not stop the type being found. Numbers are always kept."""
    from ..library import _folds

    known = _vocabulary()
    kept = []
    for word in text.split():
        word = _PLURALS.get(word, word)
        if any(ch.isdigit() for ch in word) or _folds(word) in known or len(word) <= 1:
            kept.append(word)
    return " ".join(kept)


def _room(text: str) -> tuple[str, str]:
    """The room, as a whole word ("גגון" is a canopy, not the roof)."""
    for room in sorted(ROOMS, key=len, reverse=True):
        found = re.search(rf"(?<![א-ת]){re.escape(room)}(?![א-ת])", text)
        if found:
            return room, (text[:found.start()] + " " + text[found.end():]).strip()
    return "", text


def _quoted(text: str) -> str:
    """The pasted line inside a Hebrew message, kept in its own direction."""
    return f"״\u2068{text}\u2069״"


def read_line(text: str, *, used: set[str] | None = None) -> IntakeLine:
    """One opening from one line, or the reason it could not be read."""
    from ..library import parse_query, search_openings
    from ..quoting.estimate import opening_from_preset

    line = IntakeLine(text=text)
    rest = _NUMBERED.sub("", text, count=1)
    found = _MARK.match(rest)
    if found and any(ch.isdigit() for ch in found.group(1)):
        line.mark = found.group(1).upper()
        rest = rest[found.end():]
    # Table columns arrive tab- or comma-separated; "120x140" is one size.
    rest = rest.replace("\t", " ").replace(",", " ")
    rest = _TIMES.sub("x", rest)
    line.room, rest = _room(rest)
    count = None
    said = _SAID_COUNT.search(rest)
    if said:
        count = int(said.group(1) or said.group(2))
        rest = rest[:said.start()] + " " + rest[said.end():]
    leading = _LEADING_COUNT.match(rest)
    if leading and count is None:
        count = int(leading.group(1))
        rest = rest[leading.end():]
    trailing = _TRAILING_COUNT.search(rest)
    if trailing and count is None:
        number = int(trailing.group(4))
        if number > MOST_ALIKE:
            line.problem = (f"{_quoted(text)} — לא ברור מה המספר ⁦{number}⁩ "
                            "אחרי המידות (כמות? גובה סף?) — כתבו ״יח׳״ אחרי כמות")
            return line
        count = number
        rest = (rest[:trailing.start()]
                + f"{trailing.group(1)}{trailing.group(2)}{trailing.group(3)}"
                + rest[trailing.end():])
    rest = _known_words(rest)

    query = parse_query(rest)
    if query.width is None or query.height is None:
        line.problem = f"{_quoted(text)} — חסרים רוחב וגובה"
        return line
    named = [word for word in rest.split()
             if not any(ch.isdigit() for ch in word) and word not in _NOT_A_TYPE
             and word not in _FITTED]
    fitted = [word for word in rest.split() if word in _FITTED]
    rest = " ".join(word for word in rest.split() if word not in _FITTED and word != "עם")
    if not named:
        # A size alone is not an opening: which kind is for the person to say.
        line.problem = f"{_quoted(text)} — לא כתוב איזה פתח (חלון, דלת, הזזה…)"
        return line
    presets = search_openings(rest, limit=1)
    if not presets:
        # The words named no type the shop makes: a plain window at the size
        # is not assumed — the line is shown to the person instead.
        line.problem = f"{_quoted(text)} — לא זוהה סוג הפתח"
        return line
    preset = presets[0]
    try:
        opening = opening_from_preset(preset, name=line.mark or preset.hebrew)
    except Exception as exc:  # noqa: BLE001 - shown on the line, not raised
        line.problem = f"{_quoted(text)} — {exc}"
        return line
    opening.quantity = max(1, int(count or query.quantity or 1))
    if line.mark:
        if used is not None and line.mark in used:
            line.problem = f"{_quoted(text)} — הסימון ⁦{line.mark}⁩ כבר קיים בפרויקט"
            return line
        opening.reference = line.mark
        if used is not None:
            used.add(line.mark)
    notes = [line.room] if line.room else []
    if fitted:
        notes.append("עם " + ", ".join(fitted))
    if notes:
        opening.notes = " · ".join(notes)
    line.opening = opening
    line.title = preset.title
    return line


def read_list(text: str, *, existing: list[Any] | None = None) -> Intake:
    """Every line pasted, read; marks already in the job are not doubled."""
    used = {str(opening.reference or opening.name) for opening in existing or []}
    return Intake(lines=[read_line(part, used=used) for part in _split(text)])


__all__ = ["Intake", "IntakeLine", "ROOMS", "read_line", "read_list"]
