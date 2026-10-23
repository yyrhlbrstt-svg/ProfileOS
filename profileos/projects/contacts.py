"""Customer details as people actually hand them over.

A customer's details arrive as a forwarded WhatsApp contact, an email
signature, or a line read out over the phone — not as a form. This reads them
the way they come: one pasted block is split into name, phone, email and
place; a phone number is written back in the one way the shop writes numbers;
and a customer who is already in the book is pointed out before a second copy
of them is made.

Nothing here guesses. A number that is not an Israeli number is left as it
was typed and reported; a block it cannot read leaves the fields empty for
somebody to fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..sharing import whatsapp_number
from .model import Customer

#: Places a small Binyamin-area shop is most often given as "where".
#: Only used to recognise a city in a pasted block, never to fill one in.
KNOWN_CITIES = (
    "בית אל", "ירושלים", "עפרה", "פסגות", "כוכב יעקב", "תל ציון", "שילה",
    "עלי", "אריאל", "מעלה אדומים", "גבעת זאב", "מודיעין", "מודיעין עילית",
    "ביתר עילית", "אלעד", "בני ברק", "פתח תקווה", "תל אביב", "חיפה",
    "באר שבע", "אשדוד", "נתניה", "ראשון לציון", "רחובות", "חשמונאים",
    "נווה צוף", "חלמיש", "טלמון", "דולב", "כוכב השחר", "רימונים", "מעלה מכמש",
    "אדם", "גבע בנימין", "ענתות", "קדומים", "קרני שומרון", "אלקנה", "אפרת",
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?972[\s-]?|0)(?:[\s-]?\d){8,9}")
_TAX_ID = re.compile(r"(?:ח\.?פ\.?|ע\.?מ\.?|עוסק(?:\s+מורשה)?)\s*[:\-]?\s*(\d{8,9})")
_LABELS = re.compile(
    r"^(?:שם|טלפון|טל|נייד|פלאפון|נייד|מייל|דוא\"?ל|אימייל|כתובת|עיר|"
    r"name|phone|tel|mobile|email|address|city)\s*[:.\-]\s*",
    re.IGNORECASE,
)


#: Lines a signature opens with that are not anybody's name.
_GREETINGS = {
    "בברכה", "בברכה רבה", "תודה", "תודה רבה", "שלום", "היי", "בהצלחה",
    "regards", "best regards", "thanks", "thank you", "best",
}


def format_phone(text: str) -> str:
    """An Israeli number written the shop's way; anything else as typed.

    >>> format_phone("0541234567")
    '054-123-4567'
    >>> format_phone("+972 2 997 3510")
    '02-997-3510'
    >>> format_phone("abc")
    'abc'
    """
    international = whatsapp_number(text)
    if international is None:
        return text.strip()
    local = "0" + international[3:]
    if local.startswith("05") or local.startswith("07"):
        return f"{local[:3]}-{local[3:6]}-{local[6:]}"
    return f"{local[:2]}-{local[2:5]}-{local[5:]}"


def phone_problem(text: str) -> str:
    """What is wrong with a phone number, or an empty string if nothing is."""
    text = text.strip()
    if not text:
        return ""
    if whatsapp_number(text) is None:
        digits = re.sub(r"\D", "", text)
        if len(digits) < 9:
            return "חסרות ספרות במספר הטלפון"
        if len(digits) > 12:
            return "יותר מדי ספרות במספר הטלפון"
        return "זה לא נראה כמו מספר ישראלי — בדקו שוב"
    return ""


def email_problem(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if not _EMAIL.fullmatch(text):
        return "כתובת הדוא״ל לא תקינה"
    return ""


@dataclass
class ParsedContact:
    """What could be read out of one pasted block."""

    name: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    city: str = ""
    tax_id: str = ""
    #: Further numbers, kept so a second phone is not silently lost.
    extra_phones: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        values = {
            "name": self.name, "phone": self.phone, "email": self.email,
            "address": self.address, "city": self.city, "tax_id": self.tax_id,
        }
        return {key: value for key, value in values.items() if value}


def parse_contact(text: str) -> ParsedContact:
    """Split a pasted block of details into the fields of a customer.

    The name is the first line that is not a number, an email or a place —
    which is how every signature and every shared contact starts.
    """
    result = ParsedContact()
    if not text or not text.strip():
        return result
    # Invisible direction marks travel with numbers copied out of WhatsApp
    # and split them in two; a shared contact may arrive as a vCard.
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)
    text = _from_vcard(text)

    tax = _TAX_ID.search(text)
    if tax:
        result.tax_id = tax.group(1)
        text = text.replace(tax.group(0), " ")

    emails = _EMAIL.findall(text)
    if emails:
        result.email = emails[0]
    for email in emails:
        text = text.replace(email, " ")

    phones, text = _take_phones(text)
    # Mobiles first: the number WhatsApp and the installer will both use.
    phones.sort(key=lambda number: not number.startswith("05"))
    if phones:
        result.phone = phones[0]
        result.extra_phones = phones[1:]

    lines: list[str] = []
    for raw in re.split(r"[\n\r|;,]+", text):
        line = _LABELS.sub("", raw.strip()).strip(" ,.-–:")
        if line:
            lines.append(line)

    for line in list(lines):
        # A place only as whole words, longest name first ("מודיעין עילית"
        # before "מודיעין"), and only when the line is that place or an
        # address in it — "עפרה לוי" is a person, not a town.
        city = next(
            (c for c in _CITIES_LONGEST_FIRST
             if re.search(rf"(?<![\w\u0590-\u05FF]){re.escape(c)}(?![\w\u0590-\u05FF])", line)),
            "",
        )
        rest = line.replace(city, "", 1).strip(" ,.-–") if city else line
        if city and not result.city and (not rest or re.search(r"\d", rest)):
            result.city = city
            if rest:
                result.address = rest
            lines.remove(line)
        elif re.search(r"(רח(?:וב|׳|')?\s|שד(?:רות|׳)?\s|\d+\s*/\s*\d+|ת\.?ד\.?)", line):
            if not result.address:
                result.address = line
                lines.remove(line)

    for line in lines:
        if line.casefold() in _GREETINGS:
            continue
        if re.search(r"[A-Za-z֐-׿]", line) and not re.fullmatch(r"[\d\s/,-]+", line):
            result.name = re.sub(r"\s+", " ", line)
            break
    return result


_CITIES_LONGEST_FIRST = sorted(KNOWN_CITIES, key=len, reverse=True)

#: Digits with the separators people put between them.
_NUMBERISH = re.compile(r"\+?[\d(][\d\s\-().]{6,}\d")


def _take_phones(text: str) -> tuple[list[str], str]:
    """Every Israeli number in ``text``, and the text without them.

    A run of digits may hold two numbers side by side ("02-9973510
    054-1234567"), so each run is split at its spaces until every piece is
    a number or cannot be one.
    """
    found: list[str] = []

    def pieces(run: str) -> list[str]:
        if whatsapp_number(run):
            return [run]
        parts = run.split()
        for cut in range(1, len(parts)):
            left, right = " ".join(parts[:cut]), " ".join(parts[cut:])
            if whatsapp_number(left):
                return [left] + pieces(right)
        return []

    def replace(match: re.Match[str]) -> str:
        run = re.sub(r"[()]", " ", match.group(0)).strip()
        numbers = pieces(re.sub(r"\s+", " ", run))
        if not numbers:
            return match.group(0)
        for number in numbers:
            tidy = format_phone(number)
            if tidy not in found:
                found.append(tidy)
        return " "

    return found, _NUMBERISH.sub(replace, text)


def _from_vcard(text: str) -> str:
    """A vCard as the plain lines the rest of the reader understands."""
    if "BEGIN:VCARD" not in text.upper():
        return text
    lines: list[str] = []
    for raw in text.splitlines():
        key, _, value = raw.partition(":")
        key = key.split(";")[0].upper()
        if key == "FN":
            lines.insert(0, value)
        elif key in ("TEL", "EMAIL", "ORG"):
            lines.append(value)
        elif key == "ADR":
            lines.append(" ".join(part for part in value.split(";") if part))
    return "\n".join(lines)


def _name_key(name: str) -> str:
    # Quotes go first, joined rather than split: בע"מ and בעמ are one word.
    cleaned = re.sub(r"[\"'׳״`]", "", name)
    cleaned = re.sub(r"[.,\-–]", " ", cleaned).casefold()
    words = [w for w in cleaned.split() if w not in ("בעמ", "ltd", "משפחת")]
    return " ".join(sorted(words))


def find_duplicates(
    customers: Iterable[Customer], *, name: str = "", phone: str = "",
    email: str = "", ignore_id: str = "",
) -> list[Customer]:
    """Customers already in the book who look like the one being entered.

    Same phone or same email is the same person whatever the spelling; the
    same name in any word order ("כהן משה") is very likely the same person.
    """
    number = whatsapp_number(phone)
    key = _name_key(name) if name.strip() else ""
    mail = email.strip().casefold()
    found: list[Customer] = []
    for customer in customers:
        if ignore_id and customer.customer_id == ignore_id:
            continue
        if number and whatsapp_number(customer.phone) == number:
            found.append(customer)
        elif mail and customer.email.strip().casefold() == mail:
            found.append(customer)
        elif key and _name_key(customer.name) == key:
            found.append(customer)
    return found


def search_customers(customers: Iterable[Customer], text: str) -> list[Customer]:
    """Customers matching a name, a city, or any part of a phone number."""
    needle = text.strip().casefold()
    if not needle:
        return list(customers)
    digits = re.sub(r"\D", "", needle)
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    found: list[Customer] = []
    for customer in customers:
        haystack = " ".join((
            customer.customer_id, customer.name, customer.contact, customer.city,
            customer.address, customer.email,
        )).casefold()
        if needle in haystack:
            found.append(customer)
        elif len(digits) >= 3 and digits in re.sub(r"\D", "", customer.phone):
            found.append(customer)
    return found


__all__ = [
    "KNOWN_CITIES",
    "ParsedContact",
    "email_problem",
    "find_duplicates",
    "format_phone",
    "parse_contact",
    "phone_problem",
    "search_customers",
]
