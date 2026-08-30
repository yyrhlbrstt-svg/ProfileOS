"""The quotation the client actually receives.

One self-contained HTML file: the letterhead, an elevation drawing of every
opening, the lines the display policy allows, the options side by side, VAT,
validity and payment terms. Self-contained because it is sent by email and
opened on whatever the client has — no external fonts, no scripts required to
read it, and an ``@page`` rule so the browser's own print-to-PDF produces a
clean A4 without anything being built for it.

Two documents come out of one draft, and the difference between them is the
whole reason the display policy exists:

* the **customer copy**, which shows what the policy allows and never shows
  cost or margin — those are not options that can be switched on, they simply
  are not rendered here;
* the **internal sheet**, which shows everything: the cost waterfall, labour
  hours, the operator's overrides with who made them and why, and what the
  edits did to the margin.

The elevations are the same drawings the shop works from, so the client
approves the thing that will be built, not an illustration of it.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Any

from ..core.logging_setup import get_logger
from ..i18n import get_locale, translate
from .editor import QuoteDraft

_log = get_logger("quoting.document")


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _document_css() -> str:
    """The stylesheet, built from the Paper tokens so documents and interface
    share one design system, with the Heebo face embedded so the file renders
    the same on a machine that has never seen the font."""
    from ..design.tokens import BRAND, PAPER, STATUS, font_face_css

    return (
        font_face_css(embed=True)
        + f"""
:root{{--ink:{PAPER.text};--muted:{PAPER.muted};--line:{PAPER.line};
      --line-strong:{PAPER.line_strong};--accent:__ACCENT__;
      --accent-deep:{BRAND.x600};--soft:{PAPER.tint};--paper:{PAPER.bg};
      --warn:{STATUS.warn};--warn-wash:{STATUS.warn_wash}}}
"""
        + _CSS_BODY
    )


_CSS_BODY = """
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
     font:14px/1.55 'Heebo','Segoe UI','Noto Sans Hebrew',Arial,sans-serif}
.page{max-width:820px;margin:0 auto;padding:34px 40px}
.num{font-variant-numeric:tabular-nums}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;
       border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:22px}
h1{margin:0;font-size:24px;font-weight:700;color:var(--accent-deep)}
.letterhead{font-size:12px;color:var(--muted);text-align:end;line-height:1.5}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;
      background:var(--soft);border-radius:8px;padding:12px 16px;margin-bottom:22px}
