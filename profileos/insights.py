"""What the shop should look at today, found without anybody asking.

Every other screen answers the question it was opened for. This one looks
across all of them for the things nobody opened a screen for: the quotation
sent three weeks ago that nobody chased, the job won a month ago that never
reached the floor, the cheque that can be banked this morning, the backup
that has not run since Tuesday, the customer typed in twice.

Each rule is a plain reading of records the shop already keeps — nothing is
estimated, predicted or invented, and a rule whose records cannot be read is
skipped and says so rather than taking the others down with it. The point is
the list a good office manager would hand the owner on a Sunday morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from .core.logging_setup import get_logger

_log = get_logger("insights")

#: How long a quotation may sit before somebody should be on the phone.
QUOTE_STALE_DAYS = 7
#: How long a won job may wait before it reaches the floor.
WON_STALE_DAYS = 14
#: Days after a job is won before a missing deposit is worth a word.
DEPOSIT_GRACE_DAYS = 3
#: Days something asked of the customer may wait before it is worth a word.
NEED_STALE_DAYS = 5
#: Days a repair after installation may wait before it is worth a word.
SNAG_STALE_DAYS = 7
#: How long a job may sit in production before somebody asks why.
PRODUCTION_STALE_DAYS = 30
#: Days without a backup before it is worth a line on the home page.
BACKUP_STALE_DAYS = 2

_ORDER = {"danger": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Insight:
    """One thing worth a look, and where to go to deal with it."""

    severity: str  # "danger", "warning" or "info"
    title: str
    detail: str = ""
    #: The page that deals with it, by its ``title``; empty if none does.
    page: str = ""
    #: Stable identity, so a dismissed insight stays dismissed until it changes.
    key: str = ""


def _days_since(text: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(text[:10])).days
    except (TypeError, ValueError):
        return None


def _status_since(job: Any) -> str:
    """When the job entered the status it is in now."""
    for event in reversed(getattr(job, "history", []) or []):
        if event.status is job.status:
            return event.at
    return getattr(job, "updated", "") or getattr(job, "created", "")


def _count(n: int, one: str, many: str) -> str:
    """Hebrew agrees with its number: "צ׳ק אחד חזר", "⁦3⁩ צ׳קים חזרו"."""
    return one if n == 1 else f"⁦{n}⁩ {many}"


def _names(items: list[Any], label: Callable[[Any], str], limit: int = 3) -> str:
    shown = ", ".join(label(item) for item in items[:limit])
    if len(items) > limit:
        shown += f" ועוד ⁦{len(items) - limit}⁩"
    return shown


# --------------------------------------------------------------------------- #
# The rules
# --------------------------------------------------------------------------- #

def _data_safety(context: dict[str, Any], today: date) -> list[Insight]:
    from .core import storage
    from .core.backup import backup_status, default_backup_folder

    found: list[Insight] = []
    store = context.get("store")
    unreadable = list(getattr(store, "unreadable", []) or [])
    if unreadable:
        found.append(Insight(
            "danger", _count(len(unreadable), "קובץ פרויקט אחד לא נקרא", "קבצי פרויקט לא נקראו"),
            "הם לא נמחקו ולא נדרסו. שחזרו אותם מגיבוי במסך מערכת ← גיבוי: "
            + _names(unreadable, lambda path: path.name),
            "System", "unreadable:" + ",".join(sorted(p.name for p in unreadable)),
        ))
    if storage.RECOVERED:
        found.append(Insight(
            "warning", "קובץ פגום שוחזר מהגרסה הקודמת שלו",
            "השינוי האחרון בו אולי חסר — בדקו אותו. העותק הפגום נשמר בצד: "
            + _names(storage.RECOVERED, lambda pair: pair[0].name),
            "", "recovered:" + ",".join(sorted(str(p[1]) for p in storage.RECOVERED)),
        ))

    status = backup_status()
    if status["error"]:
        found.append(Insight(
            "danger", "הגיבוי האוטומטי נכשל", status["error"], "System",
            f"backup-error:{today}:{status['error']}",
        ))
    try:
        archives = sorted(default_backup_folder().glob("profileos-*.zip"))
    except OSError:
        archives = []
    data_dir = context.get("data_dir")
    has_data = bool(data_dir and data_dir.is_dir() and any(data_dir.iterdir()))
    if has_data and not archives:
        found.append(Insight(
            "warning", "עדיין לא נלקח אף גיבוי",
            "גיבוי נלקח אוטומטית בפתיחה ובסגירה. אפשר גם עכשיו: מערכת ← גיבוי ושחזור.",
            "System", "backup-none",
        ))
    elif archives:
        newest = date.fromtimestamp(archives[-1].stat().st_mtime)
        age = (today - newest).days
        if age > BACKUP_STALE_DAYS:
            found.append(Insight(
                "warning", f"הגיבוי האחרון לפני ⁦{age}⁩ ימים",
                "התוכנה מגבה כשהיא נפתחת ונסגרת — כנראה שלא נסגרה כמה ימים, "
                "או שהגיבוי נכשל.", "System", f"backup-stale:{newest}",
            ))
    if status["mirror_error"]:
        found.append(Insight(
            "warning", "העותק השני של הגיבוי לא עודכן", status["mirror_error"],
            "System", f"mirror-error:{today}:{status['mirror_error']}",
        ))
    elif has_data and not context.get("mirror"):
        found.append(Insight(
            "info", "כל הגיבויים נמצאים על המחשב הזה בלבד",
            "אם הדיסק יתקלקל — הגיבויים איתו. הגדירו עותק שני בדיסק און קי "
            "או בתיקיית OneDrive: מערכת ← גיבוי ושחזור.", "System", "mirror-none",
        ))
    return found


def _jobs(context: dict[str, Any], today: date) -> list[Insight]:
    from .projects import JobStatus

    jobs = context.get("jobs") or []
    tasks = context.get("tasks")
    found: list[Insight] = []

    # Speed to quote: the shop that answers first usually gets the job.
    waiting_quote = [
        job for job in jobs
        if job.status is JobStatus.ENQUIRY
        and (_days_since(job.created, today) or 0) >= 2
    ]
    if waiting_quote:
        found.append(Insight(
            "warning",
            _count(len(waiting_quote), "פנייה אחת מחכה להצעת מחיר יותר מיומיים",
                   "פניות מחכות להצעת מחיר יותר מיומיים"),
            "מי שעונה ראשון בדרך כלל מקבל את העבודה. אפשר להתחיל בהערכת מחיר "
            "מהירה (Ctrl+E): "
            + _names(waiting_quote, lambda job: job.customer_name or job.name),
            "Projects", "enquiry:" + ",".join(sorted(job.job_id for job in waiting_quote)),
        ))

    # After handover: the moment a happy customer is most willing to say so —
    # but only a customer who has paid and has nothing open with service.
    from .projects.account import job_account

    cheques = list(context.get("cheques") or [])
    register = context.get("service")
    open_calls = {
        call.job_id for call in (register.open_calls() if register is not None else [])
        if call.job_id
    }
    review = [
        job for job in jobs
        if job.status is JobStatus.INSTALLED and not job.review_requested
        and 0 <= (_days_since(_status_since(job), today) or 99) <= 21
        and job.job_id not in open_calls
        and job_account(job, cheques).uncovered <= 0.5
    ]
    # One line per customer: two jobs handed over to the same family are one
    # request for a review, not two names that read like a mistake.
    # A family already asked — for this job or the one before — is not asked again.
    def who(job: Any) -> str:
        return job.customer_id or job.customer_name or job.job_id

    asked = {who(job) for job in jobs if job.review_requested}
    by_customer: dict[str, Any] = {}
    for job in review:
        if who(job) not in asked:
            by_customer.setdefault(who(job), job)
    review = list(by_customer.values())
    if review:
        found.append(Insight(
            "info",
            _count(len(review), "לקוח אחד קיבל עבודה בשבועות האחרונים — בקשו המלצה",
                   "לקוחות קיבלו עבודה בשבועות האחרונים — בקשו המלצה"),
            "המלצה בגוגל מלקוח מרוצה שווה יותר מכל מודעה. בפרויקט: ״בקשת המלצה״: "
            + _names(review, lambda job: job.customer_name or job.name),
            "Projects", "review:" + ",".join(sorted(job.job_id for job in review)),
        ))

    # A year after handover: a paid check-up — tracks, seals and drainage,
    # or the water heater and filters — is repeat work, and catches a
    # warranty fault while it is still small.
    checkup = [
        job for job in jobs
        if job.status is JobStatus.INSTALLED and not job.checkup_offered
        and 330 <= (_days_since(_status_since(job), today) or 0) <= 420
    ]
    if checkup:
        found.append(Insight(
            "info",
            _count(len(checkup), "לקוח אחד הגיע לבדיקה השנתית", "לקוחות הגיעו לבדיקה השנתית"),
            "שנה אחרי המסירה — הציעו ביקור בדיקה (בפרויקט: ״הצעת בדיקה שנתית״): "
            + _names(checkup, lambda job: job.customer_name or job.name),
            "Projects", "checkup:" + ",".join(sorted(job.job_id for job in checkup)),
        ))

    quoted = [job for job in jobs if job.status is JobStatus.QUOTED]
    waiting = [
        job for job in quoted
        if (_days_since(job.quoted_on or _status_since(job), today) or 0) >= QUOTE_STALE_DAYS
    ]
    unchased = waiting
    if tasks is not None:
        try:
            unchased = [job for job in tasks.unchased_quotes(waiting)]
        except Exception:  # noqa: BLE001 - fall back to every waiting quote
            unchased = waiting
    if unchased:
        money = sum(job.quote_total for job in unchased)
        found.append(Insight(
            "warning",
            _count(len(unchased), "הצעת מחיר אחת מחכה יותר משבוע ואף אחד לא עוקב", "הצעות מחיר מחכות יותר משבוע ואף אחד לא עוקב"),
            (f"בסך ⁦{money:,.0f} ₪⁩. " if money else "")
            + "שיחה אחת היום שווה יותר מעוד הצעה: "
            + _names(unchased, lambda job: job.customer_name or job.name),
            "Projects", "quotes:" + ",".join(sorted(job.job_id for job in unchased)),
        ))

    # Waiting on the customer: the colour, the measurement, the glass. A job
    # that cannot start is said with what it waits for, so the call to make
    # is obvious.
    blocked = []
    for job in jobs:
        # Only before production: once it is being made, what it waited for
        # evidently arrived, ticked or not.
        if job.status is not JobStatus.WON:
            continue
        late = [need for need in getattr(job, "open_needs", []) or []
                if need.blocks_production
                and (_days_since(need.requested_on, today) or 0) >= NEED_STALE_DAYS]
        if late:
            blocked.append((job, late))
    if blocked:
        found.append(Insight(
            "warning",
            _count(len(blocked), "פרויקט אחד מחכה ללקוח כדי להתחיל לייצר",
                   "פרויקטים מחכים ללקוח כדי להתחיל לייצר"),
            "בלי אלה אי אפשר להתחיל: "
            + _names(blocked, lambda pair: f"{pair[0].customer_name or pair[0].name} — "
                     + ", ".join(need.label for need in pair[1])
                     + f" (⁦{max(_days_since(n.requested_on, today) or 0 for n in pair[1])}⁩ ימים)")
            + ". בפרויקטים ← עוד פעולות ← ממתינים מהלקוח ← שליחה ללקוח.",
            "Projects", "needs:" + ",".join(sorted(
                need.need_id for _job, needs in blocked for need in needs)),
        ))

    won = [
        job for job in jobs
        if job.status is JobStatus.WON
        and (_days_since(_status_since(job), today) or 0) >= WON_STALE_DAYS
        and not any(need.blocks_production for need in getattr(job, "open_needs", []) or [])
    ]
    if won:
        found.append(Insight(
            "warning", _count(len(won), "פרויקט אחד נסגר ועדיין לא נכנס לייצור", "פרויקטים נסגרו ועדיין לא נכנסו לייצור"),
            "נסגרו לפני יותר משבועיים: "
            + _names(won, lambda job: job.customer_name or job.name),
            "Shop floor", "won:" + ",".join(sorted(job.job_id for job in won)),
        ))

    stuck = [
        job for job in jobs
        if job.status is JobStatus.IN_PRODUCTION
        and (_days_since(_status_since(job), today) or 0) >= PRODUCTION_STALE_DAYS
    ]
    if stuck:
        found.append(Insight(
            "info", _count(len(stuck), "פרויקט אחד בייצור יותר מחודש", "פרויקטים בייצור יותר מחודש"),
            "אולי הותקנו ולא סומנו, ואולי משהו תקוע: "
            + _names(stuck, lambda job: job.customer_name or job.name),
            "Projects", "stuck:" + ",".join(sorted(job.job_id for job in stuck)),
        ))

    late = []
    for job in jobs:
        if job.status in (JobStatus.INSTALLED, JobStatus.LOST) or not job.due_date:
            continue
        overdue = _days_since(job.due_date, today)
        if overdue is not None and overdue > 0:
            late.append(job)
    if late:
        found.append(Insight(
            "danger", _count(len(late), "פרויקט אחד עבר את תאריך היעד", "פרויקטים עברו את תאריך היעד"),
            _names(late, lambda job: f"{job.customer_name or job.name} ({job.due_date})"),
            "Projects", "late:" + ",".join(sorted(job.job_id for job in late)),
        ))

    no_customer = [
        job for job in jobs
        if job.status in (JobStatus.QUOTED, JobStatus.WON, JobStatus.IN_PRODUCTION)
        and not job.customer_id and not job.customer_name
    ]
    if no_customer:
        found.append(Insight(
            "info", _count(len(no_customer), "פרויקט פעיל אחד בלי לקוח", "פרויקטים פעילים בלי לקוח"),
            "בלי לקוח אין למי לשלוח הצעה או עדכון: "
            + _names(no_customer, lambda job: job.name),
            "Projects", "nocustomer:" + ",".join(sorted(j.job_id for j in no_customer)),
        ))
    return found


def _customers(context: dict[str, Any], today: date) -> list[Insight]:
    from .projects import JobStatus
    from .projects.contacts import find_duplicates
    from .sharing import whatsapp_number

    customers = context.get("customers") or []
    jobs = context.get("jobs") or []
    found: list[Insight] = []

    active_ids = {
        job.customer_id for job in jobs
        if job.status not in (JobStatus.INSTALLED, JobStatus.LOST) and job.customer_id
    }
    unreachable = [
        customer for customer in customers
        if customer.customer_id in active_ids and not whatsapp_number(customer.phone)
    ]
    if unreachable:
        found.append(Insight(
            "warning", _count(len(unreachable), "לקוח אחד עם פרויקט פתוח בלי טלפון תקין", "לקוחות עם פרויקט פתוח בלי טלפון תקין"),
            "אי אפשר לשלוח להם הצעה או עדכון בוואטסאפ: "
            + _names(unreachable, lambda c: c.name),
            "Projects", "nophone:" + ",".join(sorted(c.customer_id for c in unreachable)),
        ))

    pairs: list[tuple[Any, Any]] = []
    seen: set[str] = set()
    for index, customer in enumerate(customers):
        if customer.customer_id in seen:
            continue
        twins = find_duplicates(
            customers[index + 1:], name=customer.name, phone=customer.phone,
            email=customer.email,
        )
        for twin in twins:
            seen.add(twin.customer_id)
            pairs.append((customer, twin))
    if pairs:
        found.append(Insight(
            "info", _count(len(pairs), "לקוח אחד נראה רשום פעמיים", "לקוחות נראים רשומים פעמיים"),
            _names(pairs, lambda pair: f"{pair[0].name} / {pair[1].name}"),
            "Projects", "dupes:" + ",".join(
                sorted(f"{a.customer_id}={b.customer_id}" for a, b in pairs)
            ),
        ))
    return found


def _money(context: dict[str, Any], today: date) -> list[Insight]:
    cheques = context.get("cheques")
    if cheques is None:
        return []
    found: list[Insight] = []
    # A cheque more than six months past its date can no longer be banked
    # (Israeli banks refuse it) — it is a debt to collect again, not money.
    stale_after = today - timedelta(days=182)
    stale = [cheque for cheque in cheques.held() if cheque.due < stale_after]
    bankable = [cheque for cheque in cheques.bankable(today) if cheque.due >= stale_after]
    if stale:
        total = sum(cheque.amount for cheque in stale)
        found.append(Insight(
            "danger", _count(len(stale), "צ׳ק אחד עבר חצי שנה ולא הופקד", "צ׳קים עברו חצי שנה ולא הופקדו"),
            f"בסך ⁦{total:,.0f} ₪⁩ — הבנק כבר לא יכבד אותם; צריך לבקש מהלקוח צ׳ק חדש: "
            + _names(stale, lambda c: c.customer or c.number),
            "Collection", "stale:" + ",".join(sorted(c.cheque_id for c in stale)),
        ))
    if bankable:
        total = sum(cheque.amount for cheque in bankable)
        found.append(Insight(
            "warning", _count(len(bankable), "צ׳ק אחד אפשר להפקיד כבר היום", "צ׳קים אפשר להפקיד כבר היום"),
            f"בסך ⁦{total:,.0f} ₪⁩: " + _names(bankable, lambda c: c.customer or c.number),
            "Collection", "bankable:" + ",".join(sorted(c.cheque_id for c in bankable)),
        ))
    week = [
        cheque for cheque in cheques.held()
        if today < cheque.due <= today + timedelta(days=7)
    ]
    if week:
        total = sum(cheque.amount for cheque in week)
        found.append(Insight(
            "info", _count(len(week), "צ׳ק אחד נפרע השבוע", "צ׳קים נפרעים השבוע"),
            f"בסך ⁦{total:,.0f} ₪⁩", "Collection",
            "week:" + ",".join(sorted(c.cheque_id for c in week)),
        ))
    # Deposited a week ago and never marked cleared: either it cleared and
    # nobody ticked it (the balance still counts it as only promised), or it
    # came back and the bank's letter has not been opened yet.
    unconfirmed = [
        cheque for cheque in getattr(cheques, "deposited", lambda: [])()
        if cheque.deposited_on is not None and (today - cheque.deposited_on).days >= 7
    ]
    if unconfirmed:
        total = sum(cheque.amount for cheque in unconfirmed)
        found.append(Insight(
            "info",
            _count(len(unconfirmed), "צ׳ק אחד הופקד לפני שבוע ולא סומן כנפרע",
                   "צ׳קים הופקדו לפני שבוע ולא סומנו כנפרעו"),
            f"בסך ⁦{total:,.0f} ₪⁩ — בדקו בחשבון הבנק: נפרעו (״נפרע״) או חזרו (״סימון כחוזר״).",
            "Collection", "unconfirmed:" + ",".join(sorted(c.cheque_id for c in unconfirmed)),
        ))
    bounced = cheques.bounced()
    if bounced:
        total = sum(cheque.amount for cheque in bounced)
        found.append(Insight(
            "danger", _count(len(bounced), "צ׳ק אחד חזר ועדיין לא טופל", "צ׳קים חזרו ועדיין לא טופלו"),
            f"בסך ⁦{total:,.0f} ₪⁩: " + _names(bounced, lambda c: c.customer or c.number),
            "Collection", "bounced:" + ",".join(sorted(c.cheque_id for c in bounced)),
        ))

    # The other side: cheques the shop wrote. The week the account goes
    # below zero is found while there is still time to move a payment.
    to_pay = getattr(cheques, "to_pay", lambda: [])()
    soon = [c for c in to_pay if today <= c.due <= today + timedelta(days=7)]
    # Past its date and never marked: the supplier probably banked it.
    past = [c for c in to_pay if c.due < today - timedelta(days=3)]
    if past:
        total = sum(c.amount for c in past)
        found.append(Insight(
            "info",
            _count(len(past), "צ׳ק לספק שעבר תאריכו ולא סומן", "צ׳קים לספקים שעבר תאריכם ולא סומנו"),
            f"בסך ⁦{total:,.0f} ₪⁩ — אם ירדו מהחשבון, סמנו ״נפרע״ בגבייה כדי שהתזרים יהיה נכון: "
            + _names(past, lambda c: f"{c.customer} ⁦{c.due:%d/%m}⁩"),
            "Collection", "paid-out:" + ",".join(sorted(c.cheque_id for c in past)),
        ))
    if soon:
        total = sum(c.amount for c in soon)
        found.append(Insight(
            "info",
            _count(len(soon), "צ׳ק לספק יורד השבוע", "צ׳קים לספקים יורדים השבוע"),
            f"בסך ⁦{total:,.0f} ₪⁩ — לוודא שיש כיסוי: "
            + _names(soon, lambda c: f"{c.customer} ⁦{c.due:%d/%m}⁩"),
            "Collection", "paying:" + ",".join(sorted(c.cheque_id for c in soon)),
        ))
    if to_pay:
        settings = context.get("settings")
        opening = getattr(settings, "bank_balance", None)
        try:
            balance_on = date.fromisoformat(getattr(settings, "bank_balance_on", "") or "")
        except ValueError:
            balance_on = None
        if opening is None:
            balance_on = None
        flow = cheques.two_sided(today, weeks=8, opening=opening or 0.0,
                                 balance_on=balance_on)
        short = next((row for row in flow if row["balance"] < 0), None)
        if short is not None:
            week = row_week = short["week"]
            found.append(Insight(
                "danger" if opening is not None else "warning",
                f"בשבוע של ⁦{row_week:%d/%m}⁩ החשבון צפוי לרדת מתחת לאפס",
                (f"לפי היתרה בבנק והצ׳קים, החשבון צפוי לרדת ל־⁦{short['balance']:,.0f} ₪⁩. "
                 if opening is not None else
                 f"הצ׳קים לספקים עד אז עולים ב־⁦{-short['balance']:,.0f} ₪⁩ על הצ׳קים "
                 "מלקוחות (בלי יתרת הבנק — אפשר להזין אותה בגבייה). ")
                + "כדאי לגבות יתרות, להפקיד מה שאפשר, או לתאם דחייה עם הספק.",
                "Collection", f"short:{week.isoformat()}:{short['balance']:.0f}",
            ))
    return found


def _service(context: dict[str, Any], today: date) -> list[Insight]:
    register = context.get("service")
    if register is None:
        return []
    overdue = register.overdue(today)
    if not overdue:
        return []
    return [Insight(
        "danger", _count(len(overdue), "קריאת שירות אחת באיחור", "קריאות שירות באיחור"),
        "לקוח שמחכה לטכנאי זוכר את זה יותר מכל הצעת מחיר: "
        + _names(overdue, lambda call: call.customer_name or call.call_id),
        "Service", "service:" + ",".join(sorted(call.call_id for call in overdue)),
    )]


def _tasks(context: dict[str, Any], today: date) -> list[Insight]:
    tasks = context.get("tasks")
    if tasks is None:
        return []
    late = tasks.overdue(today)
    if not late:
        return []
    return [Insight(
        "warning", _count(len(late), "משימת מעקב אחת שעבר זמנה", "משימות מעקב שעבר זמנן"),
        _names(late, lambda task: task.subject_name or task.what),
        "Projects", "tasks:" + ",".join(sorted(task.task_id for task in late)),
    )]


def _field(context: dict[str, Any], today: date) -> list[Insight]:
    """Tomorrow's visits whose customers have not been told."""
    from .projects.agenda import day_word, next_working_day

    agenda = context.get("agenda")
    if agenda is None:
        return []
    day = next_working_day(today)
    # Every day up to the next working day: on Thursday, Friday's visits too.
    waiting = [a for a in agenda if today < a.on <= day and not a.reminded_on and not a.done]
    if not waiting:
        return []
    return [Insight(
        "warning",
        _count(len(waiting), f"ביקור אחד {day_word(day, today)} — הלקוח עוד לא קיבל תזכורת",
               f"ביקורים {day_word(day, today)} — הלקוחות עוד לא קיבלו תזכורת"),
        "לקוח שלא בבית זה חצי יום של שני אנשים. תזכורת אחת בוואטסאפ: "
        + _names(waiting, lambda a: a.customer_name or a.job_id or a.kind.hebrew),
        "Home", f"remind:{day}:" + ",".join(sorted(a.appointment_id for a in waiting)),
    )]


