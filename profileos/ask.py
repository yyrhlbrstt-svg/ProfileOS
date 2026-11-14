"""Ask the program, in plain Hebrew, and get the answer from the shop's books.

"כמה כסף יש במגירה?", "מי חייב לי הכי הרבה?", "מה יש מחר בשטח?" — the
questions an owner asks out loud in the morning. Each is recognised by the
words it is asked with, answered from the same records the pages show, and
comes with the page where it is dealt with. Nothing leaves the computer and
nothing is guessed: a question it does not recognise gets no answer, and the
search box simply goes on searching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

#: How many rows an answer lists before "ועוד".
SHOWN = 5


@dataclass
class Answer:
    #: The answer itself, in one line — the figure first.
    title: str
    detail: str = ""
    #: The rows behind it (a few at most).
    rows: list[str] = field(default_factory=list)
    #: The page where it is dealt with, and what the button says.
    page: str = ""
    go: str = ""


def _money(value: float) -> str:
    return f"⁦{value:,.0f} ₪⁩"


def _words(text: str) -> str:
    text = text.replace("׳", "'").replace("״", '"').replace("?", " ").replace(",", " ")
    return " " + re.sub(r"\s+", " ", text.strip().lower()) + " "


#: The letters Hebrew joins to the front of a word: "ו", "ה", "ב", "ל", "מ",
#: "ש", "כ" — "להתקנה" is "התקנה", "במגירה" is "מגירה".
_PREFIXES = "והבלמשכ"


def _forms(word: str) -> set[str]:
    """The word, and the word without up to two joined letters in front."""
    forms = {word}
    for cut in (1, 2):
        head = word[:cut]
        if len(word) - cut >= 2 and all(letter in _PREFIXES for letter in head):
            forms.add(word[cut:])
    return forms


def _tokens(text: str) -> set[str]:
    found: set[str] = set()
    for word in text.split():
        found |= _forms(word.strip("'\".:;!()"))
    return found


def _has(text: str, *stems: str) -> bool:
    """Whether the question has one of these: a phrase (with a space) as it
    is written, a single stem at the start of one of its words — so "חוב"
    is a debt, and not the middle of "רחוב"."""
    tokens = _tokens(text)
    for stem in stems:
        if " " in stem.strip():
            if f" {stem.strip()} " in text or f" {stem.strip()}" in text:
                return True
        elif any(token.startswith(stem) for token in tokens):
            return True
    return False


def _n(count: int, one: str, many: str) -> str:
    """Hebrew agrees with its number: "צ׳ק אחד חזר", "⁦3⁩ צ׳קים חזרו"."""
    return one if count == 1 else f"⁦{count}⁩ {many}"


def _more(rows: list[str], total: int) -> list[str]:
    return rows + ([f"ועוד ⁦{total - len(rows)}⁩"] if total > len(rows) else [])


# --------------------------------------------------------------------------- #
# The answers
# --------------------------------------------------------------------------- #
def _drawer(context: dict[str, Any], today: date, text: str) -> Answer | None:
    book = context.get("cheques")
    if book is None:
        return None
    expected = book.expected(today)
    total = sum(cheque.amount for cheque in expected)
    soon = [c for c in expected if c.due <= today + timedelta(days=30)]
    return Answer(
        f"{_money(total)} בצ׳קים שעוד לא נפרעו",
        f"⁦{len(expected)}⁩ צ׳קים · ⁦{len(soon)}⁩ מהם לפירעון ב־30 הימים הקרובים, בסך "
        f"{_money(sum(c.amount for c in soon))}.",
        page="Collection", go="לגבייה")


def _bankable(context: dict[str, Any], today: date, text: str) -> Answer | None:
    book = context.get("cheques")
    if book is None:
        return None
    ready = sorted(book.bankable(today), key=lambda c: c.due)
    week = [c for c in book.held() if today < c.due <= today + timedelta(days=7)]
    rows = [f"{c.customer} · {_money(c.amount)} · ⁦{c.due:%d/%m}⁩" for c in ready[:SHOWN]]
    return Answer(
        f"{_money(sum(c.amount for c in ready))} אפשר להפקיד כבר היום"
        if ready else "אין היום צ׳קים להפקדה",
        f"{_n(len(ready), 'צ׳ק אחד שתאריכו הגיע', 'צ׳קים שתאריכם הגיע')}"
        + (f" · עוד {_n(len(week), 'צ׳ק אחד', 'צ׳קים')} השבוע, בסך "
           f"{_money(sum(c.amount for c in week))}." if week else " · אין עוד צ׳קים השבוע."),
        _more(rows, len(ready)), page="Collection", go="לגבייה")


def _bounced(context: dict[str, Any], today: date, text: str) -> Answer | None:
    book = context.get("cheques")
    if book is None:
        return None
    back = book.bounced()
    rows = [f"{c.customer} · {_money(c.amount)} · צ׳ק ⁦{c.number}⁩" for c in back[:SHOWN]]
    return Answer(
        f"{_n(len(back), 'צ׳ק אחד חזר', 'צ׳קים חזרו')} — {_money(sum(c.amount for c in back))}"
        if back else "אין צ׳קים שחזרו",
        "צריך לבקש מהלקוח צ׳ק חדש או העברה." if back else "",
        _more(rows, len(back)), page="Collection", go="לגבייה")


def _owed(context: dict[str, Any], today: date, text: str) -> Answer | None:
    from .projects.account import job_account

    jobs = context.get("jobs") or []
    cheques = list(context.get("cheques") or [])
    owed = []
    for job in jobs:
        if job.status.value not in ("won", "in_production", "installed"):
            continue
        left = job_account(job, cheques).uncovered
        if left > 0.5:
            owed.append((left, job))
    owed.sort(key=lambda pair: -pair[0])
    rows = [f"{job.customer_name or job.name} · {_money(left)}" for left, job in owed[:SHOWN]]
    return Answer(
        f"{_money(sum(left for left, _job in owed))} לגבייה "
        + ("מפרויקט אחד" if len(owed) == 1 else f"מ־⁦{len(owed)}⁩ פרויקטים")
        if owed else "אף אחד לא חייב — כל היתרות מכוסות בתשלום או בצ׳קים",
        "מה שעוד לא שולם ולא מכוסה בצ׳ק, כולל מע״מ. הגבוה ביותר ראשון." if owed else "",
        _more(rows, len(owed)), page="Projects", go="לפרויקטים")


def _field(context: dict[str, Any], today: date, text: str) -> Answer | None:
    from .projects.agenda import day_word, next_working_day

    agenda = context.get("agenda")
    if agenda is None:
        return None
    day = today if _has(text, "היום") else next_working_day(today)
    visits = [v for v in agenda.on_day(day) if not v.done]
    rows = [visit.describe() for visit in visits[:SHOWN]]
    when = day_word(day, today)
    return Answer(
        f"{_n(len(visits), 'ביקור אחד', 'ביקורים')} {when}" if visits else f"אין ביקורים {when}",
        "לפי היומן, לפי השעה.", _more(rows, len(visits)), page="Home", go="לדף הבית")


def _quotes(context: dict[str, Any], today: date, text: str) -> Answer | None:
    jobs = [job for job in context.get("jobs") or [] if job.status.value == "quoted"]

    def age(job: Any) -> int:
        try:
            return (today - date.fromisoformat((job.quoted_on or job.created)[:10])).days
        except ValueError:
            return 0

    if _has(text, "שבוע"):
        jobs = [job for job in jobs if age(job) >= 7]
    jobs.sort(key=lambda job: -job.quote_total)
    rows = [f"{job.customer_name or job.name} · {_money(job.quote_total)} · "
            + ("נשלחה היום" if age(job) == 0 else "נשלחה אתמול" if age(job) == 1
               else f"לפני ⁦{age(job)}⁩ ימים")
            for job in jobs[:SHOWN]]
    return Answer(
        f"{_n(len(jobs), 'הצעה פתוחה אחת', 'הצעות פתוחות')} בשווי {_money(sum(j.quote_total for j in jobs))}"
        if jobs else "אין הצעות פתוחות",
        "לפני מע״מ. הגדולה ראשונה — שם כדאי להתקשר קודם." if jobs else "",
        _more(rows, len(jobs)), page="Projects", go="לפרויקטים")


def _closing(context: dict[str, Any], today: date, text: str) -> Answer | None:
    from .learning import lessons

    found = lessons(context.get("jobs") or [])
    return Answer(found[0].title, found[0].detail, page="Reports", go="לדוחות")


def _price(context: dict[str, Any], today: date, text: str) -> Answer | None:
    from .learning import lessons

    found = [lesson for lesson in lessons(context.get("jobs") or []) if "מ״ר" in lesson.title]
    if not found:
        return None
    return Answer(found[0].title, found[0].detail, page="Quotation", go="להצעת מחיר")


def _production(context: dict[str, Any], today: date, text: str) -> Answer | None:
    jobs = [job for job in context.get("jobs") or [] if job.status.value in ("won", "in_production")]
    making = [job for job in jobs if job.status.value == "in_production"]
    rows = [f"{job.customer_name or job.name} · ⁦{job.unit_count}⁩ יח׳ · {job.status.hebrew}"
            for job in jobs[:SHOWN]]
    return Answer(
        f"⁦{len(making)}⁩ בייצור, ⁦{len(jobs) - len(making)}⁩ ממתינים לייצור",
        f"בסך הכול ⁦{sum(job.unit_count for job in jobs)}⁩ יחידות בצבר.",
        _more(rows, len(jobs)), page="Shop floor", go="לרצפת הייצור")


def _tasks(context: dict[str, Any], today: date, text: str) -> Answer | None:
    book = context.get("tasks")
    if book is None:
        return None
    due = [task for task in book.open_tasks if task.due <= today]
    late = [task for task in due if task.due < today]
    rows = [f"{task.subject_name} · {task.what}" for task in due[:SHOWN]]
    return Answer(
        f"⁦{len(due)}⁩ משימות להיום" + (f", ⁦{len(late)}⁩ באיחור" if late else "")
        if due else "אין משימות פתוחות להיום",
        "", _more(rows, len(due)), page="Home", go="לדף הבית")


def _service(context: dict[str, Any], today: date, text: str) -> Answer | None:
    register = context.get("service")
    if register is None:
        return None
    calls = register.open_calls()
    rows = [f"{call.customer_name} · {call.description or call.symptom.value}" for call in calls[:SHOWN]]
    return Answer(
        _n(len(calls), "קריאת שירות פתוחה אחת", "קריאות שירות פתוחות") if calls
        else "אין קריאות שירות פתוחות",
        "", _more(rows, len(calls)), page="Service", go="לשירות")


def _sales(context: dict[str, Any], today: date, text: str) -> Answer | None:
    if _has(text, "שעבר", "קודם", "אשתקד"):
        return None                     # only this month and this year are known here
    month = _has(text, "החודש", "חודש")
    start = today.replace(day=1) if month else today.replace(month=1, day=1)
    won = []
    for job in context.get("jobs") or []:
        for event in job.history:
            if event.status.value == "won" and event.at[:10] >= start.isoformat():
                won.append(job)
                break
    total = sum(job.quote_total for job in won)
    return Answer(
        f"{_money(total)} נסגרו {'החודש' if month else 'השנה'}",
        f"⁦{len(won)}⁩ פרויקטים שאושרו מאז ⁦{start:%d/%m/%Y}⁩, לפני מע״מ.",
        [f"{job.customer_name or job.name} · {_money(job.quote_total)}" for job in won[:SHOWN]],
        page="Reports", go="לדוחות")


def _blind_frames(context: dict[str, Any], today: date, text: str) -> Answer | None:
    from .projects.blindframes import describe, is_two_stage

    jobs = [job for job in context.get("jobs") or [] if is_two_stage(job)
            and job.status.value in ("won", "in_production")]
    rows = [f"{job.customer_name or job.name}: {describe(job)}" for job in jobs[:SHOWN]]
    return Answer(
        _n(len(jobs), "פרויקט אחד בשני שלבים", "פרויקטים בשני שלבים") if jobs
        else "אין פרויקטים בשני שלבים",
        "", rows, page="Projects", go="לפרויקטים")


#: Words in customers' names that are ordinary words in a question too.
_NAME_STOP = {"משפחת", "משפחה", "מרכז", "רפואי", "מוסדות", "חינוך", "בנייה", "בניה",
              "וקבלנות", "קבלנות", "חברת", "בית", "ההר", "הגבעה", "בעמ", 'בע"מ'}


def _customer(context: dict[str, Any], today: date, text: str) -> Answer | None:
    """A question naming a customer — a whole word of their name, not the
    start of another word ("ברקוד" is not "ברק"): their jobs, and what they
    still owe."""
    from .projects.account import job_account

    cheques = list(context.get("cheques") or [])
    jobs = context.get("jobs") or []
    tokens = _tokens(text)
    best = None
    for job in jobs:
        name = (job.customer_name or "").strip()
        words = [w.lower().strip('".') for w in re.split(r"\s+", name)]
        words = [w for w in words if len(w) >= 3 and w not in _NAME_STOP]
        if words and any(word in tokens for word in words):
            best = name
            break
    if best is None:
        return None
    theirs = [job for job in jobs if (job.customer_name or "").strip() == best]
    owed = sum(job_account(job, cheques).uncovered for job in theirs
               if job.status.value in ("won", "in_production", "installed"))
    rows = [f"⁦{job.job_id}⁩ · {job.status.hebrew} · {_money(job.quote_total)}" for job in theirs]
    return Answer(
        f"{best}: {_n(len(theirs), 'פרויקט אחד', 'פרויקטים')}"
        + (f", חייבים {_money(owed)}" if owed > 0.5 else ""),
        "", rows[:SHOWN], page="Projects", go="לפרויקטים")


def _incoming(context: dict[str, Any], today: date, text: str) -> Answer | None:
    """What comes in and goes out over the next weeks — the cash flow."""
    book = context.get("cheques")
    if book is None:
        return None
    horizon = today + timedelta(days=30)
    coming = [c for c in book.expected(today) if c.due <= horizon]
    going = [c for c in book.to_pay() if c.due <= horizon]
    net = sum(c.amount for c in coming) - sum(c.amount for c in going)
    return Answer(
        f"{_money(sum(c.amount for c in coming))} נכנסים ב־30 הימים הקרובים",
        f"בצ׳קים של לקוחות · יוצאים {_money(sum(c.amount for c in going))} בצ׳קים לספקים · "
        f"נטו {_money(net)}.",
        page="Collection", go="לתזרים")


def _to_pay(context: dict[str, Any], today: date, text: str) -> Answer | None:
    book = context.get("cheques")
    if book is None:
        return None
    going = sorted(book.to_pay(), key=lambda c: c.due)
    rows = [f"{c.customer} · {_money(c.amount)} · ⁦{c.due:%d/%m}⁩" for c in going[:SHOWN]]
    return Answer(
        f"{_money(sum(c.amount for c in going))} בצ׳קים שנתתם וטרם ירדו"
        if going else "אין צ׳קים לספקים שעוד לא ירדו",
        "לפי תאריך הפירעון — הקרוב ראשון." if going else "",
        _more(rows, len(going)), page="Collection", go="לגבייה")


def _week(context: dict[str, Any], today: date, text: str) -> Answer | None:
    agenda = context.get("agenda")
    if agenda is None:
        return None
    visits = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        visits += [v for v in agenda.on_day(day) if not v.done]
    rows = [f"⁦{v.on:%d/%m}⁩ · {v.describe()}" for v in visits[:SHOWN]]
    return Answer(
        f"{_n(len(visits), 'ביקור אחד', 'ביקורים')} בשבוע הקרוב" if visits
        else "אין ביקורים ביומן בשבוע הקרוב",
        "", _more(rows, len(visits)), page="Home", go="לדף הבית")


#: (what the question has to contain, the answer). The first that fits wins,
#: so the narrower ones come before the broad.
_RULES: tuple[tuple[Callable[[str], bool], Callable[..., Answer | None]], ...] = (
    (lambda t: _has(t, "צ'ק", "צ\"ק", "שיק", "צקים", "צ'קים") and _has(t, "חזר", "חוזר"), _bounced),
    (lambda t: _has(t, "להפקיד", "הפקדה"), _bankable),
    (lambda t: _has(t, "מגירה") or (_has(t, "צ'ק", "שיק", "צקים", "צ'קים") and _has(t, "כמה", "סך")),
     _drawer),
    # Money owed to the shop before money the shop owes: "צריכים לשלם לי".
    (lambda t: (_has(t, "חייב", "חוב", "לגבות", "גבייה", "יתרות", "לשלם לי", "צריכים לשלם")
                and not _has(t, "אני חייב", "אני צריך", "אנחנו חייבים")), _owed),
    (lambda t: _has(t, "ספק", "שנתתי", "שכתבתי") and _has(t, "לשלם", "צ'ק", "צ'קים", "שנתתי",
                                                         "שכתבתי", "חייב"), _to_pay),
    (lambda t: _has(t, "ייכנס", "יכנס", "תזרים") or (_has(t, "כסף") and _has(t, "יגיע", "נכנס")),
     _incoming),
    (lambda t: _has(t, "השבוע", "שבוע הקרוב") and _has(t, "שטח", "התקנ", "ביקור", "יומן", "מתקין"),
     _week),
    (lambda t: _has(t, "מחר", "היום") and _has(t, "שטח", "התקנ", "ביקור", "יומן", "מתקין", "לאן"),
     _field),
    (lambda t: _has(t, "משקופ"), _blind_frames),
    (lambda t: _has(t, "סגירה", "סוגרים", "נסגרות", "אחוז סגירה"), _closing),
    (lambda t: _has(t, "הצעות", "הצעה") and _has(t, "פתוח", "מחכ", "ענו", "ממתינ"), _quotes),
    (lambda t: _has(t, "מחיר") and _has(t, "מטר", 'מ"ר', "למ\"ר"), _price),
    (lambda t: _has(t, "ייצור", "במפעל", "רצפת", "צבר"), _production),
    (lambda t: _has(t, "משימות", "משימה", "מעקב"), _tasks),
    (lambda t: _has(t, "שירות", "תקלות", "תקלה", "קריאות"), _service),
    (lambda t: _has(t, "מכרנו", "מכירות", "הכנסות", "סגרנו", "נסגרו"), _sales),
)


def answer(question: str, context: dict[str, Any] | None = None, *,
           today: date | None = None) -> Answer | None:
    """The answer to a question about the shop, or ``None`` when it is not one."""
    text = _words(question)
    if len(text.strip()) < 3:
        return None
    today = today or date.today()
    if context is None:
        from .insights import gather_context

        context = gather_context()
    unread = set(context.get("unread") or [])
    for fits, reply in _RULES:
        if fits(text):
            if reply in _NEEDS_CHEQUES and "cheques" in unread:
                # A book that could not be read is never answered as empty.
                return Answer("ספר הצ׳קים לא נקרא — אין תשובה עד שהוא משוחזר",
                              "הקובץ פגום ולא שוחזר מהעותק הקודם. מערכת ← גיבוי ושחזור.",
                              page="System", go="למערכת")
            return reply(context, today, text)
    return _customer(context, today, text)


#: The answers read from the cheque book.
_NEEDS_CHEQUES = (_drawer, _bankable, _bounced, _owed, _incoming, _to_pay)

#: Shown under the search box, so people know they may ask.
EXAMPLES = ("כמה כסף יש במגירה?", "מי חייב לי הכי הרבה?", "מה יש מחר בשטח?",
            "אילו הצעות פתוחות יותר משבוע?", "מה אחוז הסגירה?")

__all__ = ["Answer", "EXAMPLES", "answer"]
