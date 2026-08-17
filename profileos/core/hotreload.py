"""Continuous-update framework: static validation, hot module reloading, watching.

ProfileOS is designed to absorb new machine post-processors, macro libraries and
tool databases while the application is running. This module implements that in
three layers:

``StaticValidator``
    Parses candidate Python plugins with :mod:`ast` and rejects anything using
    constructs a plugin has no business using (``exec``, ``eval``, dynamic
    ``__import__``, ``subprocess``, filesystem deletion...). Validation happens
    *before* the module is executed, so a malformed or hostile plugin never
    runs.

``PluginLoader``
    Imports validated Python plugins under a private module namespace via
    :mod:`importlib`, and loads declarative JSON/XML plugins through their
    registered :mod:`pydantic` schema. Reloading replaces registry entries
    atomically.

``HotReloadManager``
    A polling watcher thread that notices content changes (mtime + SHA-256) in
    the configured plugin directories and triggers reloads, publishing
    ``plugin.*`` and ``config.reloaded`` events.

A plugin module must expose a module-level ``register(context)`` function.
``context`` is a :class:`PluginContext` giving access to the registries and the
active settings.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable

from .config import Settings, get_settings, reload_settings
from .errors import PluginError, PluginValidationError
from .events import Topic, publish
from .logging_setup import get_logger
from .registry import ALL_REGISTRIES, Registry

_log = get_logger("core.hotreload")

#: Namespace under which plugin modules are inserted into ``sys.modules``.
PLUGIN_NAMESPACE = "profileos_plugins"

PYTHON_SUFFIXES = {".py"}
DATA_SUFFIXES = {".json", ".xml"}


# --------------------------------------------------------------------------- #
# Static validation
# --------------------------------------------------------------------------- #

#: Names that may never be called from a plugin.
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "breakpoint",
        "input",
    }
)

#: Modules a plugin may never import.
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
    {
        "subprocess",
        "ctypes",
        "socket",
        "shutil",
        "multiprocessing",
        "pty",
        "telnetlib",
        "ftplib",
        "pickle",
        "marshal",
        "importlib",
    }
)

#: ``module.attribute`` pairs that are refused even when the module is allowed.
FORBIDDEN_ATTRIBUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("os", "system"),
        ("os", "popen"),
        ("os", "remove"),
        ("os", "unlink"),
        ("os", "rmdir"),
        ("os", "removedirs"),
        ("os", "execv"),
        ("os", "execve"),
        ("os", "fork"),
        ("os", "kill"),
        ("sys", "exit"),
        ("sys", "modules"),
    }
)


@dataclass
class ValidationReport:
    """Outcome of static analysis for one plugin file."""

    path: Path
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise PluginValidationError(
                f"Plugin rejected by static validation: {self.path.name}",
                path=str(self.path),
                errors=self.errors,
            )


class StaticValidator(ast.NodeVisitor):
    """Walks a plugin's AST and collects policy violations.

    The validator is deliberately conservative: a plugin is configuration
    expressed as code, so it needs arithmetic, string formatting and the
    ProfileOS API — nothing else.
    """

    def __init__(self, path: Path, *, required_entrypoint: str = "register") -> None:
        self.path = path
        self.required_entrypoint = required_entrypoint
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.functions: list[str] = []
        self.imports: list[str] = []

    # -- visitors ---------------------------------------------------------- #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            self.imports.append(alias.name)
            if root in FORBIDDEN_IMPORTS:
                self._err(node, f"import of forbidden module {alias.name!r}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".")[0]
        self.imports.append(module)
        if root in FORBIDDEN_IMPORTS:
            self._err(node, f"import from forbidden module {module!r}")
        for alias in node.names:
            if (root, alias.name) in FORBIDDEN_ATTRIBUTES:
                self._err(node, f"import of forbidden name {module}.{alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
            self._err(node, f"call to forbidden builtin {func.id!r}")
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            pair = (func.value.id, func.attr)
            if pair in FORBIDDEN_ATTRIBUTES:
                self._err(node, f"call to forbidden attribute {pair[0]}.{pair[1]}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in {"__globals__", "__code__", "__subclasses__", "__bases__"}:
            self._err(node, f"access to introspection attribute {node.attr!r}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)

    # -- helpers ----------------------------------------------------------- #
    def _err(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", "?")
        self.errors.append(f"{self.path.name}:{line}: {message}")

    def run(self, source: str) -> ValidationReport:
        try:
            tree = ast.parse(source, filename=str(self.path))
        except SyntaxError as exc:
            return ValidationReport(
                path=self.path,
                ok=False,
                errors=[f"{self.path.name}:{exc.lineno}: syntax error: {exc.msg}"],
            )

        self.visit(tree)

        if self.required_entrypoint and self.required_entrypoint not in self.functions:
            self.errors.append(
                f"{self.path.name}: missing required entrypoint "
                f"'{self.required_entrypoint}(context)'"
            )

        module_doc = ast.get_docstring(tree)
        if not module_doc:
            self.warnings.append(f"{self.path.name}: plugin has no module docstring")

        return ValidationReport(
            path=self.path,
            ok=not self.errors,
            errors=self.errors,
            warnings=self.warnings,
            entrypoints=self.functions,
            imports=self.imports,
        )


def validate_plugin_source(path: Path, *, required_entrypoint: str = "register") -> ValidationReport:
    """Statically validate the Python plugin at ``path``."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ValidationReport(path=path, ok=False, errors=[f"cannot read file: {exc}"])
    return StaticValidator(path, required_entrypoint=required_entrypoint).run(source)


