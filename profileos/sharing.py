"""Reaching the customer where the customer actually is.

The packages this one is measured against stop at a PDF on the office
computer. An Israeli fabricator's customer is on WhatsApp: the quotation is
sent there, the "your windows went into production" message is sent there,
and the follow-up call is often a voice note there. Nothing in the trade's
software meets them on that ground, so this module does.

It does not talk to WhatsApp's servers and holds no account: it builds a
``wa.me`` link with the message already written, which opens the chat on
whatever device the shop is using. The person still presses send. A message
the shop did not see before it went is not a message this software sends.

Phone numbers are normalised the way they are written in Israel — ``054-...``,
``+972 54 ...``, ``972-54-...`` — into the international form the link needs.
A number that cannot be read is reported as such rather than guessed at.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

#: Israeli numbers: mobile ``05X`` + seven digits, landline ``0[2-9]`` + seven.
_LOCAL = re.compile(r"^0(5\d|[2-4689]|7\d)\d{7}$")


def whatsapp_number(phone: str | None) -> str | None:
    """The number in ``972XXXXXXXXX`` form, or ``None`` if it is not one.

    >>> whatsapp_number("054-123-4567")
    '972541234567'
    >>> whatsapp_number("+972 54 123 4567")
    '972541234567'
    >>> whatsapp_number("12") is None
    True
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("972"):
        local = "0" + digits[3:]
    elif digits.startswith("0"):
        local = digits
    else:
        return None
    if not _LOCAL.match(local):
        return None
    return "972" + local[1:]


def whatsapp_link(phone: str | None, message: str) -> str:
    """A ``wa.me`` link that opens the chat with ``message`` already typed.

    Without a usable number the link still works: WhatsApp asks which chat
    to send it to, which is better than refusing to help.
    """
    number = whatsapp_number(phone)
    text = quote(message, safe="")
    return f"https://wa.me/{number}?text={text}" if number else f"https://wa.me/?text={text}"


def _greeting(customer_name: str) -> str:
    name = (customer_name or "").strip()
    return f"שלום {name}," if name else "שלום,"


def quote_message(
    *,
    brand_name: str,
    customer_name: str = "",
    job_name: str = "",
    quote_id: str = "",
    total: float | None = None,
    currency: str = "ILS",
    valid_days: int | None = 30,
    phone: str = "",
) -> str:
    """The message that goes with a quotation."""
    symbol = "₪" if currency.upper() == "ILS" else currency
    lines = [_greeting(customer_name), ""]
    subject = f"עבור {job_name}" if job_name else ""
    lines.append(f"מצורפת הצעת המחיר {subject}".rstrip() + ".")
    if quote_id:
        lines.append(f"מספר הצעה: {quote_id}")
    if total:
        lines.append(f"סה״כ: {total:,.0f} {symbol} כולל מע״מ")
    if valid_days:
        lines.append(f"ההצעה בתוקף {valid_days} יום.")
    lines += ["", "נשמח לענות על כל שאלה.", brand_name]
    if phone:
        lines.append(phone)
    return "\n".join(lines)


#: What the customer is told at each stage, keyed by job status value.
_STATUS_LINES: dict[str, str] = {
    "enquiry": "קיבלנו את הפנייה שלך ונחזור אליך בהקדם עם הצעת מחיר.",
    "quoted": "שלחנו אליך הצעת מחיר. נשמח לשמוע אם יש שאלות.",
    "won": "תודה על ההזמנה! ההזמנה נקלטה ותיכנס לתכנון הייצור.",
    "in_production": "רצינו לעדכן שהחלונות שלך נכנסו לייצור.",
    "installed": "ההתקנה הושלמה. תודה שבחרת בנו — ואם משהו לא תקין, אנחנו כאן.",
    "lost": "תודה על הפנייה. אם תרצה לחזור לזה בעתיד, נשמח לעזור.",
}


def status_message(
    *,
    brand_name: str,
    status: str,
    customer_name: str = "",
    job_name: str = "",
    phone: str = "",
    extra: str = "",
) -> str:
    """A short status update for the customer, in the shop's voice."""
    line = _STATUS_LINES.get(status, "רצינו לעדכן אותך על מצב ההזמנה.")
    lines = [_greeting(customer_name), ""]
    if job_name:
        lines.append(f"לגבי {job_name}:")
    lines.append(line)
    if extra:
        lines.append(extra)
    lines += ["", brand_name]
    if phone:
        lines.append(phone)
    return "\n".join(lines)


