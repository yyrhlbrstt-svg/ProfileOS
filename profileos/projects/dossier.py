"""The job pack: one printable document holding the whole job.

A fabricator walking to the saw carries paper. This renders everything the
shop needs about one job into a single self-contained HTML file — cover,
elevations, cut list, glass list, hardware — that prints to A4 and opens on
any machine without this software installed.

It is deliberately one file with no external requests: a workshop PC with no
internet must render it exactly as the office PC did, and a pack mailed to a
customer must not phone home.

The provenance discipline of the rest of the suite holds here too. If the
lengths came from typical figures rather than the supplier's own, the pack
says so across the top of every page — this is the last document anybody
reads before metal is cut.
"""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from typing import Any, Iterable

from ..core.logging_setup import get_logger

_log = get_logger("projects.dossier")


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _css() -> str:
    from ..design.tokens import BRAND, PAPER, STATUS, font_face_css

    return font_face_css(embed=True) + f"""
:root{{--ink:{PAPER.text};--muted:{PAPER.muted};--faint:{PAPER.faint};
      --line:{PAPER.line};--firm:{PAPER.line_strong};--tint:{PAPER.tint};
      --paper:{PAPER.bg};--accent:{BRAND.x600};--warn:{STATUS.warn}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
     font:13px/1.6 'Heebo','Segoe UI','Noto Sans Hebrew',Arial,sans-serif}}
.sheet{{max-width:860px;margin:0 auto;padding:28px 34px}}
header{{display:flex;justify-content:space-between;align-items:flex-start;gap:22px;
       border-bottom:3px solid var(--accent);padding-bottom:12px;margin-bottom:18px}}
h1{{margin:0;font-size:23px;font-weight:700;color:var(--accent)}}
h2{{font-size:14px;margin:26px 0 8px;color:var(--accent);font-weight:700;
    border-bottom:1px solid var(--line);padding-bottom:4px}}
.letterhead{{font-size:11px;color:var(--muted);text-align:end;line-height:1.5}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;
      background:var(--tint);border-radius:8px;padding:11px 14px;margin-bottom:6px}}
.meta b{{display:block;font-size:10px;color:var(--muted);font-weight:600;
        letter-spacing:.04em}}
.meta span{{font-size:13px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{border-bottom:2px solid var(--ink);padding:5px 7px;font-size:10px;color:var(--muted);
   letter-spacing:.04em;text-align:start;font-weight:600}}
td{{border-bottom:1px solid var(--line);padding:6px 7px;vertical-align:top}}
.n{{text-align:end;font-variant-numeric:tabular-nums}}
tfoot td{{border-top:2px solid var(--ink);border-bottom:0;font-weight:700}}
.elevations{{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:12px}}
.elevations figure{{margin:0;border:1px solid var(--line);border-radius:8px;padding:7px;
                   break-inside:avoid}}
.elevations svg{{width:100%;height:auto}}
.elevations figcaption{{font-size:11px;color:var(--muted);text-align:center;padding-top:3px}}
.warn{{background:#FBF0D6;border:1px solid var(--warn);border-radius:8px;
      padding:9px 13px;font-size:12px;margin:12px 0;font-weight:500}}
footer{{margin-top:26px;border-top:1px solid var(--line);padding-top:10px;
       font-size:10px;color:var(--faint);display:flex;justify-content:space-between}}
@media print{{
  .sheet{{max-width:none;padding:0}}
  @page{{size:A4;margin:14mm 12mm}}
  h2{{break-after:avoid}}
  tr{{break-inside:avoid}}
}}
"""


def _elevation_svg(build: Any) -> str:
    from ..drawing.elevation import ElevationStyle, elevation
    from ..drawing.svg import to_svg

    drawing = elevation(
        build,
        style=ElevationStyle(scale=18, show_glass_sizes=False, show_dimensions=True),
    )
    return to_svg(drawing, scale=18, background="#ffffff")


def _cut_rows(builds: Iterable[Any]) -> list[tuple[str, float, int, float]]:
    """Every cut across the job, gathered by profile and length.

    Two openings that need the same bar at the same length are one line on the
    saw's list, not two — that is how the operator counts them out.
    """
    tally: dict[tuple[str, float], int] = defaultdict(int)
    for build in builds:
        multiplier = max(1, build.opening.quantity)
        for cut in build.cuts:
            tally[(cut.profile_id, round(cut.length, 1))] += cut.quantity * multiplier
    rows = [
        (profile, length, quantity, round(length * quantity / 1000.0, 2))
        for (profile, length), quantity in tally.items()
    ]
    rows.sort(key=lambda row: (row[0], -row[1]))
    return rows