def _pace(context: dict[str, Any], today: date) -> list[Insight]:
    """The shop works slower (or faster) than its quotations assume."""
    from .quoting.labour_learning import calibrate

    book = context.get("timebook")
    if book is None:
        return []
    calibration = calibrate(context.get("jobs") or [], book)
    current = float(getattr(context.get("settings"), "labour_factor", 1.0) or 1.0)
    if not calibration.enough or abs(calibration.factor - current) / current < 0.1:
        return []
    slower = calibration.factor > current
    return [Insight(
        "warning" if slower else "info",
        ("העבודה במפעל ארוכה ממה שההצעות מתמחרות" if slower
         else "העבודה במפעל מהירה ממה שההצעות מתמחרות"),
        calibration.describe() + f" ההצעות מחושבות לפי ⁦{current:.2f}⁩. "
        + ("כל הצעה מפסידה על שעות שלא נכללו בה. " if slower else
           "ההצעות יקרות מהצורך — אפשר להתחרות במחיר. ")
        + "מערכת ← מפעיל ← תעריפי הערכה: ״לתמחר מעכשיו לפי…״.",
        "System", f"pace:{calibration.factor:.2f}:{len(calibration.jobs)}",
    )]


def _install_ready(context: dict[str, Any], today: date) -> list[Insight]:
    """Installations in the next two working days whose job is not ready."""
    from .projects.agenda import VisitKind, day_word, next_working_day
    from .projects.install_check import not_ready

    agenda = context.get("agenda")
    if agenda is None:
        return []
    first = next_working_day(today)
    second = next_working_day(first)
    jobs = {job.job_id: job for job in context.get("jobs") or []}
    cheques = list(context.get("cheques") or [])
    store = context.get("store")
    found: list[Insight] = []
    for visit in agenda:
        if (visit.done or not visit.job_id or not (today < visit.on <= second)
                or visit.kind not in (VisitKind.INSTALLATION, VisitKind.DELIVERY)):
            continue
        job = jobs.get(visit.job_id)
        if job is None:
            continue
        order = None
        if store is not None:
            try:
                from .mes.store import load_work_order

                order = load_work_order(job.job_id, store)
            except Exception:  # noqa: BLE001 - the other checks still say their part
                order = None
        missing = not_ready(job, cheques=cheques, work_order=order)
        if not missing:
            continue
        when = day_word(visit.on, today)
        found.append(Insight(
            "danger" if visit.on <= first else "warning",
            f"{visit.kind.hebrew} {when} אצל {job.customer_name or job.name} — עוד לא מוכן",
            "; ".join(missing) + ". יום התקנה שהולך לאיבוד עולה שני מתקינים ומשאית — "
            "להשלים היום, או לתאם יום אחר עם הלקוח.",
            "Projects", f"ready:{visit.appointment_id}:" + str(len(missing)) + ":"
            + ",".join(sorted(m[:12] for m in missing)),
        ))
    return found


