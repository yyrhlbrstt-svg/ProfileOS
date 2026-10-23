"""Desktop application (requires the ``ui`` extra)."""

from __future__ import annotations

__all__ = ["run"]


def __getattr__(name: str):
    """Import lazily so the package works without PySide6 installed."""
    if name == "run":
        from .app import run

        return run
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
