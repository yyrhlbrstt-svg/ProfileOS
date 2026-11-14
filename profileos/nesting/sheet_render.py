"""Cutting maps for 2D sheet layouts, as standalone SVG.

The map is what actually reaches the table operator, so it is drawn to be read
at arm's length on a printed A4 rather than admired on screen: parts filled in
a light tint with a hard outline, the label centred and dropped when it will
not fit, off-cuts hatched and dimensioned, and a caption carrying the sheet
number, stock size, yield and stage count.

The output is a self-contained ``<svg>`` element with no external references,
so the same string works in the desktop app, the web API, a print stylesheet
and an emailed job pack.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from .sheet import SheetLayout, SheetNestingResult

#: Drawing constants, in SVG user units at the final scale.
_MARGIN = 28.0
_CAPTION_HEIGHT = 34.0
_MIN_LABEL_BOX = 44.0


def _fit_scale(layout: SheetLayout, max_width: float, max_height: float) -> float:
    width = layout.stock.width or 1.0
    height = layout.stock.height or 1.0
    return min(max_width / width, max_height / height)


def render_layout_svg(
    layout: SheetLayout,
    *,
    max_width: float = 900.0,
    max_height: float = 620.0,
    show_offcuts: bool = True,
) -> str:
    """Draw one sheet as an SVG cutting map."""
    scale = _fit_scale(layout, max_width, max_height)
    sheet_w = layout.stock.width * scale
    sheet_h = layout.stock.height * scale
    trim = layout.spec.edge_trim * scale
    total_w = sheet_w + 2 * _MARGIN
    total_h = sheet_h + 2 * _MARGIN + _CAPTION_HEIGHT

    def sx(value: float) -> float:
        """Usable-area x to SVG x."""
        return _MARGIN + trim + value * scale

    def sy(value: float) -> float:
        """Usable-area y to SVG y. SVG grows downward; the sheet does not."""
        return _MARGIN + sheet_h - trim - value * scale

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total_w:.1f} {total_h:.1f}" '
        f'width="{total_w:.1f}" height="{total_h:.1f}" font-family="system-ui, sans-serif">',
        "<defs>"
        '<pattern id="offcut" width="8" height="8" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="8" stroke="#9aa5b4" stroke-width="1.2"/>'
        "</pattern></defs>",
        f'<rect x="0" y="0" width="{total_w:.1f}" height="{total_h:.1f}" fill="#ffffff"/>',
        # The bought sheet.
        f'<rect x="{_MARGIN:.1f}" y="{_MARGIN:.1f}" width="{sheet_w:.1f}" '
        f'height="{sheet_h:.1f}" fill="#f2f4f7" stroke="#33405166" stroke-width="1"/>',
    ]

    if trim > 0.2:
        parts.append(
            f'<rect x="{_MARGIN + trim:.1f}" y="{_MARGIN + trim:.1f}" '
            f'width="{sheet_w - 2 * trim:.1f}" height="{sheet_h - 2 * trim:.1f}" '
            'fill="#ffffff" stroke="#334051" stroke-width="1" stroke-dasharray="4 3"/>'
        )

    if show_offcuts:
        for rect in layout.reusable_offcuts():
            x, y = sx(rect.x), sy(rect.top)
            width, height = rect.width * scale, rect.height * scale
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
                'fill="url(#offcut)" fill-opacity="0.55" stroke="#9aa5b4" '
                'stroke-width="1" stroke-dasharray="3 3"/>'
            )
            if width > _MIN_LABEL_BOX and height > 18:
                parts.append(
                    f'<text x="{x + width / 2:.1f}" y="{y + height / 2 + 4:.1f}" '
                    'text-anchor="middle" font-size="11" fill="#5c6a7d">'
                    f"{rect.width:.0f}×{rect.height:.0f}</text>"
                )

    for placement in layout.placements:
        x, y = sx(placement.x), sy(placement.top)
        width, height = placement.width * scale, placement.height * scale
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
            'fill="#dcebf5" stroke="#1c4f6b" stroke-width="1.4"/>'
        )
        if width < _MIN_LABEL_BOX or height < 20:
            continue
        label = escape(placement.part.name)
        size = f"{placement.width:.0f}×{placement.height:.0f}"
        if placement.rotated:
            size += " ↻"
        centre_x = x + width / 2
        centre_y = y + height / 2
        parts.append(
            f'<text x="{centre_x:.1f}" y="{centre_y - 2:.1f}" text-anchor="middle" '
            f'font-size="12" font-weight="600" fill="#12303f">{label}</text>'
        )
        parts.append(
            f'<text x="{centre_x:.1f}" y="{centre_y + 13:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#43596b">{size}</text>'
        )

    caption = (
        f"Sheet {layout.sheet_index + 1} · {escape(layout.stock.name)} · "
        f"{layout.piece_count} pieces · {layout.yield_pct:.1f}% yield"
    )
    if layout.stages_used:
        caption += f" · {layout.stages_used}-stage"
    parts.append(
        f'<text x="{_MARGIN:.1f}" y="{_MARGIN + sheet_h + 22:.1f}" font-size="13" '
        f'fill="#22303d">{caption}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def render_result_svg(result: SheetNestingResult, **kwargs: object) -> list[str]:
    """One SVG per sheet, in cutting order."""
    return [render_layout_svg(layout, **kwargs) for layout in result.layouts]  # type: ignore[arg-type]


def cutting_list(result: SheetNestingResult) -> list[dict[str, object]]:
    """Flat rows for a printed cutting list or a CSV export."""
    rows: list[dict[str, object]] = []
    for layout in result.layouts:
        for placement in sorted(
            layout.placements, key=lambda p: (-p.y, p.x)
        ):
            rows.append(
                {
                    "sheet": layout.sheet_index + 1,
                    "stock": layout.stock.name,
                    "part": placement.part.name,
                    "part_id": placement.part.part_id,
                    "x_mm": round(placement.x, 1),
                    "y_mm": round(placement.y, 1),
                    "width_mm": round(placement.width, 1),
                    "height_mm": round(placement.height, 1),
                    "rotated": placement.rotated,
                }
            )
    return rows


__all__ = ["render_layout_svg", "render_result_svg", "cutting_list"]
