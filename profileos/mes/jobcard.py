"""Shop-floor job cards.

Renders a self-contained HTML page an operator can open on a workshop tablet:
the piece list with scannable codes, the machining to perform, and the assembly
sequence. Self-contained matters — a workshop tablet is often on a flaky
network, and a job card that needs to fetch a stylesheet is a job card that
sometimes shows up as unstyled text.

The layout is deliberately high-contrast and large-tap-target: the reader is
wearing gloves, standing under fluorescent light, and looking at a screen with
aluminium dust on it.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..elements.builder import ElementBuild
from .barcode import TrackingCode, code128_svg, qr_available, qr_svg
from .tracking import ItemKind, ProductionItem, Stage, WorkOrder

#: Inlined stylesheet. Dark-on-light, large type, generous tap targets.
JOB_CARD_CSS = """
:root {
  --ink: #14181d; --muted: #5b6472; --line: #d8dee7; --bg: #ffffff;
  --panel: #f5f7fa; --accent: #0b62d0; --warn: #b45309; --ok: #17803d;
  --radius: 10px;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0; background: var(--bg); color: var(--ink);
  font-family: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
  font-size: 17px; line-height: 1.5; -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 20px; }
header {
  display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
  justify-content: space-between; border-bottom: 3px solid var(--ink);
  padding-bottom: 14px; margin-bottom: 20px;
}
h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.02em; }
h2 { font-size: 20px; margin: 28px 0 10px; padding-bottom: 6px;
     border-bottom: 2px solid var(--line); }
.sub { color: var(--muted); font-size: 15px; }
.letterhead { font-size: 12px; line-height: 1.45; color: var(--muted);
              margin-bottom: 8px; padding-bottom: 8px;
              border-bottom: 1px solid var(--line); }
.letterhead:first-line { font-weight: 700; color: var(--ink); font-size: 14px; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 10px; margin-bottom: 18px; }
.meta div { background: var(--panel); border-radius: var(--radius); padding: 10px 12px; }
.meta dt { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
           color: var(--muted); margin: 0; }
