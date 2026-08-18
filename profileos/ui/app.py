"""Desktop application entry point."""

from __future__ import annotations

import sys
from typing import Sequence

from ..core.config import get_settings
from ..core.logging_setup import configure_logging, get_logger

_log = get_logger("ui.app")


def run(argv: Sequence[str] | None = None, *, theme: str = "dark") -> int:
    """Start the desktop application and return its exit code."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow
    from .theme import DARK, LIGHT, UI_FONTS, stylesheet

    configure_logging(get_settings().log_level, use_rich=True)

    # High-DPI rounding: Qt 6 defaults to rounding the device pixel ratio, which
    # makes a 1.5x display look either cramped or blurry. Pass-through keeps
    # fractional scaling sharp.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("ProfileOS")
    application.setOrganizationName("ProfileOS")

    font = QFont()
    font.setFamilies(list(UI_FONTS))
    font.setPointSize(10)
    application.setFont(font)

    palette = LIGHT if theme == "light" else DARK
    application.setStyleSheet(stylesheet(palette))

    window = MainWindow(palette)
    window.show()
    _log.info("Desktop application started")
    return application.exec()


def main() -> None:  # pragma: no cover - console entry point
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
