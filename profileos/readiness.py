"""What this installation can actually do this morning.

Every package in this trade is sold as complete, and every one of them is
complete only after somebody has spent weeks putting the shop's own facts
into it: which series they buy, what the supplier's deductions are, what the
coater charges, which machine is on the floor. Until that is done the software
knows how to do the arithmetic and has nothing true to do it on.

The difference this file makes is that the state is said out loud. Instead of
a screen that looks finished and quietly prices a job on stand-in figures,
there is a list: what works now, what is partly there, what has not been
entered yet, what each gap actually blocks, and the one command or screen that
closes it.

Nothing here is a score out of ten. A shop that can quote but not cut is in a
perfectly reasonable state for its first week, and saying so plainly is more
use than a percentage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from .core.logging_setup import get_logger

_log = get_logger("readiness")


class State(StrEnum):
    """How far along one part of the setup is."""

    READY = "ready"
    PARTIAL = "partial"
    EMPTY = "empty"
    #: Something is set up in a way that will cause a problem.
    ATTENTION = "attention"

    @property
    def hebrew(self) -> str:
        return {
            "ready": "מוכן",
            "partial": "חלקי",
            "empty": "טרם הוזן",
            "attention": "דורש טיפול",
        }[self.value]

    @property
    def is_ready(self) -> bool:
        return self is State.READY


@dataclass
class Check:
    """One thing the shop has to have, and where it stands."""

    key: str
    hebrew: str
    state: State
    detail: str
    #: What cannot be done until this is in place. Empty when nothing is.
    blocks: str = ""
    #: The exact way to close it.
    fix: str = ""
    #: Whether the shop can work at all without this.
    critical: bool = False

    def describe(self) -> str:
        return f"{self.hebrew}: {self.state.hebrew} — {self.detail}"


@dataclass
class Readiness:
    """Everything checked, and what it adds up to."""

    checks: list[Check] = field(default_factory=list)

    def __iter__(self):
        return iter(self.checks)

    def __len__(self) -> int:
        return len(self.checks)

    def of_state(self, state: State) -> list[Check]:
        return [check for check in self.checks if check.state is state]

    @property
    def blockers(self) -> list[Check]:
        """The things that stop real work, as opposed to making it nicer."""
        return [
            check for check in self.checks
            if check.critical and not check.state.is_ready
        ]

    @property
    def may_quote(self) -> bool:
        """Whether a price can honestly be given to a customer today."""
        return not any(
            check.critical and check.state is State.EMPTY
            for check in self.checks
            if check.key in ("brand", "customers", "systems_classified")
        )

    @property
    def may_cut(self) -> bool:
        """Whether a bar may be cut from what is in here.

        The one question that decides whether this is production software this
        week or next month, and the one most likely to be answered by wishful
        thinking if nobody asks it directly.
        """
        catalogue = next(
            (check for check in self.checks if check.key == "systems_cuttable"), None
        )
        return bool(catalogue and catalogue.state.is_ready)

    def verdict(self) -> str:
        ready = len(self.of_state(State.READY))
        if self.may_cut:
            return f"מוכן לייצור · ⁦{ready}/{len(self)}⁩ נבדקו בהצלחה"
        if self.may_quote:
            return (
                "מוכן להצעות מחיר, לא לחיתוך — נדרש קטלוג הספק. "
                f"⁦{ready}/{len(self)}⁩ נבדקו בהצלחה"
            )
        return f"בהקמה · ⁦{ready}/{len(self)}⁩ נבדקו בהצלחה"

    def summary(self) -> dict[str, Any]:
        return {
            "checks": len(self),
            "ready": len(self.of_state(State.READY)),
            "partial": len(self.of_state(State.PARTIAL)),
            "empty": len(self.of_state(State.EMPTY)),
            "attention": len(self.of_state(State.ATTENTION)),
            "may_quote": self.may_quote,
            "may_cut": self.may_cut,
        }


# --------------------------------------------------------------------------- #
# The checks themselves
# --------------------------------------------------------------------------- #

def _check_brand() -> Check:
    from .branding import active_brand

    brand = active_brand()
    missing = []
    if not brand.registration_number:
        missing.append("מספר עוסק מורשה")
    if not brand.address_line:
        missing.append("כתובת")
    if not brand.phone:
        missing.append("טלפון")
    if missing:
        return Check(
            "brand", "פרטי העסק", State.PARTIAL,
            "חסר: " + ", ".join(missing),
            blocks="חשבונית מס אינה חוקית בלי מספר עוסק מורשה",
            fix="עמוד ״מערכת״ → פרטי העסק",
            critical=True,
        )
    return Check(
        "brand", "פרטי העסק", State.READY,
        f"{brand.display_name} · עוסק ⁦{brand.registration_number}⁩",
        critical=True,
    )


def _check_systems() -> tuple[Check, Check]:
    from .systems import DIRECTORY

    coverage = DIRECTORY.coverage()
    total = coverage.get("total", 0)
    unclassified = coverage.get("unclassified", 0)
    classified = total - unclassified
    confirmed = coverage.get("confirmed", 0)

    if classified == 0:
        classify = Check(
            "systems_classified", "סיווג סדרות", State.EMPTY,
            f"⁦{total}⁩ סדרות ברשימה, אף אחת לא סווגה",
            blocks="אי אפשר לתמחר סדרה שלא ידוע איזה סוג מערכת היא",
            fix="עמוד ״קטלוג״ → לשונית ״מערכות״ → סווג את הסדרות שאתם עובדים איתן",
            critical=True,
        )
    elif unclassified:
        classify = Check(
            "systems_classified", "סיווג סדרות", State.PARTIAL,
            f"⁦{classified}⁩ מתוך ⁦{total}⁩ סווגו",
            blocks=f"⁦{unclassified}⁩ סדרות עדיין לא ניתנות לתמחור",
            fix="עמוד ״קטלוג״ → לשונית ״מערכות״",
            critical=True,
        )
    else:
        classify = Check(
            "systems_classified", "סיווג סדרות", State.READY,
            f"כל ⁦{total}⁩ הסדרות סווגו", critical=True,
        )

    if confirmed:
        cut = Check(
            "systems_cuttable", "נתוני היצרן לחיתוך", State.READY,
            f"⁦{confirmed}⁩ סדרות עם קיזוזים מקטלוג הספק",
            critical=True,
        )
    else:
        cut = Check(
            "systems_cuttable", "נתוני היצרן לחיתוך", State.EMPTY,
            "אף סדרה לא נקלטה מקטלוג של ספק",
            blocks=(
                "אפשר לתמחר על ערכים טיפוסיים, אבל אסור לחתוך מוט לפיהם — "
                "כל דף חיתוך יישא ״לא לייצור״"
            ),
            fix="עמוד ״קטלוג״ → ״קליטה״ עם קובצי ה-DXF והטבלה של הספק",
            critical=True,
        )
    return classify, cut


def _check_customers() -> Check:
    from .projects import default_customers

    book = default_customers()
    count = len(book.all()) if hasattr(book, "all") else 0
    if count:
        return Check(
            "customers", "ספר לקוחות", State.READY,
            f"⁦{count}⁩ לקוחות", critical=True,
        )
    return Check(
        "customers", "ספר לקוחות", State.EMPTY, "אין לקוחות",
        blocks="הצעת מחיר בלי לקוח היא טיוטה",
        fix="עמוד ״פרויקטים״ → לשונית ״לקוחות״ → ״לקוח חדש״",
        critical=True,
    )


def _check_jobs() -> Check:
    from .projects import default_store

    store = default_store()
    jobs = store.all()
    if jobs:
        return Check(
            "jobs", "תיקי עבודה", State.READY, f"⁦{len(jobs)}⁩ פרויקטים בספר",
        )
    return Check(
        "jobs", "תיקי עבודה", State.EMPTY, "אין פרויקטים",
        fix="עמוד ״פרויקטים״ → ״פרויקט חדש״, או `profileos seed` לנתוני התחלה",
    )


def _check_machines() -> Check:
    from .core.config import get_settings

    folder = get_settings().machines_dir
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    if not files:
        return Check(
            "machines", "מכונות", State.EMPTY, "אין הגדרת מכונה",
            blocks="אפשר להפיק קוד מכונה על הגדרות ברירת מחדל בלבד",
            fix="עמוד ״עיבוד CNC״, או הוסיפו קובץ מכונה לתיקיית ההגדרות",
        )
    return Check(
        "machines", "מכונות", State.READY,
        f"⁦{len(files)}⁩ מכונות מוגדרות: " + ", ".join(f.stem for f in files[:3]),
    )


def _check_post_processor() -> Check:
    """The one that has never been true and has to keep saying so."""
    return Check(
        "post_proven", "הוכחת קוד מכונה", State.ATTENTION,
        "פורמטי ה-CNC מעולם לא נחתכו על מכונה פיזית מהתוכנה הזאת",
        blocks="ייצור ישיר מקוד שלא נוסה",
        fix="הריצו תוכנית אחת על פסולת ואמתו מידות לפני ייצור",
        critical=True,
    )


def _check_tax_rule() -> Check:
    from .erp.israel import DEFAULT_ALLOCATION_RULE

    if DEFAULT_ALLOCATION_RULE.is_confirmed:
        return Check(
            "allocation", "סף מספר הקצאה", State.READY,
            f"⁦{DEFAULT_ALLOCATION_RULE.threshold:,.0f}⁩ ₪ · "
            f"{DEFAULT_ALLOCATION_RULE.source}",
        )
    return Check(
        "allocation", "סף מספר הקצאה", State.EMPTY,
        "הסף לא אומת מול רשות המסים",
        blocks="חשבונית גדולה עלולה לצאת בלי מספר הקצאה",
        fix="בדקו את הסף לשנה הנוכחית ועדכנו אותו",
        critical=True,
    )


def _check_wind() -> Check:
    return Check(
        "wind", "מהירות רוח יסודית", State.EMPTY,
        "לא נרשמה מהירות רוח לאתר מהמפה בת״י 414",
        blocks="בדיקת עומס רוח מסומנת ״לא מאומת״ ואינה לתכנון סופי",
        fix="קראו את המהירות מהמפה והזינו אותה עם המקור בעמוד ״פתח״",
    )


def _check_service() -> Check:
    from .service import default_register

    register = default_register()
    if not len(register):
        return Check(
            "service", "ספר קריאות שירות", State.EMPTY, "אין קריאות רשומות",
            fix="עמוד ״שירות״ → ״קריאה חדשה״ — רשמו גם קריאות שכבר טופלו",
        )
    overdue = len(register.overdue())
    if overdue:
        return Check(
            "service", "ספר קריאות שירות", State.ATTENTION,
            f"⁦{overdue}⁩ קריאות באיחור",
            blocks="לקוח שממתין",
            fix="עמוד ״שירות״",
        )
    return Check(
        "service", "ספר קריאות שירות", State.READY,
        f"⁦{len(register)}⁩ קריאות · ⁦{len(register.open_calls())}⁩ פתוחות",
    )


def _check_standard_times() -> Check:
    return Check(
        "times", "זמני תקן", State.ATTENTION,
        "זמני החיתוך, ההרכבה וההרכבה באתר הם ערכי התחלה, לא מדידות שלכם",
        blocks="תאריך שהובטח טוב רק כמו הדקות שמאחוריו",
        fix="מדדו שתיים-שלוש עבודות אמיתיות וקבעו את הזמנים",
    )


def _check_samples() -> Check:
    from .library import profile_library, sample_profiles

    own = len(profile_library()) - len(sample_profiles())
    if own:
        return Check(
            "profiles", "ספריית פרופילים", State.READY,
            f"⁦{own}⁩ שרטוטים בתיקייה שלכם",
        )
    return Check(
        "profiles", "ספריית פרופילים", State.PARTIAL,
        "רק שרטוטי הדוגמה — אין שרטוטים משלכם",
        blocks="ניתוח חתכים אמיתיים של הספקים שלכם",
        fix="שימו את קובצי ה-DXF של הספק בתיקיית הפרופילים",
    )


#: Everything checked, in the order a shop would work down it.
CHECKS: tuple[Callable[[], Any], ...] = (
    _check_brand,
    _check_customers,
    _check_systems,
    _check_samples,
    _check_jobs,
    _check_machines,
    _check_post_processor,
    _check_tax_rule,
    _check_wind,
    _check_standard_times,
    _check_service,
)


def readiness() -> Readiness:
    """Run every check. A check that fails to run is reported, not skipped."""
    found = Readiness()
    for check in CHECKS:
        try:
            result = check()
        except Exception as exc:  # noqa: BLE001 - a broken check is a finding
            _log.warning("Readiness check %s failed: %s", check.__name__, exc)
            found.checks.append(Check(
                check.__name__.removeprefix("_check_"), check.__name__,
                State.ATTENTION, f"הבדיקה עצמה נכשלה: {exc}",
                fix="דווחו על זה",
            ))
            continue
        if isinstance(result, tuple):
            found.checks.extend(result)
        else:
            found.checks.append(result)
    return found


__all__ = ["CHECKS", "Check", "Readiness", "State", "readiness"]