# --------------------------------------------------------------------------- #
# Declarative (data) plugins
# --------------------------------------------------------------------------- #

@dataclass
class DataSchema:
    """Binds a data-plugin ``kind`` to a validation model and a target registry.

    ``model`` must be a pydantic model class (or any callable accepting the raw
    mapping and returning the validated object). ``key_field`` names the
    attribute used as the registry key.
    """

    kind: str
    model: Callable[[dict[str, Any]], Any]
    registry: Registry[Any]
    key_field: str = "id"
    version_field: str = "version"


class DataSchemaRegistry:
    """Maps the ``kind`` discriminator of a JSON/XML plugin to its schema."""

    def __init__(self) -> None:
        self._schemas: dict[str, DataSchema] = {}

    def register(self, schema: DataSchema) -> None:
        self._schemas[schema.kind.lower()] = schema

    def get(self, kind: str) -> DataSchema | None:
        return self._schemas.get(kind.lower())

    def kinds(self) -> list[str]:
        return sorted(self._schemas)


#: Data-plugin schemas, populated by the engines at import time.
DATA_SCHEMAS = DataSchemaRegistry()


def _xml_to_dict(element: ET.Element) -> Any:
    """Convert an XML element into nested dicts/lists.

    Attributes merge into the mapping; repeated child tags become lists; a leaf
    with no attributes collapses to its text value.
    """
    node: dict[str, Any] = {}
    node.update(element.attrib)

    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if not node:
            return text
        if text:
            node["_text"] = text
        return node

    for child in children:
        value = _xml_to_dict(child)
        if child.tag in node:
            existing = node[child.tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                node[child.tag] = [existing, value]
        else:
            node[child.tag] = value
    return node


def load_data_document(path: Path) -> dict[str, Any]:
    """Read a JSON or XML plugin document into a mapping."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        elif suffix == ".xml":
            root = ET.fromstring(path.read_text(encoding="utf-8"))
            data = _xml_to_dict(root)
            if isinstance(data, dict):
                data.setdefault("kind", root.tag)
        else:  # pragma: no cover - guarded by callers
            raise PluginError(f"Unsupported data plugin type: {suffix}", path=str(path))
    except (OSError, json.JSONDecodeError, ET.ParseError) as exc:
        raise PluginValidationError(f"Cannot parse {path.name}: {exc}", path=str(path)) from exc

    if not isinstance(data, dict):
        raise PluginValidationError(
            f"{path.name}: top level must be an object/element", path=str(path)
        )
    return data


# --------------------------------------------------------------------------- #
# Plugin context and loader
# --------------------------------------------------------------------------- #

@dataclass
class PluginContext:
    """Handed to a plugin's ``register(context)`` function."""

    settings: Settings
    registries: dict[str, Registry[Any]] = field(default_factory=lambda: dict(ALL_REGISTRIES))
    source: str | None = None
    version: str = "1.0"

    def registry(self, name: str) -> Registry[Any]:
        try:
            return self.registries[name]
        except KeyError as exc:
            raise PluginError(
                f"Unknown registry {name!r}", available=sorted(self.registries)
            ) from exc

    def register(self, registry_name: str, key: str, item: Any, **metadata: Any) -> None:
        """Convenience wrapper stamping the plugin's source onto the entry."""
        self.registry(registry_name).add(
            key, item, version=self.version, source=self.source, **metadata
        )


@dataclass
class LoadedPlugin:
    """Bookkeeping for one loaded plugin file."""

    path: Path
    module_name: str | None
    digest: str
    mtime: float
    kind: str  # "python" | "data"
    keys: list[str] = field(default_factory=list)
    report: ValidationReport | None = None


def file_digest(path: Path) -> str:
    """SHA-256 of a file's contents, used to ignore no-op saves."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class PluginLoader:
    """Loads, validates and reloads plugins from disk."""

    def __init__(self, settings: Settings | None = None, *, strict: bool = True) -> None:
        self.settings = settings or get_settings()
        #: When strict, a rejected plugin raises; otherwise it is logged and skipped.
        self.strict = strict
        self._loaded: dict[Path, LoadedPlugin] = {}
        self._lock = threading.RLock()

    # -- discovery --------------------------------------------------------- #
    def discover(self, directories: Iterable[Path] | None = None) -> list[Path]:
        """Return every plugin file under the given directories.

        Files and directories whose name starts with ``_`` or ``.`` are skipped,
        which is how a plugin author disables one without deleting it.
        """
        dirs = list(directories) if directories is not None else self.settings.effective_plugin_dirs()
        found: list[Path] = []
        for directory in dirs:
            directory = Path(directory)
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                if any(part.startswith((".", "_")) for part in path.relative_to(directory).parts):
                    continue
                if path.suffix.lower() in PYTHON_SUFFIXES | DATA_SUFFIXES:
                    found.append(path)
        return found

    # -- loading ----------------------------------------------------------- #
    def load_all(self, directories: Iterable[Path] | None = None) -> list[LoadedPlugin]:
        """Load every discovered plugin. Returns the successfully loaded ones."""
        loaded: list[LoadedPlugin] = []
        for path in self.discover(directories):
            try:
                plugin = self.load(path)
            except PluginError as exc:
                _log.error("Plugin failed: %s", exc)
                publish(Topic.PLUGIN_FAILED, source="loader", path=str(path), error=str(exc))
                if self.strict and isinstance(exc, PluginValidationError):
                    # Validation failures are the author's bug; surface them but
                    # keep loading the rest so one bad file cannot brick startup.
                    continue
                continue
            if plugin is not None:
                loaded.append(plugin)
        return loaded

    def load(self, path: Path, *, force: bool = False) -> LoadedPlugin | None:
        """Load or reload a single plugin file.

        Returns ``None`` when the file is unchanged since the last load and
        ``force`` is false.
        """
        path = Path(path).resolve()
        if not path.is_file():
            raise PluginError("Plugin file does not exist", path=str(path))

        digest = file_digest(path)
        with self._lock:
            previous = self._loaded.get(path)
        if previous is not None and previous.digest == digest and not force:
            return None

        if path.suffix.lower() in PYTHON_SUFFIXES:
            plugin = self._load_python(path, digest)
        else:
            plugin = self._load_data(path, digest)

        with self._lock:
            self._loaded[path] = plugin
        return plugin

    def _module_name(self, path: Path) -> str:
        stem = path.stem.replace("-", "_").replace(".", "_")
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
        return f"{PLUGIN_NAMESPACE}.{stem}_{digest}"

    def _load_python(self, path: Path, digest: str) -> LoadedPlugin:
        report = validate_plugin_source(path)
        for warning in report.warnings:
            _log.warning("%s", warning)
        report.raise_if_failed()

        module_name = self._module_name(path)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise PluginError("Cannot create import spec", path=str(path))

        module: ModuleType = importlib.util.module_from_spec(spec)
        # Register before exec so dataclasses/pickle inside the plugin resolve.
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - plugin author's error
            sys.modules.pop(module_name, None)
            raise PluginError(
                f"Plugin raised during import: {exc}", path=str(path), error=str(exc)
            ) from exc

        source_id = str(path)
        # Drop entries from the previous generation of this same file so a
        # renamed key does not linger in the registry.
        for registry in ALL_REGISTRIES.values():
            registry.remove_by_source(source_id)

        version = str(getattr(module, "__plugin_version__", "1.0"))
        context = PluginContext(settings=self.settings, source=source_id, version=version)

        entry = getattr(module, "register", None)
        if not callable(entry):  # pragma: no cover - validator already checked
            raise PluginValidationError("Plugin has no callable register()", path=str(path))
        try:
            entry(context)
        except Exception as exc:  # noqa: BLE001
            raise PluginError(
                f"register() failed: {exc}", path=str(path), error=str(exc)
            ) from exc

        keys = [
            f"{name}:{e['key']}"
            for name, registry in ALL_REGISTRIES.items()
            for e in registry.describe()
            if e["source"] == source_id
        ]
        _log.info("Loaded python plugin %s (%d registrations)", path.name, len(keys))
        return LoadedPlugin(
            path=path,
            module_name=module_name,
            digest=digest,
            mtime=path.stat().st_mtime,
            kind="python",
            keys=keys,
            report=report,
        )

    def _load_data(self, path: Path, digest: str) -> LoadedPlugin:
        document = load_data_document(path)
        kind = str(document.get("kind") or document.get("type") or "").strip()
        if not kind:
            raise PluginValidationError(
                f"{path.name}: data plugin must declare a 'kind' field", path=str(path)
            )

        schema = DATA_SCHEMAS.get(kind)
        if schema is None:
            raise PluginValidationError(
                f"{path.name}: unknown data plugin kind {kind!r}",
                path=str(path),
                known_kinds=DATA_SCHEMAS.kinds(),
            )

        try:
            obj = schema.model(document)
        except Exception as exc:  # pydantic ValidationError
            raise PluginValidationError(
                f"{path.name}: schema validation failed: {exc}", path=str(path), kind=kind
            ) from exc

        key = getattr(obj, schema.key_field, None) or document.get(schema.key_field)
        if not key:
            raise PluginValidationError(
                f"{path.name}: missing key field {schema.key_field!r}", path=str(path)
            )
        version = str(getattr(obj, schema.version_field, None) or document.get("version") or "1.0")

        source_id = str(path)
        schema.registry.remove_by_source(source_id)
        schema.registry.add(str(key), obj, version=version, source=source_id, kind=kind)

        _log.info("Loaded data plugin %s -> %s:%s", path.name, schema.registry.name, key)
        return LoadedPlugin(
            path=path,
            module_name=None,
            digest=digest,
            mtime=path.stat().st_mtime,
            kind="data",
            keys=[f"{schema.registry.name}:{key}"],
        )

    # -- unloading --------------------------------------------------------- #
    def unload(self, path: Path) -> bool:
        """Remove every registration made by ``path`` and forget the module."""
        path = Path(path).resolve()
        with self._lock:
            plugin = self._loaded.pop(path, None)
        if plugin is None:
            return False
        removed = sum(reg.remove_by_source(str(path)) for reg in ALL_REGISTRIES.values())
        if plugin.module_name:
            sys.modules.pop(plugin.module_name, None)
        _log.info("Unloaded plugin %s (%d registrations removed)", path.name, removed)
        return True

    @property
    def loaded(self) -> list[LoadedPlugin]:
        with self._lock:
            return list(self._loaded.values())


# --------------------------------------------------------------------------- #
# Watcher
# --------------------------------------------------------------------------- #

class HotReloadManager:
    """Polls plugin directories and reloads what changed.

    Polling (rather than inotify/FSEvents) keeps the implementation dependency
    free and behaves identically on every platform, including network shares
    where native watchers are unreliable — and a plant's machine-definition
    directory is very often a network share.
    """

    def __init__(
        self,
        loader: PluginLoader | None = None,
        settings: Settings | None = None,
        *,
        watch_settings_file: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.loader = loader or PluginLoader(self.settings)
        self.watch_settings_file = watch_settings_file
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._seen: dict[Path, tuple[float, int]] = {}
        self._settings_digest: str | None = None

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        """Begin watching in a daemon thread (idempotent)."""
        if not self.settings.enable_hot_reload:
            _log.info("Hot reload disabled by configuration")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="profileos-hotreload", daemon=True
        )
        self._thread.start()
        _log.info(
            "Hot reload watching %s (every %.1fs)",
            [str(p) for p in self.settings.effective_plugin_dirs()],
            self.settings.watch_interval_s,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the watcher thread to exit and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def __enter__(self) -> "HotReloadManager":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- scanning ---------------------------------------------------------- #
    def initial_load(self) -> int:
        """Load every plugin once and prime the change-detection state."""
        plugins = self.loader.load_all()
        for path in self.loader.discover():
            self._remember(path)
        if self.watch_settings_file and self.settings.settings_file.is_file():
            self._settings_digest = file_digest(self.settings.settings_file)
        return len(plugins)

    def _remember(self, path: Path) -> None:
        try:
            stat = path.stat()
        except OSError:
            return
        self._seen[path.resolve()] = (stat.st_mtime, stat.st_size)

    def poll_once(self) -> list[Path]:
        """Perform one scan. Returns the paths that were reloaded or removed."""
        changed: list[Path] = []
        current = {p.resolve() for p in self.loader.discover()}

        for path in sorted(current):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_mtime, stat.st_size)
            if self._seen.get(path) == signature:
                continue
            self._seen[path] = signature
            try:
                # load() re-hashes and returns None when content is identical,
                # which filters out touch-only changes and editor re-saves.
                if self.loader.load(path) is not None:
                    changed.append(path)
            except PluginError as exc:
                _log.error("Hot reload rejected %s: %s", path.name, exc)
                publish(Topic.PLUGIN_FAILED, source="hotreload", path=str(path), error=str(exc))

        for path in list(self._seen):
            if path not in current:
                self._seen.pop(path, None)
                if self.loader.unload(path):
                    changed.append(path)

        if self.watch_settings_file:
            self._poll_settings()

        return changed

    def _poll_settings(self) -> None:
        path = self.settings.settings_file
        if not path.is_file():
            return
        digest = file_digest(path)
        if self._settings_digest is None:
            self._settings_digest = digest
            return
        if digest == self._settings_digest:
            return
        self._settings_digest = digest
        try:
            new_settings = reload_settings()
        except Exception as exc:  # noqa: BLE001 - keep watching on bad edits
            _log.error("Settings reload failed, keeping previous configuration: %s", exc)
            publish(Topic.PLUGIN_FAILED, source="hotreload", path=str(path), error=str(exc))
            return
        self.settings = new_settings
        self.loader.settings = new_settings
        _log.info("Configuration reloaded from %s", path)
        publish(Topic.CONFIG_RELOADED, source="hotreload", path=str(path))

    def _run(self) -> None:
        interval = max(0.25, float(self.settings.watch_interval_s))
        while not self._stop.wait(interval):
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001 - the watcher must never die
                _log.exception("Hot reload poll failed")
            interval = max(0.25, float(self.settings.watch_interval_s))


__all__ = [
    "PLUGIN_NAMESPACE",
    "FORBIDDEN_CALLS",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_ATTRIBUTES",
    "ValidationReport",
    "StaticValidator",
    "validate_plugin_source",
    "DataSchema",
    "DataSchemaRegistry",
    "DATA_SCHEMAS",
    "load_data_document",
    "PluginContext",
    "LoadedPlugin",
    "PluginLoader",
    "HotReloadManager",
    "file_digest",
]
