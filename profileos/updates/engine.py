"""The self-update engine.

Applies signed content updates — profile systems, price lists, machine
post-processors, macros, tool libraries — to a running installation, without a
restart and without a window where the install is half-updated.

The sequence, and why each step exists
--------------------------------------
1. **Fetch and verify the manifest.** Signature and freshness. A manifest that
   fails either is discarded before anything is downloaded.
2. **Select.** Channel, application compatibility, and whether the offered
   version is actually newer than what is installed.
3. **Download and verify every package.** Size, SHA-256 and per-package
   signature. Nothing touches the install directory yet.
4. **Validate every package.** Code packages go through the same AST policy
   the plugin loader uses; data packages through their pydantic schema. A
   correctly signed package can still be *broken*, and a broken machine
   post-processor is a crashed shop floor.
5. **Back up, then move into place.** Only after every package has passed does
   anything get written, so a failure in package seven does not leave packages
   one to six applied.
6. **Reload and verify liveness.** The hot-reload loader picks the new content
   up. If loading fails, the whole batch is rolled back automatically.

Step 5 is what makes the update atomic in the way that matters: either the
whole batch applies, or none of it does.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .. import __version__
from ..core.config import Settings, get_settings
from ..core.errors import ProfileOSError, SecurityError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from ..security.keys import VerifyKey
from .manifest import Package, PackageKind, UpdateChannel, UpdateManifest, Version
from .sources import UpdateSource

_log = get_logger("updates.engine")

STATE_FILENAME = "installed.json"
BACKUP_DIRNAME = "backups"
HISTORY_FILENAME = "history.json"


# --------------------------------------------------------------------------- #
# Installed state
# --------------------------------------------------------------------------- #

@dataclass
class InstalledPackage:
    """A package currently installed."""

    package_id: str
    version: str
    kind: str
    filename: str
    sha256: str
    installed_at: str
    #: Relative path of the backup taken when this replaced an earlier version.
    backup: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "version": self.version,
            "kind": self.kind,
            "filename": self.filename,
            "sha256": self.sha256,
            "installed_at": self.installed_at,
            "backup": self.backup,
        }


class InstalledState:
    """The record of what is installed, persisted as JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.packages: dict[str, InstalledPackage] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.error("Installed-state file is unreadable, starting empty: %s", exc)
            return
        for entry in raw.get("packages", []):
            try:
                package = InstalledPackage(**entry)
            except TypeError:
                continue
            self.packages[package.package_id] = package

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "packages": [p.as_dict() for p in self.packages.values()],
        }
        # Write-then-replace: a crash mid-write must not lose the record of
        # what is installed, or the next update has no idea what it is replacing.
        handle, temp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def versions(self) -> dict[str, str]:
        return {pid: p.version for pid, p in self.packages.items()}

    def record(self, package: InstalledPackage) -> None:
        self.packages[package.package_id] = package

    def remove(self, package_id: str) -> InstalledPackage | None:
        return self.packages.pop(package_id, None)


# --------------------------------------------------------------------------- #
# Plans and results
# --------------------------------------------------------------------------- #

@dataclass
class UpdatePlan:
    """What an update would do, before anything is downloaded."""

    manifest: UpdateManifest
    packages: list[Package] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    source_name: str = ""

    @property
    def has_updates(self) -> bool:
        return bool(self.packages)

    @property
    def total_size(self) -> int:
        return sum(package.size for package in self.packages)

    def describe(self) -> str:
        if not self.packages:
            return "Everything is up to date."
        lines = [f"{len(self.packages)} update(s), {self.total_size / 1024:.0f} KB:"]
        for package in self.packages:
            lines.append(
                f"  {package.package_id} {package.version} "
                f"[{package.kind.value}] {package.description}".rstrip()
            )
        return "\n".join(lines)


