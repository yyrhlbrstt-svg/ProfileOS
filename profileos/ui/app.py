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
    from .theme import DARK, LIGHT, UI_FONTS, load_fonts, stylesheet

    configure_logging(get_settings().log_level, use_rich=True)

    # High-DPI rounding: Qt 6 defaults to rounding the device pixel ratio, which
    # makes a 1.5x display look either cramped or blurry. Pass-through keeps
    # fractional scaling sharp.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # The shop's own series classifications, restored before any page reads
    # the directory — a family decided last week must not need deciding again.
    from ..systems import load_decisions

    load_decisions()

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("ProfileOS")
    application.setOrganizationName("ProfileOS")

    # Hebrew is the working language, so the whole application runs mirrored.
    # Numbers inside text stay left-to-right on their own; that is bidi, not
    # layout, and Qt handles it per text run.
    application.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

    load_fonts()
    font = QFont()
    font.setFamilies(list(UI_FONTS))
    font.setPointSize(10)
    application.setFont(font)

    palette = LIGHT if theme == "light" else DARK
    application.setStyleSheet(stylesheet(palette))

    # The gate comes before the window. Building the window first and asking
    # afterwards would put the shop's data on screen behind the login box.
    from .login import require_login

    session = require_login(palette)
    from ..security.gate import Gate

    if Gate().is_enrolled and session is None:
        _log.warning("Sign-in cancelled; not starting")
        return 1

    window = MainWindow(palette)
    if session is not None:
        window.set_session_owner(session)
    window.show()
    _log.info("Desktop application started")
    return application.exec()


def main() -> None:  # pragma: no cover - console entry point
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
