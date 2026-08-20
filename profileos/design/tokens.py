"""The design tokens: one place every surface takes its appearance from.

The rule this file enforces is that appearance is *decided once*. The desktop
application, the phone terminal, the quotation documents and the web site all
read these values — a colour or a radius that appears anywhere else in the
codebase is a bug, because it is a decision that will drift.

The choices, and why:

**One brand colour: anodised bronze.** The shop's own product — bronze-anodised
aluminium — gives the interface its accent. It is warm, it is not the default
blue of every unstyled dashboard, and it sits naturally on the warm graphite
neutrals. Everything else stays quiet so the one colour can mean something:
where the bronze is, that is the action or the selection.

**Warm neutrals.** Pure grey reads as unfinished. Every neutral here carries a
touch of the brand's warmth, from the near-black of the application background
to the paper tone of a printed quotation.

**Status is not brand.** Success, warning and danger are their own hues, used
small — a pill, a line of text — and chosen to stay legible against both the
dark surfaces and paper. Warning is a flat yellow, kept deliberately away from
the bronze so an alert never dresses like a button.

**Heebo.** A Hebrew-first family with true weights, shipped with the software
(OFL) so the interface looks the same offline in the workshop as it does
anywhere else. Numbers set in the tabular feature so columns of millimetres
align.

**The 8-point grid.** All spacing is a multiple of 8 (4 permitted for tight
in-component gaps). Radii, borders and motion come in one scale each.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).parent / "fonts"
FONT_TTF = FONT_DIR / "Heebo.ttf"
FONT_WOFF2 = FONT_DIR / "Heebo-web.woff2"

#: Font stacks. Heebo first; the fallbacks carry Hebrew everywhere.
FONT_FAMILY = "'Heebo','Segoe UI','Noto Sans Hebrew',Arial,sans-serif"
FONT_MONO = "'SF Mono',ui-monospace,Menlo,Consolas,monospace"


# --------------------------------------------------------------------------- #
# Colour
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Brand:
    """The bronze ramp. 500 is *the* brand colour; the rest serve states.

    Used the way the best products use their one colour: primary action,
    selection, focus — and never decoration. On the near-black neutrals the
    ramp runs brighter than it would on paper, or it reads as mud.
    """

    x300: str = "#F0B269"   # accent text on dark, the selected nav item
    x400: str = "#E29A47"   # hover
    x500: str = "#D07E2F"   # the colour: primary buttons, selection, links
    x600: str = "#B0671F"   # pressed
    x700: str = "#8A5019"   # borders of filled elements on light
    #: Translucent bronze washes for selected rows and subtle fills.
    wash: str = "rgba(224,154,71,0.13)"
    wash_strong: str = "rgba(224,154,71,0.22)"


@dataclass(frozen=True)
class Dark:
    """The application surfaces: near-black neutrals, elevation by luminance.

    The pattern every serious dark product converges on: a base a hair above
    black, each layer one small luminance step up, hairline borders instead of
    shadows, and near-white primary text. The warmth here is two percent — a
    hint the eye reads as material rather than as brown.
    """

    bg: str = "#0F0E0D"        # the window itself
    surface: str = "#151413"   # cards, panels, the sidebar
    raised: str = "#1C1A18"    # inputs, hovered rows, popovers
    sunken: str = "#0A0908"    # wells: code, drawings, charts
    line: str = "#262320"      # hairlines between things
    line_strong: str = "#383430"  # input borders, dividers that must be seen
    text: str = "#F4F2EF"
    muted: str = "#A7A199"
    faint: str = "#6C6862"     # placeholders, disabled


@dataclass(frozen=True)
class Paper:
    """Printed and customer-facing surfaces: warm white, dark warm ink."""

    bg: str = "#FBF8F4"
    surface: str = "#FFFFFF"
    tint: str = "#F4EEE6"      # table headers, soft panels
    line: str = "#E6DECF"
    line_strong: str = "#C9BEA9"
    text: str = "#241E17"
    muted: str = "#6E6252"
    faint: str = "#9C907D"


@dataclass(frozen=True)
class Status:
    ok: str = "#3FA672"
    ok_wash: str = "rgba(63,166,114,0.15)"
    warn: str = "#D9B13B"
    warn_wash: str = "rgba(217,177,59,0.15)"
    danger: str = "#DE5D5D"
    danger_wash: str = "rgba(222,93,93,0.15)"


BRAND = Brand()
DARK = Dark()
PAPER = Paper()
STATUS = Status()


# --------------------------------------------------------------------------- #
# Type, space, shape, motion
# --------------------------------------------------------------------------- #
#: The five sizes. Web pixels; the Qt theme maps them to points.
TYPE_SCALE = {
    "display": 26,   # the one big number or name on a screen
    "title": 19,     # page titles
    "heading": 15,   # card/section headings
    "body": 14,      # everything
    "caption": 12,   # labels, hints, table headers
}

#: Weights that exist in the interface. Nothing between or beyond.
WEIGHTS = {"regular": 400, "medium": 500, "semibold": 600, "bold": 700}


def space(steps: float) -> int:
    """The 8-point grid. ``space(1)`` = 8px; halves allowed for tight gaps."""
    return int(round(8 * steps))


RADIUS = {"sm": 6, "md": 10, "lg": 14, "pill": 999}

#: Shadows for the web surfaces. Qt approximates elevation with borders.
SHADOW = {
    "sm": "0 1px 2px rgba(12,9,6,0.25)",
    "md": "0 4px 14px rgba(12,9,6,0.32)",
    "lg": "0 12px 36px rgba(12,9,6,0.42)",
    "paper": "0 1px 3px rgba(36,30,23,0.10), 0 8px 26px rgba(36,30,23,0.07)",
}

#: Motion: two durations and one curve, everywhere.
MOTION = {"fast_ms": 150, "base_ms": 200, "ease": "cubic-bezier(0.2, 0, 0, 1)"}


# --------------------------------------------------------------------------- #
# Delivery to each surface
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def font_data_uri() -> str:
    """The web subset of Heebo as a data URI, for self-contained pages."""
    return "data:font/woff2;base64," + base64.b64encode(FONT_WOFF2.read_bytes()).decode("ascii")


@lru_cache(maxsize=2)
def font_face_css(embed: bool = True) -> str:
    """The ``@font-face`` rule. Embedded by default: the phone terminal and the
    quotation documents must render identically with no network at all."""
    source = font_data_uri() if embed else "https://fonts.gstatic.com/s/heebo/v26/NGSpv5_NC0k9P_v6ZUCbLRAHxK1EiSysd0mm_00.woff2"
    return (
        "@font-face{font-family:'Heebo';font-style:normal;font-weight:100 900;"
        f"font-display:swap;src:url({source}) format('woff2');}}"
    )


def css_variables(*, dark: bool = True) -> str:
    """The tokens as CSS custom properties, for every HTML surface."""
    neutral = DARK if dark else PAPER
    pairs = {
        "brand-300": BRAND.x300, "brand-400": BRAND.x400, "brand": BRAND.x500,
        "brand-600": BRAND.x600, "brand-700": BRAND.x700,
        "brand-wash": BRAND.wash, "brand-wash-strong": BRAND.wash_strong,
        "bg": neutral.bg, "surface": neutral.surface,
        "raised": getattr(neutral, "raised", getattr(neutral, "tint", neutral.surface)),
        "sunken": getattr(neutral, "sunken", getattr(neutral, "tint", neutral.bg)),
        "line": neutral.line, "line-strong": neutral.line_strong,
        "text": neutral.text, "muted": neutral.muted, "faint": neutral.faint,
        "ok": STATUS.ok, "ok-wash": STATUS.ok_wash,
        "warn": STATUS.warn, "warn-wash": STATUS.warn_wash,
        "danger": STATUS.danger, "danger-wash": STATUS.danger_wash,
        "radius-sm": f"{RADIUS['sm']}px", "radius-md": f"{RADIUS['md']}px",
        "radius-lg": f"{RADIUS['lg']}px",
        "shadow-sm": SHADOW["sm"], "shadow-md": SHADOW["md"], "shadow-lg": SHADOW["lg"],
        "font": FONT_FAMILY, "mono": FONT_MONO,
        "fast": f"{MOTION['fast_ms']}ms", "base": f"{MOTION['base_ms']}ms",
        "ease": MOTION["ease"],
        "size-display": f"{TYPE_SCALE['display']}px", "size-title": f"{TYPE_SCALE['title']}px",
        "size-heading": f"{TYPE_SCALE['heading']}px", "size-body": f"{TYPE_SCALE['body']}px",
        "size-caption": f"{TYPE_SCALE['caption']}px",
    }
    return ":root{" + "".join(f"--{key}:{value};" for key, value in pairs.items()) + "}"


__all__ = [
    "BRAND", "DARK", "FONT_DIR", "FONT_FAMILY", "FONT_MONO", "FONT_TTF",
    "FONT_WOFF2", "MOTION", "PAPER", "RADIUS", "SHADOW", "STATUS",
    "TYPE_SCALE", "WEIGHTS", "css_variables", "font_data_uri",
    "font_face_css", "space",
]
