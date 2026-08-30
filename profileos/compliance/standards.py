"""The standards an Israeli window is actually judged against.

Two things are true at once here, and the software has to hold both. The
physics of a window — how much heat leaves it, how much noise it stops, what
wind it takes — is calculable, published and the same everywhere. The
*requirements* — which class this particular building has to reach, which
edition of which Israeli standard applies to it — are legal facts that live in
documents this software does not have and must not invent.

So the register below names the standards and says what each governs, and
every numeric threshold is marked with where it came from. Anything not
confirmed against the published standard is reported as a figure to check,
never as a pass. A window that "passes" against a number somebody guessed is
worse than one that was never checked, because somebody signs it off.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Confidence(StrEnum):
    """How far a figure in here can be relied on."""

    #: Read off the published standard, edition recorded.
    CONFIRMED = "confirmed"
    #: An ordinary industry value, right in most cases, binding in none.
    TYPICAL = "typical"
    #: Named because it applies, with no number attached yet.
    UNKNOWN = "unknown"

    @property
    def hebrew(self) -> str:
        return {
            "confirmed": "מאומת מול התקן",
            "typical": "ערך טיפוסי — לא מהתקן",
            "unknown": "לא ידוע — יש לבדוק בתקן",
        }[self.value]

    @property
    def may_be_certified(self) -> bool:
        """Whether a compliance statement may rest on this."""
        return self is Confidence.CONFIRMED


@dataclass(frozen=True)
class Standard:
    """One standard, and what this software can and cannot say about it."""

    number: str
    hebrew: str
    english: str
    scope: str
    #: What this software computes towards it, in the shop's language.
    covered: str = ""
    #: What it cannot answer, so nobody assumes it did.
    not_covered: str = ""
    confidence: Confidence = Confidence.UNKNOWN

    @property
    def title(self) -> str:
        return f"{self.number} — {self.hebrew}"


#: The standards a fabricator in Israel meets on a facade job. They are named
#: here so that a drawing, a quote and a job pack can all cite the same thing;
#: the thresholds inside them are the shop's to confirm, and the software says
#: so rather than filling them in.
STANDARDS: tuple[Standard, ...] = (
    Standard(
        "ת״י 1068", "חלונות ודלתות", "Windows and doors",
        "ביצועי חלונות ודלתות: אטימות לאוויר ולמים, עמידות בעומס רוח, "
        "ודרישות הרכבה.",
        covered="חישוב לחץ התכן, סיווג האטימות המתאים לו, ובדיקת הפרופיל בעומס",
        not_covered="הסיווג בפועל נקבע בבדיקת מעבדה על הדגם — לא בחישוב",
        confidence=Confidence.UNKNOWN,
    ),
    Standard(
        "ת״י 1099", "זיגוג בבניינים", "Glazing in buildings",
        "בחירת זיגוג, זיגוג בטיחותי במקומות שבהם אדם עלול להיתקל בו, "
        "ועמידות הזכוכית בעומס.",
        covered="זיהוי שמשות במיקום קריטי ודרישת זכוכית בטיחותית עבורן",
        not_covered="עובי הזכוכית לעומס הרוח בפועל דורש את טבלאות התקן",
        confidence=Confidence.TYPICAL,
    ),
    Standard(
        "ת״י 1045", "בידוד תרמי של בניינים", "Thermal insulation of buildings",
        "דרישות בידוד למעטפת הבניין, כולל מעבר חום דרך פתחים.",
        covered="חישוב ⁦U_w⁩ של הפתח כולו — זיגוג, מסגרת ומרווח — בשיטת ⁦EN ISO 10077-1⁩",
        not_covered="הערך הנדרש לאזור האקלים ולסוג הבניין נקבע בתקן",
        confidence=Confidence.TYPICAL,
    ),
    Standard(
        "ת״י 414", "עומסים על מבנים — עומס רוח", "Wind loads on structures",
        "מהירות הרוח היסודית לפי אזור, מקדמי גובה, שטח פנים וחשיפה.",
        covered="חישוב לחץ התכן על הפתח מתוך מהירות רוח, גובה וחשיפה שהוזנו",
        not_covered="מהירות הרוח היסודית לאתר — מהמפה בתקן",
        confidence=Confidence.UNKNOWN,
    ),
    Standard(
        "ת״י 5281", "בנייה בת-קיימא", "Sustainable building",
        "ניקוד בנייה ירוקה, ובכללו ביצועי מעטפת ופתחים.",
        covered="הנתונים התרמיים והאקוסטיים שהניקוד נשען עליהם",
        not_covered="הניקוד עצמו — נקבע על הבניין, לא על הפתח",
        confidence=Confidence.UNKNOWN,
    ),
    Standard(
        "ת״י 1142", "מעקים ומסעדים", "Guardrails and handrails",
        "גובה מעקה ועומס אופקי, כולל מקום שבו זיגוג משמש כמעקה.",
        covered="זיהוי שמשות שמתפקדות כמעקה לפי גובה הסף",
        not_covered="בדיקת המעקה עצמו בעומס האופקי",
        confidence=Confidence.UNKNOWN,
    ),
    Standard(
        "הג״א — ממ״ד", "מרחב מוגן דירתי", "Protected space",
        "חלון ההדף והדלת של הממ״ד: דגם מאושר, מידות ואופן ההרכבה.",
        covered="סימון הפתח כממ״ד כך שלא ייחתך כחלון רגיל",
        not_covered="אישור הדגם — ניתן על ידי פיקוד העורף ליצרן, לא לתוכנה",
        confidence=Confidence.UNKNOWN,
    ),
)


def standard(number: str) -> Standard | None:
    """One standard by its number, however it was typed."""
    wanted = number.replace('"', "״").replace("'", "׳").strip()
    for entry in STANDARDS:
        if entry.number == wanted or entry.number.replace("ת״י ", "") == wanted:
            return entry
    return None


def standards_for(topic: str) -> list[Standard]:
    """Every standard that bears on a topic, searched the way people ask."""
    needle = topic.strip().casefold()
    if not needle:
        return list(STANDARDS)
    return [
        entry for entry in STANDARDS
        if needle in f"{entry.number} {entry.hebrew} {entry.english} {entry.scope}".casefold()
    ]


__all__ = ["Confidence", "STANDARDS", "Standard", "standard", "standards_for"]
