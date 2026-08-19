"""Vector PDF, written directly, because Hebrew on a drawing has to work.

There is no PDF library in this stack, and the two that exist for Python either
cannot embed a font with Hebrew coverage or cannot lay Hebrew out. Since a
title block that reads "דאדי בע\"מ" is not optional here, the file is written
out directly: a small PDF 1.7 with the drawing as one content stream and one
embedded TrueType font.

The parts that are easy to get wrong
------------------------------------
**The font.** Base-14 PDF fonts are Latin only, so the font is embedded as a
CIDFontType2 with Identity-H encoding: the text operator receives glyph indices
rather than characters, which sidesteps encoding entirely and is the only way
to reach a Hebrew glyph. Widths come from the font's own metrics, scaled to
PDF's 1000-unit em.

**The direction.** PDF has no bidirectional algorithm — it draws glyphs in the
order given. Hebrew stored in logical order therefore comes out backwards
unless it is reordered here. :func:`visual_order` does that: it splits the
string into runs by direction, reverses the Hebrew ones, mirrors the brackets
inside them, and, when the line as a whole is Hebrew, reverses the run order so
that an embedded number or Latin word still reads left to right in its place.
This is the useful subset of the Unicode algorithm, and Hebrew needs no letter
shaping, so it is correct for Hebrew. It is *not* enough for Arabic, which
needs contextual shaping; Arabic labels are drawn in SVG rather than here.

**The units.** A PDF point is 1/72 inch. Everything arriving here is in paper
millimetres, so it is multiplied by 72/25.4 once, at the boundary.
"""

from __future__ import annotations

import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..core.errors import ProfileOSError
from .model import (
    Anchor,
    Arc,
    Circle,
    Drawing,
    Hatch,
    HatchPattern,
    Line,
    Polyline,
    Text,
)

#: PDF points per millimetre.
MM = 72.0 / 25.4

#: Fonts with Hebrew coverage, in the order they are looked for.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_MIRRORED = {
    "(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{",
    "<": ">", ">": "<", "«": "»", "»": "«",
}


# --------------------------------------------------------------------------- #
# Direction
# --------------------------------------------------------------------------- #
def _direction(char: str) -> str:
    """``R`` for a right-to-left letter, ``L`` for left-to-right, ``N`` neutral."""
    category = unicodedata.bidirectional(char)
    if category in ("R", "AL"):
        return "R"
    if category in ("L",):
        return "L"
    if category in ("EN", "AN"):
        return "N"  # digits take the direction of what surrounds them
    return "N"


def base_direction(text: str) -> str:
    """The paragraph direction: the first strong character wins."""
    for char in text:
        direction = _direction(char)
        if direction in ("R", "L"):
            return direction
    return "L"


def visual_order(text: str) -> str:
    """Reorder logical text into the order the glyphs must be drawn in.

    Hebrew needs no shaping, so reordering is the whole job. Neutrals — spaces,
    punctuation — take the direction of the run they sit inside, and trailing
    neutrals fall to the base direction, which is what stops a full stop from
    ending up on the wrong end of a Hebrew sentence.
    """
    if not text:
        return text
    base = base_direction(text)
    if base == "L" and not any(_direction(c) == "R" for c in text):
        return text  # nothing to do for plain Latin

    # Group into runs, letting neutrals join the preceding strong run.
    runs: list[tuple[str, list[str]]] = []
    for char in text:
        direction = _direction(char)
        if direction == "N":
            direction = runs[-1][0] if runs else base
        if runs and runs[-1][0] == direction:
            runs[-1][1].append(char)
        else:
            runs.append((direction, [char]))

    pieces: list[str] = []
    for direction, chars in runs:
        chunk = "".join(chars)
        if direction == "R":
            # Reverse the Hebrew, mirror its brackets, then put any digit
            # groups back the right way round — a number inside Hebrew text
            # still reads left to right.
            reordered = "".join(_MIRRORED.get(c, c) for c in reversed(chunk))
            pieces.append(_restore_numbers(chunk, reordered))
        else:
            pieces.append(chunk)
    if base == "R":
        pieces.reverse()
    return "".join(pieces)


#: Separators that belong to a number when they sit between two digits:
#: a colon in "1:20", a hyphen in "02-9973510", a decimal point, a slash in a
#: date. Reversing these along with the Hebrew is what turns 1:20 into 20:1.
_NUMERIC_SEPARATORS = set(":.,/-+\u2013")


