"""The order that goes to the glazier, and what it must not get wrong.

An insulating unit is made to the size on the order. It cannot be recut, it
cannot be trimmed on site, and a pane ⁦4⁩ mm too big is a pane the shop pays
for twice. Of everything a fabricator buys, this is the item where a mistake
is least recoverable — and it is ordered on a fax-shaped document that in most
shops is retyped from a cutting list by hand.

So this document is generated from the same panes the machining came from, and
it refuses to guess in exactly one place: the pane size. A pane is the daylight
opening plus the amount the bead covers, less the edge clearance the system
specifies — two figures that belong to the system's catalogue. Where they have
not been confirmed, the order prints the panes with the sizes blank and a
banner saying so, rather than printing a number the glazier will cut to.

Everything else it does is what a good glazier asks for on the phone anyway:
which panes must be toughened and why, the make-up spelled out rather than
coded, the area totalled by make-up so the order can be priced, the mass of
each pane so the shop knows what needs two people, and the handing of stepped
units — because a stepped unit made the wrong way round is a pane made twice.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("glazing.order")

#: Above this mass one person should not be lifting a pane alone. It is a
#: handling figure, not a regulation, and it is here as one number to argue
#: with rather than a rule of thumb in somebody's head.
TWO_PERSON_LIFT_KG = 25.0

#: Above this, no two people are carrying it either.
LIFTING_GEAR_KG = 60.0


@dataclass
class OrderedPane:
    """One pane on the order, with everything the glazier needs to make it."""

    mark: str = ""
    #: Ordered size [mm]. ``None`` when the system's figures are unconfirmed —
    #: which is the one thing this document will not invent.
    width: float | None = None
    height: float | None = None
    quantity: int = 1
    #: The make-up in words, not a code: "6 מחוסם / 16 ארגון / 6:6.6 טרי".
    build_up: str = ""
    thickness: float | None = None
    mass_each: float | None = None
    toughened: bool = False
    laminated: bool = False
    #: Why safety glass is required here, in the words that would be used to
    #: justify it to a building inspector.
    safety_reason: str = ""
    #: Which way round a stepped or handed unit goes.
    handing: str = ""
    edge_work: str = ""
    note: str = ""

    @property
    def area(self) -> float:
        if self.width is None or self.height is None:
            return 0.0
        return round(self.width * self.height / 1_000_000.0, 4)

    @property
    def total_area(self) -> float:
        return round(self.area * max(1, self.quantity), 4)

    @property
    def is_sized(self) -> bool:
        return self.width is not None and self.height is not None

    @property
    def lift(self) -> str:
        """What it takes to carry one of these."""
        if self.mass_each is None:
            return ""
        if self.mass_each > LIFTING_GEAR_KG:
            return "ציוד הרמה"
        if self.mass_each > TWO_PERSON_LIFT_KG:
            return "שני אנשים"
        return ""

    def describe(self) -> str:
        size = (
            f"⁦{self.width:g}×{self.height:g}⁩"
            if self.is_sized else "מידה חסרה"
        )
        body = f"{self.mark or '—'} · {size} · ⁦{self.quantity}⁩ יח׳"
        if self.build_up:
            body += f" · {self.build_up}"
        if self.toughened:
            body += " · מחוסם"
        return body

    def as_dict(self) -> dict[str, Any]:
        return {
            "mark": self.mark, "width": self.width, "height": self.height,
            "quantity": self.quantity, "build_up": self.build_up,
            "thickness": self.thickness, "mass_each": self.mass_each,
            "toughened": self.toughened, "laminated": self.laminated,
            "safety_reason": self.safety_reason, "handing": self.handing,
            "edge_work": self.edge_work, "note": self.note,
        }


@dataclass
class GlassOrder:
    """One order to one glazier."""

    order_id: str = field(default_factory=lambda: f"GL-{uuid4().hex[:6].upper()}")
    job_id: str = ""
    job_name: str = ""
    supplier: str = ""
    issued: date = field(default_factory=date.today)
    wanted_by: date | None = None
    deliver_to: str = ""
    panes: list[OrderedPane] = field(default_factory=list)
    #: Set when the sizes could not be derived, so nothing here may be cut to.
    sizes_are_provisional: bool = False
    provisional_reason: str = ""
    note: str = ""

    # -- totals ---------------------------------------------------------------- #
    @property
    def pane_count(self) -> int:
        return sum(max(1, pane.quantity) for pane in self.panes)

    @property
    def total_area(self) -> float:
        return round(sum(pane.total_area for pane in self.panes), 2)

    @property
    def total_mass(self) -> float:
        return round(
            sum(
                (pane.mass_each or 0.0) * max(1, pane.quantity)
                for pane in self.panes
            ),
            1,
        )

    def by_build_up(self) -> list[dict[str, Any]]:
        """Area and count per make-up, which is how a glazier prices it."""
        totals: dict[str, dict[str, Any]] = {}
        for pane in self.panes:
            key = pane.build_up or "—"
            row = totals.setdefault(
                key, {"build_up": key, "panes": 0, "area": 0.0, "mass": 0.0}
            )
            row["panes"] += max(1, pane.quantity)
            row["area"] = round(row["area"] + pane.total_area, 4)
            row["mass"] = round(
                row["mass"] + (pane.mass_each or 0.0) * max(1, pane.quantity), 1
            )
        return sorted(totals.values(), key=lambda row: row["area"], reverse=True)

    @property
    def toughened_panes(self) -> list[OrderedPane]:
        return [pane for pane in self.panes if pane.toughened]

    @property
    def heavy_panes(self) -> list[OrderedPane]:
        return [pane for pane in self.panes if pane.lift]

    # -- checking --------------------------------------------------------------- #
    def problems(self) -> list[str]:
        """Everything that would make this order the wrong glass."""
        found: list[str] = []
        if not self.panes:
            return ["אין שמשות בהזמנה"]
        if not self.supplier.strip():
            found.append("לא נבחר ספק זכוכית")

        unsized = [pane for pane in self.panes if not pane.is_sized]
        if unsized:
            found.append(
                f"⁦{len(unsized)}⁩ שמשות בלי מידה — הכיסוי או מרווח הקצה של "
                "הסדרה לא אושרו, ובלעדיהם אין מידת הזמנה"
            )
        if self.sizes_are_provisional:
            found.append(
                "המידות זמניות ואינן להזמנה" +
                (f" — {self.provisional_reason}" if self.provisional_reason else "")
            )
        for pane in self.panes:
            if pane.safety_reason and not (pane.toughened or pane.laminated):
                found.append(
                    f"{pane.mark or '—'}: נדרשת זכוכית בטיחות ({pane.safety_reason}) "
                    "והמפרט אינו בטיחותי"
                )
        if self.wanted_by is not None and self.wanted_by <= self.issued:
            found.append("מועד האספקה המבוקש אינו אחרי תאריך ההזמנה")
        return found

    @property
    def may_be_sent(self) -> bool:
        """Whether this may go to a glazier. Nothing else in this file decides it."""
        return not self.problems()

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("הזמנת זכוכית", self.order_id),
            ("תיק", f"{self.job_id} · {self.job_name}"),
            ("ספק", self.supplier or "—"),
            ("תאריך", f"⁦{self.issued.strftime('%d/%m/%Y')}⁩"),
            (
                "נדרש עד",
                f"⁦{self.wanted_by.strftime('%d/%m/%Y')}⁩"
                if self.wanted_by else "—",
            ),
            ("שמשות", f"⁦{self.pane_count}⁩"),
            ("שטח", f"⁦{self.total_area:,.2f}⁩ מ״ר"),
            ("משקל", f"⁦{self.total_mass:,.0f}⁩ ק״ג"),
            ("מתוכן מחוסמות", f"⁦{len(self.toughened_panes)}⁩"),
        ]

    def describe(self) -> str:
        return (
            f"⁦{self.order_id}⁩ · {self.supplier or 'ללא ספק'} · "
            f"⁦{self.pane_count}⁩ שמשות · ⁦{self.total_area:,.2f}⁩ מ״ר"
            + ("" if self.may_be_sent else " · לא לשליחה")
        )


def order_from_builds(
    builds: Iterable[Any],
    *,
    job_id: str = "",
    job_name: str = "",
    supplier: str = "",
    wanted_by: date | None = None,
    sizes_confirmed: bool = True,
    provisional_reason: str = "",
) -> GlassOrder:
    """Build a glass order from the panes the element builder produced.

    ``sizes_confirmed`` is the caller's statement that the glazing rebate and
    edge clearance behind these sizes came from the system's own catalogue. It
    defaults to true because the builder will not have produced a pane without
    rules — but when a series is being priced on typical figures, passing
    false is what keeps the order from becoming a cutting instruction.
    """
    order = GlassOrder(
        job_id=job_id, job_name=job_name, supplier=supplier,
        wanted_by=wanted_by,
        sizes_are_provisional=not sizes_confirmed,
        provisional_reason=provisional_reason,
    )

    from ..elements.feasibility import hebrew_safety_reason

    shaped_marks: list[str] = []

    for build in builds:
        element = getattr(build, "opening", None) or getattr(build, "element", None)
        prefix = str(getattr(element, "name", "") or "")
        if getattr(element, "is_shaped", False):
            shaped_marks.append(prefix or "—")
        # An element ordered four times needs four sets of panes. The builder
        # works out one element; the quantity lives on the opening, and losing
        # it here is a job that arrives three windows short.
        copies = max(1, int(getattr(element, "quantity", 1) or 1))
        for index, panel in enumerate(getattr(build, "glass", []) or [], start=1):
            build_up = getattr(panel, "build_up", None)
            order.panes.append(OrderedPane(
                mark=str(getattr(panel, "mark", "") or f"{prefix}/{index}"),
                width=(
                    round(float(panel.width), 1)
                    if sizes_confirmed and panel.width else None
                ),
                height=(
                    round(float(panel.height), 1)
                    if sizes_confirmed and panel.height else None
                ),
                quantity=int(getattr(panel, "quantity", 1) or 1) * copies,
                build_up=(
                    build_up.describe() if build_up is not None else ""
                ),
                thickness=(
                    round(float(build_up.total_thickness), 1)
                    if build_up is not None else None
                ),
                mass_each=(
                    round(float(panel.mass), 1)
                    if build_up is not None else None
                ),
                toughened=bool(
                    build_up is not None and build_up.is_safety_glass
                ),
                laminated=bool(
                    build_up is not None
                    and any(
                        getattr(pane, "interlayer", None)
                        for pane in getattr(build_up, "panes", [])
                    )
                ),
                safety_reason=hebrew_safety_reason(
                    getattr(panel, "safety_reason", "")
                ),
            ))

    if shaped_marks:
        # A glazier sent a width and a height for an arched head sends back a
        # rectangle. Saying so on the order is cheaper than saying it after.
        from ..elements.shapes import SHAPED_GLASS_NOTE

        order.note = " · ".join(
            part for part in (
                order.note,
                f"פתחים מעוצבים ({', '.join(sorted(set(shaped_marks)))}): "
                + SHAPED_GLASS_NOTE,
            ) if part
        )
        for pane in order.panes:
            if any(pane.mark.startswith(mark) for mark in shaped_marks):
                pane.note = (pane.note + " · " if pane.note else "") + "לפי תבנית"

    _log.info(
        "Glass order %s built for %s: %d panes, %.2f m2",
        order.order_id, job_id or "-", order.pane_count, order.total_area,
    )
    return order


# --------------------------------------------------------------------------- #
# The printed order
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_glass_order(order: GlassOrder, *, company: str = "") -> str:
    """The document that goes to the glazier, in Hebrew, ready to print."""
    from ..branding import active_brand

    brand = company or active_brand().display_name

    head_rows = "".join(
        f"<div><dt>{_esc(label)}</dt><dd>{_esc(value)}</dd></div>"
        for label, value in order.summary_rows()
    )

    body_rows = []
    for pane in order.panes:
        marks = []
        if pane.toughened:
            marks.append("מחוסם")
        if pane.laminated:
            marks.append("טרי")
        if pane.handing:
            marks.append(pane.handing)
        if pane.lift:
            marks.append(pane.lift)
        body_rows.append(
            "<tr>"
            f"<td>{_esc(pane.mark)}</td>"
            f"<td class='num'>{_esc(f'{pane.width:g}') if pane.width else '—'}</td>"
            f"<td class='num'>{_esc(f'{pane.height:g}') if pane.height else '—'}</td>"
            f"<td class='num'>{pane.quantity}</td>"
            f"<td>{_esc(pane.build_up)}</td>"
            f"<td class='num'>{_esc(f'{pane.area:.3f}') if pane.is_sized else '—'}</td>"
            f"<td class='num'>"
            f"{_esc(f'{pane.mass_each:.1f}') if pane.mass_each else '—'}</td>"
            f"<td>{_esc(' · '.join(marks))}</td>"
            f"<td>{_esc(pane.safety_reason or pane.note)}</td>"
            "</tr>"
        )

    totals = "".join(
        "<tr>"
        f"<td>{_esc(row['build_up'])}</td>"
        f"<td class='num'>{row['panes']}</td>"
        f"<td class='num'>{row['area']:.3f}</td>"
        f"<td class='num'>{row['mass']:.1f}</td>"
        "</tr>"
        for row in order.by_build_up()
    )

    problems = order.problems()
    banner = ""
    if problems:
        banner = (
            '<div class="stop"><strong>לא לשליחה</strong><ul>'
            + "".join(f"<li>{_esc(problem)}</li>" for problem in problems)
            + "</ul></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>הזמנת זכוכית {_esc(order.order_id)}</title>
<style>{_ORDER_CSS}</style>
</head><body><div class="wrap">
<header>
  <div><h1>הזמנת זכוכית</h1>
  <div class="sub">{_esc(brand)} → {_esc(order.supplier or 'ספק לא נבחר')}</div></div>
  <div class="id">{_esc(order.order_id)}</div>
</header>
{banner}
<dl class="meta">{head_rows}</dl>
<h2>שמשות</h2>
<table><thead><tr>
  <th>סימון</th><th>רוחב</th><th>גובה</th><th>כמות</th><th>הרכב</th>
  <th>מ״ר</th><th>ק״ג ליחידה</th><th>סימונים</th><th>הערות</th>
</tr></thead><tbody>{"".join(body_rows)}</tbody></table>
<h2>סיכום לפי הרכב</h2>
<table><thead><tr>
  <th>הרכב</th><th>שמשות</th><th>מ״ר</th><th>ק״ג</th>
</tr></thead><tbody>{totals}</tbody></table>
<footer>
  המידות הן מידות הזמנה סופיות ליחידה מוגמרת. שמשה מבודדת אינה ניתנת
  לחיתוך מחדש — כל שינוי במידה מחייב הזמנה חדשה.
  {_esc(order.note)}
</footer>
</div></body></html>"""


