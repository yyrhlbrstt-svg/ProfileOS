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
#: "120 140 2" — after a width and a height, a table's next column is a count.
_TRAILING_COUNT = re.compile(
    r"(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d{1,3})\s*$")


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
    for room in sorted(ROOMS, key=len, reverse=True):
        index = text.find(room)
        if index >= 0:
            return room, (text[:index] + " " + text[index + len(room):]).strip()
    return "", text


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
    leading = _LEADING_COUNT.match(rest)
    if leading:
        count = int(leading.group(1))
        rest = rest[leading.end():]
    trailing = _TRAILING_COUNT.search(rest)
    if trailing:
        count = int(trailing.group(3))
        rest = rest[:trailing.start()] + f"{trailing.group(1)} {trailing.group(2)}"
    rest = _known_words(rest)

    query = parse_query(rest)
    if query.width is None or query.height is None:
        line.problem = f"״{text}״ — חסרים רוחב וגובה"
        return line
    presets = search_openings(rest, limit=1)
    if not presets:
        # The words named no type the shop makes: a plain window at the size
        # is not assumed — the line is shown to the person instead.
        line.problem = f"״{text}״ — לא זוהה סוג הפתח"
        return line
    preset = presets[0]
    try:
        opening = opening_from_preset(preset, name=line.mark or preset.hebrew)
    except Exception as exc:  # noqa: BLE001 - shown on the line, not raised
        line.problem = f"״{text}״ — {exc}"
        return line
    opening.quantity = max(1, int(count or query.quantity or 1))
    if line.mark:
        if used is not None and line.mark in used:
            line.problem = f"״{text}״ — הסימון ⁦{line.mark}⁩ כבר קיים בפרויקט"
            return line
        opening.reference = line.mark
        if used is not None:
            used.add(line.mark)
    if line.room:
        opening.notes = line.room
    line.opening = opening
    line.title = preset.title
    return line


def read_list(text: str, *, existing: list[Any] | None = None) -> Intake:
    """Every line pasted, read; marks already in the job are not doubled."""
    used = {str(opening.reference or opening.name) for opening in existing or []}
    return Intake(lines=[read_line(part, used=used) for part in _split(text)])


__all__ = ["Intake", "IntakeLine", "ROOMS", "read_line", "read_list"]
