"""Drawings as SVG, which is what everything else previews from.

SVG is the format the desktop views, the web page and the tablet terminal all
read, so this exporter is the one whose output people actually look at most.
Two things it has to get right that a naive writer gets wrong:

**The y axis.** A drawing has y increasing upwards, the way a section is
measured off a floor. SVG has y increasing downwards. Flipping with a transform
would also flip the text, so the flip is applied to coordinates and the text is
emitted upright.

**Line weight.** A 0.25 mm pen is 0.25 mm on the paper regardless of the view
scale, so stroke widths are multiplied by the scale on the way into model
space. Get this backwards and a 1:50 drawing comes out with hairlines and a 1:2
detail with lines a centimetre thick.

Hebrew needs no special handling here: browsers apply the Unicode bidirectional
algorithm to SVG text, so a logical-order string renders correctly.
"""

from __future__ import annotations

import html
from typing import Iterable

from .model import (
    Anchor,
    Arc,
    Circle,
    Drawing,
    Entity,
    Hatch,
    HatchPattern,
    Layer,
    Line,
    Polyline,
    Text,
)

#: Font stack with Hebrew coverage on every platform the shop is likely to use.
FONT_STACK = "'DejaVu Sans','Segoe UI','Noto Sans Hebrew','Arial',sans-serif"