def _numeric_spans(text: str) -> list[tuple[int, int]]:
    """Half-open ranges covering digit groups and the separators inside them."""
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(text)
    while index < length:
        if not text[index].isdigit():
            index += 1
            continue
        start = index
        while index < length:
            if text[index].isdigit():
                index += 1
            elif (
                text[index] in _NUMERIC_SEPARATORS
                and index + 1 < length
                and text[index + 1].isdigit()
            ):
                index += 1
            else:
                break
        spans.append((start, index))
    return spans


def _restore_numbers(chunk: str, reversed_chunk: str) -> str:
    """Put numeric spans back in reading order after a run has been reversed.

    A number inside Hebrew text still reads left to right — "רוחב 1200" has the
    Hebrew reversed and the 1200 left alone — and so does anything joined to it
    by a separator, which is why "1:20" must not come out as "20:1".
    """
    length = len(chunk)
    characters = list(reversed_chunk)
    for start, end in _numeric_spans(chunk):
        low, high = length - end, length - start
        characters[low:high] = reversed(characters[low:high])
    return "".join(characters)


# --------------------------------------------------------------------------- #
# The font
# --------------------------------------------------------------------------- #
@dataclass
class EmbeddedFont:
    """A TrueType font, ready to be written into a PDF as a CID font."""

    name: str
    data: bytes
    units_per_em: int
    cmap: dict[int, str]
    glyph_order: list[str]
    widths: dict[str, int]
    bbox: tuple[int, int, int, int]
    ascent: int
    descent: int
    italic_angle: float
    cap_height: int
    stem_v: int = 80

    def glyph_id(self, char: str) -> int:
        name = self.cmap.get(ord(char))
        if name is None:
            return 0  # .notdef, which draws as an empty box — visible, not silent
        try:
            return self.glyph_order.index(name)
        except ValueError:  # pragma: no cover
            return 0

    def encode(self, text: str) -> bytes:
        """Glyph indices, big-endian, which is what Identity-H expects."""
        out = bytearray()
        for char in text:
            out += self.glyph_id(char).to_bytes(2, "big")
        return bytes(out)

    def width(self, text: str, size: float) -> float:
        """Advance width of ``text`` at ``size`` points."""
        total = 0
        for char in text:
            name = self.cmap.get(ord(char))
            total += self.widths.get(name or "", self.widths.get(".notdef", 500))
        return total * size / 1000.0


