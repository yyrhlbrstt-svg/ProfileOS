"""Turning a rendered document into a PDF, without anything installed.

Quotations are rendered as HTML because that is what renders well and
archives well. A customer, though, expects a PDF — it is what opens the
same on every phone, and what can be dropped into a WhatsApp chat.

Qt ships a full Chromium engine (Qt WebEngine) in the same wheel as the
rest of the interface, so the HTML is laid out exactly as a browser would —
the elevation drawings, the right-to-left tables, the fonts — and printed to
A4. No browser or office suite has to be installed on the shop's computer.

Chromium runs in a **separate process**: it needs settings that have to be
made before the application object exists, and a crash inside it must never
take the shop's open work with it. If that process cannot produce the file,
Qt's plain text layout engine writes the PDF instead — legible, without the
drawings — and the log says which one was used.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core.logging_setup import get_logger

_log = get_logger("ui.pdf")


def _child_environment() -> dict[str, str]:
    env = dict(os.environ)
    if os.name != "nt":
        # Chromium refuses to start its sandbox as root, which is how
        # containers and some kiosks run; there is nothing to sandbox here
        # but a document the program itself just wrote.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
            if "--no-sandbox" not in flags:
                env["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --no-sandbox").strip()
        if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return env


def _webengine_pdf(html: str, target: Path, timeout_ms: int) -> bool:
    """Render in a child process. True only if a PDF actually came out."""
    with tempfile.TemporaryDirectory(prefix="profileos-pdf-") as folder:
        source = Path(folder) / "document.html"
        source.write_text(html, encoding="utf-8")
        try:
            done = subprocess.run(
                [sys.executable, "-m", "profileos.ui.pdf", str(source), str(target)],
                env=_child_environment(),
                capture_output=True,
                timeout=timeout_ms / 1000.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log.warning("PDF renderer did not finish: %s", exc)
            return False
        if done.returncode != 0:
            _log.warning(
                "PDF renderer exited %s: %s", done.returncode,
                done.stderr.decode("utf-8", "replace")[-400:],
            )
            return False
    return target.is_file() and target.stat().st_size > 0


def _text_pdf(html: str, target: Path, rtl: bool) -> None:
    from PySide6.QtCore import QMarginsF, Qt
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument, QTextOption

    document = QTextDocument()
    if rtl:
        option = QTextOption()
        option.setTextDirection(Qt.LayoutDirection.RightToLeft)
        document.setDefaultTextOption(option)
    document.setHtml(html)
    writer = QPdfWriter(str(target))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(14, 14, 14, 14), QPageLayout.Unit.Millimeter)
    writer.setTitle(target.stem)
    document.print_(writer)


def html_to_pdf(
    html: str, path: Path | str, *, rtl: bool = True, timeout_ms: int = 60_000,
    plain_fallback: bool = False,
) -> Path:
    """Write ``html`` to ``path`` as an A4 PDF and return the path.

    When the full layout cannot be rendered (the browser engine did not
    start) this raises rather than quietly writing the plain-text fallback:
    that one runs table cells together, and a garbled quotation must not
    reach a customer without anybody knowing. ``plain_fallback`` asks for it
    explicitly, for an internal copy.
    """
    from ..core.errors import ProfileOSError

    import os

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Rendered beside it and moved into place only when it worked: an old
    # PDF left there must never pass for this one, and a failed render must
    # not destroy the copy that was there.
    rendering = target.with_name(target.stem + ".rendering.pdf")
    rendering.unlink(missing_ok=True)
    if _webengine_pdf(html, rendering, timeout_ms):
        os.replace(rendering, target)
        return target
    rendering.unlink(missing_ok=True)
    if not plain_fallback:
        raise ProfileOSError("מנוע התצוגה לא עלה, וה־PDF המעוצב לא נוצר")
    _log.warning("Falling back to the text layout for %s", target.name)
    _text_pdf(html, target, rtl)
    return target


def _render_in_this_process(source: Path, target: Path) -> int:
    """The child: one page, printed, then exit. Returns a process exit code."""
    from PySide6.QtCore import QCoreApplication, QEventLoop, QMarginsF, Qt, QTimer, QUrl
    from PySide6.QtGui import QPageLayout, QPageSize
    from PySide6.QtWidgets import QApplication

    # Must precede the application object, or WebEngine cannot share a context.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    from PySide6.QtWebEngineCore import QWebEnginePage

    application = QApplication([sys.argv[0]])
    page = QWebEnginePage()
    loop = QEventLoop()
    outcome = {"loaded": False, "written": False}

    def loaded(ok: bool) -> None:
        outcome["loaded"] = ok
        loop.quit()

    def written(_path: str, ok: bool) -> None:
        outcome["written"] = ok
        loop.quit()

    page.loadFinished.connect(loaded)
    page.pdfPrintingFinished.connect(written)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)

    page.setHtml(source.read_text(encoding="utf-8"), QUrl.fromLocalFile(str(source)))
    guard.start(45_000)
    loop.exec()
    if not outcome["loaded"]:
        return 2
    layout = QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
        QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter,
    )
    page.printToPdf(str(target), layout)
    guard.start(45_000)
    loop.exec()
    del page
    application.processEvents()
    return 0 if outcome["written"] and target.is_file() else 3


if __name__ == "__main__":  # pragma: no cover - the child process
    sys.exit(_render_in_this_process(Path(sys.argv[1]), Path(sys.argv[2])))


__all__ = ["html_to_pdf"]
