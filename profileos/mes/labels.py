"""A label for every piece, so nothing on the rack is anonymous.

Two hundred cut bars leave the saw in a morning. They are all silver, most are
within a few millimetres of each other, and by the afternoon nobody can say
which four belong to the kitchen window of a job that shipped its glass last
week. Every shop solves this with a marker pen on masking tape, and every shop
loses an afternoon a month to it.

This prints the label instead: the job, the position, the profile, the finished
length, the two end angles drawn rather than described, and a barcode the
shop-floor terminal already knows how to scan. Sheets are laid out to real
label stock — the sizes an office supplier sells — so what comes out of the
printer lines up with the sheet in the tray.

Two things it will not do. It will not print a label with a length on it that
came from an unconfirmed series, because a label is an instruction to cut and
this suite does not issue instructions on figures nobody checked. And it will
not silently drop a piece that does not fit the sheet: the count printed is
reported back, so a run of ⁦212⁩ labels that produced ⁦210⁩ says so.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .barcode import TrackingCode, code128_svg

_log = get_logger("mes.labels")


@dataclass(frozen=True)
class LabelStock:
    """One kind of label sheet, by the dimensions the box states.

    Sizes are in millimetres because that is what the box says, and because a
    label laid out in pixels prints at whatever the browser decides.
    """

    key: str
    name: str
    across: int
    down: int
    label_width: float
    label_height: float
    page_width: float = 210.0
    page_height: float = 297.0
    margin_top: float = 0.0
    margin_left: float = 0.0

    @property
    def per_sheet(self) -> int:
        return self.across * self.down

    def describe(self) -> str:
        return (
            f"{self.name} · ⁦{self.label_width:g}×{self.label_height:g}⁩ מ״מ · "
            f"⁦{self.per_sheet}⁩ מדבקות בגיליון"
        )


#: The stock an Israeli office supplier actually carries. A layout that only
#: fits paper nobody sells is a layout nobody prints.
STOCKS: dict[str, LabelStock] = {
    "a4-24": LabelStock(
        "a4-24", "A4 · ⁦24⁩ מדבקות", across=3, down=8,
        label_width=70.0, label_height=37.0, margin_top=0.0, margin_left=0.0,
    ),
    "a4-12": LabelStock(
        "a4-12", "A4 · ⁦12⁩ מדבקות", across=2, down=6,
        label_width=105.0, label_height=48.0,
    ),
    "a4-8": LabelStock(
        "a4-8", "A4 · ⁦8⁩ מדבקות גדולות", across=2, down=4,
        label_width=105.0, label_height=74.0,
    ),
    "roll-100x50": LabelStock(
        "roll-100x50", "גליל ⁦100×50⁩ מ״מ", across=1, down=1,
        label_width=100.0, label_height=50.0,
        page_width=100.0, page_height=50.0,
    ),
}

DEFAULT_STOCK = "a4-24"


@dataclass
class PieceLabel:
    """What goes on one label."""

    #: The scanned payload. Everything else on the label is for a person.
    code: str
    job_id: str = ""
    job_name: str = ""
    position: str = ""
    description: str = ""
    #: Finished length in millimetres, where the piece is a bar.
    length_mm: float | None = None
    #: The two end cuts, in degrees, drawn on the label rather than written.
    left_angle: float | None = None
    right_angle: float | None = None
    profile: str = ""
    finish: str = ""
    system: str = ""
    quantity: int = 1
    note: str = ""
    #: Set when the figures behind the length were never confirmed. Such a
    #: label prints, but it prints saying so.
    provisional: bool = False

    @property
    def length_text(self) -> str:
        if self.length_mm is None:
            return ""
        return f"{self.length_mm:,.1f} מ״מ"

    @property
    def quantity_text(self) -> str:
        """Only shown when it is not one, so the common label stays quiet."""
        return f"×{self.quantity}" if self.quantity > 1 else ""

    @property
    def angle_text(self) -> str:
        parts = [
            f"{value:g}°"
            for value in (self.left_angle, self.right_angle)
            if value is not None
        ]
        return " / ".join(parts)


def labels_for_order(
    order: Any, *, provisional: bool = False, only_stage: str = ""
) -> list[PieceLabel]:
    """One label per physical piece in a released work order.

    Quantity is expanded: four identical pieces get four labels, because the
    problem the label solves is on the rack, and the rack has four bars.
    """
    found: list[PieceLabel] = []
    project = str(getattr(order, "work_order_id", "") or "")

    for item in getattr(order, "items", []) or []:
        if only_stage and getattr(item.stage, "value", "") != only_stage:
            continue
        metadata = getattr(item, "metadata", {}) or {}
        code = getattr(item, "barcode", None) or TrackingCode(
            project=project,
            element=str(getattr(item, "element_ref", "") or ""),
            piece=str(getattr(item, "item_id", "")),
        ).payload()

        for _copy in range(max(1, int(getattr(item, "quantity", 1) or 1))):
            found.append(PieceLabel(
                code=code,
                job_id=project,
                job_name=str(getattr(order, "name", "") or ""),
                position=str(getattr(item, "element_ref", "") or ""),
                description=str(getattr(item, "description", "") or ""),
                length_mm=_number(metadata.get("length_mm")),
                left_angle=_number(metadata.get("left_angle")),
                right_angle=_number(metadata.get("right_angle")),
                profile=str(metadata.get("profile", "") or ""),
                finish=str(metadata.get("finish", "") or ""),
                system=str(metadata.get("system", "") or ""),
                provisional=provisional or bool(metadata.get("provisional")),
            ))
    return found


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _end_cut_svg(left: float | None, right: float | None) -> str:
    """The two end cuts, drawn, with each angle written at the end it belongs to.

    A mitre written as ⁦45°⁩ in a corner of the label is read correctly by
    everybody and cut backwards by somebody, roughly once a month — because
    nothing on the label says which end it belongs to, and in a right-to-left
    interface a pair of numbers separated by a slash is exactly the thing a
    reader gets round the wrong way. Drawn, with the figure standing at its
    own end of the bar, the direction of the cut is not a matter of reading.
    """
    if left is None and right is None:
        return ""

    import math

    width, height = 116.0, 20.0
    # Room for a figure at each end, wide enough that an obtuse cut leaning
    # outward still does not run into its own number.
    inset = 21.0

    def lean(angle: float | None) -> float:
        """How far the cut face leans, signed.

        An obtuse cut leans the opposite way to an acute one, and a formula
        that clamps the tangent to a positive number turns ⁦135°⁩ into a
        sliver — which is worse than not drawing it, because it is wrong and
        confident. The sign is kept; only the near-flat cases are clamped, and
        the lean is bounded so the bar cannot fold through itself.
        """
        if angle is None or abs(angle - 90.0) < 0.01:
            return 0.0
        tangent = math.tan(math.radians(max(1.0, min(179.0, angle))))
        if abs(tangent) < 0.05:
            tangent = math.copysign(0.05, tangent or 1.0)
        return max(-span / 3.0, min(span / 3.0, height / tangent))

    bar_left, bar_right = inset, width - inset
    span = bar_right - bar_left
    left_lean = lean(left)
    right_lean = lean(right)

    top_left = bar_left + max(0.0, left_lean)
    top_right = bar_right - max(0.0, right_lean)
    bottom_right = bar_right - min(0.0, right_lean)
    bottom_left = bar_left - min(0.0, left_lean)
    points = " ".join([
        f"{top_left:.1f},0",
        f"{top_right:.1f},0",
        f"{bottom_right:.1f},{height:.1f}",
        f"{bottom_left:.1f},{height:.1f}",
    ])

    # The figures stand at the shape's own extremes, not at a nominal end: an
    # obtuse cut leans outward, and a number placed at the nominal end would
    # sit on top of the line it is describing.
    left_edge = min(top_left, bottom_left)
    right_edge = max(top_right, bottom_right)

    figures = ""
    if left is not None:
        figures += (
            f'<text x="{left_edge - 3:.1f}" y="{height / 2 + 3:.1f}" '
            f'text-anchor="end" font-size="8.5" fill="#111">{left:g}\u00b0</text>'
        )
    if right is not None:
        figures += (
            f'<text x="{right_edge + 3:.1f}" y="{height / 2 + 3:.1f}" '
            f'text-anchor="start" font-size="8.5" fill="#111">{right:g}\u00b0</text>'
        )

    view_left = left_edge - 26.0
    view_width = (right_edge + 26.0) - view_left
    return (
        f'<svg class="cut" viewBox="{view_left:.1f} -2 {view_width:.1f} '
        f'{height + 4:.0f}" preserveAspectRatio="xMidYMid meet" '
        f'direction="ltr" aria-hidden="true">'
        f'<polygon points="{points}" fill="none" stroke="#111" '
        f'stroke-width="1.2" stroke-linejoin="round"/>{figures}</svg>'
    )


def _label_html(label: PieceLabel, stock: LabelStock) -> str:
    barcode = code128_svg(
        label.code, height=26.0, module_width=1.0,
        quiet_zone=6.0, show_text=False,
    )
    lines: list[str] = []
    if label.profile:
        lines.append(_esc(label.profile))
    if label.system:
        lines.append(_esc(label.system))
    if label.finish:
        lines.append(_esc(label.finish))

    return f"""<div class="label">
  <div class="top">
    <span class="pos">{_esc(label.position or label.job_id)}</span>
    <span class="job">{_esc(label.job_id)}</span>
  </div>
  <div class="desc">{_esc(label.description)}</div>
  <div class="figures">
    <span class="length">{_esc(label.length_text)}</span>
    <span class="angles">{_esc(label.quantity_text)}</span>
  </div>
  {_end_cut_svg(label.left_angle, label.right_angle)}
  <div class="detail">{" · ".join(lines)}</div>
  {'<div class="provisional">לא לייצור — נתוני סדרה לא אושרו</div>'
   if label.provisional else ''}
  <div class="code">{barcode}</div>
