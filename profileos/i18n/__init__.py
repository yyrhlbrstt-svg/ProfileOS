"""Six languages, because a shop floor has more than one on it in a shift.

``translate`` is the whole interface. It takes a dotted key and a language and
gives back the words; a key with no entry for that language falls back to
English rather than to the key itself, because an operator who sees
``stage.machined`` learns nothing while one who sees "machined" at least knows
what happened to the part.

The active language is per-call, not global. A quotation is printed in the
client's language while the cutting list beside it prints in the shop's, and a
global setting makes that awkward in exactly the way that leads to somebody
issuing the wrong one.
"""

from __future__ import annotations

from typing import Any

from .locale import (
    DEFAULT_LANGUAGE,
    LOCALES,
    Language,
    Locale,
    get_locale,
    negotiate,
)
from .messages import MESSAGES, language_codes, missing


class MissingMessage(KeyError):
    """No such key. Raised only by :func:`require`, never by :func:`translate`."""


def translate(key: str, language: Language | str | None = None, /, **fields: Any) -> str:
    """The words for ``key``, in ``language``.

    ``fields`` are substituted into the result, so a message can carry a
    measurement without the number being part of the translation:

        translate("mobile.width", "he") -> "רוחב"

    An unknown key returns its own last segment rather than raising. A missing
    label on a screen is a blemish; an exception in the middle of rendering a
    job card stops the floor.
    """
    entries = MESSAGES.get(key)
    if entries is None:
        return key.rsplit(".", 1)[-1].replace("_", " ")
    locale = get_locale(language)
    text = entries.get(locale.code) or entries.get("en") or key
    if fields:
        try:
            return text.format(**fields)
        except (KeyError, IndexError):  # pragma: no cover - a malformed catalogue entry
            return text
    return text


def require(key: str, language: Language | str | None = None) -> str:
    """Like :func:`translate`, but raises on an unknown key.

    For use in tests and at start-up, where a missing key should be found by
    the people writing the software rather than by the people using it.
    """
    if key not in MESSAGES:
        raise MissingMessage(key)
    return translate(key, language)


def has(key: str) -> bool:
    return key in MESSAGES


def catalogue(language: Language | str | None = None) -> dict[str, str]:
    """Every key in one language, for handing to a browser in one go."""
    locale = get_locale(language)
    return {
        key: entries.get(locale.code) or entries.get("en", key)
        for key, entries in MESSAGES.items()
    }


def available() -> list[Locale]:
    """The locales on offer, for a picker."""
    return list(LOCALES.values())


__all__ = [
    "DEFAULT_LANGUAGE",
    "LOCALES",
    "Language",
    "Locale",
    "MESSAGES",
    "MissingMessage",
    "available",
    "catalogue",
    "get_locale",
    "has",
    "language_codes",
    "missing",
    "negotiate",
    "require",
    "translate",
]