def _paperwork(context: dict[str, Any], today: date) -> list[Insight]:
    """The shop's own certificates, and supplier price lists out of date."""
    found: list[Insight] = []
    settings = context.get("settings")
    if settings is not None:
        for field_name, label in (
            ("withholding_certificate_until", "אישור ניכוי מס במקור"),
            ("books_certificate_until", "אישור ניהול ספרים"),
        ):
            raw = getattr(settings, field_name, "") or ""
            if not raw:
                found.append(Insight(
                    "info", f"לא הוזן תוקף ל{label}",
                    "כשהאישור פג, קבלן מנכה 20–30% מכל תשלום. הזינו את התאריך "
                    "במסך מערכת ← מפעיל, והתוכנה תזכיר חודש לפני.",
                    "System", f"cert-missing:{field_name}",
                ))
                continue
            try:
                until = date.fromisoformat(raw[:10])
            except ValueError:
                continue
            days = (until - today).days
            if days < 0:
                found.append(Insight(
                    "danger", f"{label} פג ב־⁦{until:%d/%m/%Y}⁩",
                    "כל תשלום מקבלן ינוכה עכשיו במלואו. חדשו מול רואה החשבון ועדכנו "
                    "כאן את התאריך החדש.", "System", f"cert:{field_name}:{raw}",
                ))
            elif days <= 30:
                found.append(Insight(
                    "warning", f"{label} פג בעוד ⁦{days}⁩ ימים",
                    f"בתוקף עד ⁦{until:%d/%m/%Y}⁩ — כדאי לבקש חידוש מרואה החשבון עכשיו.",
                    "System", f"cert:{field_name}:{raw}",
                ))
    try:
        from .quoting.suppliers import all_suppliers

        stale = [supplier for supplier in all_suppliers() if not supplier.is_current]
    except Exception:  # noqa: BLE001 - no suppliers is not a finding
        stale = []
    if stale:
        found.append(Insight(
            "warning",
            _count(len(stale), "מחירון ספק אחד לא בתוקף", "מחירוני ספקים לא בתוקף"),
            "הצעות שמתומחרות ממנו עלולות להיות במחיר ישן: "
            + _names(stale, lambda supplier: supplier.name or supplier.id),
            "Catalogue", "suppliers:" + ",".join(sorted(s.id for s in stale)),
        ))
    return found


