"""The desktop skin: Qt reading the design tokens.

Every value here derives from :mod:`profileos.design.tokens` — the bronze, the
warm graphite neutrals, Heebo, the 8-point grid. This module only translates
them into what Qt understands: a :class:`Palette` for code that draws, and one
stylesheet for everything widget-shaped.

Two Qt-specific decisions:

*The drawing canvas keeps its own colours.* Aluminium sections are drawn in a
cool steel range on the warm dark well — metal should read as metal, and the
contrast of cool material on warm chrome is what makes the drawing the
brightest, clearest thing on the screen. The bronze appears in a drawing only
as the highlight of the selected thing.

*The interface is right-to-left.* Hebrew is the working language, so the
application runs mirrored and the stylesheet's literal sides are written for
that. Numbers inside text stay left-to-right on their own; Qt's bidi engine
handles runs correctly once the fonts carry the glyphs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..design import BRAND, DARK as TOKENS_DARK, MOTION, PAPER, RADIUS, STATUS, TYPE_SCALE


class Mode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True)
class Palette:
    """A complete colour set. Every UI colour comes from here."""

    # Surfaces, from furthest back to nearest front.
    canvas: str
    surface: str
    surface_raised: str
    surface_sunken: str
    border: str
    border_strong: str

    # Text.
    text: str
    text_muted: str
    text_faint: str
    text_inverse: str

    # Accent and state.
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_subtle: str
    success: str
    warning: str
    danger: str
    info: str

    # Drawing canvas colours.
    draw_material: str
    draw_material_edge: str
    draw_void: str
    draw_grid: str
    draw_axis: str
    draw_dimension: str
    draw_highlight: str
    draw_glass: str
    draw_frame: str
    draw_sash: str

    # Nesting diagram.
    bar_stock: str
    bar_piece: str
    bar_piece_alt: str
    bar_kerf: str
    bar_remnant_good: str
    bar_remnant_scrap: str

    mode: Mode = Mode.DARK

    def chart_series(self) -> list[str]:
        """Categorical colours for charts, ordered for maximum separation."""
        return [
            self.accent, "#5B8DB8", "#3FA672", "#B97BC9",
            "#D9B13B", "#4FA8A4", "#C97070", "#8C8CC9",
        ]


DARK = Palette(
    canvas=TOKENS_DARK.bg,
    surface=TOKENS_DARK.surface,
    surface_raised=TOKENS_DARK.raised,
    surface_sunken=TOKENS_DARK.sunken,
    border=TOKENS_DARK.line,
    border_strong=TOKENS_DARK.line_strong,
    text=TOKENS_DARK.text,
    text_muted=TOKENS_DARK.muted,
    text_faint=TOKENS_DARK.faint,
    text_inverse="#140E07",
    accent=BRAND.x500,
    accent_hover=BRAND.x400,
    accent_pressed=BRAND.x600,
    accent_subtle="#2A1F12",
    success=STATUS.ok,
    warning=STATUS.warn,
    danger=STATUS.danger,
    info="#5B8DB8",
    # The metal: a cool steel range on the warm dark well.
    draw_material="#4E7CA6",
    draw_material_edge="#A8CBE8",
    draw_void=TOKENS_DARK.sunken,
    draw_grid="#221E19",
    draw_axis="#4A4336",
    draw_dimension=TOKENS_DARK.muted,
    draw_highlight=BRAND.x400,
    draw_glass="#57A8B5",
    draw_frame="#5B84AE",
    draw_sash="#7DA3C9",
    bar_stock=TOKENS_DARK.raised,
    bar_piece="#4E7CA6",
    bar_piece_alt="#6E97BC",
    bar_kerf=STATUS.danger,
    bar_remnant_good=STATUS.ok,
    bar_remnant_scrap="#5E564B",
    mode=Mode.DARK,
)

LIGHT = Palette(
    canvas=PAPER.bg,
    surface=PAPER.surface,
    surface_raised=PAPER.tint,
    surface_sunken=PAPER.tint,
    border=PAPER.line,
    border_strong=PAPER.line_strong,
    text=PAPER.text,
    text_muted=PAPER.muted,
    text_faint=PAPER.faint,
    text_inverse="#FFFFFF",
    accent=BRAND.x600,
    accent_hover=BRAND.x500,
    accent_pressed=BRAND.x700,
    accent_subtle="#F4E3D2",
    success="#237A50",
    warning="#8A6A14",
    danger="#B23838",
    info="#3A6C96",
    draw_material="#A9C4DC",
    draw_material_edge="#2D5A85",
    draw_void=PAPER.surface,
    draw_grid="#EEE7DA",
    draw_axis="#C4B9A4",
    draw_dimension=PAPER.muted,
    draw_highlight=BRAND.x600,
    draw_glass="#BFE0E6",
    draw_frame="#8FB0CE",
    draw_sash="#AFC8E0",
    bar_stock=PAPER.tint,
    bar_piece="#4E7CA6",
    bar_piece_alt="#7DA3C9",
    bar_kerf="#B23838",
    bar_remnant_good="#237A50",
    bar_remnant_scrap="#B3A894",
    mode=Mode.LIGHT,
)


@dataclass
class Metrics:
    """Spacing on the 8-point grid; radii and type from the tokens."""

    unit: int = 4                       # space(2) == 8px: the grid step
    radius: int = RADIUS["md"]
    radius_small: int = RADIUS["sm"]
    radius_large: int = RADIUS["lg"]
    border_width: int = 1
    sidebar_width: int = 232
    panel_width: int = 344
    row_height: int = 30
    control_height: int = 30

    font_size: int = 13
    font_size_small: int = 11
    font_size_large: int = TYPE_SCALE["heading"]
    font_size_title: int = 18

    #: Which of :data:`DENSITIES` is in force, for code that wants to ask.
    density: str = "comfortable"

    #: Motion, for the code that animates (QSS cannot).
    motion_fast_ms: int = MOTION["fast_ms"]
    motion_base_ms: int = MOTION["base_ms"]

    def space(self, multiplier: int = 1) -> int:
        return self.unit * multiplier


METRICS = Metrics()

#: The three densities the shell runs at, chosen from the screen it opens on.
#: A laptop the shop already owns is a 14-inch panel at 1366×768; the same
#: layout that breathes on the office monitor arrives there squeezed, with
#: table rows clipped and cards fighting for the last hundred pixels. Rather
#: than design twice, the grid step and the control heights come down a notch
#: and everything built on them follows, because every margin, row and font in
#: this interface is derived from these numbers.
DENSITIES: dict[str, dict[str, int]] = {
    "comfortable": {
        "unit": 4, "sidebar_width": 232, "panel_width": 344,
        "row_height": 30, "control_height": 30,
        "font_size": 13, "font_size_small": 11, "font_size_title": 18,
    },
    "compact": {
        "unit": 3, "sidebar_width": 200, "panel_width": 300,
        "row_height": 26, "control_height": 26,
        "font_size": 12, "font_size_small": 10, "font_size_title": 16,
    },
    "tight": {
        "unit": 3, "sidebar_width": 176, "panel_width": 268,
        "row_height": 24, "control_height": 24,
        "font_size": 11, "font_size_small": 10, "font_size_title": 15,
    },
}


def density_for(width: int, height: int) -> str:
    """Which density a screen of this size should run at.

    The height decides it. Width can be given back by collapsing the sidebar;
    vertical space cannot be given back at all, and it is the axis a header, a
    row of figures, a drawing and a table have to share.
    """
    if height < 800 or width < 1280:
        return "tight" if height < 720 or width < 1100 else "compact"
    return "comfortable"


def set_density(name: str) -> str:
    """Apply a density to the shared metrics. Call before building the window."""
    values = DENSITIES.get(name)
    if values is None:
        return METRICS.density
    for key, value in values.items():
        setattr(METRICS, key, value)
    METRICS.font_size_large = max(METRICS.font_size + 2, TYPE_SCALE["heading"] - 2)
    METRICS.density = name
    return name


def fit_to_screen(width: int, height: int) -> str:
    """Pick and apply the density for a screen of this size."""
    return set_density(density_for(width, height))

#: Heebo first — it ships with the software; the fallbacks carry Hebrew too.
UI_FONTS = ("Heebo", "Segoe UI", "Noto Sans Hebrew", "DejaVu Sans", "sans-serif")
MONO_FONTS = ("JetBrains Mono", "SF Mono", "Cascadia Mono", "DejaVu Sans Mono", "monospace")


def stylesheet(palette: Palette, metrics: Metrics = METRICS) -> str:
    """Build the Qt stylesheet for a palette.

    Literal sides (borders, text alignment) are written for a right-to-left
    application: the sidebar sits on the right, so its hairline is on its left.
    """
    p, m = palette, metrics
    font = ", ".join(f'"{name}"' for name in UI_FONTS)
    mono = ", ".join(f'"{name}"' for name in MONO_FONTS)

    return f"""
