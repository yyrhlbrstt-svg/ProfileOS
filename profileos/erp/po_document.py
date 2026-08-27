"""The purchase order as the supplier actually receives it.

The engine already knows what to buy and can match the invoice against it. What
it could not do is produce the piece of paper — and a purchase order that lives
only inside the software is an order that gets placed by telephone, in metres
somebody remembered, against a price nobody wrote down.

Three things about ordering aluminium make this more than a table of lines.

Extrusion is not a commodity. A bar is an alloy, a temper, a mill length and a
finish, and a supplier who receives a code and a quantity will send whatever
that code means in **their** catalogue this season. Anything the shop knows
about those goes on the order; anything it does not know is printed as a
question rather than left blank, because a blank reads as "as usual".

Coating is billed on area and the fabricator's area and the coater's area are
never the same number. Putting the shop's own computed area on the order is
what makes the invoice arguable later.

And a price with no source is not a price. A line ordered against a figure
nobody can point at is marked on the order itself, so the person signing it
sees the exposure before the delivery arrives rather than when the invoice
does.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from ..core.logging_setup import get_logger

_log = get_logger("erp.po_document")


@dataclass
class Specification:
    """What an extrusion line has to say beyond a code and a quantity."""

    alloy: str = ""
    temper: str = ""
    #: Mill length ordered [mm]. The shop's cutting plan assumes one.
    mill_length: float | None = None
    finish: str = ""
    #: RAL or the anodising class, where the finish is bought from the mill.
    colour: str = ""
    #: Where each bar is marked, so the rack is not anonymous on arrival.
    marking: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.alloy and self.temper and self.mill_length)

    def questions(self) -> list[str]:
        """What the supplier will otherwise decide for the shop."""
        asked: list[str] = []
        if not self.alloy:
            asked.append("סגסוגת")
        if not self.temper:
            asked.append("מצב חומר (temper)")
        if not self.mill_length:
            asked.append("אורך מוט")
        return asked

    def describe(self) -> str:
        parts = [
            self.alloy, self.temper,
            f"⁦{self.mill_length:g}⁩ מ״מ" if self.mill_length else "",
            self.finish, self.colour,
        ]
        return " · ".join(part for part in parts if part)


@dataclass
class DocumentLine:
    """One line as it will be printed."""

    code: str = ""
    description: str = ""
    quantity: float = 0.0
    unit: str = "m"
    #: Price of one unit, in shekels. The ledger keeps agorot; people do not.
    unit_price: float | None = None
    specification: Specification = field(default_factory=Specification)
    #: Where the price came from. A line without one is flagged on the order.
    price_source: str = ""
    wanted_by: date | None = None
    note: str = ""

    @property
    def value(self) -> float | None:
        if self.unit_price is None:
            return None
        return round(self.quantity * self.unit_price, 2)

    @property
    def is_priced(self) -> bool:
        return self.unit_price is not None and self.unit_price > 0

    @property
    def price_is_sourced(self) -> bool:
        return bool(self.price_source.strip())

    def describe(self) -> str:
        body = f"{self.code} · ⁦{self.quantity:g}⁩ {self.unit}"
        if self.is_priced:
            body += f" · ⁦{self.unit_price:,.2f}⁩ ₪/{self.unit}"
        else:
            body += " · ללא מחיר"
        return body


@dataclass
class PurchaseDocument:
    """A purchase order, ready to be printed and sent."""

    order_id: str = ""
    supplier_name: str = ""
    supplier_reference: str = ""
    raised: date = field(default_factory=date.today)
    wanted_by: date | None = None
    deliver_to: str = ""
    for_job: str = ""
    lines: list[DocumentLine] = field(default_factory=list)
    vat_rate: float = 0.18
    terms: str = ""
    note: str = ""
    raised_by: str = ""

    # -- money ---------------------------------------------------------------- #
    @property
    def net(self) -> float:
        return round(
            sum(line.value or 0.0 for line in self.lines), 2
        )

    @property
    def vat(self) -> float:
        return round(self.net * self.vat_rate, 2)

    @property
    def gross(self) -> float:
        return round(self.net + self.vat, 2)

    @property
    def unpriced(self) -> list[DocumentLine]:
        return [line for line in self.lines if not line.is_priced]

    @property
    def unsourced(self) -> list[DocumentLine]:
        """Priced lines whose price nobody can point at."""
        return [
            line for line in self.lines
            if line.is_priced and not line.price_is_sourced
        ]

    @property
    def open_questions(self) -> list[tuple[str, list[str]]]:
        """Per line, what the supplier will otherwise decide for the shop."""
        return [
            (line.code, line.specification.questions())
            for line in self.lines
            if line.specification.questions()
        ]

    # -- checking --------------------------------------------------------------- #
    def problems(self) -> list[str]:
        found: list[str] = []
        if not self.lines:
            return ["אין שורות בהזמנה"]
        if not self.supplier_name.strip():
            found.append("לא נבחר ספק")
        if self.unpriced:
            found.append(
                f"⁦{len(self.unpriced)}⁩ שורות בלי מחיר — ההזמנה תיסגר על "
                "מה שהספק יחייב"
            )
        if self.unsourced:
            found.append(
                f"⁦{len(self.unsourced)}⁩ מחירים בלי מקור — אי אפשר יהיה "
                "לערער על החשבונית"
            )
        for code, questions in self.open_questions:
            found.append(
                f"{code}: לא נקבע {', '.join(questions)} — הספק יחליט במקומכם"
            )
        if self.wanted_by is not None and self.wanted_by <= self.raised:
            found.append("מועד האספקה המבוקש אינו אחרי תאריך ההזמנה")
        return found

    @property
    def may_be_sent(self) -> bool:
        return not self.problems()

    def summary_rows(self) -> list[tuple[str, str]]:
        return [
            ("הזמנת רכש", self.order_id),
            ("ספק", self.supplier_name or "—"),
            ("הזמנת הספק", self.supplier_reference or "—"),
            ("תאריך", f"⁦{self.raised.strftime('%d/%m/%Y')}⁩"),
            (
                "נדרש עד",
                f"⁦{self.wanted_by.strftime('%d/%m/%Y')}⁩"
                if self.wanted_by else "—",
            ),
            ("עבור תיק", self.for_job or "—"),
            ("אספקה אל", self.deliver_to or "—"),
            ("שורות", f"⁦{len(self.lines)}⁩"),
            ("לפני מע״מ", f"⁦{self.net:,.2f}⁩ ₪"),
            ("סה״כ", f"⁦{self.gross:,.2f}⁩ ₪"),
        ]

    def describe(self) -> str:
        return (
            f"⁦{self.order_id}⁩ · {self.supplier_name or 'ללא ספק'} · "
            f"⁦{len(self.lines)}⁩ שורות · ⁦{self.gross:,.0f}⁩ ₪"
            + ("" if self.may_be_sent else " · לא לשליחה")
        )


def document_from_order(
    order: Any,
    *,
    supplier_name: str = "",
    specifications: dict[str, Specification] | None = None,
    price_sources: dict[str, str] | None = None,
    descriptions: dict[str, str] | None = None,
    deliver_to: str = "",
    raised_by: str = "",
) -> PurchaseDocument:
    """Turn the engine's purchase order into the document a supplier receives.

    Prices come across converted from the ledger's minor units, because a
    supplier reading ⁦3450⁩ where the shop meant ⁦34.50⁩ is not a rounding
    error, it is a hundredfold one.
    """
    specs = specifications or {}
    sources = price_sources or {}
    names = descriptions or {}

    document = PurchaseDocument(
        order_id=str(getattr(order, "order_id", "")),
        supplier_name=supplier_name or str(getattr(order, "supplier_id", "")),
        raised=getattr(order, "raised", None) or date.today(),
        wanted_by=getattr(order, "promised", None),
        for_job=str(getattr(order, "project_id", "") or ""),
        deliver_to=deliver_to,
        raised_by=raised_by,
        note=str(getattr(order, "note", "") or ""),
    )

    for line in getattr(order, "lines", []) or []:
        price = getattr(line, "unit_price", None)
        document.lines.append(DocumentLine(
            code=str(getattr(line, "item", "")),
            description=(
                str(getattr(line, "description", "") or "")
                or names.get(str(getattr(line, "item", "")), "")
            ),
            quantity=float(getattr(line, "quantity", 0.0) or 0.0),
            unit=str(getattr(line, "unit", "m") or "m"),
            # The ledger prices in agorot; a purchase order is read by a person.
            unit_price=(
                round(float(price) / 100.0, 4)
                if price not in (None, 0) else None
            ),
            specification=specs.get(
                str(getattr(line, "item", "")), Specification()
            ),
            price_source=sources.get(str(getattr(line, "item", "")), ""),
            wanted_by=getattr(order, "promised", None),
        ))

    _log.info(
        "Purchase document %s for %s: %d lines",
        document.order_id, document.supplier_name, len(document.lines),
    )
    return document


def coating_order(
    *,
    order_id: str,
    supplier_name: str,
    finish: str,
    colour: str = "",
    area_m2: float = 0.0,
    pieces: int = 0,
    for_job: str = "",
    price_per_m2: float | None = None,
    price_source: str = "",
    wanted_by: date | None = None,
    note: str = "",
) -> PurchaseDocument:
    """An order to the coater, priced on the shop's own computed area.

    A coater bills on their measurement and a fabricator budgets on theirs, and
    the two are never the same number. Putting the shop's figure on the order
    is what makes the difference arguable rather than assumed.
    """
    document = PurchaseDocument(
        order_id=order_id, supplier_name=supplier_name,
        for_job=for_job, wanted_by=wanted_by, note=note,
    )
    document.lines.append(DocumentLine(
        code="COATING",
        description=(
            f"{finish}{' · ' + colour if colour else ''} · "
            f"⁦{pieces}⁩ פרופילים"
        ),
        quantity=round(area_m2, 3),
        unit="m²",
        unit_price=price_per_m2,
        price_source=price_source,
        specification=Specification(finish=finish, colour=colour),
        note=(
            "השטח מחושב מהיקף החתך החיצוני בלבד, ללא תאים פנימיים — "
            "זהו השטח שהאמבט נוגע בו"
        ),
    ))
    return document


# --------------------------------------------------------------------------- #
# The printed order
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def render_purchase_order(
    document: PurchaseDocument, *, company: str = ""
) -> str:
    """The order, in Hebrew, ready to print or to send as one file."""
    from ..branding import active_brand

    brand = company or active_brand().display_name

    meta = "".join(
        f"<div><dt>{_esc(label)}</dt><dd>{_esc(value)}</dd></div>"
        for label, value in document.summary_rows()
    )

    rows = []
    for line in document.lines:
        spec = line.specification.describe()
        missing = line.specification.questions()
        if missing:
            spec += (
                ("<br>" if spec else "")
                + '<span class="ask">לאישורכם: '
                + _esc(", ".join(missing)) + "</span>"
            )
        price = (
            f"{line.unit_price:,.2f}" if line.is_priced else
            '<span class="ask">ללא מחיר</span>'
        )
        if line.is_priced and not line.price_is_sourced:
            price += '<br><span class="ask">מחיר ללא מקור</span>'
        rows.append(
            "<tr>"
            f"<td>{_esc(line.code)}</td>"
            f"<td>{_esc(line.description)}<br>"
            f"<span class='muted'>{spec}</span></td>"
            f"<td class='num'>{_esc(f'{line.quantity:g}')}</td>"
            f"<td>{_esc(line.unit)}</td>"
            f"<td class='num'>{price}</td>"
            f"<td class='num'>"
            + (f"{line.value:,.2f}" if line.value is not None else "—")
            + "</td>"
            f"<td>{_esc(line.note)}</td>"
            "</tr>"
        )

    problems = document.problems()
    banner = ""
    if problems:
        banner = (
            '<div class="stop"><strong>לבדיקה לפני שליחה</strong><ul>'
            + "".join(f"<li>{_esc(problem)}</li>" for problem in problems)
            + "</ul></div>"
        )

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>הזמנת רכש {_esc(document.order_id)}</title>
<style>{_PO_CSS}</style>
</head><body><div class="wrap">
<header>
  <div><h1>הזמנת רכש</h1>
  <div class="sub">{_esc(brand)} → {_esc(document.supplier_name or 'ספק לא נבחר')}</div></div>
  <div class="id">{_esc(document.order_id)}</div>
</header>
{banner}
<dl class="meta">{meta}</dl>
<table><thead><tr>
  <th>קוד</th><th>פריט ומפרט</th><th>כמות</th><th>יחידה</th>
  <th>מחיר ליחידה</th><th>סה״כ</th><th>הערות</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table>
<table class="totals"><tbody>
  <tr><td>לפני מע״מ</td><td class="num">{document.net:,.2f} ₪</td></tr>
  <tr><td>מע״מ {document.vat_rate:.0%}</td>
      <td class="num">{document.vat:,.2f} ₪</td></tr>
  <tr class="grand"><td>סה״כ</td>
      <td class="num">{document.gross:,.2f} ₪</td></tr>
</tbody></table>
<footer>
  {_esc(document.terms)}
  {_esc(document.note)}
  <div class="sign"><span>אושר על ידי</span>
  <div class="line">{_esc(document.raised_by)}</div></div>
</footer>
</div></body></html>"""


