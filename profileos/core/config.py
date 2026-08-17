"""Application configuration and filesystem layout.

Settings resolve in this order (last wins):

1. Defaults baked into :class:`Settings`.
2. ``<config_dir>/settings.json``.
3. Environment variables prefixed ``PROFILEOS_`` (e.g. ``PROFILEOS_LOG_LEVEL``).
4. Explicit keyword overrides passed to :func:`load_settings`.

The resolved object is cached; call :func:`reload_settings` after editing the
file on disk. The hot-reload framework does exactly that when it sees the
settings file change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .errors import ConfigError
from .logging_setup import get_logger

_log = get_logger("core.config")

ENV_PREFIX = "PROFILEOS_"

#: Root of the installed package (used to locate bundled data).
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
#: Repository root when running from a source checkout.
PROJECT_ROOT = PACKAGE_ROOT.parent


def default_config_dir() -> Path:
    """Per-user configuration directory, honouring XDG on Linux."""
    if os.name == "nt":  # pragma: no cover - platform specific
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.uname().sysname == "Darwin":  # pragma: no cover - platform specific
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ProfileOS"


def default_data_dir() -> Path:
    """Per-user writable data directory (inventory, licenses, caches)."""
    if os.name == "nt":  # pragma: no cover - platform specific
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif os.uname().sysname == "Darwin":  # pragma: no cover - platform specific
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "ProfileOS"


class NestingDefaults(BaseModel):
    """Default parameters for the 1D cutting-stock engine (all lengths in mm)."""

    stock_lengths_mm: list[float] = Field(default_factory=lambda: [6000.0, 6500.0])
    kerf_mm: float = 3.5
    min_reusable_remnant_mm: float = 300.0
    trim_start_mm: float = 10.0
    trim_end_mm: float = 10.0
    target_yield_pct: float = 97.5
    solver_time_limit_s: float = 30.0
    max_patterns: int = 4000

    @field_validator("kerf_mm")
    @classmethod
    def _kerf_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("kerf_mm must be >= 0")
        return v

    @field_validator("stock_lengths_mm")
    @classmethod
    def _stock_positive(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("at least one stock length is required")
        if any(length <= 0 for length in v):
            raise ValueError("stock lengths must be > 0")
        return sorted(set(v))


class GeometryDefaults(BaseModel):
    """Tolerances used when reconstructing geometry from DXF."""

    #: Two endpoints closer than this are considered the same node when chaining.
    chain_tolerance_mm: float = 0.05
    #: Maximum sagitta when flattening arcs into line segments.
    arc_sagitta_mm: float = 0.02
    #: Minimum segment length kept after cleaning.
    min_segment_mm: float = 0.001
    #: Contours smaller than this area are discarded as construction noise.
    min_contour_area_mm2: float = 0.5
    #: Alarm threshold for thin walls in architectural aluminium.
    min_wall_thickness_mm: float = 1.2


class AnalysisDefaults(BaseModel):
    """Finite-element settings for torsion / warping analysis."""

    mesh_size_mm2: float = 2.0
    min_mesh_angle_deg: float = 30.0
    enable_warping: bool = True
    warping_timeout_s: float = 120.0


class CncDefaults(BaseModel):
    """Machining defaults applied when an operation omits values."""

    safe_z_mm: float = 25.0
    rapid_feed_mm_min: float = 20000.0
    default_spindle_rpm: int = 18000
    default_feed_mm_min: float = 1200.0
    clamp_clearance_mm: float = 15.0
    output_encoding: str = "utf-8"


class SecurityDefaults(BaseModel):
    """Licensing and authentication policy."""

    relying_party_id: str = "cad.system.local"
    relying_party_name: str = "ProfileOS"
    challenge_bytes: int = 32
    require_user_presence: bool = True
    require_user_verification: bool = False
    #: Grace period during which an expired offline license still opens read-only.
    offline_grace_days: int = 7
    #: Set to False on a workstation with no dongle (development mode).
    enforce_hardware_key: bool = False


class Settings(BaseModel):
    """Resolved application settings."""

    config_dir: Path = Field(default_factory=default_config_dir)
    data_dir: Path = Field(default_factory=default_data_dir)
    log_level: str = "INFO"
    log_file: Path | None = None
    locale: str = "en"
    #: Directories scanned by the hot-reload plugin framework.
    plugin_dirs: list[Path] = Field(default_factory=list)
    #: Poll interval for the plugin/config watcher, in seconds.
    watch_interval_s: float = 2.0
    enable_hot_reload: bool = True

    geometry: GeometryDefaults = Field(default_factory=GeometryDefaults)
    analysis: AnalysisDefaults = Field(default_factory=AnalysisDefaults)
    nesting: NestingDefaults = Field(default_factory=NestingDefaults)
    cnc: CncDefaults = Field(default_factory=CncDefaults)
    security: SecurityDefaults = Field(default_factory=SecurityDefaults)

    model_config = {"arbitrary_types_allowed": True}

    # -- derived locations ------------------------------------------------- #
    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def bundled_data_dir(self) -> Path:
        """Read-only catalogues shipped with the application."""
        return PROJECT_ROOT / "data"

    @property
    def machines_dir(self) -> Path:
        return self.config_dir / "machines"

    @property
    def tools_dir(self) -> Path:
        return self.config_dir / "tools"

    @property
    def macros_dir(self) -> Path:
        return self.config_dir / "macros"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def inventory_file(self) -> Path:
        return self.data_dir / "remnant_inventory.json"

    @property
    def license_file(self) -> Path:
        return self.data_dir / "license.p7"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    def ensure_directories(self) -> None:
        """Create every writable directory the application expects."""
        for path in (
            self.config_dir,
            self.data_dir,
            self.machines_dir,
            self.tools_dir,
            self.macros_dir,
            self.profiles_dir,
            self.output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def effective_plugin_dirs(self) -> list[Path]:
        """Plugin search path: explicit dirs first, then the standard ones."""
        dirs = list(self.plugin_dirs)
        for standard in (self.macros_dir, self.machines_dir, self.tools_dir):
            if standard not in dirs:
                dirs.append(standard)
        return dirs


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

_SETTINGS: Settings | None = None

#: Environment variable name -> dotted path within :class:`Settings`.
_ENV_MAP: dict[str, str] = {
    "CONFIG_DIR": "config_dir",
    "DATA_DIR": "data_dir",
    "LOG_LEVEL": "log_level",
    "LOG_FILE": "log_file",
    "LOCALE": "locale",
    "ENABLE_HOT_RELOAD": "enable_hot_reload",
    "WATCH_INTERVAL": "watch_interval_s",
    "KERF": "nesting.kerf_mm",
    "STOCK_LENGTHS": "nesting.stock_lengths_mm",
    "MIN_REMNANT": "nesting.min_reusable_remnant_mm",
    "MESH_SIZE": "analysis.mesh_size_mm2",
    "ENFORCE_HARDWARE_KEY": "security.enforce_hardware_key",
    "RP_ID": "security.relying_party_id",
}


def _coerce(raw: str) -> Any:
    """Interpret an environment string as JSON when possible."""
    lowered = raw.strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0"}:
        return False
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _deep_set(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):  # pragma: no cover - defensive
            raise ConfigError(f"Cannot set {dotted}: {part} is not a section")
    node[parts[-1]] = value


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for suffix, dotted in _ENV_MAP.items():
        raw = os.environ.get(ENV_PREFIX + suffix)
        if raw is not None:
            _deep_set(overrides, dotted, _coerce(raw))
    return overrides


def load_settings(config_dir: str | os.PathLike[str] | None = None, **overrides: Any) -> Settings:
    """Resolve settings, caching the result.

    Parameters
    ----------
    config_dir:
        Override the configuration directory before the settings file is read.
    **overrides:
        Highest-priority values, merged last.
    """
    global _SETTINGS
    if _SETTINGS is not None and not overrides and config_dir is None:
        return _SETTINGS

    base_dir = Path(config_dir) if config_dir else default_config_dir()
    if env_dir := os.environ.get(ENV_PREFIX + "CONFIG_DIR"):
        if config_dir is None:
            base_dir = Path(env_dir)

    data: dict[str, Any] = {"config_dir": str(base_dir)}

    settings_file = base_dir / "settings.json"
    if settings_file.is_file():
        try:
            file_data = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(
                f"Could not read settings file: {exc}", path=str(settings_file)
            ) from exc
        if not isinstance(file_data, dict):
            raise ConfigError("settings.json must contain a JSON object", path=str(settings_file))
        data = _deep_merge(data, file_data)
        _log.debug("Loaded settings from %s", settings_file)

    data = _deep_merge(data, _env_overrides())
    data = _deep_merge(data, overrides)

    try:
        settings = Settings.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    _SETTINGS = settings
    return settings


def get_settings() -> Settings:
    """Return the cached settings, loading them on first use."""
    return _SETTINGS if _SETTINGS is not None else load_settings()


def reload_settings() -> Settings:
    """Discard the cache and re-read configuration from disk."""
    global _SETTINGS
    previous = _SETTINGS
    _SETTINGS = None
    try:
        return load_settings(config_dir=previous.config_dir if previous else None)
    except ConfigError:
        _SETTINGS = previous  # keep the last good configuration
        raise


def save_settings(settings: Settings) -> Path:
    """Persist ``settings`` to ``<config_dir>/settings.json`` and return the path."""
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    payload = settings.model_dump(mode="json", exclude={"config_dir"})
    path = settings.settings_file
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _log.info("Settings saved to %s", path)
    return path


__all__ = [
    "Settings",
    "GeometryDefaults",
    "AnalysisDefaults",
    "NestingDefaults",
    "CncDefaults",
    "SecurityDefaults",
    "PACKAGE_ROOT",
    "PROJECT_ROOT",
    "default_config_dir",
    "default_data_dir",
    "load_settings",
    "get_settings",
    "reload_settings",
    "save_settings",
]
