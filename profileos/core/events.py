"""A small synchronous publish/subscribe event bus.

Engines emit events rather than reaching into the UI, which keeps the compute
layer importable in a headless process. The desktop application subscribes to
progress and reload events; the CLI subscribes to the same events and renders
them as log lines.

Handlers run synchronously on the publishing thread. A handler that raises is
logged and skipped — one bad listener must never abort an engine.
"""

from __future__ import annotations

import fnmatch
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .logging_setup import get_logger

_log = get_logger("core.events")

Handler = Callable[["Event"], None]


class Topic:
    """Well-known topic names.

    Topics are dotted strings; subscribers may use ``fnmatch`` wildcards, so
    ``"nesting.*"`` receives every nesting event.
    """

    GEOMETRY_LOADED = "geometry.loaded"
    GEOMETRY_FAILED = "geometry.failed"
    ANALYSIS_STARTED = "analysis.started"
    ANALYSIS_PROGRESS = "analysis.progress"
    ANALYSIS_COMPLETED = "analysis.completed"
    NESTING_STARTED = "nesting.started"
    NESTING_PROGRESS = "nesting.progress"
    NESTING_COMPLETED = "nesting.completed"
    CNC_POST_STARTED = "cnc.post.started"
    CNC_POST_COMPLETED = "cnc.post.completed"
    CNC_COLLISION = "cnc.collision"
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_RELOADED = "plugin.reloaded"
    PLUGIN_FAILED = "plugin.failed"
    CONFIG_RELOADED = "config.reloaded"
    LICENSE_GRANTED = "license.granted"
    LICENSE_DENIED = "license.denied"
    QUOTE_UPDATED = "quote.updated"


@dataclass(frozen=True)
class Event:
    """An immutable message on the bus."""

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


class _Subscription:
    """Holds a handler, weakly when it is a bound method."""

    __slots__ = ("pattern", "_ref", "_func", "once", "__weakref__")

    def __init__(self, pattern: str, handler: Handler, once: bool) -> None:
        self.pattern = pattern
        self.once = once
        self._ref: weakref.WeakMethod | None = None
        self._func: Handler | None = None
        if hasattr(handler, "__self__"):
            # Bound method: hold weakly so a closed window unsubscribes itself.
            self._ref = weakref.WeakMethod(handler)  # type: ignore[arg-type]
        else:
            self._func = handler

    def resolve(self) -> Handler | None:
        if self._func is not None:
            return self._func
        assert self._ref is not None
        return self._ref()

    def matches(self, topic: str) -> bool:
        return self.pattern == topic or fnmatch.fnmatchcase(topic, self.pattern)


class EventBus:
    """Thread-safe synchronous event bus."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list[_Subscription] = []
        self._history: list[Event] = []
        self.history_limit = 500
        self.enabled = True

    # -- subscription ------------------------------------------------------ #
    def subscribe(self, pattern: str, handler: Handler, *, once: bool = False) -> Callable[[], None]:
        """Register ``handler`` for topics matching ``pattern``.

        Returns a zero-argument callable that cancels the subscription.
        """
        sub = _Subscription(pattern, handler, once)
        with self._lock:
            self._subs.append(sub)

        def unsubscribe() -> None:
            with self._lock:
                if sub in self._subs:
                    self._subs.remove(sub)

        return unsubscribe

    def unsubscribe_all(self, pattern: str | None = None) -> None:
        """Drop every subscription, or every one matching ``pattern`` exactly."""
        with self._lock:
            if pattern is None:
                self._subs.clear()
            else:
                self._subs = [s for s in self._subs if s.pattern != pattern]

    # -- publishing -------------------------------------------------------- #
    def publish(self, topic: str, /, source: str | None = None, **payload: Any) -> Event:
        """Emit an event and dispatch it to matching handlers."""
        event = Event(topic=topic, payload=payload, source=source)
        if not self.enabled:
            return event

        with self._lock:
            self._history.append(event)
            if len(self._history) > self.history_limit:
                del self._history[: len(self._history) - self.history_limit]
            targets = [s for s in self._subs if s.matches(topic)]

        expired: list[_Subscription] = []
        for sub in targets:
            handler = sub.resolve()
            if handler is None:
                expired.append(sub)
                continue
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - a listener must not break the engine
                _log.exception("Event handler failed for topic %s", topic)
            if sub.once:
                expired.append(sub)

        if expired:
            with self._lock:
                for sub in expired:
                    if sub in self._subs:
                        self._subs.remove(sub)

        return event

    # -- introspection ----------------------------------------------------- #
    def history(self, pattern: str | None = None, limit: int | None = None) -> list[Event]:
        with self._lock:
            events = list(self._history)
        if pattern:
            events = [e for e in events if fnmatch.fnmatchcase(e.topic, pattern)]
        return events[-limit:] if limit else events

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


#: Process-wide bus used by all engines.
BUS = EventBus()


def publish(topic: str, /, source: str | None = None, **payload: Any) -> Event:
    """Publish on the process-wide :data:`BUS`."""
    return BUS.publish(topic, source=source, **payload)


def subscribe(pattern: str, handler: Handler, *, once: bool = False) -> Callable[[], None]:
    """Subscribe on the process-wide :data:`BUS`."""
    return BUS.subscribe(pattern, handler, once=once)


__all__ = ["Event", "EventBus", "Topic", "BUS", "publish", "subscribe"]
