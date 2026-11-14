"""Where updates come from.

Two transports, one interface. Both hand back raw bytes and let the engine do
all verification, so an air-gapped plant gets exactly the same guarantees as a
connected one — the trust comes from the signature, never from the channel.

``HttpSource``
    Fetches over HTTPS from an update server.

``DirectorySource``
    Reads from a local directory: a USB stick walked into an air-gapped
    workshop, or a mirror on the plant's own file server.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("updates.sources")

#: Refuse absurdly large downloads rather than filling the disk.
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class UpdateSource(ABC):
    """A place update content can be fetched from."""

    #: Shown in logs and the update dialog.
    name: str = "source"

    @abstractmethod
    def fetch_manifest(self) -> bytes:
        """Return the raw manifest bytes."""

    @abstractmethod
    def fetch_package(self, url: str, expected_size: int) -> bytes:
        """Return the raw bytes of one package."""

    @abstractmethod
    def available(self) -> bool:
        """Whether this source can be reached right now."""


class DirectorySource(UpdateSource):
    """Reads updates from a local or mounted directory."""

    def __init__(self, root: str | Path, manifest_name: str = "manifest.json") -> None:
        self.root = Path(root)
        self.manifest_name = manifest_name
        self.name = f"directory {self.root}"

    def available(self) -> bool:
        return (self.root / self.manifest_name).is_file()

    def fetch_manifest(self) -> bytes:
        path = self.root / self.manifest_name
        if not path.is_file():
            raise ProfileOSError("No manifest in the update directory", path=str(path))
        data = path.read_bytes()
        if len(data) > MAX_MANIFEST_BYTES:
            raise ProfileOSError("Manifest is implausibly large", size=len(data))
        return data

    def fetch_package(self, url: str, expected_size: int) -> bytes:
        # A url from the manifest is a relative filename here. Resolving it and
        # checking containment stops "../.." reaching outside the source root.
        candidate = (self.root / url).resolve()
        root = self.root.resolve()
        if not candidate.is_relative_to(root):
            raise ProfileOSError(
                "Package path escapes the update directory", url=url, root=str(root)
            )
        if not candidate.is_file():
            raise ProfileOSError("Package file is missing from the source", path=str(candidate))

        size = candidate.stat().st_size
        if size > MAX_PACKAGE_BYTES or (expected_size and size != expected_size):
            raise ProfileOSError(
                "Package size does not match the manifest",
                expected=expected_size,
                actual=size,
            )
        return candidate.read_bytes()


class HttpSource(UpdateSource):
    """Fetches updates over HTTPS."""

    def __init__(
        self,
        base_url: str,
        manifest_path: str = "manifest.json",
        *,
        timeout: float = 30.0,
        allow_insecure: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not allow_insecure:
            # Signatures make plain HTTP survivable, but it still leaks which
            # customer is updating what, and invites downgrade games.
            raise ProfileOSError(
                "Update source must use HTTPS (pass allow_insecure for a trusted LAN mirror)",
                url=base_url,
            )
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.manifest_path = manifest_path
        self.timeout = timeout
        self.name = f"server {parsed.netloc}"

    def available(self) -> bool:
        try:
            self.fetch_manifest()
            return True
        except Exception:  # noqa: BLE001 - offline is a normal state
            return False

    def _get(self, url: str, limit: int) -> bytes:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "ProfileOS-Updater"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read(limit + 1)
        except urllib.error.HTTPError as exc:
            raise ProfileOSError(
                f"Update server returned {exc.code}", url=url, status=exc.code
            ) from exc
        except Exception as exc:  # noqa: BLE001 - network is unreliable by nature
            raise ProfileOSError(f"Could not reach the update server: {exc}", url=url) from exc

        if len(data) > limit:
            raise ProfileOSError("Response exceeded the size limit", url=url, limit=limit)
        return data

    def fetch_manifest(self) -> bytes:
        return self._get(urljoin(self.base_url, self.manifest_path), MAX_MANIFEST_BYTES)

    def fetch_package(self, url: str, expected_size: int) -> bytes:
        absolute = url if urlparse(url).scheme else urljoin(self.base_url, url)
        limit = min(MAX_PACKAGE_BYTES, expected_size * 2 if expected_size else MAX_PACKAGE_BYTES)
        return self._get(absolute, limit)


class ChainedSource(UpdateSource):
    """Tries several sources in order, using the first that answers.

    The usual arrangement in a plant: a local mirror first (fast, works when the
    line is down), the vendor's server as fallback.
    """

    def __init__(self, *sources: UpdateSource) -> None:
        if not sources:
            raise ProfileOSError("ChainedSource needs at least one source")
        self.sources = list(sources)
        self.name = " -> ".join(source.name for source in sources)
        self._active: UpdateSource | None = None

    def available(self) -> bool:
        return any(source.available() for source in self.sources)

    def fetch_manifest(self) -> bytes:
        errors: list[str] = []
        for source in self.sources:
            try:
                data = source.fetch_manifest()
            except ProfileOSError as exc:
                errors.append(f"{source.name}: {exc.message}")
                continue
            self._active = source
            _log.info("Using update source %s", source.name)
            return data
        raise ProfileOSError("No update source could be reached", attempts=errors)

    def fetch_package(self, url: str, expected_size: int) -> bytes:
        # Packages must come from the same source the manifest did, or a
        # stale mirror could serve content the fresh manifest does not describe.
        source = self._active or self.sources[0]
        return source.fetch_package(url, expected_size)


def publish_directory(
    packages: dict[str, bytes], manifest: Any, target: str | Path
) -> Path:
    """Write a manifest and its package files into a directory.

    Used by the publishing tool and to prepare a USB stick for an air-gapped
    site. ``packages`` maps filename to bytes.
    """
    root = Path(target)
    root.mkdir(parents=True, exist_ok=True)
    for filename, data in packages.items():
        (root / filename).write_bytes(data)
    (root / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    _log.info("Published %d package(s) to %s", len(packages), root)
    return root


__all__ = [
    "UpdateSource",
    "DirectorySource",
    "HttpSource",
    "ChainedSource",
    "publish_directory",
    "MAX_MANIFEST_BYTES",
    "MAX_PACKAGE_BYTES",
]
