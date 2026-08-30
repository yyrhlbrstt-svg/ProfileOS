"""Logging configuration for ProfileOS.

Provides a single :func:`configure_logging` entry point that installs a console
handler (colourised through ``rich`` when available) and an optional rotating
file handler. Engines obtain loggers via :func:`get_logger`, which namespaces
everything under ``profileos.*`` so host applications can filter on one prefix.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_CONFIGURED = False

DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under ``profileos``.

    ``get_logger("nesting.milp")`` yields the logger ``profileos.nesting.milp``.
    Passing a name that already starts with ``profileos`` leaves it untouched,
    so ``get_logger(__name__)`` works from inside the package.
    """
    if name.startswith("profileos"):
        return logging.getLogger(name)
    return logging.getLogger(f"profileos.{name}")


def configure_logging(
    level: int | str = logging.INFO,
    *,
    log_file: str | os.PathLike[str] | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    use_rich: bool = True,
    force: bool = False,
) -> logging.Logger:
    """Install handlers on the root ``profileos`` logger.

    Parameters
    ----------
    level:
        Minimum level for the console handler.
    log_file:
        When given, a rotating file handler is added at ``DEBUG`` level so the
        full diagnostic trail survives even when the console is quiet.
    use_rich:
        Use ``rich.logging.RichHandler`` when the library is importable.
    force:
        Re-configure even if logging was already set up (drops old handlers).

    Returns
    -------
    logging.Logger
        The configured ``profileos`` root logger.
    """
    global _CONFIGURED

    root = logging.getLogger("profileos")
    if _CONFIGURED and not force:
        return root

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    root.setLevel(logging.DEBUG)
    # Do not leak our records into the application's root logger.
    root.propagate = False

    console: logging.Handler | None = None
    if use_rich:
        try:  # pragma: no cover - depends on optional dependency
            from rich.logging import RichHandler

            console = RichHandler(
                rich_tracebacks=True,
                show_path=False,
                omit_repeated_times=False,
            )
            console.setFormatter(logging.Formatter("%(name)-28s | %(message)s"))
        except ImportError:
            console = None

    if console is None:
        console = logging.StreamHandler(stream=sys.stderr)
        console.setFormatter(logging.Formatter(DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT))

    console.setLevel(level)
    root.addHandler(console)

    if log_file is not None:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT))
        root.addHandler(file_handler)

    _CONFIGURED = True
    root.debug("Logging configured (level=%s, file=%s)", logging.getLevelName(level), log_file)
    return root


def set_level(level: int | str) -> None:
    """Change the console verbosity after configuration."""
    root = logging.getLogger("profileos")
    for handler in root.handlers:
        if not isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.setLevel(level)


__all__ = ["configure_logging", "get_logger", "set_level"]
