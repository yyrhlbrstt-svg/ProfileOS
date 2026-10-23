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


# Apple-style system palettes. The shop's bronze stays on its printed
# documents (profileos.design.tokens); the working interface takes the calm,
# neutral language people already know from the tools on their phone.
DARK = Palette(
    canvas="#1C1C1E",
    surface="#2C2C2E",
    surface_raised="#3A3A3C",
    surface_sunken="#18181A",
    border="#38383A",
    border_strong="#48484A",
    text="#F5F5F7",
    text_muted="#AEAEB2",
    text_faint="#7C7C80",
    text_inverse="#FFFFFF",
    accent="#0A84FF",
    accent_hover="#409CFF",
    accent_pressed="#0071E3",
    accent_subtle="#16324F",
    success="#30D158",
    warning="#FF9F0A",
    danger="#FF453A",
    info="#64D2FF",
    draw_material="#4E7CA6",
    draw_material_edge="#A8CBE8",
    draw_void="#18181A",
    draw_grid="#242426",
    draw_axis="#48484A",
    draw_dimension="#AEAEB2",
    draw_highlight="#0A84FF",
    draw_glass="#57A8B5",
    draw_frame="#5B84AE",
    draw_sash="#7DA3C9",
    bar_stock="#3A3A3C",
    bar_piece="#4E7CA6",
    bar_piece_alt="#6E97BC",
    bar_kerf="#FF453A",
    bar_remnant_good="#30D158",
    bar_remnant_scrap="#636366",
    mode=Mode.DARK,
)

LIGHT = Palette(
    canvas="#F5F5F7",
    surface="#FFFFFF",
    surface_raised="#F2F2F7",
    surface_sunken="#FFFFFF",
    border="#E5E5EA",
    border_strong="#D1D1D6",
    text="#1D1D1F",
    text_muted="#6E6E73",
    # Apple's own secondary grey: #A1A1A6 measured 2.2:1 on the sidebar.
    text_faint="#8E8E93",
    text_inverse="#FFFFFF",
    accent="#0071E3",
    accent_hover="#0077ED",
    accent_pressed="#006EDB",
    accent_subtle="#E8F1FD",
    success="#248A3D",
    warning="#B25000",
    danger="#D70015",
    info="#0071A4",
    draw_material="#A9C4DC",
    draw_material_edge="#2D5A85",
    draw_void="#FFFFFF",
    draw_grid="#EDEDF0",
    draw_axis="#C7C7CC",
    draw_dimension="#6E6E73",
    draw_highlight="#0071E3",
    draw_glass="#BFE0E6",
    draw_frame="#8FB0CE",
    draw_sash="#AFC8E0",
    bar_stock="#F2F2F7",
    bar_piece="#4E7CA6",
    bar_piece_alt="#7DA3C9",
    bar_kerf="#D70015",
    bar_remnant_good="#248A3D",
    bar_remnant_scrap="#C7C7CC",
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


_CHEVRON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
    "<path d='{d}' fill='none' stroke='{c}' stroke-width='1.6' "
    "stroke-linecap='round' stroke-linejoin='round'/></svg>"
)


def _chevron_files(colour: str) -> tuple[str, str, str]:
    """Up, down and combo-box chevrons as small SVG files, for QSS to load.

    Qt's style sheets cannot draw an arrow from borders the way a browser can
    — the trick renders as a bar or a square — and an arrow nobody can see is
    a button nobody presses. ``image: url(...)`` is the supported route, so
    the glyphs are written once per colour to a cache folder and referenced.
    """
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "profileos-ui"
    folder.mkdir(parents=True, exist_ok=True)
    tag = colour.lstrip("#").lower()
    paths = []
    for name, d in (
        ("up", "M2.5 6.5 5 4l2.5 2.5"),
        ("down", "M2.5 3.5 5 6l2.5-2.5"),
    ):
        path = folder / f"chevron-{name}-{tag}.svg"
        if not path.exists():
            path.write_text(_CHEVRON.format(d=d, c=colour), encoding="utf-8")
        paths.append(path.as_posix())
    return paths[0], paths[1], paths[1]


def _tick_file(colour: str) -> str:
    """A check mark for ticked boxes; a filled square alone reads as "on"
    only to somebody who already knows it is a checkbox."""
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.gettempdir()) / "profileos-ui"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"tick-{colour.lstrip('#').lower()}.svg"
    if not path.exists():
        path.write_text(
            _CHEVRON.format(d="M2.3 5.2 4.2 7.1 7.8 3.2", c=colour).replace(
                "stroke-width='1.6'", "stroke-width='1.5'"
            ),
            encoding="utf-8",
        )
    return path.as_posix()


