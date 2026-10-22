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

    won = [
        job for job in jobs
        if job.status is JobStatus.WON
        and (_days_since(_status_since(job), today) or 0) >= WON_STALE_DAYS
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
    bounced = cheques.bounced()
    if bounced:
        total = sum(cheque.amount for cheque in bounced)
        found.append(Insight(
            "danger", _count(len(bounced), "צ׳ק אחד חזר ועדיין לא טופל", "צ׳קים חזרו ועדיין לא טופלו"),
            f"בסך ⁦{total:,.0f} ₪⁩: " + _names(bounced, lambda c: c.customer or c.number),
            "Collection", "bounced:" + ",".join(sorted(c.cheque_id for c in bounced)),
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
    waiting = agenda.unreminded(day)
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

        stale = [supplier for supplier in all_suppliers() if not supplier.is_current()]
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
                     f"⁦{pair[1].uncovered:,.0f} ₪⁩"),
            "Projects", "owing:" + ",".join(
                sorted(f"{job.job_id}={account.uncovered:.0f}" for job, account in owing)
            ),
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


RULES: tuple[Callable[[dict[str, Any], date], list[Insight]], ...] = (
    _data_safety, _jobs, _accounts, _field, _customers, _money, _service, _tasks,
    _paperwork, _draft,
)


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

    load("agenda", agenda)
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
    "agenda": "יומן הביקורים",
}


__all__ = ["Insight", "RULES", "dismiss", "dismissed", "gather_context", "scan"]
