"""Update manifests and package descriptors.

An update is described by a **manifest**: a signed list of packages, each with
its own hash and signature. Two levels of signing is deliberate:

* The manifest signature proves the *list* is authentic — nobody has added,
  removed or reordered packages.
* Each package signature proves that *file* is authentic — so a package cached
  or mirrored elsewhere is still verifiable on its own.

Everything that gets signed is serialised as canonical JSON (sorted keys, fixed
separators, no whitespace), so the bytes are reproducible on any machine and
any Python version. A signature over non-canonical JSON is a signature over
whichever key order happened to occur, which verifies on the signer's machine
and fails on the customer's.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.errors import ProfileOSError, SecurityError
from ..security.keys import SigningKey, VerifyKey, b64url_decode, b64url_encode


class UpdateChannel(StrEnum):
    """Release channels. A plant runs stable; a pilot site may run beta."""

    STABLE = "stable"
    BETA = "beta"
    CANARY = "canary"

    def accepts(self, other: "UpdateChannel") -> bool:
        """Whether a subscriber on this channel should take ``other``'s content.

        Channels are nested: canary sees everything, beta sees beta and stable,
        stable sees only stable.
        """
        order = {UpdateChannel.STABLE: 0, UpdateChannel.BETA: 1, UpdateChannel.CANARY: 2}
        return order[other] <= order[self]


class PackageKind(StrEnum):
    """What a package contains. Each maps to a hot-reloadable registry."""

    SYSTEM_RULES = "system_rules"
    PRICE_LIST = "price_list"
    PIPE_CATALOGUE = "pipe_catalogue"
    MACRO_LIBRARY = "macro_library"
    POST_PROCESSOR = "post_processor"
    TOOL_LIBRARY = "tool_library"
    MACHINE = "machine"
    PROFILE_LIBRARY = "profile_library"

    @property
    def is_code(self) -> bool:
        """True for packages containing executable Python.

        Code packages go through AST validation before they are ever imported;
        data packages go through schema validation. Knowing which is which up
        front is what lets the engine apply the right gate.
        """
        return self in (PackageKind.MACRO_LIBRARY, PackageKind.POST_PROCESSOR)

    @property
    def target_directory(self) -> str:
        """Which configured directory this kind is installed into."""
        return {
            PackageKind.SYSTEM_RULES: "macros",
            PackageKind.PRICE_LIST: "macros",
            PackageKind.PIPE_CATALOGUE: "macros",
            PackageKind.MACRO_LIBRARY: "macros",
            PackageKind.POST_PROCESSOR: "machines",
            PackageKind.TOOL_LIBRARY: "tools",
            PackageKind.MACHINE: "machines",
            PackageKind.PROFILE_LIBRARY: "macros",
        }[self]

    @property
    def hebrew(self) -> str:
        """What this kind is called on the ״עדכונים״ tab, not its wire value."""
        return {
            PackageKind.SYSTEM_RULES: "כללי מערכת",
            PackageKind.PRICE_LIST: "מחירון",
            PackageKind.PIPE_CATALOGUE: "קטלוג צנרת",
            PackageKind.MACRO_LIBRARY: "ספריית מאקרו",
            PackageKind.POST_PROCESSOR: "מעבד פוסט",
            PackageKind.TOOL_LIBRARY: "ספריית כלים",
            PackageKind.MACHINE: "הגדרת מכונה",
            PackageKind.PROFILE_LIBRARY: "ספריית פרופילים",
        }[self]


class Version(BaseModel):
    """A semantic version, comparable and orderable."""

    model_config = ConfigDict(frozen=True)

    major: int = 0
    minor: int = 0
    patch: int = 0

    @classmethod
    def parse(cls, text: str) -> "Version":
        parts = str(text).strip().split(".")
        if not 1 <= len(parts) <= 3:
            raise ProfileOSError("Version must be MAJOR[.MINOR[.PATCH]]", version=text)
        try:
            numbers = [int(part) for part in parts] + [0] * (3 - len(parts))
        except ValueError as exc:
            raise ProfileOSError("Version components must be integers", version=text) from exc
        return cls(major=numbers[0], minor=numbers[1], patch=numbers[2])

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def _tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def __lt__(self, other: "Version") -> bool:
        return self._tuple() < other._tuple()

    def __le__(self, other: "Version") -> bool:
        return self._tuple() <= other._tuple()

    def __gt__(self, other: "Version") -> bool:
        return self._tuple() > other._tuple()

    def __ge__(self, other: "Version") -> bool:
        return self._tuple() >= other._tuple()


class Package(BaseModel):
    """One downloadable, verifiable unit of content."""

    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(description="Stable id, e.g. 'klil.systems'")
    kind: PackageKind
    version: str = "1.0.0"
    #: Filename to install as. Kept separate from the id so a package can be
    #: renamed without changing its identity.
    filename: str
    #: Where to fetch it, relative to the source root or absolute.
    url: str
    size: int = Field(ge=0)
    #: Hex SHA-256 of the package bytes.
    sha256: str
    #: base64url signature over the package bytes.
    signature: str = ""

    channel: UpdateChannel = UpdateChannel.STABLE
    #: Minimum application version required; older installs skip this package.
    min_app_version: str = "0.0.0"
    #: Human-readable summary shown in the update dialog.
    description: str = ""
    released_on: date | None = None
    #: Package ids this one supersedes, so old files are removed on apply.
    replaces: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256")
    @classmethod
    def _check_digest(cls, v: str) -> str:
        digest = v.strip().lower()
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("sha256 must be 64 hex characters")
        return digest

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, v: str) -> str:
        """Reject anything that could escape the install directory.

        A package named ``../../etc/cron.d/x`` would otherwise be written
        outside the plugin directory entirely. Path traversal in an updater is
        remote code execution, so this is checked at the model boundary rather
        than trusted from the manifest.
        """
        name = v.strip()
        if not name:
            raise ValueError("filename must not be empty")
        if "/" in name or "\\" in name or name in (".", "..") or name.startswith("."):
            raise ValueError(f"unsafe package filename: {v!r}")
        if not name.endswith((".json", ".xml", ".py")):
            raise ValueError(f"unsupported package type: {v!r}")
        return name

    @property
    def semver(self) -> Version:
        return Version.parse(self.version)

    def matches_digest(self, data: bytes) -> bool:
        return hashlib.sha256(data).hexdigest() == self.sha256

    def verify(self, data: bytes, verify_key: VerifyKey) -> None:
        """Check size, digest and signature of downloaded bytes.

        Raises
        ------
        SecurityError
            Any check failed. The package is not written to disk.
        """
        if len(data) != self.size:
            raise SecurityError(
                "Package size does not match the manifest",
                package=self.package_id,
                expected=self.size,
                received=len(data),
            )
        if not self.matches_digest(data):
            raise SecurityError(
                "Package digest does not match the manifest — the file is corrupt or altered",
                package=self.package_id,
                expected=self.sha256,
                received=hashlib.sha256(data).hexdigest(),
            )
        if not self.signature:
            raise SecurityError("Package is unsigned", package=self.package_id)
        if not verify_key.verify(b64url_decode(self.signature), data):
            raise SecurityError(
                "Package signature is not valid — it was not published by the update issuer",
                package=self.package_id,
            )

    def signing_body(self) -> dict[str, Any]:
        """The fields covered by the manifest signature."""
        return {
            "package_id": self.package_id,
            "kind": self.kind.value,
            "version": self.version,
            "filename": self.filename,
            "url": self.url,
            "size": self.size,
            "sha256": self.sha256,
            "signature": self.signature,
            "channel": self.channel.value,
            "min_app_version": self.min_app_version,
        }


class UpdateManifest(BaseModel):
    """A signed catalogue of available packages."""

    model_config = ConfigDict(extra="forbid")

    manifest_version: int = 1
    issuer: str = "ProfileOS"
    #: Key id of the signing key, so a client can select the right public key
    #: during a key rotation.
    issuer_key_id: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    #: Clients should refuse a manifest older than this many days, to stop an
    #: attacker pinning them to a stale catalogue that omits a security fix.
    max_age_days: int = 30
    packages: list[Package] = Field(default_factory=list)
    #: base64url signature over :meth:`canonical_bytes`.
    signature: str = ""
    notes: str | None = None

    # -- signing ------------------------------------------------------------- #
    def canonical_bytes(self) -> bytes:
        """The exact bytes covered by the manifest signature."""
        body = {
            "manifest_version": self.manifest_version,
            "issuer": self.issuer,
            "issuer_key_id": self.issuer_key_id,
            "generated_at": self.generated_at.isoformat(),
            "max_age_days": self.max_age_days,
            "packages": [package.signing_body() for package in self.packages],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sign(self, signing_key: SigningKey) -> "UpdateManifest":
        """Return a copy signed by ``signing_key``."""
        signed = self.model_copy(update={"issuer_key_id": signing_key.key_id, "signature": ""})
        signature = signing_key.sign(signed.canonical_bytes())
        return signed.model_copy(update={"signature": b64url_encode(signature)})

    def verify(self, verify_key: VerifyKey) -> None:
        """Check the manifest signature and freshness.

        Raises
        ------
        SecurityError
            The signature is missing or invalid, or the manifest is stale.
        """
        if not self.signature:
            raise SecurityError("Update manifest is unsigned")

        unsigned = self.model_copy(update={"signature": ""})
        if not verify_key.verify(b64url_decode(self.signature), unsigned.canonical_bytes()):
            raise SecurityError(
                "Update manifest signature is not valid — it was not published by the issuer",
                issuer=self.issuer,
                key_id=self.issuer_key_id,
            )

        age_days = (datetime.now(timezone.utc) - self.generated_at).days
        if self.max_age_days and age_days > self.max_age_days:
            raise SecurityError(
                "Update manifest is stale — a mirror may be withholding newer content",
                age_days=age_days,
                max_age_days=self.max_age_days,
            )

    # -- selection ------------------------------------------------------------ #
    def for_channel(self, channel: UpdateChannel) -> list[Package]:
        return [p for p in self.packages if channel.accepts(p.channel)]

    def applicable(
        self, channel: UpdateChannel, app_version: str, installed: dict[str, str] | None = None
    ) -> list[Package]:
        """Packages this installation should take.

        Filters on channel, application compatibility, and whether the version
        offered is actually newer than what is installed.
        """
        installed = installed or {}
        current = Version.parse(app_version)
        selected: list[Package] = []

        for package in self.for_channel(channel):
            if current < Version.parse(package.min_app_version):
                continue
            existing = installed.get(package.package_id)
            if existing is not None and Version.parse(existing) >= package.semver:
                continue
            selected.append(package)

        return sorted(selected, key=lambda p: (p.kind.value, p.package_id))

    def find(self, package_id: str) -> Package | None:
        return next((p for p in self.packages if p.package_id == package_id), None)

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str | bytes) -> "UpdateManifest":
        try:
            return cls.model_validate_json(data)
        except Exception as exc:  # noqa: BLE001 - remote input
            raise ProfileOSError(f"Update manifest is malformed: {exc}") from exc


def build_package(
    package_id: str,
    kind: PackageKind,
    data: bytes,
    filename: str,
    signing_key: SigningKey,
    *,
    version: str = "1.0.0",
    url: str | None = None,
    channel: UpdateChannel = UpdateChannel.STABLE,
    description: str = "",
    min_app_version: str = "0.0.0",
) -> Package:
    """Describe and sign one package's bytes. Used by the publishing tool."""
    return Package(
        package_id=package_id,
        kind=kind,
        version=version,
        filename=filename,
        url=url or filename,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        signature=b64url_encode(signing_key.sign(data)),
        channel=channel,
        description=description,
        min_app_version=min_app_version,
        released_on=datetime.now(timezone.utc).date(),
    )


def build_manifest(
    packages: Iterable[Package], signing_key: SigningKey, *, issuer: str = "ProfileOS", **kwargs: Any
) -> UpdateManifest:
    """Assemble and sign a manifest."""
    manifest = UpdateManifest(issuer=issuer, packages=list(packages), **kwargs)
    return manifest.sign(signing_key)


__all__ = [
    "UpdateChannel",
    "PackageKind",
    "Version",
    "Package",
    "UpdateManifest",
    "build_package",
    "build_manifest",
]
