"""Self-update engine: signed content updates applied without a restart."""

from __future__ import annotations

from .engine import (
    InstalledPackage,
    InstalledState,
    UpdateEngine,
    UpdatePlan,
    UpdateResult,
)
from .manifest import (
    Package,
    PackageKind,
    UpdateChannel,
    UpdateManifest,
    Version,
    build_manifest,
    build_package,
)
from .sources import (
    ChainedSource,
    DirectorySource,
    HttpSource,
    UpdateSource,
    publish_directory,
)

__all__ = [
    "UpdateChannel", "PackageKind", "Version", "Package", "UpdateManifest",
    "build_package", "build_manifest",
    "UpdateSource", "DirectorySource", "HttpSource", "ChainedSource",
    "publish_directory",
    "InstalledPackage", "InstalledState", "UpdatePlan", "UpdateResult",
    "UpdateEngine",
]