def load_font(path: str | Path | None = None) -> EmbeddedFont:
    """Read a TrueType font and pull out everything the PDF needs."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise ProfileOSError(
            "Writing PDF needs fontTools (pip install fonttools), which reads "
            "the font that carries the Hebrew glyphs."
        ) from exc

    candidates = [Path(path)] if path else [Path(p) for p in FONT_CANDIDATES]
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        raise ProfileOSError(
            "No font with Hebrew coverage was found. Install DejaVu Sans "
            "(package: fonts-dejavu-core) or pass a font path."
        )

    font = TTFont(str(source))
    upem = font["head"].unitsPerEm
    scale = 1000.0 / upem
    hmtx = font["hmtx"]
    widths = {name: int(round(hmtx[name][0] * scale)) for name in font.getGlyphOrder()}
    head = font["head"]
    os2 = font["OS/2"] if "OS/2" in font else None
    post = font["post"]
    return EmbeddedFont(
        name=str(font["name"].getDebugName(6) or source.stem).replace(" ", ""),
        data=source.read_bytes(),
        units_per_em=upem,
        cmap=font.getBestCmap(),
        glyph_order=list(font.getGlyphOrder()),
        widths=widths,
        bbox=(
            int(head.xMin * scale), int(head.yMin * scale),
            int(head.xMax * scale), int(head.yMax * scale),
        ),
        ascent=int((os2.sTypoAscender if os2 else head.yMax) * scale),
        descent=int((os2.sTypoDescender if os2 else head.yMin) * scale),
        italic_angle=float(post.italicAngle),
        cap_height=int((getattr(os2, "sCapHeight", None) or head.yMax) * scale),
    )


# --------------------------------------------------------------------------- #
# Writing the file
# --------------------------------------------------------------------------- #
def _hex_colour(colour: str) -> tuple[float, float, float]:
    text = colour.lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


class _Writer:
    """Assembles PDF objects and keeps their byte offsets for the xref."""

    def __init__(self) -> None:
        self.objects: list[bytes] = []

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def render(self, root: int) -> bytes:
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, body in enumerate(self.objects, start=1):
            offsets.append(len(out))
            out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
        xref_at = len(out)
        count = len(self.objects) + 1
        out += f"xref\n0 {count}\n".encode("ascii")
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += f"{offset:010d} 00000 n \n".encode("ascii")
        out += (
            f"trailer\n<< /Size {count} /Root {root} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
        ).encode("ascii")
        return bytes(out)


def _content_stream(
    drawing: Drawing, font: EmbeddedFont, height_pt: float, font_key: str
) -> bytes:
    """The page's drawing operators, in PDF's y-up coordinate system.

    PDF already has y increasing upwards, the same as the drawing model, so no
    flip is needed here — only the millimetre-to-point conversion.
    """
    out: list[str] = []

    def move(point: tuple[float, float]) -> str:
        return f"{point[0] * MM:.3f} {point[1] * MM:.3f}"

    for name, layer in drawing.layers.items():
        entities = drawing.on_layer(name)
        if not entities or not layer.printable:
            continue
        red, green, blue = _hex_colour(layer.colour)
        out.append("q")
        out.append(f"{red:.3f} {green:.3f} {blue:.3f} RG")
        out.append(f"{red:.3f} {green:.3f} {blue:.3f} rg")
        out.append(f"{max(layer.lineweight, 0.05) * MM:.3f} w")
        dashes = layer.line_type.dash_pattern()
        out.append(
            f"[{' '.join(f'{d * MM:.2f}' for d in dashes)}] 0 d" if dashes else "[] 0 d"
        )

        for entity in entities:
            if isinstance(entity, Line):
                out.append(f"{move(entity.start)} m {move(entity.end)} l S")
            elif isinstance(entity, Polyline):
                if len(entity.points) < 2:
                    continue
                path = f"{move(entity.points[0])} m " + " ".join(
                    f"{move(p)} l" for p in entity.points[1:]
                )
                if entity.closed:
                    path += " h"
                out.append(path + (" f" if entity.filled else " S"))
            elif isinstance(entity, Circle):
                out.append(_circle_path(entity.centre, entity.radius) + " S")
            elif isinstance(entity, Arc):
                points = entity.sample(48)
                out.append(
                    f"{move(points[0])} m "
                    + " ".join(f"{move(p)} l" for p in points[1:])
                    + " S"
                )
            elif isinstance(entity, Hatch):
                if len(entity.boundary) < 3:
                    continue
                path = f"{move(entity.boundary[0])} m " + " ".join(
                    f"{move(p)} l" for p in entity.boundary[1:]
                ) + " h"
                for hole in entity.holes:
                    if len(hole) < 3:
                        continue
                    path += f" {move(hole[0])} m " + " ".join(
                        f"{move(p)} l" for p in hole[1:]
                    ) + " h"
                if entity.fill:
                    fr, fg, fb = _hex_colour(entity.fill)
                    out.append(f"q {fr:.3f} {fg:.3f} {fb:.3f} rg {path} f* Q")
                elif entity.pattern is HatchPattern.NONE:
                    out.append(path + " S")
                else:
                    # A tint stands in for the pattern: PDF tiling patterns are
                    # a separate resource tree, and a flat tone at the right
                    # density reads correctly at plotting size while a wrong
                    # pattern does not.
                    out.append(f"q 0.86 0.86 0.86 rg {path} f* Q")
                    out.append(path + " S")
            elif isinstance(entity, Text):
                if not entity.value:
                    continue
                size = entity.height * MM
                shown = visual_order(entity.value)
                width = font.width(shown, size)
                shift = {
                    Anchor.LEFT: 0.0,
                    Anchor.CENTRE: -width / 2.0,
                    Anchor.RIGHT: -width,
                }[entity.anchor]
                x = entity.position[0] * MM
                y = entity.position[1] * MM - size * 0.35
                out.append("BT")
                out.append(f"/{font_key} {size:.3f} Tf")
                if entity.rotation:
                    from math import cos, radians, sin

                    angle = radians(entity.rotation)
                    ca, sa = cos(angle), sin(angle)
                    dx = shift * ca
                    dy = shift * sa
                    out.append(
                        f"{ca:.6f} {sa:.6f} {-sa:.6f} {ca:.6f} {x + dx:.3f} {y + dy:.3f} Tm"
                    )
                else:
                    out.append(f"1 0 0 1 {x + shift:.3f} {y:.3f} Tm")
                out.append(f"<{font.encode(shown).hex()}> Tj")
                out.append("ET")
        out.append("Q")
    return "\n".join(out).encode("latin-1", errors="replace")


def _circle_path(centre: tuple[float, float], radius: float) -> str:
    """A circle from four Bezier arcs — PDF has no circle operator."""
    k = 0.5522847498307936 * radius
    cx, cy = centre[0], centre[1]

    def pt(x: float, y: float) -> str:
        return f"{x * MM:.3f} {y * MM:.3f}"

    return (
        f"{pt(cx + radius, cy)} m "
        f"{pt(cx + radius, cy + k)} {pt(cx + k, cy + radius)} {pt(cx, cy + radius)} c "
        f"{pt(cx - k, cy + radius)} {pt(cx - radius, cy + k)} {pt(cx - radius, cy)} c "
        f"{pt(cx - radius, cy - k)} {pt(cx - k, cy - radius)} {pt(cx, cy - radius)} c "
        f"{pt(cx + k, cy - radius)} {pt(cx + radius, cy - k)} {pt(cx + radius, cy)} c h"
    )


def _used_glyphs(drawing: Drawing, font: EmbeddedFont) -> set[int]:
    used = {0}
    for entity in drawing:
        if isinstance(entity, Text):
            for char in visual_order(entity.value):
                used.add(font.glyph_id(char))
    return used


def to_pdf(
    drawing: Drawing,
    path: str | Path,
    *,
    page_size: tuple[float, float] = (420.0, 297.0),
    font_path: str | Path | None = None,
    title: str | None = None,
) -> Path:
    """Write a paper-space drawing to a PDF.

    ``drawing`` must already be in paper millimetres — the sheet composes it —
    and ``page_size`` is the paper in millimetres, A3 landscape by default.
    """
    font = load_font(font_path)
    writer = _Writer()

    content = _content_stream(drawing, font, page_size[1], "F1")
    compressed = zlib.compress(content)
    contents_ref = writer.add(
        b"<< /Length "
        + str(len(compressed)).encode("ascii")
        + b" /Filter /FlateDecode >>\nstream\n"
        + compressed
        + b"\nendstream"
    )

    used = sorted(_used_glyphs(drawing, font))
    font_data = zlib.compress(font.data)
    file_ref = writer.add(
        b"<< /Length "
        + str(len(font_data)).encode("ascii")
        + b" /Filter /FlateDecode /Length1 "
        + str(len(font.data)).encode("ascii")
        + b" >>\nstream\n"
        + font_data
        + b"\nendstream"
    )
    descriptor_ref = writer.add(
        (
            f"<< /Type /FontDescriptor /FontName /{font.name} /Flags 4 "
            f"/FontBBox [{font.bbox[0]} {font.bbox[1]} {font.bbox[2]} {font.bbox[3]}] "
            f"/ItalicAngle {font.italic_angle:g} /Ascent {font.ascent} "
            f"/Descent {font.descent} /CapHeight {font.cap_height} "
            f"/StemV {font.stem_v} /FontFile2 {file_ref} 0 R >>"
        ).encode("ascii")
    )
    order = font.glyph_order
    widths = " ".join(
        f"{gid} [{font.widths.get(order[gid], 500) if gid < len(order) else 500}]"
        for gid in used
        if gid
    )
    cid_ref = writer.add(
        (
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{font.name} "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {descriptor_ref} 0 R /DW 1000 /W [{widths}] "
            f"/CIDToGIDMap /Identity >>"
        ).encode("ascii")
    )
    font_ref = writer.add(
        (
            f"<< /Type /Font /Subtype /Type0 /BaseFont /{font.name} "
            f"/Encoding /Identity-H /DescendantFonts [{cid_ref} 0 R] >>"
        ).encode("ascii")
    )

    pages_ref = len(writer.objects) + 2  # written after the page
    page_ref = writer.add(
        (
            f"<< /Type /Page /Parent {pages_ref} 0 R "
            f"/MediaBox [0 0 {page_size[0] * MM:.3f} {page_size[1] * MM:.3f}] "
            f"/Resources << /Font << /F1 {font_ref} 0 R >> >> "
            f"/Contents {contents_ref} 0 R >>"
        ).encode("ascii")
    )
    writer.add(
        f"<< /Type /Pages /Kids [{page_ref} 0 R] /Count 1 >>".encode("ascii")
    )
    info_ref = writer.add(
        (
            "<< /Producer (ProfileOS) /Title ("
            + (title or drawing.name).replace("(", r"\(").replace(")", r"\)")
            + ") >>"
        ).encode("utf-8", errors="replace")
    )
    root = writer.add(
        f"<< /Type /Catalog /Pages {pages_ref} 0 R >>".encode("ascii")
    )
    del info_ref

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(writer.render(root))
    return target


__all__ = [
    "EmbeddedFont",
    "FONT_CANDIDATES",
    "MM",
    "base_direction",
    "load_font",
    "to_pdf",
    "visual_order",
]