def material_list_message(
    rows: Any, *, brand_name: str, job_name: str = "", site: str = "",
    needed_by: str = "",
) -> str:
    """A materials order for the supplier, as one WhatsApp message.

    ``rows`` are ``(kind, description, quantity, unit, note)`` — the shape a
    quantity take-off gives. Read down a phone screen: one item a line, the
    quantity first, because that is what the person at the counter picks by.
    """
    lines = ["שלום,", "", f"הזמנת חומרים מ{brand_name}" + (f" — {job_name}" if job_name else "")]
    if site:
        lines.append(f"לאתר: {site}")
    if needed_by:
        lines.append(f"נדרש עד: ⁦{needed_by}⁩")
    lines.append("")
    count = 0
    for _kind, description, quantity, unit, note in rows:
        if not str(quantity).strip() or str(quantity).strip() in ("0", "0.0"):
            continue
        count += 1
        text = f"• ⁦{quantity}⁩ {unit} — {description}"
        if note:
            text += f" ({note})"
        lines.append(text)
    if not count:
        lines.append("(אין פריטים)")
    lines += ["", "תודה, נא לאשר זמינות ומחיר."]
    return "\n".join(lines)


def on_the_way_message(*, brand_name: str, customer_name: str = "", minutes: int = 0,
                       technician: str = "") -> str:
    """'We are on our way' — the message that saves the call asking where we are."""
    who = f"{technician} מ{brand_name}" if technician else brand_name
    eta = f" — נגיע בעוד כ־⁦{minutes}⁩ דקות" if minutes else ""
    return "\n".join([_greeting(customer_name), "", f"{who} בדרך אליך{eta}.", "", brand_name])


def visit_reminder_message(*, brand_name: str, customer_name: str = "", when: str = "",
                           what: str = "", phone: str = "", day: str = "מחר") -> str:
    """The evening-before reminder: the visit, the time, and how to move it."""
    lines = [_greeting(customer_name), "", f"תזכורת: {day} {what or 'אנחנו מגיעים אליך'}"
             + (f" ב־⁦{when}⁩" if when else "") + ".",
             "אם הזמן לא מתאים, אפשר לענות להודעה הזאת ונתאם מחדש.", "", brand_name]
    if phone:
        lines.append(phone)
    return "\n".join(lines)


def waze_link(address: str) -> str:
    """A link that opens Waze driving to the address."""
    return f"https://waze.com/ul?q={quote(address.strip(), safe='')}&navigate=yes"


def crew_plan_message(visits: Any, *, day: str, brand_name: str) -> str:
    """The day's visits for the crew: in order, each with a Waze link.

    The crew reads it in the van at seven; every line answers "where, when,
    who to call, what to bring", and the link starts the navigation.
    """
    lines = [f"תוכנית עבודה {day} — {brand_name}", ""]
    count = 0
    for visit in visits:
        count += 1
        when = f"⁦{visit.time}⁩ " if visit.time else ""
        who = visit.customer_name or visit.job_id or ""
        lines.append(f"{count}. {when}{visit.kind.hebrew} — {who}".rstrip(" —"))
        if visit.address:
            lines.append(f"   📍 {visit.address}")
            lines.append(f"   {waze_link(visit.address)}")
        if visit.phone:
            lines.append(f"   ☎ ⁦{visit.phone}⁩")
        if visit.note:
            lines.append(f"   {visit.note}")
        if visit.crew:
            lines.append(f"   מי: {visit.crew}")
        lines.append("")
    if not count:
        lines.append("אין ביקורים ביומן.")
    return "\n".join(lines).rstrip()


def checkup_message(*, brand_name: str, customer_name: str = "", trade: str = "aluminium",
                    job_name: str = "") -> str:
    """A year after handover: offer the yearly check, in the trade's terms."""
    what = (
        "בדיקה שנתית למערכת: דוד המים החמים, מסננים, ברזים וניקוזים"
        if trade == "plumbing" else
        "בדיקה שנתית לחלונות ולדלתות: מסילות, אטמים, ניקוז וכיוון כנפיים"
    )
    lines = [
        _greeting(customer_name), "",
        f"עברה שנה מאז שסיימנו{(' את ' + job_name) if job_name else ''}.",
        f"אנחנו מציעים {what} — כדי שהכול ימשיך לעבוד כמו ביום הראשון.",
        "רוצים שנקבע ביקור? אפשר לענות להודעה הזאת.",
        "", "להסרה מרשימת התזכורות — השיבו ״הסר״.", brand_name,
    ]
    return "\n".join(lines)