def _glass_rows(builds: Iterable[Any]) -> list[tuple[str, str, int, float, bool]]:
    tally: dict[tuple[str, str, bool], list[float]] = defaultdict(list)
    for build in builds:
        multiplier = max(1, build.opening.quantity)
        for pane in build.glass:
            key = (
                f"{pane.width:.0f} × {pane.height:.0f}",
                pane.build_up.name,
                pane.safety_required,
            )
            tally[key].extend([pane.area] * pane.quantity * multiplier)
    rows = [
        (size, build_up, len(areas), round(sum(areas), 2), safety)
        for (size, build_up, safety), areas in tally.items()
    ]
    rows.sort(key=lambda row: -row[3])
    return rows


def _hardware_rows(builds: Iterable[Any]) -> list[tuple[str, str, int, str]]:
    tally: dict[tuple[str, str, str], int] = defaultdict(int)
    for build in builds:
        multiplier = max(1, build.opening.quantity)
        for item in build.hardware:
            tally[(item.code, item.name, item.unit)] += item.quantity * multiplier
    rows = [(code, name, quantity, unit) for (code, name, unit), quantity in tally.items()]
    rows.sort(key=lambda row: row[0])
    return rows


def _accessory_rows(builds: Iterable[Any]) -> list[tuple[str, str, str, int, str]]:
    """Every fitting on every opening, with the hole in the wall it needs."""
    from ..accessories import accessories_for

    rows: list[tuple[str, str, str, int, str]] = []
    for build in builds:
        opening = build.opening
        try:
            fitted = accessories_for(opening)
        except Exception:  # noqa: BLE001 - a pack is worth more than a fitting
            continue
        if not len(fitted):
            continue
        width, height = fitted.structural_opening(opening.width, opening.height)
        structural = f"⁦{width:.0f} × {height:.0f}⁩ מ״מ"
        for accessory in fitted:
            rows.append((
                opening.name,
                accessory.hebrew,
                f"⁦{accessory.width:.0f} × {accessory.height:.0f}⁩",
                accessory.quantity,
                structural,
            ))
            structural = ""
    return rows