.meta dd { margin: 2px 0 0; font-size: 18px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--line);
         vertical-align: middle; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
     color: var(--muted); background: var(--panel); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:nth-child(even) td { background: #fbfcfe; }
.code svg { display: block; max-width: 100%; height: auto; }
.pill { display: inline-block; padding: 3px 10px; border-radius: 999px;
        font-size: 13px; font-weight: 600; }
.pill.planned { background: #eef1f5; color: var(--muted); }
.pill.cut, .pill.machined { background: #e5f0fd; color: var(--accent); }
.pill.assembled, .pill.glazed { background: #fdf1dc; color: var(--warn); }
.pill.inspected, .pill.shipped { background: #e3f5e9; color: var(--ok); }
.pill.rework, .pill.scrapped { background: #fde8e8; color: #b91c1c; }
.card { border: 1px solid var(--line); border-radius: var(--radius);
        padding: 14px 16px; margin-bottom: 12px; background: var(--bg); }
.card h3 { margin: 0 0 8px; font-size: 18px; }
.steps { counter-reset: step; list-style: none; padding: 0; margin: 0; }
.steps li { counter-increment: step; position: relative; padding: 10px 0 10px 44px;
            border-bottom: 1px solid var(--line); }
.steps li:last-child { border-bottom: 0; }
.steps li::before {
  content: counter(step); position: absolute; left: 0; top: 8px;
  width: 30px; height: 30px; border-radius: 50%; background: var(--ink);
  color: #fff; font-weight: 700; font-size: 15px;
  display: flex; align-items: center; justify-content: center;
}
.warn { background: #fdf1dc; border-left: 4px solid var(--warn);
        padding: 10px 14px; border-radius: 0 var(--radius) var(--radius) 0;
        margin-bottom: 10px; }
.qr { text-align: center; }
.qr svg { width: 120px; height: 120px; }
footer { margin-top: 32px; padding-top: 14px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 13px; }
@media print {
  body { font-size: 12pt; }
  .wrap { max-width: none; padding: 0; }
  .card, tr { break-inside: avoid; }
  header { border-bottom-width: 2px; }
}
@media (max-width: 640px) {
  body { font-size: 16px; }
  th, td { padding: 12px 6px; }
  .hide-sm { display: none; }
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


@dataclass
class JobCardOptions:
    """What to include on the card."""

    include_qr: bool = True
    include_barcodes: bool = True
    include_assembly_steps: bool = True
    include_machining: bool = True
    title: str = "Job card"
    #: Defaults to the active brand's name when left as None.
    company: str | None = None
    #: Print the full letterhead (address and contact) rather than just a name.
    include_letterhead: bool = True


def _stage_pill(stage: Stage) -> str:
    return f'<span class="pill {stage.value}">{_esc(stage.value)}</span>'


def render_job_card(
    order: WorkOrder,
    builds: list[ElementBuild] | None = None,
    *,
    options: JobCardOptions | None = None,
) -> str:
    """Render a complete job card as a self-contained HTML document."""
    from ..branding import active_brand

    options = options or JobCardOptions()
    builds = builds or []
    brand = active_brand()
    company = options.company or brand.display_name

    pieces = order.by_kind(ItemKind.PROFILE_PIECE)
    panes = order.by_kind(ItemKind.GLASS_PANE)
    elements = order.by_kind(ItemKind.ELEMENT)
    summary = order.summary()

    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(options.title)} - {_esc(order.name or order.work_order_id)}</title>",
        f"<style>{JOB_CARD_CSS}</style>",
        "</head><body><div class='wrap'>",
    ]

    # -- header ------------------------------------------------------------- #
    qr_block = ""
    if options.include_qr and qr_available():
        payload = f"WO|{order.project_id}|{order.work_order_id}"
        qr_block = f'<div class="qr">{qr_svg(payload, scale=3, border=1)}</div>'

    letterhead = ""
    if options.include_letterhead and brand.letterhead():
        letterhead = (
            '<div class="letterhead">'
            + "<br>".join(_esc(line) for line in brand.letterhead())
            + "</div>"
        )

    parts.append(
        "<header><div>"
        f"{letterhead}"
        f"<h1>{_esc(order.name or 'Work order')}</h1>"
        f'<div class="sub">{_esc(order.work_order_id)}'
        f" &middot; project {_esc(order.project_id or 'n/a')}"
        f" &middot; {_esc(company)}</div>"
        f"</div>{qr_block}</header>"
    )

    # -- summary tiles -------------------------------------------------------- #
    tiles = [
        ("Items", summary["items"]),
        ("Progress", f"{summary['progress_pct']}%"),
        ("Profile pieces", len(pieces)),
        ("Glass panes", len(panes)),
        ("Elements", len(elements)),
    ]
    if summary["rework"]:
        tiles.append(("Rework", summary["rework"]))
    if summary["scrapped"]:
        tiles.append(("Scrapped", summary["scrapped"]))

    parts.append('<div class="meta">')
    for label, value in tiles:
        parts.append(f"<div><dt>{_esc(label)}</dt><dd>{_esc(value)}</dd></div>")
    parts.append("</div>")

    # -- warnings ------------------------------------------------------------- #
    warnings = [w for build in builds for w in build.warnings]
    if warnings:
        parts.append("<h2>Attention</h2>")
        for warning in warnings:
            parts.append(f'<div class="warn">{_esc(warning)}</div>')

    # -- cut list -------------------------------------------------------------- #
    if pieces:
        parts.append(f"<h2>Cut list &mdash; {len(pieces)} pieces</h2>")
        parts.append(
            "<table><thead><tr><th>ID</th><th>Description</th>"
            '<th class="num">Length</th><th class="num">Angles</th>'
            "<th>Stage</th>"
            + ('<th class="hide-sm">Code</th>' if options.include_barcodes else "")
            + "</tr></thead><tbody>"
        )
        for item in pieces:
            meta = item.metadata
            length = meta.get("length")
            length_cell = (
                f'<td class="num">{length:.1f}</td>'
                if isinstance(length, (int, float))
                else '<td class="num">&mdash;</td>'
            )
            angles = (
                f'{meta.get("angle_left", 90):g}/{meta.get("angle_right", 90):g}'
                if "angle_left" in meta
                else "&mdash;"
            )
            code_cell = ""
            if options.include_barcodes and item.barcode:
                code_cell = (
                    '<td class="code hide-sm">'
                    + code128_svg(item.barcode, module_width=0.7, height=26, show_text=False)
                    + "</td>"
                )
            parts.append(
                "<tr>"
                f"<td><strong>{_esc(item.item_id)}</strong></td>"
                f"<td>{_esc(item.description)}</td>"
                f"{length_cell}"
                f'<td class="num">{angles}</td>'
                f"<td>{_stage_pill(item.stage)}</td>"
                f"{code_cell}</tr>"
            )
        parts.append("</tbody></table>")

    # -- glass ------------------------------------------------------------------ #
    if panes:
        parts.append(f"<h2>Glass &mdash; {len(panes)} panes</h2>")
        parts.append(
            "<table><thead><tr><th>ID</th><th>Specification</th>"
            '<th class="num">Mass</th><th>Safety</th><th>Stage</th>'
            "</tr></thead><tbody>"
        )
        for item in panes:
            mass = item.metadata.get("mass_kg")
            safety = (
                '<strong style="color:#b45309">required</strong>'
                if item.metadata.get("safety_required")
                else "&mdash;"
            )
            parts.append(
                "<tr>"
                f"<td><strong>{_esc(item.item_id)}</strong></td>"
                f"<td>{_esc(item.description)}</td>"
                f'<td class="num">{mass if mass is not None else "&mdash;"}</td>'
                f"<td>{safety}</td>"
                f"<td>{_stage_pill(item.stage)}</td></tr>"
            )
        parts.append("</tbody></table>")

    # -- machining -------------------------------------------------------------- #
    if options.include_machining and builds:
        machining_rows: list[str] = []
        for build in builds:
            for item in build.hardware:
                machining_rows.append(
                    f"<tr><td>{_esc(build.opening.name)}</td>"
                    f"<td>{_esc(item.code)}</td><td>{_esc(item.name)}</td>"
                    f'<td class="num">{item.quantity} {_esc(item.unit)}</td></tr>'
                )
        if machining_rows:
            parts.append("<h2>Hardware to fit</h2>")
            parts.append(
                "<table><thead><tr><th>Element</th><th>Code</th><th>Item</th>"
                '<th class="num">Qty</th></tr></thead><tbody>'
                + "".join(machining_rows)
                + "</tbody></table>"
            )

    # -- assembly steps ---------------------------------------------------------- #
    if options.include_assembly_steps and builds:
        parts.append("<h2>Assembly sequence</h2>")
        for build in builds:
            opening = build.opening
            steps = _assembly_steps(build)
            parts.append(
                f'<div class="card"><h3>{_esc(opening.name)} '
                f"&mdash; {opening.width:.0f} &times; {opening.height:.0f} mm "
                f"&times;{opening.quantity}</h3>"
                '<ol class="steps">'
                + "".join(f"<li>{_esc(step)}</li>" for step in steps)
                + "</ol></div>"
            )

    # -- element labels ------------------------------------------------------------ #
    if elements and options.include_qr and qr_available():
        parts.append("<h2>Element labels</h2>")
        parts.append(
            '<table><thead><tr><th>Element</th><th>Description</th>'
            "<th>Stage</th><th>Label</th></tr></thead><tbody>"
        )
        for item in elements:
            label = qr_svg(item.barcode or item.item_id, scale=2, border=1)
            parts.append(
                f"<tr><td><strong>{_esc(item.item_id)}</strong></td>"
                f"<td>{_esc(item.description)}</td>"
                f"<td>{_stage_pill(item.stage)}</td>"
                f'<td class="qr">{label}</td></tr>'
            )
        parts.append("</tbody></table>")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(
        f"<footer>Generated {generated} by {_esc(company)}. "
        "Scan any code to record progress.</footer>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def _assembly_steps(build: ElementBuild) -> list[str]:
    """Derive an ordered assembly sequence from the element's own content."""
    opening = build.opening
    steps: list[str] = [
        f"Check all {sum(c.quantity for c in build.cuts)} cut pieces against the cut list "
        "and confirm the mitre angles.",
        "Crimp or screw the frame corners, checking the diagonals are equal "
        "before the joint sets.",
    ]

    if opening.mullion_positions or opening.transom_positions:
        steps.append(
            f"Fit {len(opening.mullion_positions)} mullion(s) and "
            f"{len(opening.transom_positions)} transom(s), then re-check square."
        )

    operable = opening.operable_cells()
    if operable:
        types = sorted({cell.sash.opening_type.value for cell in operable if cell.sash})
        steps.append(
            f"Assemble {len(operable)} sash(es) ({', '.join(types)}) and hang them in the frame."
        )
        steps.append("Fit the hardware, then adjust the sash for even gap all round.")

    if build.gaskets:
        steps.append(
            "Run the glazing gaskets, leaving the corners long and trimming "
            "them once the pane is set."
        )

    if build.glass:
        heaviest = max(build.glass, key=lambda panel: panel.mass)
        steps.append(
            f"Set the glass on blocks and bead it in. Heaviest pane is "
            f"{heaviest.mass:.1f} kg &mdash; use lifting equipment."
        )

    steps.append("Final inspection: operation, seal, finish. Scan the element label to close it out.")
    return steps


def write_job_card(
    order: WorkOrder,
    path: str,
    builds: list[ElementBuild] | None = None,
    *,
    options: JobCardOptions | None = None,
) -> str:
    """Render and write a job card, returning the path written."""
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_job_card(order, builds, options=options), encoding="utf-8")
    return str(target)


__all__ = ["JOB_CARD_CSS", "JobCardOptions", "render_job_card", "write_job_card"]
