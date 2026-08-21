"""The icon set, drawn rather than downloaded.

Every glyph is a few stroked paths on a 24-unit grid, defined here as SVG and
rendered to a pixmap at the size and colour asked for. Three reasons it is done
this way rather than by shipping image files:

*One weight.* Every icon is a 1.6-unit stroke with round caps, so the set reads
as one hand rather than a collection.

*Two colours from the palette.* A navigation icon is muted until its page is
open and bronze when it is, and both come from the same source as the text
beside them.

*No files to lose.* The icons live in the module that draws them, so a build
that imports cannot be missing its artwork.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

#: name -> the body of a 24x24 SVG. ``__C__`` is replaced with the colour.
_PATHS: dict[str, str] = {
    # A house: the front elevation of the simplest building there is.
    "home": "<path d='M4 11 12 4l8 7'/><path d='M6.5 9.6V20h11V9.6'/>"
            "<path d='M10 20v-5.5h4V20'/>",
    # A folder with a raised tab: the job dossier.
    "folder": "<path d='M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2.4h9A1.5 1.5 0 0 1 21 10v8.5"
              "A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5z'/>",
    # A hollow extrusion seen end-on: chambers inside a wall.
    "section": "<rect x='3.2' y='6' width='17.6' height='12' rx='1.4'/>"
               "<path d='M9 6v12M15 6v12M3.2 12h17.6'/>",
    # A window: frame, mullion, transom.
    "window": "<rect x='3.5' y='3.5' width='17' height='17' rx='1.4'/>"
              "<path d='M12 3.5v17M3.5 10.5h17'/>",
    # A cube in isometric: the presentation view.
    "cube": "<path d='M12 3 20.5 7.6v8.8L12 21l-8.5-4.6V7.6z'/>"
            "<path d='m3.5 7.6 8.5 4.7 8.5-4.7M12 12.3V21'/>",
    # Stacked bars of differing length: the cutting plan.
    "bars": "<path d='M3.5 6.5h17M3.5 12h11M3.5 17.5h14'/>"
            "<path d='M20.5 6.5v0M14.5 12v0M17.5 17.5v0'/>",
    # A pane with its corner reflection.
    "glass": "<rect x='3.5' y='3.5' width='17' height='17' rx='1.4'/>"
             "<path d='m7 17 10-10'/><path d='m12 17 5-5'/>",
    # A spindle over the work: machining.
    "tool": "<path d='M12 3v6'/><path d='M8.5 9h7l-1.2 4.5H9.7z'/>"
            "<path d='M12 13.5V21'/><path d='M8 21h8'/>",
    # A document with a price line.
    "document": "<path d='M6 3.5h8L18.5 8v12.5h-12.5z'/><path d='M13.6 3.6V8.4h4.8'/>"
                "<path d='M9 13h6M9 16.5h4'/>",
    # A ledger: two columns that must balance.
    "ledger": "<rect x='3.5' y='4.5' width='17' height='15' rx='1.4'/>"
              "<path d='M12 4.5v15M3.5 9h17'/>",
    # A barcode: what the shop floor scans.
    "barcode": "<path d='M4 5.5v13M7.2 5.5v13M10 5.5v9M12.8 5.5v13"
               "M16 5.5v9M18.8 5.5v13M21 5.5v13'/>",
    # Books on a shelf: the catalogue.
    "books": "<path d='M4 20V5.2a1 1 0 0 1 1-1h3.2a1 1 0 0 1 1 1V20z'/>"
             "<path d='M9.2 20V6.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V20z'/>"
             "<path d='m14.9 20 2.4-12.6a1 1 0 0 1 1.2-.8l1.6.3L18 20z'/>",
    # A sheet with a corner fold and a dimension line: the drawing set.
    "sheet": "<path d='M5.5 3.5h9L18.5 7.5v13h-13z'/><path d='M14.2 3.6v4.1h4.2'/>"
             "<path d='M8 12.5h7.5'/><path d='M8 11.4v2.2M15.5 11.4v2.2'/>"
             "<path d='M8 16.5h5'/>",
    # A pipe with a valve on it: the plumbing side of the job.
    "pipe": "<path d='M3 9.5h6.2v5H3zM14.8 9.5H21v5h-6.2z'/>"
            "<path d='M9.2 8h5.6v8H9.2z'/><path d='M12 8V4.5'/>"
            "<path d='M9.6 4.5h4.8'/>",
    # A gear, drawn as an octagon with a hub so it stays legible at 18 px.
    "gear": "<path d='M9.6 3.6h4.8l1.4 2.4 2.8.4 1.4 2.4-1.4 2.4 1.4 2.4-1.4 2.4"
            "-2.8.4-1.4 2.4H9.6l-1.4-2.4-2.8-.4L4 15.6 5.4 13.2 4 10.8l1.4-2.4"
            " 2.8-.4z'/><circle cx='12' cy='12' r='3.1'/>",
}

#: Which glyph belongs to which page, by the page's stable title.
#: A wrench for the calls that come back, and a cheque for the money that has
#: been promised. Drawn on the same 24-unit grid and stroke as the rest.
_PATHS["wrench"] = (
    "<path d='M15.5 3.5a5 5 0 0 0-4.6 6.9L3.8 17.5a1.7 1.7 0 0 0 2.4 2.4l7.1-7.1"
    "A5 5 0 1 0 15.5 3.5z'/><path d='M15.6 8.4v0'/>"
)
_PATHS["lorry"] = (
    "<path d='M2.8 7.4h10.4v9H2.8z'/><path d='M13.2 10.4h4l3 3v3h-7z'/>"
    "<circle cx='7' cy='18' r='1.7'/><circle cx='16.6' cy='18' r='1.7'/>"
)
_PATHS["cheque"] = (
    "<rect x='2.8' y='6' width='18.4' height='12' rx='1.6'/>"
    "<path d='M6.4 11h6M6.4 14h3.6'/><path d='m15 13.6 1.7 1.7 3-3.6'/>"
)

PAGE_ICONS: dict[str, str] = {
    "Home": "home",
    "Projects": "folder",
    "Profile": "section",
    "Element": "window",
    "3D view": "cube",
    "Drawings": "sheet",
    "Nesting": "bars",
    "Glass": "glass",
    "Machining": "tool",
    "Quotation": "document",
    "Accounts": "ledger",
    "Shop floor": "barcode",
    "Delivery": "lorry",
    "Service": "wrench",
    "Collection": "cheque",
    "Plumbing": "pipe",
    "Catalogue": "books",
    "System": "gear",
}

_TEMPLATE = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    "stroke='__C__' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'>{body}</svg>"
)


@lru_cache(maxsize=256)
def pixmap(name: str, colour: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    """One glyph, rendered at ``size`` points in ``colour``.

    Rendered at twice the logical size and marked as such, so the stroke stays
    crisp on a high-density display instead of being scaled up from 18 px.
    """
    body = _PATHS.get(name)
    if body is None:
        return QPixmap()
    svg = _TEMPLATE.format(body=body).replace("__C__", colour)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixels = max(1, int(size * ratio))
    image = QPixmap(pixels, pixels)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


@lru_cache(maxsize=256)
def icon(name: str, colour: str, active_colour: str = "", size: int = 18) -> QIcon:
    """A two-state icon: ``colour`` at rest, ``active_colour`` when checked."""
    result = QIcon()
    result.addPixmap(pixmap(name, colour, size), QIcon.Mode.Normal, QIcon.State.Off)
    lit = pixmap(name, active_colour or colour, size)
    result.addPixmap(lit, QIcon.Mode.Normal, QIcon.State.On)
    result.addPixmap(lit, QIcon.Mode.Active, QIcon.State.Off)
    return result


def page_icon(title: str, colour: str, active_colour: str = "", size: int = 18) -> QIcon:
    """The icon for a page, by its stable title. Unknown pages get no icon."""
    name = PAGE_ICONS.get(title)
    return icon(name, colour, active_colour, size) if name else QIcon()


__all__ = ["PAGE_ICONS", "icon", "page_icon", "pixmap"]
