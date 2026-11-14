"""Barcode and QR code generation for shop-floor tracking.

Every cut piece, glass pane and assembled frame gets a machine-readable label.
Two symbologies cover the floor:

**Code 128** for linear scanners, which is what most saw and machining-centre
controls have built in. Implemented here directly — the encoding is small
enough that a dependency is not worth it, and it emits SVG that prints
crisply at any size.

**QR** for tablets, which carry far more payload (a whole job-card URL plus the
piece identity) and survive the partial damage a workshop inflicts on labels.
Delegated to ``segno`` when installed; otherwise the QR helpers raise a clear
error rather than emitting an unscannable placeholder.

Payload format
--------------
Codes carry a structured, human-readable identifier rather than an opaque
number, so that a label remains meaningful when the database is not to hand::

    POS|<project>|<element>|<piece>|<stage>
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..core.errors import ProfileOSError

#: Code 128 bar/space width patterns, indexed by symbol value 0-106.
_CODE128_PATTERNS: tuple[str, ...] = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232",
    # Value 106 is the stop character. Unlike every other symbol it is seven
    # elements (13 modules) rather than six (11): it carries an extra
    # terminating bar. Truncating it to six produces a symbol that trailing-bar
    # checks in many scanners reject.
    "2331112",
)

_START_B = 104
_STOP = 106


def _code128b_values(data: str) -> list[int]:
    """Encode ASCII text as Code 128 subset B symbol values, with checksum.

    Subset B covers printable ASCII 32-126, which is everything a piece
    identifier needs and avoids the subset switching that trips up scanners.
    """
    for character in data:
        if not (32 <= ord(character) <= 126):
            raise ProfileOSError(
                "Code 128 subset B supports printable ASCII only",
                character=character,
                data=data,
            )

    values = [_START_B]
    values.extend(ord(character) - 32 for character in data)

    # Modulo-103 weighted checksum; the start character has weight 1.
    checksum = _START_B
    for position, value in enumerate(values[1:], start=1):
        checksum += value * position
    values.append(checksum % 103)
    values.append(_STOP)
    return values


def code128_svg(
    data: str,
    *,
    module_width: float = 1.0,
    height: float = 40.0,
    quiet_zone: float = 10.0,
    show_text: bool = True,
    text_height: float = 10.0,
) -> str:
    """Render ``data`` as a Code 128 barcode in SVG.

    ``module_width`` is the width of one narrow bar; scanners generally need at
    least 0.25 mm, so a label printed at 1 module = 1 px at 96 dpi is safe.
    """
    values = _code128b_values(data)
    # Each pattern is six digits: alternating bar and space widths in modules.
    modules = "".join(_CODE128_PATTERNS[value] for value in values)

    bars: list[str] = []
    cursor = quiet_zone
    is_bar = True
    for digit in modules:
        width = int(digit) * module_width
        if is_bar:
            bars.append(
                f'<rect x="{cursor:.3f}" y="0" width="{width:.3f}" '
                f'height="{height:.3f}" fill="#000"/>'
            )
        cursor += width
        is_bar = not is_bar

    total_width = cursor + quiet_zone
    total_height = height + (text_height + 4.0 if show_text else 0.0)

    text = ""
    if show_text:
        text = (
            f'<text x="{total_width / 2:.3f}" y="{height + text_height:.3f}" '
            f'font-family="monospace" font-size="{text_height:.2f}" '
            f'text-anchor="middle" fill="#000">{_escape(data)}</text>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width:.3f}" '
        f'height="{total_height:.3f}" viewBox="0 0 {total_width:.3f} {total_height:.3f}">'
        f'<rect width="100%" height="100%" fill="#fff"/>'
        f'{"".join(bars)}{text}</svg>'
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def qr_available() -> bool:
    """True when a QR backend is installed."""
    try:
        import segno  # noqa: F401

        return True
    except ImportError:
        return False


def qr_svg(data: str, *, scale: int = 4, border: int = 2, dark: str = "#000") -> str:
    """Render ``data`` as a QR code in SVG.

    Raises
    ------
    ProfileOSError
        No QR backend is installed. Emitting a placeholder instead would put an
        unscannable label on the shop floor, which is worse than failing.
    """
    try:
        import io

        import segno
    except ImportError as exc:
        raise ProfileOSError(
            "QR generation needs the 'segno' package (pip install segno)"
        ) from exc

    # Error correction M tolerates ~15% damage, which suits a workshop label.
    code = segno.make(data, error="m")
    # segno's SVG writer emits bytes, so the buffer must be binary.
    buffer = io.BytesIO()
    code.save(buffer, kind="svg", scale=scale, border=border, dark=dark, xmldecl=False)
    return buffer.getvalue().decode("utf-8")


def qr_data_uri(data: str, *, scale: int = 4, border: int = 2) -> str:
    """Render a QR code as a ``data:`` URI, for embedding in an HTML job card."""
    import base64

    svg = qr_svg(data, scale=scale, border=border)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


@dataclass(frozen=True)
class TrackingCode:
    """A structured identifier printed on a shop-floor label."""

    project: str
    element: str
    piece: str
    stage: str = "CUT"
    prefix: str = "POS"

    def payload(self) -> str:
        """The string encoded in the barcode/QR."""
        return "|".join([self.prefix, self.project, self.element, self.piece, self.stage])

    @classmethod
    def parse(cls, payload: str) -> "TrackingCode":
        """Decode a scanned payload.

        Raises
        ------
        ProfileOSError
            The payload is not a ProfileOS tracking code.
        """
        parts = payload.strip().split("|")
        if len(parts) != 5:
            raise ProfileOSError(
                "Not a ProfileOS tracking code", payload=payload, fields=len(parts)
            )
        prefix, project, element, piece, stage = parts
        return cls(
            project=project, element=element, piece=piece, stage=stage, prefix=prefix
        )

    def barcode_svg(self, **kwargs: Any) -> str:
        return code128_svg(self.payload(), **kwargs)

    def qr_svg(self, **kwargs: Any) -> str:
        return qr_svg(self.payload(), **kwargs)

    def short_label(self) -> str:
        """Compact human-readable form for the label's printed text."""
        return f"{self.element}/{self.piece}"


def batch_codes(codes: Iterable[TrackingCode]) -> dict[str, str]:
    """Render a batch of codes to SVG, keyed by payload."""
    return {code.payload(): code.barcode_svg() for code in codes}


__all__ = [
    "code128_svg",
    "qr_svg",
    "qr_data_uri",
    "qr_available",
    "TrackingCode",
    "batch_codes",
]
