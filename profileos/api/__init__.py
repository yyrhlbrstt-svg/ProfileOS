"""HTTP service API (requires the ``api`` extra)."""

from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str):
    """Import the app lazily so the package works without FastAPI installed."""
    if name == "app":
        from .server import app

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