def _draft(context: dict[str, Any], today: date) -> list[Insight]:
    from .projects.draft import draft_path

    if context.get("has_job") or not draft_path().is_file():
        return []
    return [Insight(
        "info", "יש תכנון שמור שלא שויך לפרויקט",
        "הוא נשמר אוטומטית. פתחו פרויקט כדי לתייק אותו.", "Projects",
        f"draft:{draft_path().stat().st_mtime:.0f}",
    )]


def _last_reminder(job: Any, today: date) -> str:
    sent = list(getattr(job, "balance_reminders", []) or [])
    if not sent:
        return " (עוד לא נשלחה תזכורת)"
    days = _days_since(sent[-1], today)
    if days is None:
        return ""
    return " (תזכורת נשלחה היום)" if days == 0 else f" (תזכורת לפני ⁦{days}⁩ ימים)"


def _open_snags(job: Any) -> str:
    """Said beside a balance: the customer is probably waiting for these first."""
    left = len(getattr(job, "open_snags", []) or [])
    if not left:
        return ""
    return " (תיקון אחד פתוח)" if left == 1 else f" (⁦{left}⁩ תיקונים פתוחים)"


def _accounts(context: dict[str, Any], today: date) -> list[Insight]:
    """Money a job is still owed, extras nobody agreed, invoices that will bounce."""
    from .projects import JobStatus
    from .projects.account import invoices_missing_allocation, job_account

    jobs = context.get("jobs") or []
    cheques = list(context.get("cheques") or [])
    found: list[Insight] = []

    owing = []
    for job in jobs:
        if job.status is not JobStatus.INSTALLED:
            continue
        account = job_account(job, cheques)
        if account.uncovered > 1.0:
            owing.append((job, account))
    if owing:
        total = sum(account.uncovered for _job, account in owing)
        found.append(Insight(
            "warning",
            _count(len(owing), "פרויקט אחד הותקן ולא שולם במלואו",
                   "פרויקטים הותקנו ולא שולמו במלואם"),
            f"נותר לגבות ⁦{total:,.0f} ₪⁩: "
            + _names(owing, lambda pair: f"{pair[0].customer_name or pair[0].name} "
                     f"⁦{pair[1].uncovered:,.0f} ₪⁩" + _open_snags(pair[0])
                     + _last_reminder(pair[0], today))
            + ". בפרויקטים ← עוד פעולות ← שליחת היתרה ללקוח.",
            "Projects", "owing:" + ",".join(
                sorted(f"{job.job_id}={account.uncovered:.0f}" for job, account in owing)
            ),
        ))

    # A document sent to Morning with no answer: it may exist. Before anybody
    # issues it again, somebody looks.
    unsure = [job for job in jobs if getattr(job, "document_pending", "")]
    if unsure:
        found.append(Insight(
            "danger",
            _count(len(unsure), "מסמך מס שלא ידוע אם הופק", "מסמכי מס שלא ידוע אם הופקו"),
            "Morning לא ענה כשנשלחו — בדקו שם לפני שמפיקים שוב, ורשמו בחשבון הפרויקט: "
            + _names(unsure, lambda job: f"⁦{job.job_id}⁩ {job.document_pending}"),
            "Projects", "pending-doc:" + ",".join(sorted(job.job_id for job in unsure)),
        ))

    # A job won, built or installed with no price on it: nothing to collect
    # against, so every check above (deposit, balance owed) stays silent.
    unpriced = []
    for job in jobs:
        if job.status not in (JobStatus.WON, JobStatus.IN_PRODUCTION, JobStatus.INSTALLED):
            continue
        if job_account(job, cheques).total_gross <= 0:
            unpriced.append(job)
    if unpriced:
        found.append(Insight(
            "warning",
            _count(len(unpriced), "פרויקט אחד בעבודה בלי מחיר", "פרויקטים בעבודה בלי מחיר"),
            "בלי מחיר אי אפשר לדעת כמה נשאר לגבות: "
            + _names(unpriced, lambda job: job.customer_name or job.name)
            + ". רשמו את ההצעה שהלקוח אישר (הצעת מחיר ← ״רישום בפרויקט״), "
            "או את העבודה שסוכמה בחשבון הפרויקט ← ״שורה חופשית״.",
            "Projects", "unpriced:" + ",".join(sorted(job.job_id for job in unpriced)),
        ))

    # Work under way with not a shekel in hand and not a cheque promised:
    # a customer who changes their mind leaves the shop with the material.
    unpaid = []
    for job in jobs:
        if job.status not in (JobStatus.WON, JobStatus.IN_PRODUCTION):
            continue
        account = job_account(job, cheques)
        if account.total_gross <= 0 or account.received + account.in_cheques > 0.5:
            continue
        ordered = job.material_ordered
        settled_days = _days_since(_status_since(job), today) or 0
        if ordered or job.status is JobStatus.IN_PRODUCTION or settled_days >= DEPOSIT_GRACE_DAYS:
            unpaid.append((job, account, ordered))
    if unpaid:
        exposed = [entry for entry in unpaid if entry[2]]
        found.append(Insight(
            "danger" if exposed else "warning",
            _count(len(unpaid), "פרויקט אחד בעבודה בלי מקדמה", "פרויקטים בעבודה בלי מקדמה"),
            "לא התקבל תשלום ולא צ׳ק: "
            + _names(unpaid, lambda entry: f"{entry[0].customer_name or entry[0].name} "
                     f"⁦{entry[1].total_gross:,.0f} ₪⁩"
                     + (" — חומר כבר הוזמן" if entry[2] else ""))
            + ". מקדמה לפני הזמנת חומר שומרת עליכם אם הלקוח מתחרט. "
            "תשלום שהתקבל נרשם בפרויקט ← חשבון הפרויקט ← רישום תשלום.",
            "Projects", "deposit:" + ",".join(sorted(job.job_id for job, _a, _o in unpaid)),
        ))

    # The punch list: a customer waiting on a repair is a customer waiting
    # to pay the last of it.
    waiting_fix = [
        (job, [snag for snag in job.open_snags
               if (_days_since(snag.raised_on, today) or 0) >= SNAG_STALE_DAYS])
        for job in jobs if job.status is not JobStatus.LOST
    ]
    waiting_fix = [(job, snags) for job, snags in waiting_fix if snags]
    if waiting_fix:
        count = sum(len(snags) for _job, snags in waiting_fix)
        found.append(Insight(
            "warning",
            _count(count, "תיקון אחד אחרי התקנה מחכה יותר משבוע",
                   "תיקונים אחרי התקנה מחכים יותר משבוע"),
            "הלקוח מחכה — ובדרך כלל גם התשלום האחרון: "
            + _names(waiting_fix, lambda pair: f"{pair[0].customer_name or pair[0].name}: "
                     + ", ".join(snag.description for snag in pair[1][:2])
                     + ("…" if len(pair[1]) > 2 else ""))
            + ". בפרויקטים ← עוד פעולות ← רשימת תיקונים.",
            "Projects", "snags:" + ",".join(sorted(
                snag.snag_id for _job, snags in waiting_fix for snag in snags)),
        ))

    pending = [
        (job, line) for job in jobs if job.status.is_open
        for line in job.lines if line.kind.value == "extra" and not line.approved
    ]
    if pending:
        found.append(Insight(
            "warning",
            _count(len(pending), "תוספת אחת לא אושרה עם הלקוח",
                   "תוספות לא אושרו עם הלקוח"),
            "תוספת שלא אושרה בכתב היא ויכוח בסוף העבודה — שלחו לאישור בוואטסאפ: "
            + _names(pending, lambda pair: f"{pair[1].description} ({pair[0].name})"),
            "Projects", "extras:" + ",".join(sorted(line.line_id for _job, line in pending)),
        ))

    from .projects.progress import retention_held

    held = [(job, retention_held(job)) for job in jobs
            if job.status is JobStatus.INSTALLED and job.progress_bills]
    held = [(job, amount) for job, amount in held if amount > 0]
    if held:
        found.append(Insight(
            "warning",
            _count(len(held), "עיכבון אחד מחכה לגבייה מקבלן", "עיכבונות מחכים לגבייה מקבלנים"),
            "העבודה נמסרה — העיכבון משתחרר עכשיו: "
            + _names(held, lambda pair: f"{pair[0].name} ⁦{pair[1]:,.0f} ₪⁩"),
            "Projects", "retention:" + ",".join(sorted(job.job_id for job, _a in held)),
        ))

    missing = [(job, invoice) for job in jobs for invoice in invoices_missing_allocation(job)]
    if missing:
        found.append(Insight(
            "danger",
            _count(len(missing), "חשבונית אחת ללקוח עסקי בלי מספר הקצאה",
                   "חשבוניות ללקוחות עסקיים בלי מספר הקצאה"),
            "בלי מספר הקצאה הלקוח לא יכול לקזז את המע״מ, והחשבונית תחזור: "
            + _names(missing, lambda pair: f"⁦{pair[1].number}⁩ ({pair[0].name})"),
            "Projects", "allocation:" + ",".join(sorted(i.number for _j, i in missing)),
        ))
    return found