@dataclass
class UpdateResult:
    """The outcome of applying a plan."""

    applied: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    rolled_back: bool = False
    reloaded: int = 0
    warnings: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed and not self.rolled_back

    def summary(self) -> dict[str, Any]:
        return {
            "applied": len(self.applied),
            "failed": len(self.failed),
            "rolled_back": self.rolled_back,
            "reloaded": self.reloaded,
            "ok": self.ok,
            "duration_s": round(self.duration_s, 3),
        }


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class UpdateEngine:
    """Checks for, verifies and applies content updates."""

    def __init__(
        self,
        source: UpdateSource,
        verify_key: VerifyKey,
        settings: Settings | None = None,
        *,
        channel: UpdateChannel = UpdateChannel.STABLE,
        app_version: str = __version__,
        loader: Any = None,
    ) -> None:
        self.source = source
        self.verify_key = verify_key
        self.settings = settings or get_settings()
        self.channel = channel
        self.app_version = app_version
        self.loader = loader

        self.root = self.settings.data_dir / "updates"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = InstalledState(self.root / STATE_FILENAME)
        self.backup_root = self.root / BACKUP_DIRNAME
        self.history_path = self.root / HISTORY_FILENAME

    # -- checking -------------------------------------------------------------- #
    def check(self) -> UpdatePlan:
        """Fetch the manifest and work out what applies.

        Raises
        ------
        SecurityError
            The manifest is unsigned, forged or stale.
        ProfileOSError
            The source could not be reached or returned nonsense.
        """
        raw = self.source.fetch_manifest()
        manifest = UpdateManifest.from_json(raw)
        manifest.verify(self.verify_key)  # signature and freshness

        installed = self.state.versions()
        selected = manifest.applicable(self.channel, self.app_version, installed)

        skipped: list[tuple[str, str]] = []
        for package in manifest.packages:
            if package in selected:
                continue
            if not self.channel.accepts(package.channel):
                skipped.append((package.package_id, f"channel {package.channel.value}"))
            elif Version.parse(self.app_version) < Version.parse(package.min_app_version):
                skipped.append(
                    (package.package_id, f"needs application {package.min_app_version}")
                )
            else:
                skipped.append((package.package_id, "already current"))

        plan = UpdatePlan(
            manifest=manifest,
            packages=selected,
            skipped=skipped,
            source_name=self.source.name,
        )
        _log.info(
            "Update check via %s: %d applicable, %d skipped",
            self.source.name,
            len(selected),
            len(skipped),
        )
        publish(
            "updates.checked",
            source="updates",
            available=len(selected),
            channel=self.channel.value,
        )
        return plan

    # -- applying ---------------------------------------------------------------- #
    def apply(self, plan: UpdatePlan, *, reload: bool = True) -> UpdateResult:
        """Download, verify, validate and install a plan atomically."""
        import time

        started = time.perf_counter()
        result = UpdateResult()
        if not plan.has_updates:
            return result

        publish("updates.started", source="updates", packages=len(plan.packages))

        # -- stage 1: download and verify every package before touching disk --
        staged: dict[str, tuple[Package, bytes]] = {}
        for package in plan.packages:
            try:
                data = self.source.fetch_package(package.url, package.size)
                package.verify(data, self.verify_key)
                self._validate(package, data)
            except (ProfileOSError, SecurityError) as exc:
                _log.error("Rejected %s: %s", package.package_id, exc)
                result.failed.append((package.package_id, str(exc)))
                continue
            staged[package.package_id] = (package, data)

        if result.failed:
            # Nothing has been written; refusing the whole batch keeps the
            # install in a known state rather than partly new and partly old.
            result.warnings.append(
                f"{len(result.failed)} package(s) failed verification; "
                "no changes were applied."
            )
            publish("updates.failed", source="updates", failures=len(result.failed))
            result.duration_s = time.perf_counter() - started
            return result

        # -- stage 2: back up and install --------------------------------------
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        batch_backup = self.backup_root / timestamp
        installed_now: list[tuple[Package, Path, Path | None]] = []

        try:
            for package_id, (package, data) in staged.items():
                target = self._target_path(package)
                backup = self._backup(target, batch_backup) if target.exists() else None
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                installed_now.append((package, target, backup))

                self.state.record(
                    InstalledPackage(
                        package_id=package.package_id,
                        version=package.version,
                        kind=package.kind.value,
                        filename=str(target),
                        sha256=package.sha256,
                        installed_at=datetime.now(timezone.utc).isoformat(),
                        backup=str(backup) if backup else None,
                    )
                )
                result.applied.append(package.package_id)

            self.state.save()
        except OSError as exc:
            _log.error("Install failed, rolling back: %s", exc)
            self._restore(installed_now)
            result.failed.append(("install", str(exc)))
            result.rolled_back = True
            result.applied.clear()
            result.duration_s = time.perf_counter() - started
            publish("updates.rolled_back", source="updates", reason=str(exc))
            return result

        # -- stage 3: make it live ------------------------------------------------
        if reload and self.loader is not None:
            try:
                for package, target, _backup in installed_now:
                    self.loader.load(target, force=True)
                    result.reloaded += 1
            except Exception as exc:  # noqa: BLE001 - a plugin can fail on load
                _log.error("Reload failed after update, rolling back: %s", exc)
                self._restore(installed_now)
                for package, _target, _backup in installed_now:
                    self.state.remove(package.package_id)
                self.state.save()
                result.failed.append(("reload", str(exc)))
                result.rolled_back = True
                result.applied.clear()
                result.duration_s = time.perf_counter() - started
                publish("updates.rolled_back", source="updates", reason=str(exc))
                return result

        result.duration_s = time.perf_counter() - started
        self._append_history(plan, result)
        _log.info(
            "Applied %d update(s) in %.2f s (%d reloaded)",
            len(result.applied),
            result.duration_s,
            result.reloaded,
        )
        publish(
            "updates.applied",
            source="updates",
            applied=len(result.applied),
            reloaded=result.reloaded,
        )
        return result

    def update(self, *, reload: bool = True) -> UpdateResult:
        """Check and apply in one call."""
        return self.apply(self.check(), reload=reload)

    # -- validation ---------------------------------------------------------------- #
    def _validate(self, package: Package, data: bytes) -> None:
        """Check a package is loadable before it is installed.

        A signature proves *who* published a package, not that it *works*. This
        runs the same gates the plugin loader would, on a temporary copy, so a
        broken package is refused while the working one is still in place.
        """
        from ..core.hotreload import (
            DATA_SCHEMAS,
            load_data_document,
            register_builtin_schemas,
            validate_plugin_source,
        )

        register_builtin_schemas()

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / package.filename
            scratch.write_bytes(data)

            if package.kind.is_code:
                report = validate_plugin_source(scratch)
                if not report.ok:
                    raise SecurityError(
                        "Package failed static validation",
                        package=package.package_id,
                        errors=report.errors[:5],
                    )
                return

            document = load_data_document(scratch)
            kind = str(document.get("kind") or document.get("type") or "")
            schema = DATA_SCHEMAS.get(kind)
            if schema is None:
                raise ProfileOSError(
                    "Package declares an unknown document kind",
                    package=package.package_id,
                    kind=kind,
                    known=DATA_SCHEMAS.kinds(),
                )
            try:
                schema.model(document)
            except Exception as exc:  # noqa: BLE001 - schema rejection
                raise ProfileOSError(
                    f"Package failed schema validation: {exc}", package=package.package_id
                ) from exc

    # -- filesystem ------------------------------------------------------------------ #
    def _target_path(self, package: Package) -> Path:
        directory = {
            "macros": self.settings.macros_dir,
            "machines": self.settings.machines_dir,
            "tools": self.settings.tools_dir,
        }[package.kind.target_directory]
        # filename is validated at the model boundary, so this cannot escape.
        return directory / package.filename

    def _backup(self, target: Path, batch_backup: Path) -> Path:
        batch_backup.mkdir(parents=True, exist_ok=True)
        backup = batch_backup / target.name
        shutil.copy2(target, backup)
        return backup

    def _restore(self, installed: Iterable[tuple[Package, Path, Path | None]]) -> None:
        """Undo an installation: restore backups, delete newly added files."""
        for _package, target, backup in installed:
            try:
                if backup is not None and Path(backup).is_file():
                    shutil.copy2(backup, target)
                elif target.is_file():
                    target.unlink()
            except OSError as exc:  # pragma: no cover - filesystem failure
                _log.error("Could not restore %s: %s", target, exc)

    # -- rollback ---------------------------------------------------------------------- #
    def rollback(self, package_id: str) -> bool:
        """Restore one package to the version it replaced."""
        installed = self.state.packages.get(package_id)
        if installed is None:
            _log.warning("Cannot roll back unknown package %s", package_id)
            return False

        target = Path(installed.filename)
        if installed.backup and Path(installed.backup).is_file():
            shutil.copy2(installed.backup, target)
            _log.info("Rolled %s back to its previous version", package_id)
        elif target.is_file():
            # No backup means the package was newly added, so removing it is
            # the correct inverse.
            target.unlink()
            _log.info("Removed newly added package %s", package_id)
        else:
            return False

        self.state.remove(package_id)
        self.state.save()

        if self.loader is not None:
            try:
                if target.is_file():
                    self.loader.load(target, force=True)
                else:
                    self.loader.unload(target)
            except Exception as exc:  # noqa: BLE001
                _log.error("Reload after rollback failed: %s", exc)

        publish("updates.rolled_back", source="updates", package=package_id)
        return True

    # -- reporting -------------------------------------------------------------------- #
    def _append_history(self, plan: UpdatePlan, result: UpdateResult) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "source": plan.source_name,
            "channel": self.channel.value,
            "applied": result.applied,
            "failed": [{"package": p, "reason": r} for p, r in result.failed],
            "duration_s": round(result.duration_s, 3),
        }
        history: list[dict[str, Any]] = []
        if self.history_path.is_file():
            try:
                history = json.loads(self.history_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                history = []
        history.append(entry)
        # Keep the log bounded; the last 200 updates is plenty to diagnose with.
        history = history[-200:]
        self.history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.history_path.is_file():
            return []
        try:
            entries = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return entries[-limit:]

    def installed(self) -> list[InstalledPackage]:
        return sorted(self.state.packages.values(), key=lambda p: p.package_id)

    def status(self) -> dict[str, Any]:
        return {
            "channel": self.channel.value,
            "app_version": self.app_version,
            "source": self.source.name,
            "source_available": self.source.available(),
            "installed_packages": len(self.state.packages),
            "issuer_key_id": self.verify_key.key_id,
        }


__all__ = [
    "InstalledPackage",
    "InstalledState",
    "UpdatePlan",
    "UpdateResult",
    "UpdateEngine",
]