/* ---------- base ---------- */
QWidget {{
    background: {p.canvas};
    color: {p.text};
    font-family: {font};
    font-size: {m.font_size}px;
}}
QMainWindow, QDialog {{ background: {p.canvas}; }}
/* QWidget's background cascades to child labels, which would paint the canvas
   colour on top of whatever card they sit in. Labels are text, not surfaces. */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QLabel#Muted {{ color: {p.text_muted}; }}
QToolTip {{
    background: {p.surface_raised}; color: {p.text};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px; padding: {m.space(2)}px;
}}

/* ---------- sidebar ---------- */
#Sidebar {{
    background: {p.surface};
    border-left: {m.border_width}px solid {p.border};
}}
#SidebarLogo {{
    font-size: {m.font_size_large + 2}px; font-weight: 700;
    color: {p.text}; padding: {m.space(5)}px {m.space(5)}px {m.space(1)}px;
}}
#SidebarVersion {{
    font-size: {m.font_size_small}px; color: {p.text_faint};
    padding: 0 {m.space(5)}px {m.space(4)}px;
}}
#SidebarSection {{
    font-size: 10px; font-weight: 600;
    color: {p.text_faint}; letter-spacing: 0.08em;
    padding: {m.space(4)}px {m.space(4)}px {m.space(1)}px;
}}
QPushButton#NavButton {{
    background: transparent; border: none; border-radius: {m.radius_small}px;
    color: {p.text_muted}; text-align: right;
    padding: 0; margin: 1px {m.space(2)}px; min-height: 32px;
    font-size: {m.font_size}px; font-weight: 500;
}}
QLabel#NavLabel {{ font-size: {m.font_size}px; }}
QPushButton#NavButton:hover {{ background: {p.surface_raised}; color: {p.text}; }}
QPushButton#NavButton:checked {{
    background: {p.accent_subtle}; color: {BRAND.x300}; font-weight: 600;
}}