def _catalogue(context: dict[str, Any], today: date) -> list[Insight]:
    """New supplier files, catalogue editions waiting, and figures they made stale."""
    from .catalogue.editions import (
        confirmation_hits, open_jobs_on, stale_confirmation, stock_of_removed,
    )

    found: list[Insight] = []
    jobs = context.get("jobs") or []
    confirmations = context.get("confirmations")

    findings = context.get("findings")
    if findings is not None:
        fresh = findings.open_findings()
        if fresh:
            suppliers = sorted({f.supplier for f in fresh if f.supplier})
            found.append(Insight(
                "warning",
                _count(len(fresh), "ספק פרסם קובץ קטלוג חדש", "קבצי קטלוג חדשים אצל הספקים"),
                "כדאי לבדוק אם השתנו פרופילים לפני החיתוך הבא: "
                + _names(fresh, lambda f: f"⁦{f.name}⁩") + (
                    f" ({', '.join(suppliers[:3])})" if suppliers else ""
                ),
                "Catalogue", "catalogue-files:" + ",".join(sorted(f.finding_id for f in fresh)),
            ))

    watch = context.get("watch")
    if watch is not None:
        for source in watch:
            if source.last_error:
                found.append(Insight(
                    "info", f"לא ניתן לבדוק את {source.title}",
                    source.last_error, "Catalogue",
                    f"catalogue-source:{source.source_id}:{today}",
                ))

    editions = context.get("editions")
    if editions is None:
        return found
    for edition in editions.pending():
        diff = editions.diff(edition)
        affected = open_jobs_on(edition.system_id, jobs)
        confirmation = confirmations.get(edition.system_id) if (
            confirmations is not None and edition.system_id
        ) else None
        hits = confirmation_hits(diff, confirmation)
        detail = diff.summary()
        if affected:
            detail += " · " + _count(
                len(affected), "פרויקט פתוח אחד על הסדרה", "פרויקטים פתוחים על הסדרה"
            ) + ": " + _names(affected, lambda job: f"⁦{job.job_id}⁩")
        if hits:
            detail += " · נתוני החיתוך שאושרו נוגעים ל־" + ", ".join(
                hit.describe() for hit in hits[:3]
            )
        stock = stock_of_removed(diff, context.get("stocktakes") or [])
        if stock:
            detail += " · במלאי מפרופילים שלא נמצאו בקטלוג החדש: " + ", ".join(
                left.describe() for left in stock[:3]
            )
        if not edition.system_id:
            detail += (" · המהדורה לא שויכה לסדרה מהרשימה, ולכן לא נבדקו "
                       "פרויקטים ונתוני חיתוך")
        severity = "danger" if hits else ("warning" if (affected and not diff.empty) else "info")
        found.append(Insight(
            severity, f"מהדורת קטלוג חדשה ל{edition.label} ממתינה לבדיקה",
            detail, "Catalogue", f"edition:{edition.edition_id}",
        ))

    if confirmations is not None:
        for confirmation in confirmations:
            stale = stale_confirmation(editions, confirmation.entry_id, confirmation)
            if not stale:
                continue
            try:
                from .systems import DIRECTORY

                entry = DIRECTORY.get(confirmation.entry_id)
                series = entry.display if entry is not None else confirmation.entry_id
            except Exception:  # noqa: BLE001 - the id still says which series
                series = confirmation.entry_id
            detail = (
                f"הנתונים נקראו מ״{confirmation.source}״, ומאז אושר "
                f"״{stale.latest.source}״. בדקו בקטלוג החדש את טבלאות החיתוך "
                "והזיגוג והזינו את נתוני היצרן מחדש, עם המהדורה שממנה נקראו."
            )
            if stale.hits:
                detail += " השתנו פרופילים שהנתונים נשענים עליהם: " + ", ".join(
                    hit.describe() for hit in stale.hits[:4]
                ) + "."
            elif stale.no_articles:
                detail += (" לנתונים לא צוינו מק״טים, ולכן אי אפשר לדעת אילו "
                           "פרופילים נוגעים בהם.")
            affected = open_jobs_on(confirmation.entry_id, jobs)
            if affected:
                detail += " " + _count(
                    len(affected), "פרויקט פתוח אחד על הסדרה", "פרויקטים פתוחים על הסדרה"
                ) + ": " + _names(affected, lambda job: f"⁦{job.job_id}⁩") + "."
            found.append(Insight(
                "danger" if stale.hits else "warning",
                f"נתוני החיתוך של {series} נקראו מקטלוג קודם",
                detail, "Catalogue",
                f"stale-confirmation:{confirmation.entry_id}:"
                f"{getattr(confirmation, 'edition_id', '') or confirmation.entered_at or confirmation.entered_on}:"
                f"{stale.latest.edition_id}",
            ))
    return found