def review_request_message(*, brand_name: str, customer_name: str = "",
                           link: str = "") -> str:
    """After handover: thank the customer and ask for a review, once."""
    lines = [
        _greeting(customer_name), "",
        f"תודה שבחרתם ב{brand_name}. מקווים שאתם נהנים מהעבודה.",
        "אם הייתם מרוצים, נשמח מאוד אם תכתבו לנו המלצה קצרה — זה עוזר לנו יותר מכל פרסום:",
    ]
    if link:
        lines.append(link)
    lines += ["", "ואם משהו לא בסדר — פשוט ענו להודעה הזאת ונטפל.", brand_name]
    return "\n".join(lines)


def catalogue_change_message(edition: Any, diff: Any, *, brand_name: str,
                             jobs: Any = (), hits: Any = (), stock: Any = ()) -> str:
    """A new supplier catalogue, for the shop floor and the office.

    What the foreman needs before the next bar is cut: which articles moved
    and by how much, which are gone, which open jobs are on the series, and
    whether the deductions the saw works to have to be read again.
    """
    lines = [f"עדכון קטלוג — {edition.label}", edition.source]
    if getattr(edition, "revision", ""):
        lines.append(f"מהדורת הספק: {edition.revision}")
    lines.append("")
    lines.append(diff.summary())
    if diff.changed:
        lines += ["", "השתנו:"]
        lines += [f"• {change.describe()}" for change in diff.changed[:12]]
        if len(diff.changed) > 12:
            lines.append(f"ועוד ⁦{len(diff.changed) - 12}⁩")
    if diff.removed:
        lines += ["", "לא נמצאו בקטלוג החדש (לברר מול הספק):"]
        lines += [f"• ⁦{s.profile_id}⁩ {s.name}".rstrip() for s in diff.removed[:12]]
    if diff.added:
        lines += ["", f"חדשים: ⁦{len(diff.added)}⁩"]
    jobs = list(jobs)
    if jobs:
        lines += ["", "פרויקטים פתוחים על הסדרה:"]
        lines += [f"• ⁦{job.job_id}⁩ {job.name}" for job in jobs[:10]]
    hits = list(hits)
    if hits:
        lines += ["", "⚠️ לפני החיתוך הבא צריך לקרוא שוב את נתוני היצרן:"]
        lines += [f"• {hit.describe()}" for hit in hits]
    stock = list(stock)
    if stock:
        lines += ["", "במלאי מפרופילים שלא נמצאו בקטלוג החדש:"]
        lines += [f"• {left.describe()}" for left in stock]
    lines += ["", brand_name]
    return "\n".join(lines).rstrip()


def balance_message(*, brand_name: str, customer_name: str = "", job_name: str = "",
                    account: Any, reminders: int = 0, payment_note: str = "") -> str:
    """What is still owed on a finished job, laid out so it cannot be argued.

    Every figure is from the job's own records — the agreed price, the extras
    the customer approved, what came in, the cheques still to clear — so the
    customer sees the same sum the shop sees. A second reminder says it is
    one, politely; it never threatens.
    """
    def money(value: float) -> str:
        return f"⁦{value:,.0f} ₪⁩"

    lines = [_greeting(customer_name), ""]
    lines.append(
        ("תזכורת נוספת לגבי " if reminders else "לגבי ")
        + (f"העבודה ״{job_name}״:" if job_name else "העבודה שהסתיימה:")
    )
    lines.append("")
    lines.append(f"סכום העבודה כולל מע״מ: {money(account.total_gross)}")
    if account.extras_net:
        lines.append(f"  מתוכו תוספות שאושרו: {money(account.extras_net)} לפני מע״מ")
    if account.paid or account.withheld or account.cleared:
        lines.append(f"שולם: {money(account.received)}")
    if account.in_cheques:
        lines.append(f"בצ׳קים שטרם נפרעו: {money(account.in_cheques)}")
    lines.append(f"יתרה לתשלום: {money(account.uncovered)}")
    lines.append("")
    if payment_note:
        lines.append(payment_note)
        lines.append("")
    lines.append("נשמח להסדיר — ואם משהו לא ברור, רק תגידו ונעבור על זה יחד.")
    lines += ["", brand_name]
    return "\n".join(lines)


