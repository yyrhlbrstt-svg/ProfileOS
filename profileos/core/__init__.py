"""Core services: configuration, logging, events, plugin registries, hot reload."""

from __future__ import annotations

from .config import (
    Settings,
    get_settings,
    load_settings,
    reload_settings,
    save_settings,
)
from .errors import ProfileOSError
from .events import BUS, Event, EventBus, Topic, publish, subscribe
from .hotreload import (
    DATA_SCHEMAS,
    DataSchema,
    HotReloadManager,
    PluginContext,
    PluginLoader,
    register_builtin_schemas,
    validate_plugin_source,
)
from .logging_setup import configure_logging, get_logger, set_level
from .profiling import REGISTRY as PROFILER
from .profiling import Timer, timed
from .registry import ALL_REGISTRIES, Registry, registry_report

__all__ = [
    "Settings",
    "get_settings",
    "load_settings",
    "reload_settings",
    "save_settings",
    "ProfileOSError",
    "BUS",
    "Event",
    "EventBus",
    "Topic",
    "publish",
    "subscribe",
    "DATA_SCHEMAS",
    "DataSchema",
    "HotReloadManager",
    "PluginContext",
    "PluginLoader",
    "register_builtin_schemas",
    "validate_plugin_source",
    "configure_logging",
    "get_logger",
    "set_level",
    "PROFILER",
    "Timer",
    "timed",
    "ALL_REGISTRIES",
    "Registry",
    "registry_report",
]
