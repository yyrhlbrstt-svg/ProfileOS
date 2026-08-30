"""Elevations: the drawing the architect approves and the shop works from.

An elevation is looked at from outside the building, which decides everything
about how it is read. A sash hinged on the left as you stand in the room is
hinged on the right in the drawing, and the opening symbol — the pair of dashed
lines meeting at the hinged edge — is the only thing on the sheet that says
which. Get it mirrored and the shop machines the lock stile on the wrong side
of every leaf in the job.

So the symbols here follow the usual convention and say which convention they
follow, on the drawing, in the legend: **dashed lines meet at the hinged edge;
dashed for a sash opening towards the viewer, solid for one opening away.**

The geometry comes from the element build rather than from the opening's
nominal sizes, so what is dimensioned on the sheet is what will be cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..elements.builder import ElementBuild, Rect
from ..elements.model import Cell, HingeSide, Opening, OpeningType
from . import dimension as dim
from .model import (
    Anchor,
    Drawing,
    Line,
    LineType,
    Point,
    Polyline,
    Text,
    rectangle,
)


@dataclass
class ElevationStyle:
    """How much detail an elevation carries, which depends on who reads it."""

    #: Plot scale the view will be placed at, so paper sizes come out right.
    scale: int = 20
    #: Draw the inner face of each member as well as its outer edge.
    show_profile_faces: bool = True
    show_opening_symbols: bool = True
    show_glass_marks: bool = True
    show_glass_sizes: bool = True
    show_dimensions: bool = True
    #: Distance from the element to the first dimension line [mm].
    first_dim_offset: float = 90.0
    #: Spacing between successive dimension lines [mm].
    dim_spacing: float = 90.0
    text_height: float = 2.5
    mark_height: float = 4.0

    @property
    def dim_style(self) -> dim.DimensionStyle:
        return dim.DimensionStyle(text_height=self.text_height)


def _sash_outline(build: ElementBuild, cell_key: tuple[int, int]) -> tuple[float, float] | None:
    """Outer sash size, read off the parts that will be cut."""
    width = height = None
    for cut in build.cuts:
        if cut.cell_key != cell_key:
            continue
        if cut.role == "sash_horizontal":
            width = cut.length
        elif cut.role == "sash_vertical":
            height = cut.length
    return (width, height) if width and height else None


#: Which way each kind of leaf opens by default, seen from the room. A sash
#: can override it with ``metadata={"opens_outward": True}``.
OPENS_OUTWARD: dict[OpeningType, bool] = {
    OpeningType.CASEMENT: True,
    OpeningType.TOP_HUNG: True,
    OpeningType.BOTTOM_HUNG: False,
    OpeningType.TILT_TURN: False,
    OpeningType.DOOR: True,
    OpeningType.PIVOT: True,
    OpeningType.SLIDING: False,
    OpeningType.LIFT_SLIDE: False,
}


def opens_outward(cell: Cell) -> bool:
    """Whether this leaf swings away from the room."""
    if cell.sash is None:
        return False
    override = cell.sash.metadata.get("opens_outward")
    if isinstance(override, bool):
        return override
    return OPENS_OUTWARD.get(cell.sash.opening_type, True)


def opening_symbol(
    rect: Rect, cell: Cell, *, layer: str | None = None, inset: float = 0.0
) -> list[Any]:
    """The dashed triangle that says which edge a sash is hinged on.

    Drawn as two lines from the corners of the hinged edge to the midpoint of
    the opposite edge, which is the convention every fabricator reads without
    being told. A tilt-and-turn gets both its symbols — the turn about its
    stile and the tilt about its bottom rail — because a leaf that does one and
    not the other is a different leaf with different gear.
    """
    if cell.sash is None or not cell.sash.opening_type.is_operable:
        return []
    # Elevations here are drawn from outside, so a leaf opening outward opens
    # towards the reader and is drawn solid; one opening into the room is
    # dashed. The legend states this, because offices differ.
    if layer is None:
        layer = "OPEN-OUT" if opens_outward(cell) else "OPEN-IN"

    left = rect.x + inset
    right = rect.x + rect.width - inset
    bottom = rect.y + inset
    top = rect.y + rect.height - inset
    opening_type = cell.sash.opening_type

    if opening_type in (OpeningType.SLIDING, OpeningType.LIFT_SLIDE):
        # A sliding leaf gets an arrow along its travel, not a hinge symbol.
        y = (bottom + top) / 2.0
        direction = 1.0 if cell.sash.hinge_side is HingeSide.RIGHT else -1.0
        start = (left + rect.width * 0.25, y)
        end = (left + rect.width * 0.75, y)
        if direction < 0:
            start, end = end, start
        head = rect.width * 0.06
        return [
            Line(layer=layer, start=start, end=end),
            Polyline(
                layer=layer,
                points=(
                    end,
                    (end[0] - direction * head, y + head * 0.5),
                    (end[0] - direction * head, y - head * 0.5),
                ),
                closed=True,
                filled=True,
            ),
        ]

    # Where the hinges are, seen from outside.
    hinge = cell.sash.hinge_side
    if opening_type is OpeningType.TOP_HUNG:
        hinge = HingeSide.TOP
    elif opening_type is OpeningType.BOTTOM_HUNG:
        hinge = HingeSide.BOTTOM

    def triangle(side: HingeSide) -> list[Any]:
        """Two lines from the free edge meeting at the hinged edge.

        The apex is *on the hinges*. Drawn the other way round the symbol still
        looks like an opening symbol, which is why getting it backwards is
        expensive: every leaf in the job comes back with its lock stile on the
        wrong side and nothing about the drawing looked wrong.
        """
        middle_y = (bottom + top) / 2.0
        middle_x = (left + right) / 2.0
        base_a, base_b, apex = {
            HingeSide.LEFT: ((right, bottom), (right, top), (left, middle_y)),
            HingeSide.RIGHT: ((left, bottom), (left, top), (right, middle_y)),
            HingeSide.TOP: ((left, bottom), (right, bottom), (middle_x, top)),
            HingeSide.BOTTOM: ((left, top), (right, top), (middle_x, bottom)),
        }[side]
        return [
            Line(layer=layer, start=base_a, end=apex),
            Line(layer=layer, start=base_b, end=apex),
        ]

    symbols = triangle(hinge)
    if opening_type is OpeningType.TILT_TURN:
        # A leaf that turns but does not tilt is a different leaf with
        # different gear, so both symbols are drawn.
        symbols.extend(triangle(HingeSide.BOTTOM))
    return symbols


def elevation(
    build: ElementBuild,
    *,
    style: ElevationStyle | None = None,
    rules: Any = None,
) -> Drawing:
    """Draw one element in elevation, seen from outside."""
    style = style or ElevationStyle()
    opening = build.opening
    rules = rules or build.rules
    scale = float(style.scale)
    drawing = Drawing(name=opening.name or opening.element_id)

    width, height = opening.width, opening.height
    face = rules.frame.face_width

    # The frame: outer edge heavy, inner face lighter.
    drawing.add(rectangle(0.0, 0.0, width, height, "ALU-CUT"))
    if style.show_profile_faces:
        drawing.add(rectangle(face, face, width - 2 * face, height - 2 * face, "ALU-SEEN"))

    half_mullion = rules.mullion.face_width / 2.0
    for position in opening.mullion_positions:
        drawing.add(Line(layer="ALU-CUT", start=(position - half_mullion, face),
                         end=(position - half_mullion, height - face)))
        drawing.add(Line(layer="ALU-CUT", start=(position + half_mullion, face),
                         end=(position + half_mullion, height - face)))
    for position in opening.transom_positions:
        drawing.add(Line(layer="ALU-CUT", start=(face, position - half_mullion),
                         end=(width - face, position - half_mullion)))
        drawing.add(Line(layer="ALU-CUT", start=(face, position + half_mullion),
                         end=(width - face, position + half_mullion)))

    # Cells: sash outlines, opening symbols, glass marks.
    from ..elements.builder import ElementBuilder

    rects = ElementBuilder(rules).cell_rects(opening, rules)
    panes = {panel.cell_key: panel for panel in build.glass}

    for cell in opening.all_cells():
        rect = rects.get(cell.key)
        if rect is None or rect.width <= 0 or rect.height <= 0:
            continue

        symbol_rect = rect
        if cell.sash is not None:
            size = _sash_outline(build, cell.key)
            if size:
                sash_width, sash_height = size
                sash_x = rect.x + (rect.width - sash_width) / 2.0
                sash_y = rect.y + (rect.height - sash_height) / 2.0
                drawing.add(rectangle(sash_x, sash_y, sash_width, sash_height, "ALU-CUT"))
                if style.show_profile_faces:
                    inset = rules.sash.sash_face_width
                    drawing.add(
                        rectangle(
                            sash_x + inset, sash_y + inset,
                            sash_width - 2 * inset, sash_height - 2 * inset,
                            "ALU-SEEN",
                        )
                    )
                # The symbol belongs to the leaf, not to the bay: drawn on the
                # bay it crosses the mullion and points at the wrong stile.
                symbol_rect = Rect(sash_x, sash_y, sash_width, sash_height)
            if style.show_opening_symbols:
                drawing.extend(opening_symbol(symbol_rect, cell))

        panel = panes.get(cell.key)
        centre = (rect.x + rect.width / 2.0, rect.y + rect.height / 2.0)
        if panel is not None and style.show_glass_marks:
            drawing.add(
                Text(
                    layer="TEXT",
                    position=centre,
                    value=panel.mark or "",
                    height=style.text_height,
                    anchor=Anchor.CENTRE,
                )
            )
            if style.show_glass_sizes:
                drawing.add(
                    Text(
                        layer="TEXT",
                        position=(centre[0], centre[1] - style.text_height * scale * 1.6),
                        value=f"{panel.width:.0f} × {panel.height:.0f}",
                        height=style.text_height * 0.85,
                        anchor=Anchor.CENTRE,
                    )
                )
        elif cell.panel:
            drawing.add(
                Text(
                    layer="TEXT", position=centre, value="פאנל / panel",
                    height=style.text_height, anchor=Anchor.CENTRE,
                )
            )

    if style.show_dimensions:
        _dimension(drawing, opening, style)

    # The element mark, under the drawing, with the quantity.
    quantity = f" ×{opening.quantity}" if opening.quantity > 1 else ""
    drawing.add(
        Text(
            layer="TEXT",
            position=(width / 2.0, -style.first_dim_offset - style.dim_spacing * 2.4),
            value=f"{opening.name or opening.element_id}{quantity}",
            height=style.mark_height,
            anchor=Anchor.CENTRE,
            bold=True,
        )
    )
    return drawing


def _dimension(drawing: Drawing, opening: Opening, style: ElevationStyle) -> None:
    """Chain dimensions on the bay lines, overall dimensions outside them."""
    scale = float(style.scale)
    dim_style = style.dim_style
    width, height = opening.width, opening.height

    horizontal = [0.0, *opening.mullion_positions, width]
    vertical = [0.0, *opening.transom_positions, height]

    first = -style.first_dim_offset
    second = first - style.dim_spacing

    if len(horizontal) > 2:
        drawing.extend(
            dim.chain(
                [(x, 0.0) for x in horizontal], first, scale=scale, style=dim_style
            )
        )
        drawing.extend(
            dim.overall([(0.0, 0.0), (width, 0.0)], second, scale=scale, style=dim_style)
        )
    else:
        drawing.extend(
            dim.overall([(0.0, 0.0), (width, 0.0)], first, scale=scale, style=dim_style)
        )

    # Running up the right-hand edge, the left-hand normal points back into the
    # element, so the offset is negative to put the dimension outside it.
    right_first = -style.first_dim_offset
    right_second = right_first - style.dim_spacing
    if len(vertical) > 2:
        drawing.extend(
            dim.chain(
                [(width, y) for y in vertical], right_first, scale=scale, style=dim_style
            )
        )
        drawing.extend(
            dim.overall(
                [(width, 0.0), (width, height)], right_second, scale=scale, style=dim_style
            )
        )
    else:
        drawing.extend(
            dim.overall(
                [(width, 0.0), (width, height)], right_first, scale=scale, style=dim_style
            )
        )


def legend(
    origin: Point = (0.0, 0.0),
    *,
    height: float = 3.0,
    scale: float = 20.0,
    language: Any = "he",
) -> Drawing:
    """The key that makes the opening symbols mean something.

    A drawing whose symbols rely on the reader sharing an unstated convention is
    a drawing that gets built wrong once and argued about afterwards.
    """
    drawing = Drawing(name="legend")
    x, y = origin
    box = height * scale * 4.0
    from ..i18n import translate

    def bilingual(key: str) -> str:
        local = translate(key, language)
        english = translate(key, "en")
        return local if local == english else f"{local} / {english}"

    entries = [
        (bilingual("drawing.opens_outward"), "OPEN-OUT"),
        (bilingual("drawing.opens_inward"), "OPEN-IN"),
    ]
    for index, (label, layer) in enumerate(entries):
        top = y - index * box * 1.4
        drawing.add(rectangle(x, top - box, box, box, "ALU-SEEN"))
        drawing.add(Line(layer=layer, start=(x, top - box), end=(x + box, top - box / 2.0)))
        drawing.add(Line(layer=layer, start=(x, top), end=(x + box, top - box / 2.0)))
        drawing.add(
            Text(
                layer="TEXT",
                position=(x + box * 1.3, top - box / 2.0),
                value=label,
                height=height,
            )
        )
    drawing.add(
        Text(
            layer="TEXT",
            position=(x, y + box * 0.6),
            value=f"{bilingual('drawing.legend')} — {translate('drawing.legend_note', language)}",
            height=height,
            bold=True,
        )
    )
    return drawing


__all__ = ["ElevationStyle", "elevation", "legend", "opening_symbol"]