def snag_message(*, brand_name: str, customer_name: str = "", job_name: str = "",
                 open_snags: list[Any] = (), fixed_snags: list[Any] = (),
                 visit: str = "") -> str:
    """The punch list, to the customer: what is left and when it will be put
    right — or that everything was. Written down, it stops being a feeling
    that "something is still not right" and becomes a list that gets shorter."""
    def item(snag: Any) -> str:
        where = f" (⁦{snag.where}⁩)" if getattr(snag, "where", "") else ""
        return f"{snag.description}{where}"

    work = f"בעבודה ״{job_name}״" if job_name else "בעבודה"
    lines = [_greeting(customer_name), ""]
    if open_snags:
        lines.append(f"רשמנו את מה שנשאר לתקן {work}:")
        lines += [f"• {item(snag)}" for snag in open_snags]
        lines.append("")
        lines.append(f"נגיע לטפל בזה ב־⁦{visit}⁩." if visit
                     else "נתאם איתכם מועד להגיע ולטפל בזה.")
        if fixed_snags:
            lines.append("")
            lines.append("כבר תוקן:")
            lines += [f"✓ {item(snag)}" for snag in fixed_snags]
    else:
        lines.append(f"כל התיקונים {work} בוצעו:")
        lines += [f"✓ {item(snag)}" for snag in fixed_snags]
        lines.append("")
        lines.append("תודה על הסבלנות. אם משהו עדיין לא תקין — רק תגידו.")
    lines += ["", brand_name]
    return "\n".join(lines)


def cheque_message(
    *,
    brand_name: str,
    customer_name: str = "",
    amount: float,
    due: Any,
    bounced: bool = False,
    phone: str = "",
) -> str:
    """A reminder about one cheque — factual, polite, and specific."""
    when = due.strftime("%d/%m/%Y") if hasattr(due, "strftime") else str(due)
    lines = [_greeting(customer_name), ""]
    if bounced:
        lines.append(
            f"הצ׳ק על סך ⁦{amount:,.0f} ₪⁩ לתאריך ⁦{when}⁩ חזר מהבנק."
        )
        lines.append("נשמח לתאם איתך תשלום חלופי.")
    else:
        lines.append(f"תזכורת ידידותית: הצ׳ק על סך ⁦{amount:,.0f} ₪⁩ ייפרע ב־⁦{when}⁩.")
        lines.append("תודה!")
    lines += ["", brand_name]
    if phone:
        lines.append(phone)
    return "\n".join(lines)


def phone_for(*, job_id: str = "", customer_name: str = "") -> str:
    """A customer's phone from a job id, or failing that from their name."""
    try:
        from .projects import default_customers, default_store

        if job_id:
            for job in default_store().all():
                if job.job_id == job_id:
                    found = customer_phone(job)
                    if found:
                        return found
        name = (customer_name or "").strip()
        if name:
            for customer in default_customers().all():
                if customer.name.strip() == name and customer.phone:
                    return customer.phone
    except Exception:  # noqa: BLE001 - a missing number is not an error
        return ""
    return ""


def customer_phone(job: Any) -> str:
    """The phone number on file for a job's customer, or an empty string."""
    customer_id = str(getattr(job, "customer_id", "") or "")
    if not customer_id:
        return ""
    try:
        from .projects import default_customers

        customer = default_customers().get(customer_id)
    except Exception:  # noqa: BLE001 - a missing number is not an error
        return ""
    return str(getattr(customer, "phone", "") or "")


__all__ = [
    "cheque_message",
    "balance_message",
    "snag_message",
    "catalogue_change_message",
    "checkup_message",
    "crew_plan_message",
    "customer_phone",
    "material_list_message",
    "review_request_message",
    "waze_link",
    "on_the_way_message",
    "phone_for",
    "quote_message",
    "status_message",
    "visit_reminder_message",
    "whatsapp_link",
    "whatsapp_number",
]