_ORDER_CSS = """
:root { --ink:#101828; --muted:#5b6472; --line:#e4e7ec; --panel:#f7f9fc;
        --stop:#8a1c1c; --stop-bg:#fdecec; }
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:var(--ink);
       font-family:"Heebo","Segoe UI",system-ui,sans-serif; font-size:15px; }
.wrap { max-width:1000px; margin:0 auto; padding:24px; }
header { display:flex; justify-content:space-between; align-items:flex-start;
         border-bottom:3px solid var(--ink); padding-bottom:12px; }
h1 { margin:0; font-size:26px; letter-spacing:-0.02em; }
h2 { font-size:17px; margin:26px 0 8px; padding-bottom:5px;
     border-bottom:2px solid var(--line); }
.sub { color:var(--muted); font-size:14px; margin-top:4px; }
.id { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:8px; margin:16px 0 0; padding:0; }
.meta div { background:var(--panel); border-radius:8px; padding:8px 10px; }
.meta dt { margin:0; font-size:11px; letter-spacing:.05em; color:var(--muted); }
.meta dd { margin:2px 0 0; font-size:16px; font-weight:600; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:right; padding:8px 6px; border-bottom:1px solid var(--line); }
th { font-size:11px; letter-spacing:.05em; color:var(--muted);
     background:var(--panel); }
td.num { text-align:left; font-variant-numeric:tabular-nums; }
.stop { background:var(--stop-bg); border-right:5px solid var(--stop);
        border-radius:0 8px 8px 0; padding:10px 14px; margin:16px 0;
        color:var(--stop); }
.stop ul { margin:6px 0 0; padding-inline-start:18px; }
footer { margin-top:26px; padding-top:12px; border-top:1px solid var(--line);
         color:var(--muted); font-size:13px; }
@media print { .wrap { max-width:none; padding:0; } tr { break-inside:avoid; } }
"""


def write_glass_order(order: GlassOrder, path: Any, **kwargs: Any) -> Any:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_glass_order(order, **kwargs), encoding="utf-8")
    return target


__all__ = [
    "LIFTING_GEAR_KG",
    "TWO_PERSON_LIFT_KG",
    "GlassOrder",
    "OrderedPane",
    "order_from_builds",
    "render_glass_order",
    "write_glass_order",
]