def _price_lists(context: dict[str, Any], today: date) -> list[Insight]:
    """New supplier prices: waiting to be checked, eating a quotation's margin,
    or a reason to order before they take effect."""
    from .quoting.pricelists import LOAD_PROBLEMS, impact

    found: list[Insight] = []
    for problem in LOAD_PROBLEMS:
        found.append(Insight(
            "danger", "מחירון ספק לא הופעל",
            problem + " — הצעות מחיר מתומחרות עכשיו בלעדיו. בדקו במסך קטלוג ← מחירוני ספקים.",
            "Catalogue", f"price-load:{problem}:{today}",
        ))
    book = context.get("price_lists")
    if book is None:
        return found
    jobs = context.get("jobs") or []
    # A job's bill of materials is built once for the whole scan, not once
    # per supplier.
    materials: dict[str, list[Any]] = {}

    def name(text: str) -> str:
        return f"\u2068{text}\u2069"  # a supplier's name, Hebrew or Latin, kept whole

    for edition in book.pending():
        diff = book.diff(edition)
        found.append(Insight(
            "warning" if diff.up else "info",
            f"מחירון חדש מ{name(edition.supplier)} ממתין לבדיקה",
            diff.summary() + (f" · בתוקף מ־⁦{edition.valid_from}⁩" if edition.valid_from else ""),
            "Catalogue", f"price-list:{edition.edition_id}",
        ))
    for supplier_id in book.suppliers():
        # The latest accepted list, whether in force yet or about to be.
        accepted = book.accepted(supplier_id)
        if not accepted:
            continue
        latest = accepted[-1]
        try:
            effect = impact(book, latest, jobs, materials=materials)
        except Exception as exc:  # noqa: BLE001 - one supplier, not the scan
            _log.warning("Price impact for %s failed: %s", supplier_id, exc)
            continue
        # Worth a line only when it is worth acting on: a quotation is not
        # re-sent over a hundred shekels, and a list that cries wolf is ignored.
        dearer = [c for c in effect.quoted if c.delta > 0 and c.worth_saying]
        if dearer:
            total = sum(c.delta for c in dearer)
            found.append(Insight(
                "warning",
                _count(len(dearer), "הצעה פתוחה תומחרה לפני ההתייקרות",
                       "הצעות פתוחות תומחרו לפני ההתייקרות")
                + f" של {name(latest.supplier)}",
                f"החומר בהן עולה לפחות ⁦{total:,.0f} ₪⁩ יותר (הערכה: אורך נטו בלי פחת, "
                "אחרי ההנחה שלכם) — כדאי לעדכן לפני שהלקוח חותם: "
                + _names(dearer, lambda c: c.describe()),
                "Projects", f"price-quoted:{latest.edition_id}:"
                + ",".join(sorted(c.job.job_id for c in dearer)),
            ))
        dearer_orders = [c for c in effect.to_order if c.delta > 0 and c.worth_saying]
        if dearer_orders and effect.buy_before:
            saving = sum(c.delta for c in dearer_orders)
            when = date.fromisoformat(effect.buy_before[:10])
            found.append(Insight(
                "warning",
                f"להזמין מ{name(latest.supplier)} לפני ⁦{when:%d/%m/%Y}⁩",
                f"המחירון החדש נכנס לתוקף בעוד ⁦{(when - today).days}⁩ ימים. הזמנת החומר "
                f"לפרויקטים שנסגרו (אם עוד לא הזמנתם) חוסכת לפחות ⁦{saving:,.0f} ₪⁩: "
                + _names(dearer_orders, lambda c: f"⁦{c.job.job_id}⁩")
                + ". בדרך כלל המחיר הישן תקף להזמנה שבוצעה (ולפעמים שולמה) לפני "
                "התאריך — ושווה לשקול מול המזומנים שיתפסו עד שהלקוח משלם.",
                "Projects", f"price-order:{latest.edition_id}",
            ))
    return found


