"""Dimension lines, drawn out of primitives rather than left to the viewer.

A dimension is the part of a shop drawing that gets argued about, so it has to
survive the trip through every output format identically. CAD packages each
have their own dimension entity with its own idea of arrow size, text gap and
extension-line overshoot, and a drawing that renders one way in the office and
another on the consultant's screen is a drawing that will be measured off
wrongly. So dimensions here are exploded into lines and text at the moment they
are created, against one explicit style, and every format receives the same
geometry.

Sizes in :class:`DimensionStyle` are **paper** millimetres: an arrow is 2.5 mm
on the sheet whether the view is at 1:5 or 1:50. The model-space geometry is
therefore built at a known scale and the sheet does not rescale it — which is
why :func:`linear` takes the view scale it will be plotted at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .model import Anchor, Entity, Line, Point, Polyline, Text


@dataclass(frozen=True)
class DimensionStyle:
    """Everything about how a dimension looks, in paper millimetres."""

    text_height: float = 2.5
    arrow_length: float = 2.5
    arrow_width: float = 0.9
    #: How far the extension line stops short of the thing being measured.
    extension_gap: float = 1.5
    #: How far it runs past the dimension line.
    extension_overshoot: float = 2.0
    #: Gap between the dimension line and the text sitting above it.
    text_gap: float = 1.0
    #: Below this the text is pushed outside and a leader is drawn instead.
    min_text_space: float = 8.0
    layer: str = "DIM"
    text_layer: str = "TEXT"
    #: Decimal places. Aluminium is set out to the millimetre; halves are noise.
    decimals: int = 0
    suffix: str = ""

    def format(self, value: float) -> str:
        return f"{value:.{self.decimals}f}{self.suffix}"


STANDARD_STYLE = DimensionStyle()


def _unit(a: Point, b: Point) -> tuple[float, float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 1.0, 0.0, 0.0
    return dx / length, dy / length, length


def _arrow(tip: Point, direction: tuple[float, float], style: DimensionStyle, scale: float
           ) -> Polyline:
    """A solid arrowhead pointing along ``direction``, sized on the paper."""
    length = style.arrow_length * scale
    half = style.arrow_width * scale / 2.0
    back = (tip[0] - direction[0] * length, tip[1] - direction[1] * length)
    normal = (-direction[1], direction[0])
    return Polyline(
        layer=style.layer,
        points=(
            tip,
            (back[0] + normal[0] * half, back[1] + normal[1] * half),
            (back[0] - normal[0] * half, back[1] - normal[1] * half),
        ),
        closed=True,
        filled=True,
    )


def linear(
    start: Point,
    end: Point,
    offset: float,
    *,
    scale: float = 1.0,
    style: DimensionStyle = STANDARD_STYLE,
    text: str | None = None,
    vertical: bool | None = None,
) -> list[Entity]:
    """One dimension between two points, offset to one side.

    ``offset`` is in model millimetres and signed: positive puts the dimension
    line to the left of the direction start→end, which for a left-to-right
    dimension means above it. ``scale`` is the plot scale the view will be
    drawn at, so paper sizes come out right — at 1:20 an arrow is drawn 20 model
    millimetres long in order to plot 1 mm.

    Set ``vertical`` to force the reading direction; by default a dimension
    whose run is more vertical than horizontal reads bottom-to-top, which is
    the convention on every construction drawing.
    """
    ux, uy, length = _unit(start, end)
    normal = (-uy, ux)
    line_start = (start[0] + normal[0] * offset, start[1] + normal[1] * offset)
    line_end = (end[0] + normal[0] * offset, end[1] + normal[1] * offset)

    gap = style.extension_gap * scale
    overshoot = style.extension_overshoot * scale
    sign = 1.0 if offset >= 0 else -1.0

    entities: list[Entity] = []
    for anchor, dim_point in ((start, line_start), (end, line_end)):
        entities.append(
            Line(
                layer=style.layer,
                start=(anchor[0] + normal[0] * gap * sign, anchor[1] + normal[1] * gap * sign),
                end=(
                    dim_point[0] + normal[0] * overshoot * sign,
                    dim_point[1] + normal[1] * overshoot * sign,
                ),
            )
        )

    label = text if text is not None else style.format(length)
    room = style.min_text_space * scale
    fits = length > room

    if fits:
        # Tips touch the extension lines and the bodies lie inside the span,
        # so the arrows point outwards at the thing being measured.
        entities.append(Line(layer=style.layer, start=line_start, end=line_end))
        entities.append(_arrow(line_start, (-ux, -uy), style, scale))
        entities.append(_arrow(line_end, (ux, uy), style, scale))
    else:
        # Too tight for arrows between the extension lines: put them outside,
        # pointing in, which is what a draughtsman does with a 40 mm gap.
        outside = style.arrow_length * scale * 2.0
        entities.append(
            Line(
                layer=style.layer,
                start=(line_start[0] - ux * outside, line_start[1] - uy * outside),
                end=(line_end[0] + ux * outside, line_end[1] + uy * outside),
            )
        )
        entities.append(_arrow(line_start, (ux, uy), style, scale))
        entities.append(_arrow(line_end, (-ux, -uy), style, scale))

    reads_vertical = abs(uy) > abs(ux) if vertical is None else vertical
    rotation = 90.0 if reads_vertical else 0.0
    text_offset = (style.text_gap + style.text_height / 2.0) * scale
    if not fits:
        # Push the text clear of the arrows as well as clear of the line.
        text_offset += style.text_height * scale
    middle = ((line_start[0] + line_end[0]) / 2.0, (line_start[1] + line_end[1]) / 2.0)
    entities.append(
        Text(
            layer=style.text_layer,
            position=(
                middle[0] + normal[0] * text_offset * sign,
                middle[1] + normal[1] * text_offset * sign,
            ),
            value=label,
            height=style.text_height,
            rotation=rotation,
            anchor=Anchor.CENTRE,
        )
    )
    return entities


def chain(
    points: Sequence[Point],
    offset: float,
    *,
    scale: float = 1.0,
    style: DimensionStyle = STANDARD_STYLE,
    labels: Sequence[str] | None = None,
) -> list[Entity]:
    """A running row of dimensions between consecutive points.

    This is how a mullion layout is set out: each bay dimensioned separately
    along one line, with the overall on a second line further out.
    """
    if len(points) < 2:
        return []
    entities: list[Entity] = []
    for index, (a, b) in enumerate(zip(points, points[1:])):
        text = labels[index] if labels and index < len(labels) else None
        entities.extend(linear(a, b, offset, scale=scale, style=style, text=text))
    return entities


def overall(
    points: Sequence[Point],
    offset: float,
    *,
    scale: float = 1.0,
    style: DimensionStyle = STANDARD_STYLE,
) -> list[Entity]:
    """One dimension across the whole run, for the line outside the chain."""
    if len(points) < 2:
        return []
    return linear(points[0], points[-1], offset, scale=scale, style=style)


def leader(
    tip: Point,
    elbow: Point,
    text: str,
    *,
    scale: float = 1.0,
    style: DimensionStyle = STANDARD_STYLE,
    tail: float | None = None,
) -> list[Entity]:
    """An arrow at a thing, a kink, and a horizontal tail carrying a note."""
    ux, uy, _ = _unit(elbow, tip)
    run = tail if tail is not None else style.text_height * 4.0 * scale
    direction = 1.0 if elbow[0] >= tip[0] else -1.0
    end = (elbow[0] + run * direction, elbow[1])
    return [
        _arrow(tip, (ux, uy), style, scale),
        Line(layer=style.layer, start=tip, end=elbow),
        Line(layer=style.layer, start=elbow, end=end),
        Text(
            layer=style.text_layer,
            position=(end[0] + style.text_gap * scale * direction, end[1]),
            value=text,
            height=style.text_height,
            anchor=Anchor.LEFT if direction > 0 else Anchor.RIGHT,
        ),
    ]


__all__ = [
    "DimensionStyle",
    "STANDARD_STYLE",
    "chain",
    "leader",
    "linear",
    "overall",
]