/* ---------- header ---------- */
#PageHeader {{
    background: {p.surface};
    border-bottom: {m.border_width}px solid {p.border};
}}
#PageTitle {{ font-size: {m.font_size_title}px; font-weight: 700; color: {p.text}; }}
#PageSubtitle {{ font-size: {m.font_size_small + 1}px; color: {p.text_muted}; }}

/* ---------- cards and panels ---------- */
#Card {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
}}
#CardTitle {{
    font-size: {m.font_size_small}px; font-weight: 600;
    color: {p.text_faint}; letter-spacing: 0.05em;
}}
#StatValue {{
    font-size: 21px; font-weight: 600; color: {p.text};
    font-family: {mono};
}}
#StatLabel {{ font-size: {m.font_size_small}px; color: {p.text_muted}; }}
#Canvas {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
}}
#EmptyTitle {{ font-size: {m.font_size_large}px; font-weight: 600; color: {p.text}; }}
#EmptyBody {{ color: {p.text_muted}; }}
#EmptyGlyph {{ color: {p.text_faint}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px;
    color: {p.text}; padding: 0 {m.space(4)}px;
    min-height: {m.control_height}px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {p.border}; border-color: {p.text_faint}; }}
QPushButton:pressed {{ background: {p.surface_sunken}; }}
QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface}; }}
QPushButton#Primary {{
    background: {p.accent}; border-color: {p.accent};
    color: #FFF6EC; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}
