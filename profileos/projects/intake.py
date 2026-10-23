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
        if len(self.unread) == 1:
            text += " · שורה אחת לא נקלטה — היא לא תתווסף"
        elif self.unread:
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
MOST_ALIKE = 20
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
        if (any(ch.isdigit() for ch in word) or word in ("על", "x")
                or (word.isalpha() and (_folds(word) in known or len(word) <= 1))):
            kept.append(word)
    return " ".join(kept)


#: A unit said after a size: kept, so "420x250 ס"מ" is 4200 × 2500 mm.
_UNITS = {"ס\"מ", "ס״מ", "סמ", "סנטימטר", "cm", "מ\"מ", "מ״מ", "ממ", "mm", "מטר", "מטרים",
          "מ'", "מ׳", "m", "על", "x"}


_UNIT_FACTOR = {"ס\"מ": 10, "ס״מ": 10, "סמ": 10, "סנטימטר": 10, "cm": 10,
                "מ\"מ": 1, "מ״מ": 1, "ממ": 1, "mm": 1,
                "מטר": 1000, "מטרים": 1000, "מ'": 1000, "מ׳": 1000, "m": 1000}
_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:x|/|על)\s*(\d+(?:\.\d+)?)"
    r"(?:\s*(" + "|".join(re.escape(u) for u in sorted(_UNIT_FACTOR, key=len, reverse=True))
    + r"))?(?=\s|$)")


#: "120 140 ס״מ": two numbers and a unit, no sign between them.
_SPACED = re.compile(
    r"(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*("
    + "|".join(re.escape(u) for u in sorted(_UNIT_FACTOR, key=len, reverse=True))
    + r")(?=\s|$)")


def _sizes(text: str) -> str:
    """A width and height, with the unit said or not, as millimetres —
    the pair read as one: "450/230" is 4.5 m by 2.3 m, not 450 mm by 2.3 m."""
    def as_mm(found: re.Match) -> str:
        a, b = float(found.group(1)), float(found.group(2))
        unit = found.group(3)
        if unit:
            factor = _UNIT_FACTOR[unit]
        elif max(a, b) < 10:
            factor = 1000                      # 1.2 x 1.4: metres
        elif max(a, b) < 1000 and min(a, b) < 400:
            factor = 10                        # 120 x 140, 450 / 230: centimetres
        else:
            factor = 1                         # 1200 x 1400: millimetres
        # Under 400 the library would read a bare number as centimetres:
        # those are written in metres, which it reads as they are.
        def plain(mm: float) -> str:
            return f"{mm:.0f}" if mm >= 400 else f"{mm / 1000:.3f}"

        return f"{plain(a * factor)}x{plain(b * factor)}"
    text = _SPACED.sub(lambda found: f"{found.group(1)}x{found.group(2)} {found.group(3)}", text)
    return _SIZE.sub(as_mm, text)


def _numbers(text: str) -> str:
    """A decimal comma is a decimal point (1,2 m); a comma before three
    digits is a thousands mark (1,200 mm); any other comma separates."""
    if re.search(r",\s*[^\d\s]|[^\d\s,]\s*,", text):
        # "W3,120,140,חלון הזזה,2": a row of columns — every comma separates.
        return text.replace(",", " ")
    text = re.sub(r"(?<![\d.])(\d{1,2}),(?=\d{3}(?!\d))", r"\1", text)
    text = re.sub(r"(?<=\d),(?=\d{1,2}(?!\d))", ".", text)
    return text.replace(",", " ")


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
    rest = _numbers(rest.replace("\t", " "))
    rest = _TIMES.sub("x", rest)
    rest = _sizes(rest)
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
    if count is None:
        # "W3 120 140 חלון 2": the count column after the type.
        last = re.search(r"(?<=\s)(\d{1,2})\s*$", rest)
        before = rest[:last.start()] if last else ""
        if last and len(re.findall(r"\d+(?:\.\d+)?", before)) >= 2:
            number = int(last.group(1))
            if number > MOST_ALIKE:
                line.problem = (f"{_quoted(text)} — לא ברור מה המספר ⁦{number}⁩ "
                                "(כמות? גובה סף?) — כתבו ״יח׳״ אחרי כמות")
                return line
            count = number
            rest = before
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
    presets = search_openings(rest, limit=12)
    if not presets:
        # The words named no type the shop makes: a plain window at the size
        # is not assumed — the line is shown to the person instead.
        words_only = " ".join(w for w in rest.split()
                              if not any(ch.isdigit() for ch in w) and w not in _UNITS)
        if words_only and search_openings(words_only, limit=1):
            line.problem = (f"{_quoted(text)} — אין בספרייה סוג כזה במידה "
                            f"⁦{query.width:.0f}×{query.height:.0f}⁩ מ״מ — הוסיפו אותו בעמוד ״פתח״")
        else:
            line.problem = f"{_quoted(text)} — לא זוהה סוג הפתח"
        return line
    families = {preset.family_id for preset in presets}
    generic = {"חלון", "דלת", "פתח"}
    if len(families) > 1 and set(named) <= generic:
        # "חלון 120x140" fits a casement, a slider and a fixed light alike:
        # which one is the person's to say, not the first match's.
        kinds = []
        for preset in presets:
            label = preset.hebrew.split(" ⁦")[0]
            if label not in kinds:
                kinds.append(label)
        line.problem = (f"{_quoted(text)} — לא כתוב איזה סוג: "
                        + ", ".join(kinds[:4]) + (" ועוד" if len(kinds) > 4 else ""))
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
    if line.room:
        opening.metadata["room"] = line.room
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
