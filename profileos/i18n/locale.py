"""Languages, and the things that change with them besides the words.

A shop floor in Israel has Hebrew, Arabic and Russian speakers on it in the
same shift, and the systems come from Italian and Spanish houses. So the
software speaks six languages — and speaking a language is more than swapping
strings:

* **Direction.** Hebrew and Arabic run right to left, which changes the layout,
  not the vocabulary. Every surface that renders text asks the locale rather
  than assuming.
* **Digits.** Arabic is written right to left but its numbers are not, and
  Arabic-Indic digits are not used in an Israeli workshop; European digits are.
  Guessing wrong here puts a wrong dimension on a cutting list.
* **Decimal marks.** An Italian reads 1.234,5 and an Englishman 1,234.5. A
  quotation that shows the wrong one is a quotation with a thousandfold error
  in it, and nobody notices until it is accepted.

What is deliberately *not* localised: profile article numbers, system series
names, machine codes and file formats. Those are identifiers, and translating
an identifier makes it a different identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Language(StrEnum):
    """The languages the software speaks."""

    HEBREW = "he"
    ENGLISH = "en"
    ARABIC = "ar"
    RUSSIAN = "ru"
    ITALIAN = "it"
    SPANISH = "es"


@dataclass(frozen=True)
class Locale:
    """One language, with the formatting conventions that come with it."""

    language: Language
    #: What the language calls itself. Shown in the picker, because somebody
    #: looking for their own language is looking for its own name.
    native: str
    english: str
    rtl: bool = False
    decimal: str = "."
    thousands: str = ","
    #: strftime pattern for a date on a document.
    date_format: str = "%d/%m/%Y"
    #: Currency symbol position: True when it precedes the amount.
    currency_first: bool = False

    @property
    def code(self) -> str:
        return self.language.value

    def format_number(self, value: float, decimals: int = 0) -> str:
        """A number written the way this language writes it."""
        text = f"{value:,.{decimals}f}"
        # Swap through a placeholder so the two separators cannot collide when
        # one language's thousands mark is another's decimal mark.
        return (
            text.replace(",", "\x00").replace(".", self.decimal).replace("\x00", self.thousands)
        )

    def format_money(self, value: float, symbol: str = "₪", decimals: int = 2) -> str:
        amount = self.format_number(value, decimals)
        return f"{symbol}{amount}" if self.currency_first else f"{amount} {symbol}"

    def format_date(self, value) -> str:
        return value.strftime(self.date_format)


LOCALES: dict[Language, Locale] = {
    Language.HEBREW: Locale(
        language=Language.HEBREW,
        native="עברית",
        english="Hebrew",
        rtl=True,
        date_format="%d/%m/%Y",
    ),
    Language.ENGLISH: Locale(
        language=Language.ENGLISH,
        native="English",
        english="English",
        date_format="%d/%m/%Y",
        currency_first=True,
    ),
    Language.ARABIC: Locale(
        language=Language.ARABIC,
        native="العربية",
        english="Arabic",
        rtl=True,
        date_format="%d/%m/%Y",
    ),
    Language.RUSSIAN: Locale(
        language=Language.RUSSIAN,
        native="Русский",
        english="Russian",
        decimal=",",
        thousands=" ",
        date_format="%d.%m.%Y",
    ),
    Language.ITALIAN: Locale(
        language=Language.ITALIAN,
        native="Italiano",
        english="Italian",
        decimal=",",
        thousands=".",
        date_format="%d/%m/%Y",
    ),
    Language.SPANISH: Locale(
        language=Language.SPANISH,
        native="Español",
        english="Spanish",
        decimal=",",
        thousands=".",
        date_format="%d/%m/%Y",
    ),
}

#: Hebrew, because that is the language of the shop this was built for.
DEFAULT_LANGUAGE = Language.HEBREW


def get_locale(language: Language | str | None = None) -> Locale:
    """Resolve a language to a locale, falling back to the default."""
    if language is None:
        return LOCALES[DEFAULT_LANGUAGE]
    if isinstance(language, Locale):  # pragma: no cover - convenience
        return language
    try:
        return LOCALES[Language(str(language).split("-")[0].lower())]
    except (ValueError, KeyError):
        return LOCALES[DEFAULT_LANGUAGE]


def negotiate(header: str | None) -> Locale:
    """Pick a locale from an HTTP ``Accept-Language`` header.

    Quality values are honoured, so a phone set to Russian with Hebrew second
    gets Russian, and one set to a language this software does not speak falls
    through to the next one it asked for rather than straight to the default.
    """
    if not header:
        return LOCALES[DEFAULT_LANGUAGE]
    candidates: list[tuple[float, str]] = []
    for part in header.split(","):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        candidates.append((quality, tag.strip().lower()))
    for _, tag in sorted(candidates, key=lambda pair: -pair[0]):
        base = tag.split("-")[0]
        try:
            return LOCALES[Language(base)]
        except ValueError:
            continue
    return LOCALES[DEFAULT_LANGUAGE]


__all__ = [
    "DEFAULT_LANGUAGE",
    "LOCALES",
    "Language",
    "Locale",
    "get_locale",
    "negotiate",
]