QPushButton#Ghost {{
    background: transparent; border: {m.border_width}px solid {p.border_strong};
}}
QPushButton#Ghost:hover {{ background: {p.surface_raised}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px;
    color: {p.text}; padding: 0 {m.space(2)}px;
    min-height: {m.control_height}px;
    selection-background-color: {p.accent};
    selection-color: #FFF6EC;
}}
QPlainTextEdit, QTextEdit {{ padding: {m.space(2)}px; font-family: {mono}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {p.accent};
}}
QLineEdit::placeholder {{ color: {p.text_faint}; }}
QComboBox::drop-down {{ border: none; width: {m.space(6)}px; }}
QComboBox QAbstractItemView {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px;
    selection-background-color: {p.accent_subtle};
    selection-color: {BRAND.x300};
    outline: none;
}}
QLabel#FieldLabel {{ color: {p.text_muted}; font-size: {m.font_size_small}px; }}

/* ---------- tables ---------- */
QTableWidget, QTableView, QTreeWidget, QTreeView {{
    background: {p.surface};
    alternate-background-color: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
    gridline-color: transparent;
    selection-background-color: {BRAND.wash};
    selection-color: {p.text};
    outline: none;
}}
QHeaderView::section {{
    background: {p.surface};
    color: {p.text_faint};
    border: none;
    border-bottom: {m.border_width}px solid {p.border_strong};
    padding: {m.space(1)}px {m.space(2)}px;
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.04em;
}}
QTableWidget::item, QTreeWidget::item {{
    padding: {m.space(1)}px {m.space(2)}px;
    border-bottom: 1px solid {p.border};
}}
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {BRAND.wash};
}}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
    background: {p.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {p.text_muted};
    padding: {m.space(2)}px {m.space(4)}px; margin-left: {m.space(1)}px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {BRAND.x300}; font-weight: 600;
    border-bottom: 2px solid {p.accent};
}}
QTabBar::tab:hover:!selected {{ color: {p.text}; }}

/* ---------- scrollbars ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {p.border_strong}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- misc ---------- */
QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QProgressBar {{
    background: {p.surface_sunken}; border: none;
    border-radius: {m.radius_small}px; height: {m.space(2)}px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: {m.radius_small}px; }}
QStatusBar {{
    background: {p.surface};
    border-top: {m.border_width}px solid {p.border};
    color: {p.text_muted};
}}
QStatusBar::item {{ border: none; }}
#Badge {{
    border-radius: {RADIUS["pill"]}px; padding: 2px {m.space(2)}px;
    font-size: {m.font_size_small}px; font-weight: 600;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: {m.border_width}px solid {p.border_strong};
    border-radius: 3px; background: {p.surface_sunken};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent}; border-color: {p.accent};
}}