def _material(context: dict[str, Any], today: date) -> list[Insight]:
    """Won jobs nobody ordered material for, and material that has not come."""
    found: list[Insight] = []
    jobs = context.get("jobs") or []
    waiting: list[Any] = []
    late: list[tuple[Any, Any]] = []
    for job in jobs:
        status = job.status.value
        if status not in ("won", "in_production"):
            continue
        openings = getattr(getattr(job, "schedule", None), "openings", None)
        # Won and not yet ordered. A job already in production had its
        # material bought somehow — this software just was not told.
        if status == "won" and openings and not job.material_ordered:
            since = _days_since(_status_since(job), today)
            if since is None or since >= 2:
                waiting.append(job)
        for order in job.material_orders:
            if not order.open or not order.needed_by:
                continue
            try:
                needed = date.fromisoformat(order.needed_by[:10])
            except ValueError:
                continue
            if needed < today:
                late.append((job, order))
    # The supplier's invoice asks for more than the order came to at its
    # own price list: a price that changed, a line nobody ordered.
    dearer = [(job, order, order.invoice_gap or 0.0) for job in jobs
              for order in job.material_orders
              if order.overcharged and order.state.value != "cancelled"]
    # A combined order: the invoice, recorded on any job's part, against all parts.
    batches: dict[str, list[tuple[Any, Any]]] = {}
    for job in jobs:
        for order in job.material_orders:
            if order.batch and order.state.value != "cancelled":
                batches.setdefault(order.batch, []).append((job, order))
    for parts in batches.values():
        # The one invoice may have been recorded on several parts: once each.
        invoiced = sum(net for _number, net in {
            (order.invoice_number or order.order_id, order.invoice_net)
            for _job, order in parts if order.invoice_net > 0})
        total = sum(order.total for _job, order in parts)
        if invoiced <= 0 or total <= 0 or any(order.unpriced for _job, order in parts):
            continue
        if invoiced - total > total * parts[0][1].INVOICE_TOLERANCE:
            holder = next(pair for pair in parts if pair[1].invoice_net > 0)
            dearer.append((holder[0], holder[1], round(invoiced - total, 2)))
    if dearer:
        extra = sum(gap for _job, _order, gap in dearer)
        found.append(Insight(
            "warning",
            _count(len(dearer), "חשבונית ספק גבוהה מההזמנה", "חשבוניות ספק גבוהות מההזמנות"),
            f"בסך ⁦{extra:,.0f} ₪⁩ מעל המחירון שלפיו הוזמן — כדאי לבדוק לפני התשלום: "
            + _names(dearer, lambda entry: f"{entry[1].supplier} "
                     + (f"(הזמנה משותפת ⁦{entry[1].batch}⁩) " if entry[1].batch
                        else f"ל⁦{entry[0].job_id}⁩ ")
                     + f"(⁦+{entry[2]:,.0f} ₪⁩)"),
            "Projects", "invoice-gap:" + ",".join(
                sorted(f"{o.order_id}={o.invoice_net:.0f}" for _j, o, _g in dearer)),
        ))
    if waiting:
        found.append(Insight(
            "warning",
            _count(len(waiting), "פרויקט שנסגר ועוד לא הוזמן לו חומר",
                   "פרויקטים שנסגרו ועוד לא הוזמן להם חומר"),
            "כל יום בלי הזמנה הוא יום בלוח הזמנים של ההתקנה: "
            + _names(waiting, lambda job: f"⁦{job.job_id}⁩ {job.name}")
            + ". בפרויקטים ← עוד פעולות ← הזמנת חומר.",
            "Projects", "material-waiting:" + ",".join(sorted(j.job_id for j in waiting)),
        ))
    if late:
        found.append(Insight(
            "warning",
            _count(len(late), "הזמנת חומר שלא הגיעה בזמן", "הזמנות חומר שלא הגיעו בזמן"),
            "כדאי להתקשר לספק: " + _names(
                late, lambda pair: f"{pair[1].supplier} ל⁦{pair[0].job_id}⁩ "
                f"(נדרש עד ⁦{date.fromisoformat(pair[1].needed_by[:10]):%d/%m}⁩)"
            ),
            "Projects", "material-late:" + ",".join(sorted(o.order_id for _j, o in late)),
        ))
    return found


def _remeasured(context: dict[str, Any], today: date) -> list[Insight]:
    """Openings measured bigger than they were sold, with no extra for it."""
    from .delivery.remeasure import accepted_revision, compare, unbilled, worth_asking
    from .projects import JobStatus

    surveys = context.get("surveys")
    revisions = context.get("revisions")
    if surveys is None or revisions is None:
        return []
    owed = []
    for job in context.get("jobs") or []:
        if job.status not in (JobStatus.WON, JobStatus.IN_PRODUCTION, JobStatus.INSTALLED):
            continue
        sheets = surveys.for_job(job.job_id)
        if not sheets:
            continue
        revision = accepted_revision(job, revisions.for_job(job.job_id))
        grown = unbilled(job, compare(job, sheets[0], revision))
        if grown and worth_asking(grown):
            owed.append((job, sum(change.amount for change in grown)))
    if not owed:
        return []
    total = sum(amount for _job, amount in owed)
    return [Insight(
        "warning",
        _count(len(owed), "פרויקט אחד נמדד גדול מההצעה ולא נוספה תוספת",
               "פרויקטים נמדדו גדולים מההצעה ולא נוספה תוספת"),
        f"לפי מחיר ההצעה עצמה — ⁦{total:,.0f} ₪⁩: "
        + _names(owed, lambda pair: f"{pair[0].customer_name or pair[0].name} "
                 f"⁦{pair[1]:,.0f} ₪⁩")
        + ". מדידה באתר ← ״השוואה להצעה״ מוסיפה תוספת לאישור הלקוח.",
        "Survey", "remeasured:" + ",".join(
            sorted(f"{job.job_id}={amount:.0f}" for job, amount in owed)),
    )]


