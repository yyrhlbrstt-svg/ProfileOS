"""Is the job really ready to be installed on the day it is booked?

A wasted installation day is two fitters, a truck and sometimes a crane —
and a customer who took the day off. It is almost always something the
shop's own records already knew: the glass that has not come, three sashes
never glazed, the colour the customer never confirmed, the deposit nobody
paid. This reads those records two working days ahead and names what is
missing, while there is still time to fix it or move the day.

Only what the records say is said. A shop that does not scan its production
is not told its frames are unfinished; a job with no material orders is told
that no order was recorded, not that the material is missing.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Stages at which a piece is finished as far as the site is concerned.
READY_STAGES = ("inspected", "shipped", "scrapped")


def not_ready(job: Any, *, cheques: Iterable[Any] = (), work_order: Any = None) -> list[str]:
    """What stands between this job and its installation, in the shop's words."""
    from .account import job_account

    from .blindframes import windows_not_ready

    # Two stages: the windows wait for their blind frames, set and checked.
    missing: list[str] = list(windows_not_ready(job))
    orders = [o for o in getattr(job, "material_orders", []) if o.state.value != "cancelled"]
    has_openings = bool(getattr(getattr(job, "schedule", None), "openings", None))
    if has_openings and not orders:
        missing.append("לא נרשמה הזמנת חומר")
    for order in orders:
        if order.open:
            missing.append(f"החומר מ{order.supplier} עוד לא סומן שהגיע")
    if work_order is not None and len(work_order):
        open_items = [item for item in work_order.items
                      if item.stage.value not in READY_STAGES]
        if open_items:
            missing.append(
                f"⁦{len(open_items)}⁩ מתוך ⁦{len(work_order)}⁩ פריטים ברצפת הייצור "
                "עוד לא עברו בדיקה")
    needs = [need for need in getattr(job, "waiting_for", []) if not need.received_on]
    if needs:
        missing.append("מחכים מהלקוח: " + ", ".join(need.label for need in needs[:3]))
    account = job_account(job, list(cheques))
    if account.total_gross > 0 and account.received + account.in_cheques <= 0.5:
        missing.append("לא התקבלה מקדמה ולא צ׳ק")
    return missing


#: An opening at least this large comes as a unit two people cannot carry up
#: a stairwell [m²] — the customer is asked about access.
LARGE_UNIT_M2 = 4.0


def site_questions(job: Any) -> list[str]:
    """What to ask the customer before the crew comes, from the openings
    themselves: only questions this job raises."""
    openings = list(getattr(getattr(job, "schedule", None), "openings", None) or [])
    questions: list[str] = []
    motorised = 0
    for opening in openings:
        accessories = (getattr(opening, "metadata", {}) or {}).get("accessories") or {}
        shutter = accessories.get("shutter") if isinstance(accessories, dict) else None
        if isinstance(shutter, dict) and str(shutter.get("drive", "")).startswith("motor"):
            motorised += opening.quantity
    if motorised:
        questions.append(
            f"יש נקודת חשמל מוכנה ליד כל אחד מ־⁦{motorised}⁩ התריסים החשמליים?"
            if motorised > 1 else "יש נקודת חשמל מוכנה ליד התריס החשמלי?")
    if any(opening.width * opening.height / 1e6 >= LARGE_UNIT_M2 for opening in openings):
        questions.append("יש גישה להכנסת יחידות גדולות — מעלית, מרפסת או מקום למנוף?")
    if openings:
        questions.append("הטיח והריצוף סביב הפתחים גמורים, והחדרים פנויים ליד החלונות?")
    return questions


def blind_frame_questions() -> list[str]:
    """Before blind frames: what the contractor has to have done — a frame
    set to a wrong mark is the contractor's, set without one is the shop's."""
    return [
        "קו הגובה של הקבלן (קו המטר) מסומן בכל קומה שבה מתקינים?",
        "הפתחים בנויים לפי התכנית, והבלוקים סביבם יבשים?",
        "מישהו מטעם הקבלן יהיה באתר לאשר את קווי הגובה?",
    ]


__all__ = ["LARGE_UNIT_M2", "READY_STAGES", "blind_frame_questions", "not_ready",
           "site_questions"]