.meta b{display:block;font-size:11px;color:var(--muted);font-weight:600}
.meta span{font-size:14px}
h2{font-size:15px;margin:26px 0 8px;color:var(--accent-deep)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{border-bottom:2px solid var(--ink);padding:6px 8px;font-size:11px;color:var(--muted);
   text-transform:uppercase;letter-spacing:.03em}
td{border-bottom:1px solid var(--line);padding:8px;vertical-align:top}
th,td{text-align:start}
.n{text-align:end}
.totals{margin-top:8px;margin-inline-start:auto;width:290px}
.totals td{border:0;padding:3px 8px}
.totals tr:last-child td{border-top:2px solid var(--ink);font-weight:700;font-size:15px}
.elevations{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.elevations figure{margin:0;border:1px solid var(--line);border-radius:8px;padding:8px;
                   page-break-inside:avoid}
.elevations svg{width:100%;height:auto}
.elevations figcaption{font-size:12px;color:var(--muted);text-align:center;padding-top:4px}
.badge{display:inline-block;font-size:10px;border:1px solid var(--line);border-radius:99px;
       padding:1px 8px;color:var(--muted);margin-inline-start:6px;vertical-align:middle}
.options td:first-child{font-weight:600}
.terms{font-size:12px;color:var(--muted);border-top:1px solid var(--line);
       margin-top:28px;padding-top:12px;white-space:pre-line}
.warn{background:var(--warn-wash);border:1px solid var(--warn);border-radius:8px;
      padding:10px 14px;font-size:12px;margin:14px 0}
.internal{background:var(--soft);border:1px solid var(--line-strong);border-radius:8px;
          padding:2px 14px 10px;margin-top:26px}
.internal h2{margin-top:12px}
.muted{color:var(--muted);font-size:12px}
.parties{display:flex;justify-content:space-between;gap:24px;margin:18px 0 8px;
         font-size:13px;line-height:1.6}
.issuer{text-align:end;font-size:12px;line-height:1.6;color:var(--muted)}
.issuer strong{color:var(--ink);font-size:14px}
.totals tr.grand td{border-top:2px solid var(--ink);font-weight:700;font-size:15px}
footer{margin-top:34px;font-size:11px;color:var(--muted);text-align:center;
       display:flex;justify-content:space-between}
@media print{
  .page{max-width:none;padding:0}
  @page{size:A4;margin:16mm 14mm}
  .elevations figure{break-inside:avoid}
}
"""


def _elevation_svg(build: Any, language: Any) -> str:
    from ..drawing.elevation import ElevationStyle, elevation
    from ..drawing.svg import to_svg

    drawing = elevation(
        build,
        style=ElevationStyle(scale=20, show_glass_sizes=False, show_dimensions=True),
    )
    return to_svg(drawing, scale=20, background="#ffffff")


def render_quotation(
    draft: QuoteDraft,
    *,
    language: Any = "he",
    internal: bool = False,
    today: date | None = None,
) -> str:
    """The quotation as one printable page.

    ``internal`` adds the shop's own section — cost, margin, overrides — and is
    never set on the copy that leaves the building. It is a separate render,
    not a stylesheet trick: hidden-by-CSS numbers are still in the file, and a
    client who opens the source has then seen the margin.
    """
    from ..branding import active_brand

    locale = get_locale(language)
    brand = active_brand()
    variant = draft.variant()
    quote = draft.quotation
    display = draft.display
    lines = draft.customer_lines()
    totals = draft.totals()
    when = today or date.today()

    def t(key: str) -> str:
        return translate(key, locale.language)

    def money(value: float) -> str:
        return locale.format_money(value, "₪" if locale.code in ("he", "ar") else quote.currency)

    # Regenerate line descriptions in the document's language, except where the
    # operator wrote one — a rewritten description is theirs, verbatim.
    from ..i18n import has
    from .editor import OverrideKind

    openings = {opening.element_id: opening for opening in draft.openings}
    for row in lines:
        if (row["code"], OverrideKind.DESCRIPTION.value) in draft.overrides:
            continue
        opening = openings.get(row["code"])
        if opening is None:
            continue
        kind_key = f"element.{opening.kind.value}"
        kind = t(kind_key) if has(kind_key) else opening.kind.value.replace("_", " ")
        row["description"] = (
            f"{opening.name or opening.element_id} — {kind} "
            f"{opening.width:.0f} × {opening.height:.0f} {t('unit.mm')}"
        )

    accent = brand.colours.document_colour()
    parts: list[str] = []
    parts.append(
        f'<!doctype html><html lang="{locale.code}" dir="{"rtl" if locale.rtl else "ltr"}">'
        f'<head><meta charset="utf-8"><title>{_esc(t("quote.quotation"))} — '
        f'{_esc(draft.project_name)}</title><style>{_document_css().replace("__ACCENT__", accent)}</style></head><body>'
        '<div class="page">'
    )

    # -- letterhead ---------------------------------------------------------- #
    letterhead = "<br>".join(_esc(line) for line in brand.letterhead())
    parts.append(
        f"<header><div><h1>{_esc(t('quote.quotation'))}</h1>"
        f'<div class="num" style="color:var(--muted);font-size:13px">{_esc(quote.quote_id)}</div></div>'
        f'<div class="letterhead">{letterhead}</div></header>'
    )

    # -- meta ---------------------------------------------------------------- #
    parts.append('<div class="meta">')
    for label, value in (
        (t("drawing.project"), draft.project_name),
        (t("drawing.client"), draft.customer),
        (t("drawing.date"), locale.format_date(when)),
        (t("quote.valid_until"), locale.format_date(quote.valid_until)),
    ):
        parts.append(f"<div><b>{_esc(label)}</b><span>{_esc(value or '—')}</span></div>")
    parts.append("</div>")

    # -- elevations ------------------------------------------------------------ #
    parts.append(f"<h2>{_esc(t('drawing.elevation'))}</h2>")
    parts.append('<div class="elevations">')
    for build in variant.builds:
        opening = build.opening
        quantity = f" ×{opening.quantity}" if opening.quantity > 1 else ""
        parts.append(
            "<figure>"
            + _elevation_svg(build, locale.language)
            + f"<figcaption class='num'>{_esc(opening.name or opening.element_id)}{quantity} — "
            f"{opening.width:.0f} × {opening.height:.0f} {_esc(t('unit.mm'))}</figcaption></figure>"
        )
    parts.append("</div>")

    # -- lines ------------------------------------------------------------------ #
    parts.append(f"<h2>{_esc(t('quote.item'))}</h2><table><thead><tr>")
    headers = [t("quote.item"), t("quote.description"), t("quote.quantity")]
    if display.show_unit_prices:
        headers.append(t("quote.unit_price"))
    headers.append(t("quote.total"))
    parts.append("".join(
        f'<th class="{"n" if i >= 2 else ""}">{_esc(h)}</th>' for i, h in enumerate(headers)
    ))
    parts.append("</tr></thead><tbody>")
    for row in lines:
        cells = [
            f"<td class='num'>{_esc(row['code'] or '')}</td>",
            f"<td>{_esc(row['description'])}</td>",
            f"<td class='n num'>{locale.format_number(row['quantity'])} {_esc(t('unit.pieces'))}</td>",
        ]
        if display.show_unit_prices:
            cells.append(f"<td class='n num'>{money(row['unit_price'])}</td>")
        cells.append(f"<td class='n num'>{money(row['total'])}</td>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</tbody></table>")

    # -- totals: summed from the printed lines, always ---------------------------- #
    parts.append('<table class="totals"><tbody>')
    parts.append(
        f"<tr><td>{_esc(t('quote.subtotal'))}</td>"
        f"<td class='n num'>{money(totals['net'])}</td></tr>"
    )
    if display.show_vat:
        parts.append(
            f"<tr><td>{_esc(t('quote.vat'))} ({variant.policy.tax_pct:g}%)</td>"
            f"<td class='n num'>{money(totals['vat'])}</td></tr>"
        )
        parts.append(
            f"<tr><td>{_esc(t('quote.grand_total'))}</td>"
            f"<td class='n num'>{money(totals['gross'])}</td></tr>"
        )
    else:
        parts.append(
            f"<tr><td>{_esc(t('quote.grand_total'))}</td>"
            f"<td class='n num'>{money(totals['net'])}</td></tr>"
        )
    parts.append("</tbody></table>")

    # -- options ------------------------------------------------------------------ #
    if display.show_options_comparison and len(draft.variants) > 1:
        parts.append(f"<h2>{_esc(t('quote.option'))}</h2>")
        parts.append('<table class="options"><thead><tr>')
        for header in (t("quote.option"), t("member.glass"), "U", t("quote.total"), "±"):
            parts.append(f"<th>{_esc(header)}</th>")
        parts.append("</tr></thead><tbody>")
        for row in draft.compare():
            u_value = f"{row['u_value']:.2f}" if row["u_value"] else "—"
            difference = (
                "—" if abs(row["difference"]) < 0.005
                else f"{'+' if row['difference'] > 0 else '−'}{money(abs(row['difference']))}"
            )
            finish = row["finish_hebrew"] if locale.code == "he" else row["finish"]
            parts.append(
                f"<tr><td>{_esc(row['name'])}</td><td>{_esc(row['glass'])} · "
                f"{_esc(finish)}</td><td class='num'>{u_value}</td>"
                f"<td class='n num'>{money(row['net'])}</td>"
                f"<td class='n num'>{difference}</td></tr>"
            )
        parts.append("</tbody></table>")

    # -- terms ---------------------------------------------------------------- #
    terms = display.payment_terms or ""
    if display.notes:
        terms = f"{terms}\n{display.notes}" if terms else display.notes
    if terms:
        parts.append(f'<div class="terms">{_esc(terms)}</div>')

    # -- the internal sheet ------------------------------------------------------ #
    if internal:
        sheet = draft.internal_sheet()
        parts.append('<div class="internal">')
        parts.append("<h2>Internal — not for the client / פנימי</h2>")
        parts.append("<table><tbody>")
        for label, value in sheet["breakdown"]:
            parts.append(
                f"<tr><td>{_esc(label)}</td><td class='n num'>{money(value)}</td></tr>"
            )
        parts.append(
            f"<tr><td><b>Margin after edits</b></td>"
            f"<td class='n num'><b>{money(sheet['margin_after_edits'])}</b></td></tr>"
        )
        parts.append("</tbody></table>")
        if sheet["overrides"]:
            parts.append("<h2>Hand edits</h2><table><tbody>")
            for override in sheet["overrides"]:
                stale = ' <span class="badge">stale</span>' if override["stale"] else ""
                parts.append(
                    f"<tr><td class='num'>{_esc(override['element'])}</td>"
                    f"<td>{_esc(override['kind'])} → {_esc(override['value'])}{stale}</td>"
                    f"<td>{_esc(override['by'] or '—')}</td>"
                    f"<td>{_esc(override['reason'] or '')}</td></tr>"
                )
            parts.append("</tbody></table>")
        for warning in sheet["warnings"]:
            parts.append(f'<div class="warn">{_esc(warning)}</div>')
        parts.append("</div>")

    issued_by = quote.metadata.get("issued_by", brand.name)
    parts.append(f"<footer>{_esc(issued_by)}</footer>")
    parts.append("</div></body></html>")
    document = "".join(parts)
    _log.info(
        "Rendered %s quotation %s: %d line(s), %d option(s)",
        "internal" if internal else "customer",
        quote.quote_id,
        len(lines),
        len(draft.variants),
    )
    return document


__all__ = ["render_quotation"]
