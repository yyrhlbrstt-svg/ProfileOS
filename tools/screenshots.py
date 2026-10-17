"""Photograph every page of the application, headless, in both themes.

Used to check the interface by looking at it rather than by reading code:

    QT_QPA_PLATFORM=offscreen python3 tools/screenshots.py OUT_DIR [light|dark|both]

The run uses throw-away data and settings folders, so it never touches the
shop's real files. Each page is given time to finish its fade-in before it is
grabbed; an early grab looks washed out and is not a real defect.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    which = argv[2] if len(argv) > 2 else "both"

    scratch = Path(tempfile.mkdtemp(prefix="profileos-shots-"))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PROFILEOS_DATA_DIR"] = str(scratch / "data")
    os.environ["PROFILEOS_CONFIG_DIR"] = str(scratch / "config")

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    from profileos.ui.theme import DARK, LIGHT, UI_FONTS, fit_to_screen, load_fonts, stylesheet

    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    load_fonts()
    fit_to_screen(1920, 1040)
    font = QFont()
    font.setFamilies(list(UI_FONTS))
    font.setPointSize(10)
    app.setFont(font)

    from profileos.ui.main_window import MainWindow

    themes = {"light": [LIGHT], "dark": [DARK]}.get(which, [LIGHT, DARK])
    for palette in themes:
        app.setStyleSheet(stylesheet(palette))
        window = MainWindow(palette)
        window.resize(1800, 990)
        window.show()
        mode = palette.mode.value
        for index, page in enumerate(window.pages):
            window.go_to(index)
            QTest.qWait(450)
            name = getattr(page, "title", f"page{index}").replace(" ", "_")
            window.grab().save(str(out / f"{mode}_{index:02d}_{name}.png"))

        # A designed opening, so the pages that show one are not all empty.
        element = window.page("Element")
        window.go_to_page("Element")
        QTest.qWait(400)
        element.width.setValue(1800)
        element.build_element()
        QTest.qWait(300)
        window.grab().save(str(out / f"{mode}_with_element.png"))
        window.go_to_page("Quotation")
        QTest.qWait(400)
        window.page("Quotation").start_draft()
        QTest.qWait(300)
        window.grab().save(str(out / f"{mode}_quotation.png"))
        window.close()
    print(f"saved to {out}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main(sys.argv))