RULES: tuple[Callable[[dict[str, Any], date], list[Insight]], ...] = (
    _data_safety, _jobs, _accounts, _field, _customers, _money, _service, _tasks,
    _paperwork, _draft, _catalogue, _price_lists, _material, _remeasured, _install_ready,
    _pace,
)


def morning_brief(found: list[Insight], context: dict[str, Any], *,
                  today: date | None = None, brand_name: str = "") -> str:
    """The owner's morning, as one WhatsApp message to themself.

    Read at the first coffee, before the phone starts: the money, the field,
    and the few things the scan found — most urgent first, no more than fit
    on a phone screen.
    """
    from .projects.account import job_account
    from .projects.agenda import day_word

    today = today or date.today()
    lines = [f"בוקר טוב ☀️ — {brand_name}".rstrip(" —"), f"⁦{today:%d/%m/%Y}⁩", ""]
    jobs = context.get("jobs") or []
    cheques = list(context.get("cheques") or [])
    owed = sum(
        job_account(job, cheques).uncovered for job in jobs
        if job.status.value in ("won", "in_production", "installed")
    )
    if owed:
        lines.append(f"💰 לגבייה בפרויקטים: ⁦{owed:,.0f} ₪⁩")
    quoted = [job for job in jobs if job.status.value == "quoted"]
    if quoted:
        lines.append(
            f"📨 הצעות פתוחות: ⁦{len(quoted)}⁩ בשווי ⁦{sum(j.quote_total for j in quoted):,.0f} ₪⁩"
        )
    agenda = context.get("agenda")
    if agenda is not None:
        visits = [a for a in agenda.on_day(today) if not a.done]
        if visits:
            lines.append(f"🚐 {day_word(today, today)} בשטח: ⁦{len(visits)}⁩ ביקורים")
            for visit in visits[:6]:
                lines.append(f"   • {visit.describe()}")
    important = [insight for insight in found if insight.severity in ("danger", "warning")]
    if important:
        lines += ["", "שווה לבדוק היום:"]
        for insight in important[:8]:
            mark = "🔴" if insight.severity == "danger" else "🟠"
            lines.append(f"{mark} {insight.title}")
    else:
        lines += ["", "✅ אין שום דבר דחוף."]
    return "\n".join(lines)


def gather_context(**given: Any) -> dict[str, Any]:
    """Read every book once. A book that cannot be read is left out."""
    from .core.config import get_settings

    context: dict[str, Any] = dict(given)
    settings = get_settings()
    context.setdefault("data_dir", settings.data_dir)
    context.setdefault("mirror", settings.backup_mirror)
    context.setdefault("settings", settings)

    def load(key: str, reader: Callable[[], Any]) -> None:
        if key in context:
            return
        try:
            context[key] = reader()
        except Exception as exc:  # noqa: BLE001 - one book, not the whole list
            _log.warning("Insights could not read %s: %s", key, exc)
            context.setdefault("unread", []).append(key)
            return
        if getattr(context[key], "damaged", None) is not None:
            context.setdefault("unread", []).append(key)

    def store() -> Any:
        from .projects import default_store

        return default_store()

    load("store", store)
    if "jobs" not in context and context.get("store") is not None:
        load("jobs", context["store"].all)

    def customers() -> Any:
        from .projects import default_customers

        return default_customers().all()

    def tasks() -> Any:
        from .projects.followups import default_tasks

        return default_tasks()

    def cheques() -> Any:
        from .erp.collection import default_cheques

        return default_cheques()

    def service() -> Any:
        from .service.register import default_register

        return default_register()

    def agenda() -> Any:
        from .projects.agenda import default_agenda

        return default_agenda()

    def editions() -> Any:
        from .catalogue.editions import default_editions

        return default_editions()

    def findings() -> Any:
        from .catalogue.watch import default_findings

        return default_findings()

    def watch() -> Any:
        from .catalogue.watch import default_watch

        return default_watch()

    def confirmations() -> Any:
        from .systems import default_confirmations

        return default_confirmations()

    load("agenda", agenda)
    load("editions", editions)
    load("findings", findings)
    load("watch", watch)
    def stocktakes() -> Any:
        from .erp.stocktake import default_stocktakes

        return default_stocktakes()

    def price_lists() -> Any:
        from .quoting.pricelists import default_price_lists

        return default_price_lists()

    def surveys() -> Any:
        from .delivery.survey import default_surveys

        return default_surveys()

    def revisions() -> Any:
        from .quoting.revisions import default_revisions

        return default_revisions()

    def timebook() -> Any:
        from .erp.timesheets import default_timebook

        return default_timebook()

    load("timebook", timebook)
    load("surveys", surveys)
    load("revisions", revisions)
    load("confirmations", confirmations)
    load("price_lists", price_lists)
    load("stocktakes", stocktakes)
    load("customers", customers)
    load("tasks", tasks)
    load("cheques", cheques)
    load("service", service)
    return context


def scan(today: date | None = None, **given: Any) -> list[Insight]:
    """Everything worth a look today, most urgent first."""
    today = today or date.today()
    context = gather_context(**given)
    found: list[Insight] = []
    for rule in RULES:
        try:
            found.extend(rule(context, today))
        except Exception as exc:  # noqa: BLE001 - a broken rule hides itself, not the rest
            _log.warning("Insight rule %s failed: %s", rule.__name__, exc)
    for key in context.get("unread", []):
        found.append(Insight(
            "danger", "חלק מהנתונים לא נקרא",
            f"לא ניתן היה לקרוא את {_BOOK_NAMES.get(key, key)} — הקובץ לא נדרס. "
            "בדקו במסך מערכת ← גיבוי ושחזור.", "System", f"unread:{key}:{today}",
        ))
    found.sort(key=lambda insight: _ORDER.get(insight.severity, 3))
    return found


def _dismissed_path() -> Any:
    from .core.config import get_settings

    return get_settings().data_dir / "dismissed_insights.json"


def dismissed() -> set[str]:
    """Insights somebody has already seen and put aside."""
    from .core.storage import read_json

    try:
        return set(read_json(_dismissed_path(), default=[]) or [])
    except Exception:  # noqa: BLE001 - worst case, a seen line shows again
        return set()


def _dismissed_strict() -> set[str]:
    from .core.storage import read_json

    return set(read_json(_dismissed_path(), default=[]) or [])


def dismiss(key: str) -> None:
    """Put one insight aside. Its key changes when what it is about changes —
    a new late quotation brings the line back — so nothing is hidden for good.
    """
    from .core.storage import write_json

    # Read strictly: a list that cannot be read is not rewritten as one line.
    keys = _dismissed_strict() | {key}
    write_json(_dismissed_path(), sorted(keys)[-500:])


_BOOK_NAMES = {
    "store": "תיקי העבודה", "jobs": "תיקי העבודה", "customers": "ספר הלקוחות",
    "tasks": "משימות המעקב", "cheques": "פנקס הצ׳קים", "service": "קריאות השירות",
    "agenda": "יומן הביקורים", "editions": "מהדורות הקטלוג",
    "findings": "ממצאי מעקב הספקים", "watch": "מקורות הספקים במעקב",
    "confirmations": "נתוני היצרן שאושרו לחיתוך", "stocktakes": "ספירות המלאי",
    "price_lists": "מחירוני הספקים",
    "surveys": "גיליונות המדידה", "revisions": "גרסאות ההצעות",
    "timebook": "דיווחי השעות",
}


__all__ = [
    "Insight", "RULES", "dismiss", "dismissed", "gather_context", "morning_brief", "scan",
]