</div>"""


def _stylesheet(stock: LabelStock) -> str:
    return f"""
@page {{ size: {stock.page_width:g}mm {stock.page_height:g}mm; margin: 0; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 0; background: #fff; color: #111;
  font-family: "Heebo", "Segoe UI", system-ui, sans-serif;
  direction: rtl;
}}
.sheet {{
  width: {stock.page_width:g}mm; height: {stock.page_height:g}mm;
  padding: {stock.margin_top:g}mm {stock.margin_left:g}mm;
  display: grid;
  grid-template-columns: repeat({stock.across}, {stock.label_width:g}mm);
  grid-template-rows: repeat({stock.down}, {stock.label_height:g}mm);
  page-break-after: always;
}}
.sheet:last-child {{ page-break-after: auto; }}
.label {{
  width: {stock.label_width:g}mm; height: {stock.label_height:g}mm;
  padding: 2.2mm 2.6mm; overflow: hidden;
  display: flex; flex-direction: column; gap: 0.6mm;
  border: 0.2mm dashed #d8dce3;
}}
.top {{ display: flex; justify-content: space-between; align-items: baseline; }}
.pos {{ font-size: 3.6mm; font-weight: 800; letter-spacing: -0.01em; }}
.job {{ font-size: 2.4mm; color: #5b6472; font-variant-numeric: tabular-nums; }}
.desc {{ font-size: 2.6mm; line-height: 1.15;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.figures {{ display: flex; justify-content: space-between; align-items: baseline; }}
.length {{ font-size: 4.4mm; font-weight: 700; font-variant-numeric: tabular-nums; }}
.angles {{ font-size: 2.8mm; color: #5b6472; font-variant-numeric: tabular-nums; }}
.cut {{ width: 100%; height: 5.2mm; }}
.detail {{ font-size: 2.2mm; color: #5b6472; line-height: 1.2;
          overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }}
.provisional {{
  font-size: 2.3mm; font-weight: 700; color: #8a1c1c;
  background: #fdecec; border-radius: 0.8mm; padding: 0.4mm 1.2mm;
  text-align: center;
}}
.code {{ margin-top: auto; }}
.code svg {{ display: block; width: 100%; height: 6mm; }}
@media screen {{
  body {{ background: #eef1f5; padding: 6mm; }}
  .sheet {{ background: #fff; margin: 0 auto 6mm;
           box-shadow: 0 1mm 3mm rgba(16,24,40,.14); }}
}}
"""


@dataclass
class LabelRun:
    """What a print run produced, so nothing goes missing quietly."""

    stock: LabelStock
    requested: int = 0
    printed: int = 0
    sheets: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.printed == self.requested

    def describe(self) -> str:
        body = (
            f"⁦{self.printed}⁩ מדבקות על ⁦{self.sheets}⁩ גיליונות "
            f"({self.stock.name})"
        )
        if not self.is_complete:
            body += f" · הוזמנו ⁦{self.requested}⁩"
        return body


def render_labels(
    labels: Sequence[PieceLabel],
    *,
    stock: str | LabelStock = DEFAULT_STOCK,
    title: str = "מדבקות ייצור",
    start_at: int = 0,
) -> tuple[str, LabelRun]:
    """Lay labels out on sheets, and say what the run produced.

    ``start_at`` skips that many label positions on the first sheet, which is
    how a part-used sheet gets reused rather than thrown away — a small thing
    that a shop printing two hundred labels a week notices.
    """
    chosen = stock if isinstance(stock, LabelStock) else STOCKS.get(str(stock))
    if chosen is None:
        raise ProfileOSError(
            f"אין גיליון מדבקות בשם {stock!r} — הידועים: "
            + ", ".join(sorted(STOCKS))
        )
    if start_at < 0 or start_at >= chosen.per_sheet:
        raise ProfileOSError(
            f"אפשר לדלג על ⁦0⁩ עד ⁦{chosen.per_sheet - 1}⁩ מקומות בגיליון"
        )

    run = LabelRun(stock=chosen, requested=len(labels))
    cells: list[str] = ['<div class="label"></div>'] * start_at
    for label in labels:
        cells.append(_label_html(label, chosen))
        run.printed += 1

    sheets: list[str] = []
    for index in range(0, len(cells), chosen.per_sheet):
        block = cells[index:index + chosen.per_sheet]
        block += ['<div class="label"></div>'] * (chosen.per_sheet - len(block))
        sheets.append('<div class="sheet">' + "".join(block) + "</div>")
    run.sheets = len(sheets)

    if not labels:
        run.warnings.append("אין פריטים להדפסה")

    body = "".join(sheets) or '<div class="sheet"></div>'
    document = f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_stylesheet(chosen)}</style>
</head><body>{body}</body></html>"""

    _log.info(
        "Rendered %d labels on %d sheets of %s",
        run.printed, run.sheets, chosen.key,
    )
    return document, run


def write_labels(
    labels: Sequence[PieceLabel], path: Any, **kwargs: Any
) -> LabelRun:
    """Write a label sheet to disk and return what it produced."""
    from pathlib import Path

    document, run = render_labels(labels, **kwargs)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document, encoding="utf-8")
    return run


__all__ = [
    "DEFAULT_STOCK",
    "STOCKS",
    "LabelRun",
    "LabelStock",
    "PieceLabel",
    "labels_for_order",
    "render_labels",
    "write_labels",
]