_PO_CSS = """
:root { --ink:#101828; --muted:#5b6472; --line:#e4e7ec; --panel:#f7f9fc;
        --stop:#8a1c1c; --stop-bg:#fdecec; --ask:#8a5a00; }
* { box-sizing: border-box; }
body { margin:0; background:#fff; color:var(--ink);
       font-family:"Heebo","Segoe UI",system-ui,sans-serif; font-size:15px; }
.wrap { max-width:1000px; margin:0 auto; padding:24px; }
header { display:flex; justify-content:space-between; align-items:flex-start;
         border-bottom:3px solid var(--ink); padding-bottom:12px; }
h1 { margin:0; font-size:26px; letter-spacing:-0.02em; }
.sub { color:var(--muted); font-size:14px; margin-top:4px; }
.id { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
        gap:8px; margin:16px 0; padding:0; }
.meta div { background:var(--panel); border-radius:8px; padding:8px 10px; }
.meta dt { margin:0; font-size:11px; letter-spacing:.05em; color:var(--muted); }
.meta dd { margin:2px 0 0; font-size:16px; font-weight:600; }
table { width:100%; border-collapse:collapse; }
th,td { text-align:right; padding:8px 6px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { font-size:11px; letter-spacing:.05em; color:var(--muted);
     background:var(--panel); }
td.num { text-align:left; font-variant-numeric:tabular-nums; }
.muted { color:var(--muted); font-size:12.5px; }
.ask { color:var(--ask); font-weight:600; font-size:12.5px; }
.totals { width:auto; margin-inline-start:auto; margin-top:12px; }
.totals td { border:0; padding:4px 12px; }
.totals .grand td { border-top:2px solid var(--ink); font-weight:700;
                    font-size:17px; }
.stop { background:var(--stop-bg); border-right:5px solid var(--stop);
        border-radius:0 8px 8px 0; padding:10px 14px; margin:16px 0;
        color:var(--stop); }
.stop ul { margin:6px 0 0; padding-inline-start:18px; }
footer { margin-top:26px; padding-top:12px; border-top:1px solid var(--line);
         color:var(--muted); font-size:13px; }
.sign { margin-top:20px; max-width:260px; }
.sign span { font-size:11px; letter-spacing:.05em; }
.sign .line { border-bottom:1px solid var(--ink); min-height:32px;
              padding-top:10px; font-weight:600; color:var(--ink); }
@media print { .wrap { max-width:none; padding:0; } tr { break-inside:avoid; } }
"""


def write_purchase_order(
    document: PurchaseDocument, path: Any, **kwargs: Any
) -> Any:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_purchase_order(document, **kwargs), encoding="utf-8")
    return target


__all__ = [
    "DocumentLine",
    "PurchaseDocument",
    "Specification",
    "coating_order",
    "document_from_order",
    "render_purchase_order",
    "write_purchase_order",
]