def _fmt(value: float) -> str:
    """Trim coordinates so the file does not carry meaningless precision."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _points(points: Iterable[tuple[float, float]], flip: float) -> str:
    return " ".join(f"{_fmt(x)},{_fmt(flip - y)}" for x, y in points)


def _pattern_defs(pattern: HatchPattern, spacing: float, colour: str) -> str:
    """One SVG pattern per material, in the usual section conventions."""
    size = max(spacing, 0.4)
    ident = f"hatch-{pattern.value}"
    stroke = f'stroke="{colour}" stroke-width="{size * 0.08:.3f}" fill="none"'
    if pattern is HatchPattern.CONCRETE:
        body = (
            f'<path d="M0,0 L{size},{size}" {stroke}/>'
            f'<circle cx="{size * 0.3:.2f}" cy="{size * 0.7:.2f}" r="{size * 0.09:.2f}" '
            f'fill="{colour}"/>'
            f'<circle cx="{size * 0.75:.2f}" cy="{size * 0.25:.2f}" r="{size * 0.06:.2f}" '
            f'fill="{colour}"/>'
        )
    elif pattern is HatchPattern.BLOCKWORK:
        body = (
            f'<path d="M0,{size / 2:.2f} L{size},{size / 2:.2f}" {stroke}/>'
            f'<path d="M{size / 2:.2f},0 L{size / 2:.2f},{size / 2:.2f}" {stroke}/>'
            f'<path d="M0,{size:.2f} L0,{size / 2:.2f}" {stroke}/>'
        )
    elif pattern is HatchPattern.INSULATION:
        body = (
            f'<path d="M0,{size / 2:.2f} q{size / 4:.2f},{-size / 2:.2f} '
            f'{size / 2:.2f},0 q{size / 4:.2f},{size / 2:.2f} {size / 2:.2f},0" {stroke}/>'
        )
    elif pattern is HatchPattern.STONE:
        body = (
            f'<path d="M0,0 L{size},{size} M{size},0 L0,{size}" {stroke}/>'
        )
    elif pattern is HatchPattern.TIMBER:
        body = (
            f'<path d="M0,{size * 0.25:.2f} L{size},{size * 0.25:.2f} '
            f'M0,{size * 0.75:.2f} L{size},{size * 0.75:.2f}" {stroke}/>'
        )
    elif pattern is HatchPattern.EARTH:
        body = (
            f'<path d="M0,{size:.2f} L{size / 2:.2f},{size / 2:.2f} '
            f'L{size:.2f},{size:.2f}" {stroke}/>'
        )
    else:
        body = f'<path d="M0,{size:.2f} L{size:.2f},0" {stroke}/>'
    return (
        f'<pattern id="{ident}" width="{size:.3f}" height="{size:.3f}" '
        f'patternUnits="userSpaceOnUse">{body}</pattern>'
    )


def _stroke_attrs(layer: Layer, scale: float) -> str:
    """Pen only. Fill is the entity's business, and setting both here would
    emit the attribute twice — which strict SVG readers reject outright."""
    width = max(layer.lineweight * scale, 1e-3)
    attrs = f'stroke="{layer.colour}" stroke-width="{_fmt(width)}"'
    dashes = layer.line_type.dash_pattern(scale)
    if dashes:
        attrs += f' stroke-dasharray="{",".join(_fmt(d) for d in dashes)}"'
    return attrs


def _entity_svg(entity: Entity, layer: Layer, flip: float, scale: float) -> str:
    stroke = _stroke_attrs(layer, scale)
    if isinstance(entity, Line):
        return (
            f'<line x1="{_fmt(entity.start[0])}" y1="{_fmt(flip - entity.start[1])}" '
            f'x2="{_fmt(entity.end[0])}" y2="{_fmt(flip - entity.end[1])}" {stroke} fill="none"/>'
        )
    if isinstance(entity, Polyline):
        tag = "polygon" if entity.closed else "polyline"
        fill = f'fill="{layer.colour}"' if entity.filled else 'fill="none"'
        return f'<{tag} points="{_points(entity.points, flip)}" {stroke} {fill}/>'
    if isinstance(entity, Circle):
        return (
            f'<circle cx="{_fmt(entity.centre[0])}" cy="{_fmt(flip - entity.centre[1])}" '
            f'r="{_fmt(entity.radius)}" {stroke} fill="none"/>'
        )
    if isinstance(entity, Arc):
        return f'<polyline points="{_points(entity.sample(48), flip)}" {stroke} fill="none"/>'
    if isinstance(entity, Hatch):
        fill = entity.fill or (
            f"url(#hatch-{entity.pattern.value})"
            if entity.pattern is not HatchPattern.NONE
            else "none"
        )
        path = "M " + " L ".join(_points(entity.boundary, flip).split(" ")) + " Z"
        for hole in entity.holes:
            path += " M " + " L ".join(_points(hole, flip).split(" ")) + " Z"
        return (
            f'<path d="{path}" fill="{fill}" fill-rule="evenodd" '
            f'stroke="{layer.colour}" stroke-width="{_fmt(layer.lineweight * scale)}"/>'
        )
    if isinstance(entity, Text):
        anchor = {Anchor.LEFT: "start", Anchor.CENTRE: "middle", Anchor.RIGHT: "end"}[
            entity.anchor
        ]
        x, y = entity.position[0], flip - entity.position[1]
        height = entity.height * scale
        transform = ""
        if entity.rotation:
            transform = f' transform="rotate({-entity.rotation} {_fmt(x)} {_fmt(y)})"'
        weight = ' font-weight="600"' if entity.bold else ""
        return (
            f'<text x="{_fmt(x)}" y="{_fmt(y + height * 0.35)}" '
            f'font-family="{FONT_STACK}" font-size="{_fmt(height)}" '
            f'fill="{layer.colour}" text-anchor="{anchor}"{weight}{transform}>'
            f"{html.escape(entity.value)}</text>"
        )
    raise TypeError(f"Cannot draw {type(entity).__name__} in SVG")


def to_svg(
    drawing: Drawing,
    *,
    scale: float = 1.0,
    margin: float = 10.0,
    background: str | None = "#ffffff",
    title: str | None = None,
) -> str:
    """Render a drawing to a standalone SVG document.

    ``scale`` is the model-units-per-paper-millimetre factor the drawing was
    built for — 20 for a 1:20 view. It is used only to size pens and text; the
    geometry is emitted at model size and the viewBox does the fitting.
    """
    left, bottom, right, top = drawing.bounds()
    left -= margin * scale
    bottom -= margin * scale
    right += margin * scale
    top += margin * scale
    width = max(right - left, 1e-6)
    height = max(top - bottom, 1e-6)
    flip = top + bottom

    used = {
        entity.pattern
        for entity in drawing
        if isinstance(entity, Hatch) and entity.pattern is not HatchPattern.NONE
    }
    defs = "".join(
        _pattern_defs(
            pattern,
            next(
                (e.spacing * scale for e in drawing if isinstance(e, Hatch) and e.pattern is pattern),
                2.0 * scale,
            ),
            drawing.layers.get("STRUCTURE", Layer("STRUCTURE")).colour,
        )
        for pattern in sorted(used, key=lambda p: p.value)
    )

    body: list[str] = []
    if background:
        body.append(
            f'<rect x="{_fmt(left)}" y="{_fmt(flip - top)}" width="{_fmt(width)}" '
            f'height="{_fmt(height)}" fill="{background}"/>'
        )
    # Grouped by layer so a viewer can switch one off, and so the file reads
    # the way the drawing is organised.
    for name, layer in drawing.layers.items():
        entities = drawing.on_layer(name)
        if not entities or not layer.printable:
            continue
        body.append(f'<g id="{html.escape(name)}">')
        body.extend(_entity_svg(entity, layer, flip, scale) for entity in entities)
        body.append("</g>")

    heading = f"<title>{html.escape(title or drawing.name)}</title>"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_fmt(left)} {_fmt(flip - top)} '
        f'{_fmt(width)} {_fmt(height)}" width="100%">'
        f"{heading}<defs>{defs}</defs>{''.join(body)}</svg>"
    )


__all__ = ["FONT_STACK", "to_svg"]
