"""Paper: sizes, borders, title blocks, revision tables and view placement.

Everything up to here is drawn in real millimetres of aluminium. This module is
where it meets paper, and paper is the part with the conventions: a border with
a wide binding edge, a title block in the bottom right that an architect's
office can file by, a revision table that grows upwards so the latest issue is
nearest the title block, and a stated scale that is actually true.

The scale is the load-bearing detail. A view placed at 1:20 is scaled by
exactly 1/20 and the label says 1:20, so anybody can lay a scale rule on the
print and get the real dimension. Nothing is ever "fitted to the page" behind a
label claiming a round number — a drawing that lies about its scale is worse
than one with no scale on it.

Text and pen weights do not scale with the view: a 2.5 mm note is 2.5 mm on the
paper whether the detail beside it is 1:5 or 1:50. That falls out of the model —
:class:`~profileos.drawing.model.Text` carries a paper height and layers carry
paper line weights — so placing a view only transforms geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from .model import Anchor, Drawing, Layer, Line, Point, Text, rectangle


class SheetSize(StrEnum):
    """ISO A sizes, in landscape."""

    A0 = "A0"
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"
    A4 = "A4"

    @property
    def landscape(self) -> tuple[float, float]:
        return {
            SheetSize.A0: (1189.0, 841.0),
            SheetSize.A1: (841.0, 594.0),
            SheetSize.A2: (594.0, 420.0),
            SheetSize.A3: (420.0, 297.0),
            SheetSize.A4: (297.0, 210.0),
        }[self]

    @property
    def portrait(self) -> tuple[float, float]:
        width, height = self.landscape
        return (height, width)

    def size(self, portrait: bool = False) -> tuple[float, float]:
        return self.portrait if portrait else self.landscape


#: Scales a shop drawing is actually issued at. A view is placed at one of
#: these or at whatever the operator names — but always at a stated one.
STANDARD_SCALES: tuple[int, ...] = (1, 2, 5, 10, 20, 25, 50, 100, 200)


@dataclass(frozen=True)
class Revision:
    """One issue of the drawing."""

    mark: str
    date: date
    description: str
    by: str = ""


@dataclass
class TitleBlock:
    """What the drawing says about itself.

    Filled from the project and the operator's branding rather than typed, so
    two sheets of the same package cannot disagree about the client's name.
    """

    company: str = ""
    company_line: str = ""
    project: str = ""
    client: str = ""
    title: str = ""
    number: str = ""
    revision: str = "-"
    scale: str = "1:20"
    sheet_size: str = "A3"
    drawn_by: str = ""
    checked_by: str = ""
    issued: date = field(default_factory=date.today)
    #: Free text under the block: standards, assumptions, a not-for-construction
    #: note when the systems data behind the drawing is not confirmed.
    notes: tuple[str, ...] = ()

    def rows(self) -> list[tuple[str, str]]:
        """Label/value pairs, in the order a drawing office reads them."""
        return [
            ("פרויקט / Project", self.project),
            ("לקוח / Client", self.client),
            ("תוכן / Title", self.title),
            ("מס' שרטוט / Dwg", self.number),
            ("קנ\"מ / Scale", self.scale),
            ("גיליון / Sheet", self.sheet_size),
            ("מהדורה / Rev", self.revision),
            ("תאריך / Date", self.issued.isoformat()),
            ("שורטט / Drawn", self.drawn_by),
            ("נבדק / Checked", self.checked_by),
        ]


@dataclass
class Viewport:
    """One drawing placed on the sheet at a stated scale."""

    drawing: Drawing
    #: The N in 1:N. 1 means full size.
    scale: int = 20
    #: Where on the paper the view goes, in paper mm: (x, y, width, height).
    frame: tuple[float, float, float, float] = (0.0, 0.0, 100.0, 100.0)
    label: str = ""
    #: Draw a thin box round the view. Useful on a sheet of many details.
    show_frame: bool = False

    @property
    def scale_text(self) -> str:
        return f"1:{self.scale}" if self.scale != 1 else "1:1"

    def fits(self) -> bool:
        """Whether the drawing at its stated scale is inside its frame."""
        _, _, width, height = self.frame
        return (
            self.drawing.width / self.scale <= width
            and self.drawing.height / self.scale <= height
        )

    def place(self) -> Drawing:
        """The view in paper millimetres, centred in its frame.

        The scale is applied exactly. If the drawing does not fit, it is still
        placed at the stated scale and overflows — because the alternative,
        quietly shrinking it, produces a print that measures wrong.
        """
        left, bottom, right, top = self.drawing.bounds()
        factor = 1.0 / self.scale
        model_width = (right - left) * factor
        model_height = (top - bottom) * factor
        x, y, width, height = self.frame
        dx = x + (width - model_width) / 2.0 - left * factor
        dy = y + (height - model_height) / 2.0 - bottom * factor
        return self.drawing.transformed(dx, dy, factor)


@dataclass
class Sheet:
    """A sheet of paper with a border, a title block and views on it."""

    size: SheetSize = SheetSize.A3
    portrait: bool = False
    title_block: TitleBlock = field(default_factory=TitleBlock)
    viewports: list[Viewport] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    #: Border insets: left is wider for the binding edge.
    margin_left: float = 20.0
    margin: float = 10.0
    #: Title block size in paper millimetres.
    block_width: float = 170.0
    block_row_height: float = 7.0

    @property
    def paper(self) -> tuple[float, float]:
        return self.size.size(self.portrait)

    @property
    def border(self) -> tuple[float, float, float, float]:
        """The frame rectangle: (x, y, width, height) in paper mm."""
        width, height = self.paper
        return (
            self.margin_left,
            self.margin,
            width - self.margin_left - self.margin,
            height - 2 * self.margin,
        )

    def drawing_area(self) -> tuple[float, float, float, float]:
        """The space left for views once the title block has taken its corner."""
        x, y, width, height = self.border
        block_height = self.block_row_height * len(self.title_block.rows())
        return (x, y + block_height, width, height - block_height)

    def add(self, viewport: Viewport) -> Viewport:
        self.viewports.append(viewport)
        return viewport

    # -- composition -------------------------------------------------------- #
    def compose(self) -> Drawing:
        """Everything on this sheet, in paper millimetres, ready to export."""
        paper_width, paper_height = self.paper
        sheet = Drawing(name=self.title_block.number or self.title_block.title or "sheet")

        sheet.add(rectangle(0.0, 0.0, paper_width, paper_height, "SHEET"))
        x, y, width, height = self.border
        sheet.add(rectangle(x, y, width, height, "SHEET-HEAVY"))

        for viewport in self.viewports:
            placed = viewport.place()
            for entity in placed:
                if entity.layer not in sheet.layers:
                    sheet.layers[entity.layer] = placed.layers.get(
                        entity.layer, Layer(entity.layer)
                    )
                sheet.entities.append(entity)
            if viewport.show_frame:
                sheet.add(rectangle(*viewport.frame, "HIDDEN"))
            if viewport.label:
                fx, fy, fwidth, _ = viewport.frame
                sheet.add(
                    Text(
                        layer="TEXT",
                        position=(fx + fwidth / 2.0, fy - 6.0),
                        value=f"{viewport.label}   {viewport.scale_text}",
                        height=3.5,
                        anchor=Anchor.CENTRE,
                        bold=True,
                    )
                )

        self._draw_title_block(sheet)
        self._draw_revisions(sheet)
        return sheet

    def _draw_title_block(self, sheet: Drawing) -> None:
        x, y, width, height = self.border
        rows = self.title_block.rows()
        block_height = self.block_row_height * len(rows)
        left = x + width - self.block_width
        bottom = y

        sheet.add(rectangle(left, bottom, self.block_width, block_height, "SHEET-HEAVY"))
        label_width = self.block_width * 0.38

        for index, (label, value) in enumerate(reversed(rows)):
            row_y = bottom + index * self.block_row_height
            if index:
                sheet.add(
                    Line(
                        layer="SHEET",
                        start=(left, row_y),
                        end=(left + self.block_width, row_y),
                    )
                )
            sheet.add(
                Text(
                    layer="TEXT",
                    position=(left + 2.0, row_y + self.block_row_height / 2.0),
                    value=label,
                    height=2.0,
                )
            )
            sheet.add(
                Text(
                    layer="TEXT",
                    position=(left + label_width + 2.0, row_y + self.block_row_height / 2.0),
                    value=value,
                    height=2.8,
                    bold=True,
                )
            )
        sheet.add(
            Line(
                layer="SHEET",
                start=(left + label_width, bottom),
                end=(left + label_width, bottom + block_height),
            )
        )

        # The company sits above the block, in its own band.
        if self.title_block.company:
            sheet.add(
                rectangle(left, bottom + block_height, self.block_width, 12.0, "SHEET-HEAVY")
            )
            sheet.add(
                Text(
                    layer="TEXT",
                    position=(left + self.block_width / 2.0, bottom + block_height + 7.5),
                    value=self.title_block.company,
                    height=4.5,
                    anchor=Anchor.CENTRE,
                    bold=True,
                )
            )
            if self.title_block.company_line:
                sheet.add(
                    Text(
                        layer="TEXT",
                        position=(left + self.block_width / 2.0, bottom + block_height + 2.5),
                        value=self.title_block.company_line,
                        height=2.5,
                        anchor=Anchor.CENTRE,
                    )
                )

        for index, note in enumerate(self.title_block.notes):
            sheet.add(
                Text(
                    layer="TEXT",
                    position=(x + 2.0, y + 3.0 + index * 4.0),
                    value=note,
                    height=2.8,
                )
            )

    def _draw_revisions(self, sheet: Drawing) -> None:
        """The revision table, growing upwards from just above the title block."""
        if not self.revisions:
            return
        x, y, width, _ = self.border
        rows = len(self.revisions)
        row_height = 5.0
        table_width = self.block_width
        left = x + width - table_width
        company_band = 12.0 if self.title_block.company else 0.0
        bottom = y + self.block_row_height * len(self.title_block.rows()) + company_band

        sheet.add(rectangle(left, bottom, table_width, row_height * (rows + 1), "SHEET"))
        columns = (0.0, 12.0, 40.0, table_width - 22.0)
        headings = ("Rev", "Date", "Description", "By")
        for offset, heading in zip(columns, headings):
            sheet.add(
                Text(
                    layer="TEXT",
                    position=(left + offset + 1.5, bottom + row_height * rows + row_height / 2.0),
                    value=heading,
                    height=2.0,
                )
            )
        for index, revision in enumerate(self.revisions):
            row_y = bottom + index * row_height
            sheet.add(
                Line(layer="SHEET", start=(left, row_y + row_height),
                     end=(left + table_width, row_y + row_height))
            )
            values = (
                revision.mark,
                revision.date.isoformat(),
                revision.description,
                revision.by,
            )
            for offset, value in zip(columns, values):
                sheet.add(
                    Text(
                        layer="TEXT",
                        position=(left + offset + 1.5, row_y + row_height / 2.0),
                        value=value,
                        height=2.2,
                    )
                )

    # -- output ------------------------------------------------------------- #
    def to_svg(self, **kwargs) -> str:
        from .svg import to_svg

        kwargs.setdefault("margin", 0.0)
        return to_svg(self.compose(), scale=1.0, **kwargs)

    def to_pdf(self, path, **kwargs):
        from .pdf import to_pdf

        return to_pdf(self.compose(), path, page_size=self.paper, **kwargs)

    def to_dxf(self, path, **kwargs):
        from .dxf import to_dxf

        return to_dxf(self.compose(), path, scale=1.0, **kwargs)


def grid_frames(
    area: tuple[float, float, float, float], columns: int, rows: int, gap: float = 8.0
) -> list[tuple[float, float, float, float]]:
    """Split a drawing area into a grid of view frames, top-left first.

    Details are read across then down, so the frames come back in that order
    rather than in the order the arithmetic produces them.
    """
    x, y, width, height = area
    cell_width = (width - gap * (columns - 1)) / columns
    cell_height = (height - gap * (rows - 1)) / rows
    frames: list[tuple[float, float, float, float]] = []
    for row in range(rows):
        for column in range(columns):
            frames.append(
                (
                    x + column * (cell_width + gap),
                    y + height - (row + 1) * cell_height - row * gap,
                    cell_width,
                    cell_height,
                )
            )
    return frames


__all__ = [
    "Revision",
    "STANDARD_SCALES",
    "Sheet",
    "SheetSize",
    "TitleBlock",
    "Viewport",
    "grid_frames",
]
