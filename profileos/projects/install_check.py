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

    missing: list[str] = []
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


__all__ = ["READY_STAGES", "not_ready"]
