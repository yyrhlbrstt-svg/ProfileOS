"""Visual design system for the desktop application.

One place defines colour, type, spacing and the stylesheet, so every widget
looks like it belongs to the same product rather than to whichever developer
wrote it.

Design decisions
----------------
*Dark by default.* A CAD workspace shows drawings for hours; a dark canvas
keeps the drawing the brightest thing on screen instead of the chrome.

*One accent colour.* Blue carries selection and primary action, and nothing
else uses it — so when something is blue, it means "this is the live thing".
Semantic colours (success, warning, danger) are reserved for state, never
decoration.

*Numbers are tabular.* Every measurement is rendered with tabular figures so
columns of dimensions line up and a changing value does not make the row jitter.

*Spacing is an 4 px scale.* Every margin and gap is a multiple of 4, which is
what makes an interface feel deliberate rather than assembled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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
            self.accent, "#e8833a", "#3fa66a", "#c05fd6",
            "#d4544f", "#3fb0b8", "#c8a03c", "#8a8fd6",
        ]


DARK = Palette(
    canvas="#0f1216",
    surface="#161a20",
    surface_raised="#1d222a",
    surface_sunken="#0b0e12",
    border="#272d37",
    border_strong="#39414e",
    text="#e6eaf0",
    text_muted="#9aa5b4",
    text_faint="#68727f",
    text_inverse="#0f1216",
    accent="#3d8bfd",
    accent_hover="#5a9dff",
    accent_pressed="#2f74d9",
    accent_subtle="#16283f",
    success="#3fa66a",
    warning="#d99b3c",
    danger="#e05555",
    info="#3fb0b8",
    draw_material="#2f6ea8",
    draw_material_edge="#8fc4f0",
    draw_void="#0b0e12",
    draw_grid="#1b212a",
    draw_axis="#3c4654",
    draw_dimension="#9aa5b4",
    draw_highlight="#e8b83a",
    draw_glass="#3aa8bd",
    draw_frame="#4f7fb5",
    draw_sash="#6f9dd0",
    bar_stock="#1d222a",
    bar_piece="#3d8bfd",
    bar_piece_alt="#5a9dff",
    bar_kerf="#e05555",
    bar_remnant_good="#3fa66a",
    bar_remnant_scrap="#5c646f",
    mode=Mode.DARK,
)

LIGHT = Palette(
    canvas="#f2f4f7",
    surface="#ffffff",
    surface_raised="#ffffff",
    surface_sunken="#eceff3",
    border="#d9dee6",
    border_strong="#bcc4d0",
    text="#14181d",
    text_muted="#5b6472",
    text_faint="#8a94a3",
    text_inverse="#ffffff",
    accent="#0b62d0",
    accent_hover="#0a56b6",
    accent_pressed="#084a9c",
    accent_subtle="#e5effc",
    success="#17803d",
    warning="#b45309",
    danger="#c0342f",
    info="#0e7490",
    draw_material="#a8c8e8",
    draw_material_edge="#2c5f92",
    draw_void="#ffffff",
    draw_grid="#e4e8ee",
    draw_axis="#b0b8c4",
    draw_dimension="#5b6472",
    draw_highlight="#b45309",
    draw_glass="#bfe3ea",
    draw_frame="#8fb3d6",
    draw_sash="#b3cce6",
    bar_stock="#e8ebf0",
    bar_piece="#0b62d0",
    bar_piece_alt="#3d8bfd",
    bar_kerf="#c0342f",
    bar_remnant_good="#17803d",
    bar_remnant_scrap="#a3abb7",
    mode=Mode.LIGHT,
)


@dataclass(frozen=True)
class Metrics:
    """Spacing, radii and type sizes, all on a 4 px scale."""

    unit: int = 4
    radius: int = 8
    radius_small: int = 5
    border_width: int = 1
    sidebar_width: int = 232
    panel_width: int = 340
    row_height: int = 30
    control_height: int = 32

    font_size: int = 13
    font_size_small: int = 11
    font_size_large: int = 16
    font_size_title: int = 21

    def space(self, multiplier: int = 1) -> int:
        return self.unit * multiplier


METRICS = Metrics()

#: Font stacks, most preferred first. Set separately so the numeric font can
#: differ from the prose font.
UI_FONTS = ("Inter", "Segoe UI", "SF Pro Text", "Ubuntu", "DejaVu Sans", "sans-serif")
MONO_FONTS = ("JetBrains Mono", "SF Mono", "Cascadia Mono", "DejaVu Sans Mono", "monospace")


def stylesheet(palette: Palette, metrics: Metrics = METRICS) -> str:
    """Build the Qt stylesheet for a palette."""
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
QToolTip {{
    background: {p.surface_raised}; color: {p.text};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px; padding: {m.space(2)}px;
}}

/* ---------- sidebar ---------- */
#Sidebar {{
    background: {p.surface};
    border-right: {m.border_width}px solid {p.border};
}}
#SidebarLogo {{
    font-size: {m.font_size_large}px; font-weight: 700;
    color: {p.text}; padding: {m.space(5)}px {m.space(5)}px {m.space(1)}px;
}}
#SidebarVersion {{
    font-size: {m.font_size_small}px; color: {p.text_faint};
    padding: 0 {m.space(5)}px {m.space(5)}px;
}}
#SidebarSection {{
    font-size: {m.font_size_small}px; font-weight: 700;
    color: {p.text_faint}; letter-spacing: 1px;
    padding: {m.space(4)}px {m.space(5)}px {m.space(1)}px;
}}
QPushButton#NavButton {{
    background: transparent; border: none; border-radius: {m.radius_small}px;
    color: {p.text_muted}; text-align: left;
    padding: {m.space(2)}px {m.space(3)}px;
    margin: 1px {m.space(3)}px; min-height: {m.row_height}px;
}}
QPushButton#NavButton:hover {{ background: {p.surface_raised}; color: {p.text}; }}
QPushButton#NavButton:checked {{
    background: {p.accent_subtle}; color: {p.accent}; font-weight: 600;
}}

/* ---------- header ---------- */
#PageHeader {{
    background: {p.surface};
    border-bottom: {m.border_width}px solid {p.border};
}}
#PageTitle {{ font-size: {m.font_size_title}px; font-weight: 700; color: {p.text}; }}
#PageSubtitle {{ font-size: {m.font_size}px; color: {p.text_muted}; }}

/* ---------- cards and panels ---------- */
#Card {{
    background: {p.surface};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
}}
#CardTitle {{
    font-size: {m.font_size_small}px; font-weight: 700;
    color: {p.text_faint}; letter-spacing: 1px;
}}
#StatValue {{
    font-size: 26px; font-weight: 700; color: {p.text};
    font-family: {mono};
}}
#StatLabel {{ font-size: {m.font_size_small}px; color: {p.text_muted}; }}
#Canvas {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px;
    color: {p.text}; padding: 0 {m.space(4)}px;
    min-height: {m.control_height}px;
}}
QPushButton:hover {{ background: {p.border}; }}
QPushButton:pressed {{ background: {p.surface_sunken}; }}
QPushButton:disabled {{ color: {p.text_faint}; background: {p.surface}; }}
QPushButton#Primary {{
    background: {p.accent}; border-color: {p.accent};
    color: {p.text_inverse}; font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:pressed {{ background: {p.accent_pressed}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface_sunken};
    border: {m.border_width}px solid {p.border_strong};
    border-radius: {m.radius_small}px;
    color: {p.text}; padding: 0 {m.space(2)}px;
    min-height: {m.control_height}px;
    selection-background-color: {p.accent};
    selection-color: {p.text_inverse};
}}
QPlainTextEdit, QTextEdit {{ padding: {m.space(2)}px; font-family: {mono}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QComboBox::drop-down {{ border: none; width: {m.space(6)}px; }}
QComboBox QAbstractItemView {{
    background: {p.surface_raised};
    border: {m.border_width}px solid {p.border_strong};
    selection-background-color: {p.accent_subtle};
    selection-color: {p.accent};
    outline: none;
}}
QLabel#FieldLabel {{ color: {p.text_muted}; font-size: {m.font_size_small}px; }}

/* ---------- tables ---------- */
QTableWidget, QTableView, QTreeWidget, QTreeView {{
    background: {p.surface};
    alternate-background-color: {p.surface_raised};
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
    gridline-color: {p.border};
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text};
    outline: none;
}}
QHeaderView::section {{
    background: {p.surface_raised};
    color: {p.text_faint};
    border: none;
    border-bottom: {m.border_width}px solid {p.border};
    padding: {m.space(2)}px;
    font-size: {m.font_size_small}px; font-weight: 700;
    letter-spacing: 0.5px;
}}
QTableWidget::item, QTreeWidget::item {{ padding: {m.space(1)}px {m.space(2)}px; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{
    border: {m.border_width}px solid {p.border};
    border-radius: {m.radius}px;
    background: {p.surface};
    top: -1px;
}}
QTabBar::tab {{
    background: transparent; color: {p.text_muted};
    padding: {m.space(2)}px {m.space(4)}px; margin-right: {m.space(1)}px;
    border-top-left-radius: {m.radius_small}px;
    border-top-right-radius: {m.radius_small}px;
}}
QTabBar::tab:selected {{ background: {p.surface}; color: {p.text}; font-weight: 600; }}
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
    border-radius: {m.radius_small}px; padding: 2px {m.space(2)}px;
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
"""


def badge_style(palette: Palette, kind: str = "info") -> str:
    """Inline style for a small status badge."""
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
        f"border: 1px solid {colour}55; border-radius: 5px; "
        f"padding: 2px 8px; font-size: 11px; font-weight: 600;"
    )


__all__ = [
    "Mode",
    "Palette",
    "Metrics",
    "DARK",
    "LIGHT",
    "METRICS",
    "UI_FONTS",
    "MONO_FONTS",
    "stylesheet",
    "badge_style",
]