/* ---------- home pipeline ---------- */
QPushButton#PipeStep {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
    padding: {m.space(2)}px {m.space(3)}px;
    min-height: 52px; text-align: right;
    color: {p.text_muted}; font-weight: 500;
}}
QPushButton#PipeStep:hover {{ border-color: {p.border_strong}; color: {p.text}; }}
QPushButton#PipeStep[state="done"] {{
    color: {p.text};
    border-color: {p.border_strong};
}}
QPushButton#PipeStep[state="active"] {{
    background: {p.accent_subtle};
    border: {m.border_width}px solid {p.accent};
    color: {BRAND.x300}; font-weight: 600;
}}
#PipeArrow {{ color: {p.text_faint}; font-size: {m.font_size_large}px; }}
#PipeCaption {{
    color: {p.text_faint}; font-size: 10px; font-weight: 600;
    letter-spacing: 0.08em;
}}
#HomeGreeting {{
    font-size: 26px; font-weight: 700; color: {p.text};
}}
#HomeNext {{
    color: {p.text_muted}; font-size: {m.font_size}px;
}}

/* ---------- toasts ---------- */
#Toast {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius}px;
    color: {p.text}; font-weight: 500;
    padding: {m.space(2)}px {m.space(4)}px;
}}
#Toast[kind="success"] {{ border-color: {p.success}; }}
#Toast[kind="danger"] {{ border-color: {p.danger}; }}

/* ---------- command palette ---------- */
#Palette {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_large}px;
}}
#PaletteInput {{
    background: transparent; border: none;
    border-bottom: {m.border_width}px solid {p.border};
    border-radius: 0;
    font-size: {m.font_size_title}px;
    padding: {m.space(3)}px {m.space(4)}px;
}}
#PaletteList {{
    background: transparent; border: none; outline: none;
    font-size: {m.font_size}px;
}}
#PaletteList::item {{
    padding: {m.space(2)}px {m.space(4)}px;
    border-radius: {m.radius_small}px;
    margin: 1px {m.space(2)}px;
    color: {p.text_muted};
}}
#PaletteList::item:selected {{
    background: {p.accent_subtle}; color: {BRAND.x300};
}}
#PaletteHint {{
    color: {p.text_faint}; font-size: {m.font_size_small}px;
    padding: {m.space(1)}px {m.space(4)}px {m.space(2)}px;
}}

/* ---------- finder ---------- */
#FinderTitle {{
    color: {p.text_faint}; font-size: {m.font_size_small}px;
    font-weight: 600; letter-spacing: 0.6px;
    padding: {m.space(3)}px {m.space(4)}px 0;
}}
#FinderName {{ color: {p.text}; font-size: {m.font_size}px; font-weight: 600; }}
#FinderNote {{ color: {p.text_muted}; font-size: {m.font_size_small}px; }}
#FinderMeta {{
    color: {BRAND.x300}; font-size: {m.font_size_small}px;
    background: {p.accent_subtle}; border-radius: {m.radius_small}px;
    padding: {m.space(1)}px {m.space(2)}px;
}}
"""


def badge_style(palette: Palette, kind: str = "info") -> str:
    """Inline style for a small status pill."""
    colour = {
        "success": palette.success,
        "warning": palette.warning,
        "danger": palette.danger,
        "info": palette.info,
        "accent": palette.accent,
        "muted": palette.text_faint,
    }.get(kind, palette.info)
    return (
        f"background: {colour}22; color: {colour}; "
        f"border: 1px solid {colour}55; border-radius: {RADIUS['pill']}px; "
        f"padding: 2px 10px; font-size: {METRICS.font_size_small}px; font-weight: 600;"
    )


def load_fonts() -> None:
    """Register the shipped Heebo with Qt. Safe to call more than once."""
    from PySide6.QtGui import QFontDatabase

    from ..design import FONT_TTF

    if FONT_TTF.is_file() and "Heebo" not in QFontDatabase.families():
        QFontDatabase.addApplicationFont(str(FONT_TTF))


__all__ = [
    "Mode",
    "Palette",
    "Metrics",
    "DARK",
    "LIGHT",
    "METRICS",
    "UI_FONTS",
    "MONO_FONTS",
    "badge_style",
    "load_fonts",
    "stylesheet",
]
