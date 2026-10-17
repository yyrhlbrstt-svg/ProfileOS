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

    tax = _TAX_ID.search(text)
    if tax:
        result.tax_id = tax.group(1)
        text = text.replace(tax.group(0), " ")

    emails = _EMAIL.findall(text)
    if emails:
        result.email = emails[0]
    for email in emails:
        text = text.replace(email, " ")

    phones: list[str] = []
    for match in _PHONE.finditer(text):
        if whatsapp_number(match.group(0)):
            phones.append(format_phone(match.group(0)))
    # Mobiles first: the number WhatsApp and the installer will both use.
    phones.sort(key=lambda number: not number.startswith("05"))
    if phones:
        result.phone = phones[0]
        result.extra_phones = phones[1:]
    text = _PHONE.sub(" ", text)

    lines: list[str] = []
    for raw in re.split(r"[\n\r|;,]+", text):
        line = _LABELS.sub("", raw.strip()).strip(" ,.-–:")
        if line:
            lines.append(line)

    for line in list(lines):
        city = next((c for c in KNOWN_CITIES if c in line), "")
        if city and not result.city:
            result.city = city
            rest = line.replace(city, "").strip(" ,.-–")
            if rest and re.search(r"\d", rest):
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


def _name_key(name: str) -> str:
    cleaned = re.sub(r"[\"'׳״`.,\-–]", " ", name).casefold()
    words = [w for w in cleaned.split() if w not in ("בע\"מ", "בעמ", "ltd", "משפחת")]
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
