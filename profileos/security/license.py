"""Offline licences, sealed with AES-256-GCM and bound to hardware.

A licence is a signed, encrypted document a plant can carry to an air-gapped
workstation. Two independent protections apply:

**Signature.** The issuer signs the licence body with its private key. Anyone
can verify it with the public key shipped in the application; nobody without
the private key can mint one. This is what stops forgery.

**Seal.** The body is then encrypted with AES-256-GCM under a key derived from
the machine's own fingerprint. This stops a licence file being copied to
another machine and used there — the key simply cannot be derived off the
machine it was issued for.

The order matters: **sign, then encrypt**. Encrypting first would let an
attacker strip the signature and re-seal, because the signature would cover only
ciphertext they could replace wholesale.

AES-GCM is authenticated, so tampering with the sealed bytes fails at decryption
rather than producing plausible garbage.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..core.errors import LicenseError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from .hwid import HardwareFingerprint, current_fingerprint
from .keys import SigningKey, VerifyKey, b64url_decode, b64url_encode

_log = get_logger("security.license")

#: Magic header so a licence file is identifiable and versioned.
LICENSE_MAGIC = b"PROFILEOS-LIC\x01"
#: Context string for key derivation, so this key cannot collide with another use.
HKDF_INFO = b"profileos.license.seal.v1"


@dataclass
class LicenseTerms:
    """What a licence grants."""

    licensee: str
    licence_id: str = field(default_factory=lambda: f"LIC-{os.urandom(4).hex().upper()}")
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_on: date | None = None
    #: Enabled feature keys; an empty set means everything.
    features: set[str] = field(default_factory=set)
    #: Maximum concurrent seats, or None for unlimited.
    seats: int | None = None
    #: Credential ids of the hardware keys that may unlock this licence.
    credential_ids: list[str] = field(default_factory=list)
    #: Support/maintenance expiry, which gates updates rather than the software.
    maintenance_until: date | None = None
    notes: str | None = None

    def allows(self, feature: str) -> bool:
        return not self.features or feature in self.features

    @property
    def is_expired(self) -> bool:
        return self.expires_on is not None and datetime.now(timezone.utc).date() > self.expires_on

    def days_remaining(self) -> int | None:
        if self.expires_on is None:
            return None
        return (self.expires_on - datetime.now(timezone.utc).date()).days

    def to_dict(self) -> dict[str, Any]:
        return {
            "licence_id": self.licence_id,
            "licensee": self.licensee,
            "issued_at": self.issued_at.isoformat(),
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "features": sorted(self.features),
            "seats": self.seats,
            "credential_ids": list(self.credential_ids),
            "maintenance_until": (
                self.maintenance_until.isoformat() if self.maintenance_until else None
            ),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LicenseTerms":
        return cls(
            licence_id=data["licence_id"],
            licensee=data["licensee"],
            issued_at=datetime.fromisoformat(data["issued_at"]),
            expires_on=date.fromisoformat(data["expires_on"]) if data.get("expires_on") else None,
            features=set(data.get("features") or []),
            seats=data.get("seats"),
            credential_ids=list(data.get("credential_ids") or []),
            maintenance_until=(
                date.fromisoformat(data["maintenance_until"])
                if data.get("maintenance_until")
                else None
            ),
            notes=data.get("notes"),
        )


def _derive_seal_key(fingerprint: HardwareFingerprint, salt: bytes) -> bytes:
    """Derive the AES key from the machine fingerprint via HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,  # AES-256
        salt=salt,
        info=HKDF_INFO,
    ).derive(fingerprint.fingerprint.encode("utf-8"))


def issue_license(
    terms: LicenseTerms,
    signing_key: SigningKey,
    fingerprint: HardwareFingerprint | None = None,
) -> bytes:
    """Create a signed, sealed licence file for one machine."""
    fingerprint = fingerprint or current_fingerprint()

    body = {
        "terms": terms.to_dict(),
        "hardware": fingerprint.to_dict(),
        "issuer_key_id": signing_key.key_id,
    }
    # Canonical JSON: sorted keys and fixed separators, so the bytes that get
    # signed are reproducible on any machine and in any Python version.
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = signing_key.sign(payload)

    signed = json.dumps(
        {"payload": b64url_encode(payload), "signature": b64url_encode(signature)},
        separators=(",", ":"),
    ).encode("utf-8")

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_seal_key(fingerprint, salt)
    sealed = AESGCM(key).encrypt(nonce, signed, LICENSE_MAGIC)

    _log.info("Issued licence %s for %s", terms.licence_id, terms.licensee)
    return LICENSE_MAGIC + salt + nonce + sealed


