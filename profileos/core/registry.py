"""Typed plugin registries.

Everything extensible in ProfileOS — post-processors, machining macros, tool
databases, supplier price lists, pipe catalogues — is registered into a
:class:`Registry`. Registries are keyed by a short string id and support
versioning, so a hot-reloaded plugin can replace an entry atomically without
callers holding a stale reference (they look items up by id on each use).

Registration is normally declarative::

    from profileos.core.registry import POST_PROCESSORS

    @POST_PROCESSORS.register("elumatec.ncx", version="3.2")
    class NcxPostProcessor(BasePostProcessor):
        ...
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generic, Iterator, TypeVar

from .errors import PluginError
from .events import Topic, publish
from .logging_setup import get_logger

_log = get_logger("core.registry")

T = TypeVar("T")


@dataclass
class RegistryEntry(Generic[T]):
    """One registered item plus its provenance."""

    key: str
    item: T
    version: str = "1.0"
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Incremented each time this key is replaced; lets callers detect updates.
    generation: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "source": self.source,
            "generation": self.generation,
            "registered_at": self.registered_at.isoformat(),
            **self.metadata,
        }


class Registry(Generic[T]):
    """A thread-safe, replaceable mapping of ``key -> item``.

    Parameters
    ----------
    name:
        Human readable registry name, used in log and error messages.
    allow_replace:
        When ``False``, re-registering an existing key raises instead of
        replacing it. Hot-reloadable registries keep this ``True``.
    """

    def __init__(self, name: str, *, allow_replace: bool = True) -> None:
        self.name = name
        self.allow_replace = allow_replace
        self._lock = threading.RLock()
        self._entries: dict[str, RegistryEntry[T]] = {}
        self._aliases: dict[str, str] = {}

    # -- registration ------------------------------------------------------ #
    def register(
        self,
        key: str,
        *,
        version: str = "1.0",
        source: str | None = None,
        aliases: tuple[str, ...] = (),
        **metadata: Any,
    ) -> Callable[[T], T]:
        """Decorator form: ``@REGISTRY.register("my.key")``."""

        def decorator(item: T) -> T:
            self.add(
                key,
                item,
                version=version,
                source=source,
                aliases=aliases,
                **metadata,
            )
            return item

        return decorator

    def add(
        self,
        key: str,
        item: T,
        *,
        version: str = "1.0",
        source: str | None = None,
        aliases: tuple[str, ...] = (),
        **metadata: Any,
    ) -> RegistryEntry[T]:
        """Register ``item`` under ``key``, replacing any previous entry."""
        key = key.strip().lower()
        if not key:
            raise PluginError(f"{self.name}: empty registry key")

        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and not self.allow_replace:
                raise PluginError(
                    f"{self.name}: key already registered", key=key, registry=self.name
                )
            entry = RegistryEntry(
                key=key,
                item=item,
                version=version,
                source=source,
                metadata=metadata,
                generation=(existing.generation + 1) if existing else 0,
            )
            self._entries[key] = entry
            for alias in aliases:
                self._aliases[alias.strip().lower()] = key

        if existing is None:
            _log.debug("%s: registered %r (v%s)", self.name, key, version)
            publish(Topic.PLUGIN_LOADED, source=self.name, key=key, version=version)
        else:
            _log.info("%s: replaced %r (v%s -> v%s)", self.name, key, existing.version, version)
            publish(
                Topic.PLUGIN_RELOADED,
                source=self.name,
                key=key,
                version=version,
                previous_version=existing.version,
            )
        return entry

    def remove(self, key: str) -> bool:
        """Unregister ``key``. Returns ``True`` when something was removed."""
        key = self._resolve_key(key)
        with self._lock:
            removed = self._entries.pop(key, None) is not None
            if removed:
                for alias, target in list(self._aliases.items()):
                    if target == key:
                        del self._aliases[alias]
        return removed

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._aliases.clear()

    def remove_by_source(self, source: str) -> int:
        """Drop every entry that came from ``source`` (used when a file is deleted)."""
        with self._lock:
            keys = [k for k, e in self._entries.items() if e.source == source]
        for key in keys:
            self.remove(key)
        return len(keys)

    # -- lookup ------------------------------------------------------------ #
    def _resolve_key(self, key: str) -> str:
        key = key.strip().lower()
        return self._aliases.get(key, key)

    def get(self, key: str) -> T:
        """Return the registered item, raising :class:`PluginError` if absent."""
        resolved = self._resolve_key(key)
        with self._lock:
            entry = self._entries.get(resolved)
        if entry is None:
            raise PluginError(
                f"{self.name}: no entry registered for {key!r}",
                key=key,
                registry=self.name,
                available=sorted(self._entries),
            )
        return entry.item

    def get_or_none(self, key: str) -> T | None:
        try:
            return self.get(key)
        except PluginError:
            return None

    def entry(self, key: str) -> RegistryEntry[T] | None:
        with self._lock:
            return self._entries.get(self._resolve_key(key))

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return self._resolve_key(key) in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(sorted(self._entries))

    def keys(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)

    def items(self) -> list[tuple[str, T]]:
        with self._lock:
            return [(k, e.item) for k, e in sorted(self._entries.items())]

    def describe(self) -> list[dict[str, Any]]:
        """Machine-readable listing, used by the CLI and the UI plugin panel."""
        with self._lock:
            return [e.describe() for _, e in sorted(self._entries.items())]


# --------------------------------------------------------------------------- #
# Well-known registries
# --------------------------------------------------------------------------- #

#: CNC post-processors, keyed like ``"elumatec.ncx"``.
POST_PROCESSORS: Registry[Any] = Registry("post_processors")

#: Parametric machining macros, keyed like ``"lock.euro_cylinder"``.
MACROS: Registry[Any] = Registry("macros")

#: Cutting tool catalogues, keyed by tool database id.
TOOL_LIBRARIES: Registry[Any] = Registry("tool_libraries")

#: Machine definitions (envelope, clamps, axes), keyed by machine id.
MACHINES: Registry[Any] = Registry("machines")

#: Aluminium alloys and other materials, keyed like ``"en-aw-6060-t66"``.
MATERIALS: Registry[Any] = Registry("materials")

#: Profile system libraries, keyed by system series.
PROFILE_SYSTEMS: Registry[Any] = Registry("profile_systems")

#: Supplier price lists for the quoting engine.
SUPPLIERS: Registry[Any] = Registry("suppliers")

#: Pipe / fitting catalogues for the plumbing engine.
PIPE_CATALOGUES: Registry[Any] = Registry("pipe_catalogues")

#: Nesting strategies, keyed like ``"milp"`` or ``"ffd"``.
NESTING_STRATEGIES: Registry[Any] = Registry("nesting_strategies")

ALL_REGISTRIES: dict[str, Registry[Any]] = {
    "post_processors": POST_PROCESSORS,
    "macros": MACROS,
    "tool_libraries": TOOL_LIBRARIES,
    "machines": MACHINES,
    "materials": MATERIALS,
    "profile_systems": PROFILE_SYSTEMS,
    "suppliers": SUPPLIERS,
    "pipe_catalogues": PIPE_CATALOGUES,
    "nesting_strategies": NESTING_STRATEGIES,
}


def registry_report() -> dict[str, list[dict[str, Any]]]:
    """Snapshot of every registry, for diagnostics and the UI."""
    return {name: reg.describe() for name, reg in ALL_REGISTRIES.items()}


__all__ = [
    "Registry",
    "RegistryEntry",
    "POST_PROCESSORS",
    "MACROS",
    "TOOL_LIBRARIES",
    "MACHINES",
    "MATERIALS",
    "PROFILE_SYSTEMS",
    "SUPPLIERS",
    "PIPE_CATALOGUES",
    "NESTING_STRATEGIES",
    "ALL_REGISTRIES",
    "registry_report",
]