def render_dossier(job: Any, builds: list[Any], *, today: date | None = None) -> str:
    """The whole job as one printable page."""
    from ..branding import active_brand

    brand = active_brand()
    when = today or date.today()
    parts: list[str] = []

    parts.append(
        "<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'>"
        f"<title>תיק עבודה {_esc(job.job_id)} — {_esc(job.name)}</title>"
        f"<style>{_css()}</style></head><body><div class='sheet'>"
    )

    letterhead = "<br>".join(_esc(line) for line in brand.letterhead())
    parts.append(
        f"<header><div><h1>תיק עבודה</h1>"
        f"<div style='color:var(--muted);font-size:12px'>{_esc(job.job_id)} · "
        f"{_esc(job.name)}</div></div>"
        f"<div class='letterhead'>{letterhead}</div></header>"
    )

    # -- the facts anybody asks for first ---------------------------------- #
    parts.append("<div class='meta'>")
    for label, value in (
        ("לקוח", job.customer_name or "—"),
        ("אתר", job.site_address or "—"),
        ("אסמכתה", job.reference or "—"),
        ("מערכת", job.system_id),
        ("סטטוס", job.status.hebrew),
        ("תאריך", when.strftime("%d/%m/%Y")),
    ):
        parts.append(f"<div><b>{_esc(label)}</b><span>{_esc(value)}</span></div>")
    parts.append("</div>")

    units = sum(max(1, build.opening.quantity) for build in builds)
    area = sum(build.opening.area * max(1, build.opening.quantity) for build in builds)
    parts.append("<div class='meta'>")
    for label, value in (
        ("פתחים", f"{len(builds)}"),
        ("יחידות", f"{units}"),
        ("שטח חזית", f"{area:.2f} m²"),
    ):
        parts.append(f"<div><b>{_esc(label)}</b><span>{_esc(value)}</span></div>")
    parts.append("</div>")

    # A pack whose lengths are not the supplier's own says so, once, loudly.
    if builds and not all(build.may_be_cut for build in builds):
        parts.append(
            "<div class='warn'>לא לייצור — חלק מהמידות חושבו מערכים טיפוסיים "
            "ולא מנתוני היצרן. אמתו מול הקטלוג לפני חיתוך.</div>"
        )

    # -- elevations --------------------------------------------------------- #
    if builds:
        parts.append("<h2>חזיתות</h2><div class='elevations'>")
        for build in builds:
            opening = build.opening
            try:
                svg = _elevation_svg(build)
            except Exception:  # noqa: BLE001 - a drawing must not lose the pack
                _log.exception("Could not draw %s", opening.element_id)
                continue
            caption = (
                f"{opening.name or opening.element_id} — "
                f"⁦{opening.width:.0f} × {opening.height:.0f}⁩ מ\"מ"
            )
            if opening.quantity > 1:
                caption += f" · ×{opening.quantity}"
            parts.append(f"<figure>{svg}<figcaption>{_esc(caption)}</figcaption></figure>")
        parts.append("</div>")

    # -- cut list ------------------------------------------------------------ #
    cuts = _cut_rows(builds)
    if cuts:
        parts.append(
            "<h2>רשימת חיתוך</h2><table><thead><tr>"
            "<th>פרופיל</th><th class='n'>אורך (מ\"מ)</th>"
            "<th class='n'>כמות</th><th class='n'>סה\"כ (מ')</th>"
            "</tr></thead><tbody>"
        )
        for profile, length, quantity, metres in cuts:
            parts.append(
                f"<tr><td>{_esc(profile)}</td><td class='n'>{length:,.1f}</td>"
                f"<td class='n'>{quantity}</td><td class='n'>{metres:,.2f}</td></tr>"
            )
        total_metres = sum(row[3] for row in cuts)
        parts.append(
            f"</tbody><tfoot><tr><td colspan='3'>סה\"כ</td>"
            f"<td class='n'>{total_metres:,.2f}</td></tr></tfoot></table>"
        )

    # -- glass ---------------------------------------------------------------- #
    panes = _glass_rows(builds)
    if panes:
        parts.append(
            "<h2>זכוכית</h2><table><thead><tr>"
            "<th>מידה (מ\"מ)</th><th>הרכב</th><th class='n'>כמות</th>"
            "<th class='n'>שטח (m²)</th><th>בטיחות</th>"
            "</tr></thead><tbody>"
        )
        for size, build_up, quantity, pane_area, safety in panes:
            parts.append(
                f"<tr><td>⁦{_esc(size)}⁩</td><td>⁦{_esc(build_up)}⁩</td>"
                f"<td class='n'>{quantity}</td><td class='n'>{pane_area:,.2f}</td>"
                f"<td>{'נדרשת' if safety else '—'}</td></tr>"
            )
        total_glass = sum(row[3] for row in panes)
        parts.append(
            f"</tbody><tfoot><tr><td colspan='3'>סה\"כ</td>"
            f"<td class='n'>{total_glass:,.2f}</td><td></td></tr></tfoot></table>"
        )

    # -- hardware -------------------------------------------------------------- #
    hardware = _hardware_rows(builds)
    if hardware:
        parts.append(
            "<h2>פרזול ואביזרים</h2><table><thead><tr>"
            "<th>קוד</th><th>תיאור</th><th class='n'>כמות</th><th>יחידה</th>"
            "</tr></thead><tbody>"
        )
        for code, name, quantity, unit in hardware:
            parts.append(
                f"<tr><td>{_esc(code)}</td><td>{_esc(name)}</td>"
                f"<td class='n'>{quantity}</td><td>{_esc(unit)}</td></tr>"
            )
        parts.append("</tbody></table>")

    # -- accessories and the hole in the wall --------------------------------- #
    # The builder reads one number off this pack and casts a lintel to it. It
    # is the last thing anybody can still change for free, so it gets a
    # section of its own rather than a column somewhere.
    fitted_rows = _accessory_rows(builds)
    if fitted_rows:
        parts.append(
            "<h2>אביזרים ופתחי בנייה</h2><table><thead><tr>"
            "<th>פתח</th><th>אביזר</th><th>מידה</th><th class='n'>כמות</th>"
            "<th>פתח בנייה נדרש</th>"
            "</tr></thead><tbody>"
        )
        for name, hebrew, size, quantity, structural in fitted_rows:
            parts.append(
                f"<tr><td>{_esc(name)}</td><td>{_esc(hebrew)}</td>"
                f"<td>{_esc(size)}</td><td class='n'>{quantity}</td>"
                f"<td>{_esc(structural)}</td></tr>"
            )
        parts.append("</tbody></table>")

    if not builds:
        parts.append(
            "<div class='warn'>בפרויקט הזה עדיין אין פתחים. תכננו פתחים "
            "ושמרו את העבודה כדי שהתיק יכלול חזיתות ורשימת חיתוך.</div>"
        )

    parts.append(
        f"<footer><span>{_esc(brand.document_name)}</span>"
        f"<span>{_esc(job.job_id)} · הופק ⁦{when.strftime('%d/%m/%Y')}⁩</span></footer>"
    )
    parts.append("</div></body></html>")

    document = "".join(parts)
    _log.info(
        "Rendered dossier for %s: %d element(s), %d cut line(s)",
        job.job_id, len(builds), len(cuts),
    )
    return document


def write_dossier(job: Any, builds: list[Any], path: Any) -> Any:
    """Render the pack and write it, returning the path written."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dossier(job, builds), encoding="utf-8")
    return target


__all__ = ["render_dossier", "write_dossier"]