@dataclass
class LicenseStatus:
    """The outcome of validating a licence."""

    valid: bool
    terms: LicenseTerms | None = None
    reason: str | None = None
    hardware_score: float = 0.0
    in_grace_period: bool = False

    @property
    def read_only(self) -> bool:
        """True when the licence has expired but is inside its grace period."""
        return self.valid and self.in_grace_period


def load_license(
    data: bytes,
    verify_key: VerifyKey,
    *,
    fingerprint: HardwareFingerprint | None = None,
    hardware_threshold: float = 0.6,
    grace_days: int = 7,
) -> LicenseStatus:
    """Open, verify and validate a licence.

    Returns a :class:`LicenseStatus` rather than raising for a *rejected*
    licence — refusing to run is a normal outcome that the application must
    explain to the user. Malformed input still raises, because that is a
    different problem.
    """
    fingerprint = fingerprint or current_fingerprint()

    if not data.startswith(LICENSE_MAGIC):
        raise LicenseError("Not a ProfileOS licence file")

    offset = len(LICENSE_MAGIC)
    salt = data[offset : offset + 16]
    nonce = data[offset + 16 : offset + 28]
    sealed = data[offset + 28 :]
    if len(salt) != 16 or len(nonce) != 12 or not sealed:
        raise LicenseError("Licence file is truncated")

    key = _derive_seal_key(fingerprint, salt)
    try:
        signed = AESGCM(key).decrypt(nonce, sealed, LICENSE_MAGIC)
    except Exception:  # noqa: BLE001 - InvalidTag or malformed
        # The seal key comes from this machine's fingerprint, so a failure here
        # means either another machine or a tampered file. Both are refusals,
        # not crashes.
        return LicenseStatus(
            valid=False,
            reason=(
                "Licence could not be unsealed on this machine. It was issued for "
                "different hardware, or the file has been altered."
            ),
        )

    try:
        envelope = json.loads(signed.decode("utf-8"))
        payload = b64url_decode(envelope["payload"])
        signature = b64url_decode(envelope["signature"])
    except Exception as exc:  # noqa: BLE001
        raise LicenseError("Licence contents are malformed") from exc

    if not verify_key.verify(signature, payload):
        publish(Topic.LICENSE_DENIED, source="license", reason="bad signature")
        return LicenseStatus(valid=False, reason="Licence signature is not valid — possible forgery")

    body = json.loads(payload.decode("utf-8"))
    terms = LicenseTerms.from_dict(body["terms"])
    score = fingerprint.match_score(body.get("hardware", {}))

    if score < hardware_threshold:
        publish(Topic.LICENSE_DENIED, source="license", reason="hardware mismatch")
        return LicenseStatus(
            valid=False,
            terms=terms,
            hardware_score=score,
            reason=(
                f"Licence is bound to different hardware "
                f"({score * 100:.0f}% of traits match, {hardware_threshold * 100:.0f}% required)."
            ),
        )

    if terms.is_expired:
        overdue = -(terms.days_remaining() or 0)
        if overdue <= grace_days:
            publish(Topic.LICENSE_GRANTED, source="license", grace=True)
            return LicenseStatus(
                valid=True,
                terms=terms,
                hardware_score=score,
                in_grace_period=True,
                reason=(
                    f"Licence expired {overdue} day(s) ago. Running read-only for the "
                    f"remaining {grace_days - overdue} day(s) of the grace period."
                ),
            )
        publish(Topic.LICENSE_DENIED, source="license", reason="expired")
        return LicenseStatus(
            valid=False,
            terms=terms,
            hardware_score=score,
            reason=f"Licence expired on {terms.expires_on} and the grace period has passed.",
        )

    publish(Topic.LICENSE_GRANTED, source="license", licence=terms.licence_id)
    return LicenseStatus(valid=True, terms=terms, hardware_score=score)


def save_license(data: bytes, path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def load_license_file(
    path: str | os.PathLike[str], verify_key: VerifyKey, **kwargs: Any
) -> LicenseStatus:
    target = Path(path)
    if not target.is_file():
        raise LicenseError("Licence file not found", path=str(target))
    return load_license(target.read_bytes(), verify_key, **kwargs)


__all__ = [
    "LICENSE_MAGIC",
    "LicenseTerms",
    "LicenseStatus",
    "issue_license",
    "load_license",
    "load_license_file",
    "save_license",
]
