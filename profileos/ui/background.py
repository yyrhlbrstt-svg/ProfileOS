"""Slow work off the screen's thread: a supplier's website, a large download.

A check of five supplier pages can take a minute on a workshop's line, and a
catalogue PDF can be thirty megabytes. Neither may freeze the window, so the
work runs on a plain thread and its result comes back to the screen's thread
through a Qt signal — the only safe way back to widgets.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class BackgroundJob(QObject):
    """Run one function on a thread; call ``done(result, error)`` on the screen."""

    finished = Signal(object, object)

    #: Jobs still running, kept alive until they report back.
    _running: set["BackgroundJob"] = set()

    def __init__(self, work: Callable[[], Any], done: Callable[[Any, BaseException | None], None]) -> None:
        super().__init__()
        self._work = work
        self._done = done
        self.finished.connect(self._finish)

    def start(self) -> "BackgroundJob":
        BackgroundJob._running.add(self)
        threading.Thread(target=self._run, name="profileos-background", daemon=True).start()
        return self

    def _run(self) -> None:
        try:
            result = self._work()
        except BaseException as exc:  # noqa: BLE001 - handed to the screen to show
            self.finished.emit(None, exc)
            return
        self.finished.emit(result, None)

    def _finish(self, result: Any, error: Any) -> None:
        BackgroundJob._running.discard(self)
        self._done(result, error)


def run_in_background(work: Callable[[], Any],
                      done: Callable[[Any, BaseException | None], None]) -> BackgroundJob:
    return BackgroundJob(work, done).start()


__all__ = ["BackgroundJob", "run_in_background"]