def stylesheet(palette: Palette, metrics: Metrics = METRICS) -> str:
    """Build the Qt stylesheet for a palette.

    Literal sides (borders, text alignment) are written for a right-to-left
    application: the sidebar sits on the right, so its hairline is on its left.
    """
    p, m = palette, metrics
    font = ", ".join(f'"{name}"' for name in UI_FONTS)
    mono = ", ".join(f'"{name}"' for name in MONO_FONTS)
    try:
        up_arrow, down_arrow, combo_arrow = _chevron_files(p.text_muted)
    except OSError:  # a read-only temp folder costs the glyphs, not the app
        up_arrow = down_arrow = combo_arrow = ""
    try:
        tick = _tick_file("#FFFFFF")
    except OSError:
        tick = ""

    light = p.mode is Mode.LIGHT
    # WhatsApp's own green, darkened on white so it reads as text.
    whatsapp = "#1A8D47" if light else "#25D366"
    # White text on the dark theme's bright blue is 3.6:1; the pressed shade
    # carries it at 4.7:1. Filled surfaces with white text use this one.
    accent_fill = p.accent if light else p.accent_pressed
    # Derived shades, so each palette only has to state its base colours.
    sidebar_bg = "#ECECEF" if light else "#232325"
    nav_hover = "rgba(0,0,0,0.05)" if light else "rgba(255,255,255,0.07)"
    row_line = "#F0F0F3" if light else "#333336"
    row_hover = "#F7F7F9" if light else "#323235"
    segment_bg = "#E8E8ED" if light else "#2C2C2E"
    segment_on = "#FFFFFF" if light else "#636366"
    segment_hover = "#DEDEE4" if light else "#3A3A3C"
    scroll_handle = "rgba(0,0,0,0.22)" if light else "rgba(255,255,255,0.22)"
    tooltip_bg = "#3A3A3C"
    toast_bg = "#1D1D1F" if light else "#48484A"
    toast_ok = "#1F7A36" if light else "#248A3D"
    toast_bad = "#B3261E"

    return f"""
/* =====================================================================
   The look: calm neutral surfaces, white cards, one accent, generous
   space — the language people already trust from Apple and Google
   products. Nothing decorates; colour means "action" or "state".
   ===================================================================== */

/* ---------- base ---------- */
QWidget {{
    background: {p.canvas};
    color: {p.text};
    font-family: {font};
    font-size: {m.font_size}px;
}}
QMainWindow, QDialog {{ background: {p.canvas}; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QLabel#Muted {{ color: {p.text_muted}; }}
QLabel#Hint {{ color: {p.text_muted}; font-size: {m.font_size_small + 1}px; }}
QToolTip {{
    background: {tooltip_bg}; color: #FFFFFF;
    border: none; border-radius: 6px; padding: 6px 10px;
}}

/* ---------- sidebar ---------- */
#Sidebar {{
    background: {sidebar_bg};
    border-left: {m.border_width}px solid {p.border};
}}
#SidebarLogo {{
    font-size: {m.font_size_large + 3}px; font-weight: 700;
    color: {p.text}; padding: {m.space(4)}px {m.space(5)}px 2px;
}}
#SidebarVersion {{
    font-size: {m.font_size_small}px; color: {p.text_muted};
    padding: 0 {m.space(5)}px {m.space(4)}px;
}}
#SidebarSection {{
    font-size: {m.font_size_small}px; font-weight: 600;
    color: {p.text_muted};
    padding: {m.space(1)}px {m.space(5)}px 0;
}}
#SidebarScroll, #SidebarScroll > QWidget > QWidget, #SidebarNavBody {{
    background: transparent; border: none;
}}
QPushButton#NavButton {{
    background: transparent; border: none; border-radius: 8px;
    color: {p.text}; text-align: right;
    padding: 0; margin: 0 {m.space(2)}px; min-height: {m.row_height}px;
    font-size: {m.font_size}px; font-weight: 400;
}}
QLabel#NavLabel {{ font-size: {m.font_size}px; }}
QPushButton#NavButton:hover {{ background: {nav_hover}; }}
QPushButton#NavButton:checked {{
    background: {accent_fill}; color: #FFFFFF; font-weight: 600;
}}
QPushButton#SidebarTheme {{
    background: transparent; border: none;
    border-top: {m.border_width}px solid {p.border};
    border-radius: 0;
    color: {p.text_muted}; font-size: {m.font_size_small + 1}px;
    padding: {m.space(3)}px {m.space(4)}px;
}}
QPushButton#SidebarTheme:hover {{ color: {p.text}; background: {nav_hover}; }}
#SidebarFooter {{
    font-size: {m.font_size_small}px; color: {p.text_faint};
    padding: {m.space(2)}px {m.space(4)}px {m.space(3)}px;
}}
#SidebarSafety {{
    font-size: {m.font_size_small}px; color: {p.success};
    padding: {m.space(2)}px {m.space(4)}px 0;
}}
#SidebarSafety[state="warn"] {{ color: {p.warning}; }}
#SidebarSafety[state="danger"] {{ color: {p.danger}; }}

/* ---------- header: a large title on the canvas, like Settings ---------- */
#PageHeader {{
    background: {p.canvas};
    border-bottom: {m.border_width}px solid {p.border};
}}
#PageTitle {{ font-size: {m.font_size_title + 5}px; font-weight: 700; color: {p.text}; }}
#PageSubtitle {{ font-size: {m.font_size_small + 1}px; color: {p.text_muted}; }}

/* ---------- cards ---------- */
#Card {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 14px;
}}
#CardTitle {{
    font-size: {m.font_size}px; font-weight: 600; color: {p.text};
}}
#StatValue[empty="true"] {{ color: {p.text_faint}; }}
#StatValue {{
    font-size: 26px; font-weight: 600; color: {p.text};
}}
#StatLabel {{ font-size: {m.font_size_small + 1}px; color: {p.text_muted}; }}
/* The one figure on a page that matters most (what the customer pays). */
#Card[hero="true"] {{ border: {m.border_width}px solid {p.accent}; background: {p.accent_subtle}; }}
#Card[hero="true"] #StatValue {{ color: {p.accent}; }}
/* The shop-floor card is a card too, with controls sized for a hand. */
#FloorCard {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 14px;
}}
#FloorCard QComboBox, #FloorCard QPushButton, #FloorCard QLineEdit {{
    min-height: 44px; font-size: {m.font_size + 3}px;
}}
/* A table inside a card: the card is the frame, not a second one. */
#Card QTableWidget, #Card QTreeWidget, #Card QListWidget {{ border: none; border-radius: 0; }}
#Canvas {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border};
    border-radius: 14px;
}}
#EmptyTitle {{ font-size: {m.font_size_large}px; font-weight: 600; color: {p.text}; }}
#EmptyBody {{ color: {p.text_muted}; }}
#EmptyGlyph {{ color: {p.text_faint}; }}
QLabel#EmptyState {{
    color: {p.text_muted}; font-size: {m.font_size + 3}px;
    padding: {m.space(4)}px;
}}
QLabel#EstimateResult {{
    font-size: {m.font_size + 5}px; font-weight: 600; color: {p.text};
    padding: {m.space(2)}px 0;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: 8px;
    color: {p.text}; padding: 0 {m.space(4)}px;
    min-height: {m.control_height}px;
    font-weight: 500;
}}
QPushButton:hover {{ background: {p.surface_raised}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface}; border-color: {p.border}; }}
QPushButton#Primary {{
    background: {accent_fill}; border: none; color: #FFFFFF; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}
QPushButton#Primary:disabled {{
    /* Nothing to act on yet: plainly not a button to press. */
    background: {p.surface_raised}; border: none; color: {p.text_faint};
}}
QPushButton#WhatsApp:disabled {{
    background: transparent; border-color: {p.border}; color: {p.text_faint};
}}
QPushButton#Ghost {{
    background: transparent; border: none; color: {p.accent}; font-weight: 500;
}}
QPushButton#Ghost:hover {{ background: {p.accent_subtle}; }}
QPushButton#WhatsApp {{
    /* A quiet secondary with a green label — not a second primary. */
    background: {p.surface}; color: {whatsapp};
    border: {m.border_width}px solid {p.border_strong}; font-weight: 600;
}}
QPushButton#WhatsApp:hover {{ background: {whatsapp}; color: #FFFFFF; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QDateEdit {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: 8px;
    color: {p.text}; padding: 0 {m.space(3)}px;
    min-height: {m.control_height}px;
    selection-background-color: {p.accent};
    selection-color: #FFFFFF;
}}
QPlainTextEdit, QTextEdit {{ padding: {m.space(2)}px; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {p.text_faint};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus, QDateEdit:focus {{
    border-color: {p.accent};
}}
QLineEdit[invalid="true"] {{ border-color: {p.danger}; }}
QToolButton#MoreButton {{
    background: {p.surface}; border: {m.border_width}px solid {p.border_strong};
    border-radius: 8px; padding: 4px 12px; color: {p.text};
}}
QToolButton#MoreButton:hover {{ background: {p.surface_raised}; }}
QToolButton#MoreButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#MoreButton:disabled {{ color: {p.text_faint}; border-color: {p.border}; }}
QFrame#SegmentBar {{
    background: {p.surface_raised}; border-radius: 9px;
}}
QPushButton#Segment {{
    background: transparent; border: none; border-radius: 7px;
    padding: 4px 16px; min-height: 24px;
    color: {p.text_muted}; font-weight: 500;
}}
QPushButton#Segment:hover {{ color: {p.text}; }}
QPushButton#Segment:checked {{
    background: {p.surface}; color: {p.text}; font-weight: 600;
    border: {m.border_width}px solid {p.border};
}}
QPushButton#Segment:disabled {{ color: {p.text_faint}; }}
QLabel#FieldError {{ color: {p.danger}; font-size: {m.font_size_small}px; }}
QLabel#FieldWarning {{
    color: {p.text}; background: {p.surface_raised};
    border: {m.border_width}px solid {p.warning};
    border-radius: 8px; padding: {m.space(2)}px {m.space(3)}px;
    font-size: {m.font_size_small}px;
}}
QComboBox::drop-down {{ border: none; width: {m.space(6)}px; }}
QSpinBox, QDoubleSpinBox {{ padding: 0 {m.space(6)}px; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: {m.space(5)}px;
    border: none;
    background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p.surface_raised};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_arrow}"); width: 9px; height: 9px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{down_arrow}"); width: 9px; height: 9px;
}}
QComboBox::down-arrow {{
    image: url("{combo_arrow}"); width: 10px; height: 10px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 10px;
    padding: 4px;
    selection-background-color: {p.accent};
    selection-color: #FFFFFF;
    outline: none;
}}
QLabel#FieldLabel {{ color: {p.text_muted}; font-size: {m.font_size_small + 1}px; }}

/* ---------- tables ---------- */
QTableWidget, QTableView, QTreeWidget, QTreeView, QListWidget {{
    background: {p.surface};
    alternate-background-color: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 12px;
    gridline-color: transparent;
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text};
    outline: none;
}}
QHeaderView::section {{
    background: {p.surface};
    color: {p.text_muted};
    border: none;
    border-bottom: {m.border_width}px solid {p.border};
    padding: {m.space(2)}px {m.space(3)}px;
    font-size: {m.font_size_small + 1}px; font-weight: 600;
}}
QTableWidget::item, QTreeWidget::item {{
    padding: {m.space(1)}px {m.space(3)}px;
    border-bottom: 1px solid {row_line};
}}
QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: {p.accent_subtle}; color: {p.text};
}}
QTableWidget::item:hover, QTreeWidget::item:hover {{ background: {row_hover}; }}

/* ---------- tabs: a segmented control ---------- */
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: {m.space(2)}px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: {segment_bg}; color: {p.text};
    padding: {m.space(1) + 2}px {m.space(4)}px;
    margin: 0; border: none;
    min-width: 56px;
}}
QTabBar::tab:first {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
QTabBar::tab:last {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QTabBar::tab:only-one {{ border-radius: 8px; }}
QTabBar::tab:selected {{
    background: {segment_on}; color: {p.text}; font-weight: 600;
    border: {m.border_width}px solid {p.border};
    border-radius: 7px;
}}
QTabBar::tab:hover:!selected {{ color: {p.text}; background: {segment_hover}; }}

/* ---------- scrollbars: thin, out of the way ---------- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle {{ background: {scroll_handle}; border-radius: 4px; min-height: 32px; }}
QScrollBar::handle:hover {{ background: {p.text_faint}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- misc ---------- */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: {m.space(2)}px; }}
QSplitter::handle:vertical {{ height: {m.space(2)}px; }}
QProgressBar {{
    background: {p.surface_raised}; border: none;
    border-radius: 3px; height: 6px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 3px; }}
QProgressBar#ReadinessBar {{ background: {p.surface_raised}; border-radius: 3px; }}
QProgressBar#ReadinessBar::chunk {{ background: {p.success}; border-radius: 3px; }}
QStatusBar {{
    background: {p.canvas};
    border-top: {m.border_width}px solid {p.border};
    color: {p.text_muted};
}}
QStatusBar::item {{ border: none; }}
#Badge {{
    border-radius: {RADIUS["pill"]}px; padding: 2px {m.space(2)}px;
    font-size: {m.font_size_small}px; font-weight: 600;
}}
QCheckBox {{ spacing: {m.space(2)}px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: {m.border_width}px solid {p.border_strong};
    border-radius: 5px; background: {p.surface};
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent}; border-color: {p.accent};
}}
QCheckBox::indicator:checked {{ image: url({tick}); }}
QMenu {{
    background: {p.surface}; border: {m.border_width}px solid {p.border};
    border-radius: 10px; padding: 4px;
}}
QMenu::item {{ padding: 6px 16px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_fill}; color: #FFFFFF; }}
QMessageBox QLabel {{ font-size: {m.font_size + 1}px; }}

/* ---------- home ---------- */
QPushButton#PipeStep {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 12px;
    padding: {m.space(2)}px {m.space(3)}px;
    min-height: 52px; text-align: left;
    color: {p.text_muted}; font-weight: 500;
}}
QPushButton#PipeStep:hover {{ border-color: {p.border_strong}; color: {p.text}; }}
QPushButton#PipeStep[state="done"] {{ color: {p.success}; font-weight: 600; }}
QPushButton#PipeStep[state="active"] {{
    background: {accent_fill}; border: none; color: #FFFFFF; font-weight: 600;
}}
#PipeArrow {{ color: {p.text_faint}; font-size: {m.font_size_large}px; }}
#PipeCaption {{ color: {p.text_faint}; font-size: {m.font_size_small}px; font-weight: 600; }}
#HomeGreeting {{ font-size: 30px; font-weight: 700; color: {p.text}; }}
#HomeNext {{ color: {p.text_muted}; font-size: {m.font_size + 1}px; }}
#InsightRow {{
    background: {p.surface}; border: {m.border_width}px solid {p.border};
    border-radius: 12px;
}}
#InsightText {{ color: {p.text}; font-size: {m.font_size}px; font-weight: 600; }}
#InsightDetail {{ color: {p.text_muted}; font-size: {m.font_size_small}px; }}
#InsightDot {{ font-size: 12px; color: {p.text_faint}; padding-top: 2px; }}
#InsightDot[severity="danger"] {{ color: {p.danger}; }}
#InsightDot[severity="warning"] {{ color: {p.warning}; }}
#InsightDot[severity="info"] {{ color: {p.accent}; }}
#InsightCalm {{ color: {p.success}; font-size: {m.font_size}px; font-weight: 600; }}
QPushButton#InsightDismiss {{
    background: transparent; border: none; color: {p.text_faint};
    min-width: 24px; max-width: 24px; padding: 0;
}}
QPushButton#InsightDismiss:hover {{ color: {p.text}; }}

/* ---------- toasts: a dark capsule, like a phone notification ---------- */
#Toast {{
    background: {toast_bg};
    border: none;
    border-radius: 18px;
    color: #FFFFFF; font-weight: 500;
    padding: {m.space(2) + 2}px {m.space(5)}px;
}}
#Toast[kind="success"] {{ background: {toast_ok}; }}
#Toast[kind="danger"] {{ background: {toast_bad}; }}

/* ---------- command palette ---------- */
#Palette {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: 16px;
}}
#PaletteInput {{
    background: transparent; border: none;
    border-bottom: {m.border_width}px solid {p.border};
    border-radius: 0;
    font-size: {m.font_size_title + 2}px;
    padding: {m.space(4)}px {m.space(5)}px;
}}
#PaletteList {{
    background: transparent; border: none; outline: none;
    font-size: {m.font_size + 1}px;
}}
#PaletteList::item {{
    padding: {m.space(2)}px {m.space(4)}px;
    border-radius: 8px;
    margin: 1px {m.space(2)}px;
    color: {p.text};
}}
#PaletteList::item:selected {{ background: {p.accent}; color: #FFFFFF; }}
#PaletteHint {{
    color: {p.text_faint}; font-size: {m.font_size_small}px;
    padding: {m.space(1)}px {m.space(5)}px {m.space(3)}px;
}}

/* ---------- finder ---------- */
#FinderTitle {{
    color: {p.text_muted}; font-size: {m.font_size_small + 1}px; font-weight: 600;
    padding: {m.space(3)}px {m.space(4)}px 0;
}}
#FinderName {{ color: {p.text}; font-size: {m.font_size}px; font-weight: 600; }}
#FinderNote {{ color: {p.text_muted}; font-size: {m.font_size_small}px; }}
#FinderMeta {{
    color: {p.accent}; font-size: {m.font_size_small}px;
    background: {p.accent_subtle}; border-radius: 6px;
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
    # Qt reads eight-digit hex as #AARRGGBB, not the CSS #RRGGBBAA, so the
    # translucent tints are written as rgba().
    red, green, blue = (int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return (
        f"background: rgba({red}, {green}, {blue}, 34); color: {colour}; "
        f"border: 1px solid rgba({red}, {green}, {blue}, 85); "
        f"border-radius: {RADIUS['pill']}px; "
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
