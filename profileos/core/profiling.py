"""Lightweight performance instrumentation.

Every long-running engine step is wrapped in :func:`timed` or the
:class:`Timer` context manager. Measurements accumulate in a process-wide
:class:`ProfileRegistry` that the UI reads to display a live performance panel,
and that the CLI can dump after a batch run.

The overhead is a single :func:`time.perf_counter` pair per call, so the
instrumentation can stay enabled in production.
"""

from __future__ import annotations

import functools
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar
from contextlib import contextmanager

from .logging_setup import get_logger

_log = get_logger("core.profiling")

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class Measurement:
    """Aggregated timings for one instrumented label."""

    label: str
    count: int = 0
    total_s: float = 0.0
    min_s: float = float("inf")
    max_s: float = 0.0

    def add(self, elapsed: float) -> None:
        self.count += 1
        self.total_s += elapsed
        self.min_s = min(self.min_s, elapsed)
        self.max_s = max(self.max_s, elapsed)

    @property
    def mean_s(self) -> float:
        return self.total_s / self.count if self.count else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
            "total_ms": round(self.total_s * 1000.0, 3),
            "mean_ms": round(self.mean_s * 1000.0, 3),
            "min_ms": round(self.min_s * 1000.0, 3) if self.count else 0.0,
            "max_ms": round(self.max_s * 1000.0, 3),
        }


class ProfileRegistry:
    """Thread-safe collection of :class:`Measurement` records."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Measurement] = {}
        self.enabled: bool = True

    def record(self, label: str, elapsed: float) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data.setdefault(label, Measurement(label)).add(elapsed)

    def get(self, label: str) -> Measurement | None:
        with self._lock:
            return self._data.get(label)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return all measurements, slowest cumulative first."""
        with self._lock:
            rows = [m.as_dict() for m in self._data.values()]
        rows.sort(key=lambda r: r["total_ms"], reverse=True)
        return rows

    def reset(self) -> None:
        with self._lock:
            self._data.clear()

    def report(self) -> str:
        """Render a fixed-width text table of the current measurements."""
        rows = self.snapshot()
        if not rows:
            return "No timings recorded."
        head = f"{'label':<44}{'n':>6}{'total ms':>12}{'mean ms':>11}{'max ms':>11}"
        lines = [head, "-" * len(head)]
        for r in rows:
            lines.append(
                f"{r['label']:<44}{r['count']:>6}{r['total_ms']:>12.3f}"
                f"{r['mean_ms']:>11.3f}{r['max_ms']:>11.3f}"
            )
        return "\n".join(lines)


#: Process-wide registry used by the decorators below.
REGISTRY = ProfileRegistry()


@contextmanager
def Timer(label: str, *, log_level: int | None = None) -> Iterator[dict[str, float]]:
    """Context manager timing the enclosed block.

    Yields a dict that receives the key ``elapsed_s`` on exit, so callers can
    read the duration without re-querying the registry::

        with Timer("nesting.solve") as t:
            solve()
        print(t["elapsed_s"])
    """
    holder: dict[str, float] = {}
    start = time.perf_counter()
    try:
        yield holder
    finally:
        elapsed = time.perf_counter() - start
        holder["elapsed_s"] = elapsed
        REGISTRY.record(label, elapsed)
        if log_level is not None:
            _log.log(log_level, "%s took %.1f ms", label, elapsed * 1000.0)


def timed(label: str | None = None) -> Callable[[F], F]:
    """Decorator recording the wall-clock duration of every call.

    ``label`` defaults to ``"<module tail>.<qualname>"``.
    """

    def decorator(func: F) -> F:
        name = label or f"{func.__module__.rsplit('.', 1)[-1]}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                REGISTRY.record(name, time.perf_counter() - start)

        return wrapper  # type: ignore[return-value]

    return decorator


__all__ = ["Measurement", "ProfileRegistry", "REGISTRY", "Timer", "timed"]
