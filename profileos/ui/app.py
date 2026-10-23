"""Desktop application entry point."""

from __future__ import annotations

from typing import Any

import sys
from typing import Sequence

from ..core.config import get_settings
from ..core.logging_setup import configure_logging, get_logger

_log = get_logger("ui.app")


def _through_launcher() -> int | None:
    """Started without the launcher (an old shortcut, ``profileos ui``)?
    Start through it instead, so a ready update is put in place first and a
    version that does not open is rolled back. None when already under it."""
    import os
    import subprocess

    from ..core.selfupdate import install_root, launched_by_launcher

    launcher = install_root() / "launcher.py"
    if launched_by_launcher() or os.environ.get("PROFILEOS_NO_LAUNCHER") or not launcher.is_file():
        return None
    # This process only waits: it holds no file of the program open, so the
    # launcher may put a new version in place underneath it.
    return subprocess.call([sys.executable, str(launcher)], cwd=launcher.parent)


def run(argv: Sequence[str] | None = None, *, theme: str | None = None) -> int:
    """Start the desktop application and return its exit code."""
    delegated = _through_launcher()
    if delegated is not None:
        return delegated
    # While this runs, the launcher puts no update in place under it.
    from ..core.selfupdate import hold_instance

    hold_instance()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow
    from .theme import DARK, LIGHT, METRICS, UI_FONTS, fit_to_screen, load_fonts, stylesheet

    configure_logging(get_settings().log_level, use_rich=True)

    # High-DPI rounding: Qt 6 defaults to rounding the device pixel ratio, which
    # makes a 1.5x display look either cramped or blurry. Pass-through keeps
    # fractional scaling sharp.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # The shop's own series classifications, restored before any page reads
    # the directory — a family decided last week must not need deciding again.
    from ..systems import load_confirmations, load_decisions

    load_decisions()
    # And the supplier figures the shop has entered, which is what decides
    # whether a cut sheet may be worked to. Restoring them at start-up is the
    # difference between a demonstration and a tool.
    load_confirmations()
    # The suppliers' price lists in force, which quotations are priced from.
    from ..quoting.pricelists import load_price_lists

    load_price_lists()

    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("ProfileOS")
    application.setOrganizationName("ProfileOS")

    from .icons import app_icon

    application.setWindowIcon(app_icon())

    # Hebrew is the working language, so the whole application runs mirrored.
    # Numbers inside text stay left-to-right on their own; that is bidi, not
    # layout, and Qt handles it per text run.
    application.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    _install_hebrew_buttons(application)

    load_fonts()

    # The interface is sized for the screen it landed on before a single
    # widget is built. A shop laptop is usually a 14-inch 1366x768 panel, and
    # the spacing that reads well on the office monitor arrives there squeezed;
    # dropping the grid step a notch gives every screen its air back. This has
    # to happen first, because margins, row heights and type are all derived
    # from these numbers at the moment each widget is constructed.
    screen = application.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        density = fit_to_screen(available.width(), available.height())
        _log.info(
            "Screen %dx%d, running %s", available.width(), available.height(), density
        )

    font = QFont()
    font.setFamilies(list(UI_FONTS))
    font.setPointSize(10 if METRICS.density == "comfortable" else 9)
    application.setFont(font)

    if theme is None:
        # Whatever was chosen last time — a shop that switched to the light
        # theme for the workshop screen should not have to switch every
        # morning.
        try:
            theme = get_settings().theme
        except Exception:  # noqa: BLE001 - a theme is not worth a failed start
            theme = "dark"
    palette = LIGHT if theme == "light" else DARK
    application.setStyleSheet(stylesheet(palette))

    # The gate comes before the window. Building the window first and asking
    # afterwards would put the shop's data on screen behind the login box.
    from .login import require_login

    session = require_login(palette)
    from ..security.gate import Gate

    if Gate().is_enrolled and session is None:
        _log.warning("Sign-in cancelled; not starting")
        # Its own code: the launcher must not take a cancelled sign-in for
        # a version that failed to open.
        from ..core.selfupdate import CANCELLED_CODE

        return CANCELLED_CODE

    window = MainWindow(palette)
    window.auto_backup = True
    window.autosave_enabled = True
    window.watch_enabled = True
    window.self_update_enabled = True
    from PySide6.QtCore import QTimer

    QTimer.singleShot(20_000, window.watch_suppliers)
    # The program's own updates: shortly after it opens, then every few hours.
    from ..core.selfupdate import CHECK_EVERY_S

    QTimer.singleShot(60_000, window.check_for_update)
    # The phones' way in, if the shop turned it on: open with the window.
    QTimer.singleShot(3_000, window.start_phone_access)
    update_timer = QTimer(window)
    update_timer.setInterval(CHECK_EVERY_S * 1000)
    update_timer.timeout.connect(window.check_for_update)
    update_timer.start()
    _install_error_handler(window)
    if session is not None:
        window.set_session_owner(session)
    window.show()
    # A morning backup as well as the evening one: a day that ends in a crash
    # still has the state it started with.
    from ..core.backup import backup_if_due

    backup_if_due()
    window._update_safety()
    # The window opened and stayed open with the shop's real data: this
    # version works — the launcher's proof, so it does not roll back an
    # update that started fine. Not at once: a version that opens and then
    # falls over on the first refresh is not a working one.
    from ..core.selfupdate import PROVEN_AFTER_S, mark_started

    QTimer.singleShot(PROVEN_AFTER_S * 1000, mark_started)
    QTimer.singleShot(1500, window.announce_update)
    QTimer.singleShot(4000, window.offer_new_shortcut)
    window.offer_draft_recovery()
    _log.info("Desktop application started")
    return application.exec()


def _install_error_handler(window: object) -> None:
    """Nothing that goes wrong is allowed to go unseen.

    Qt swallows an exception raised inside a button handler: it is printed
    to a console nobody has open and the button simply appears to do
    nothing — which is how a save that failed looks exactly like a save
    that worked. Every uncaught error is logged with its traceback and shown
    in plain Hebrew, saying what to do next.
    """
    import traceback

    from PySide6.QtWidgets import QMessageBox

    previous = sys.excepthook

    def show(kind, value, tb) -> None:  # type: ignore[no-untyped-def]
        _log.error("Unhandled error", exc_info=(kind, value, tb))
        if issubclass(kind, KeyboardInterrupt):
            previous(kind, value, tb)
            return
        box = QMessageBox(window if hasattr(window, "isVisible") else None)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("משהו השתבש")
        box.setText(
            f"{value}\n\nהפעולה האחרונה לא הושלמה. הנתונים שכבר נשמרו לא נפגעו."
        )
        box.setDetailedText("".join(traceback.format_exception(kind, value, tb)))
        box.exec()

    sys.excepthook = show


def _install_hebrew_buttons(application: Any) -> None:
    """Qt's own words — Yes, No, Cancel, OK, Save — in Hebrew.

    Every standard dialog button and file dialog otherwise speaks English
    in the middle of a Hebrew program. Qt ships the translation; it only has
    to be loaded. Kept on the application so it is not collected.
    """
    from PySide6.QtCore import QLibraryInfo, QTranslator

    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    application._translators = []
    for name in ("qtbase_he", "qt_he"):
        translator = QTranslator(application)
        if translator.load(name, folder):
            application.installTranslator(translator)
            application._translators.append(translator)


def main() -> None:  # pragma: no cover - console entry point
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
