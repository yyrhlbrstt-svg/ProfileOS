"""WebAuthn / FIDO2 (CTAP2) registration and assertion verification.

This is the server half of WebAuthn: it issues challenges and verifies what the
authenticator sends back. The authenticator half runs on the hardware key
itself and is not something software can (or should) emulate.

The two ceremonies
------------------
**Registration** binds a hardware key to an installation. The authenticator
returns an ``attestationObject`` (CBOR) containing ``authenticatorData`` with
the newly created credential's public key. The server records the credential id
and that public key.

**Authentication** proves the same key is physically present. The authenticator
signs ``authenticatorData || SHA-256(clientDataJSON)`` with the private key it
never reveals. The server verifies with the recorded public key.

What is actually checked
------------------------
Skipping any of these turns WebAuthn into decoration, so all of them are
enforced and each has its own error:

1. ``clientDataJSON.type`` matches the ceremony (blocks cross-ceremony replay).
2. The challenge matches one this server issued and has not expired or been
   used (blocks replay of a captured response).
3. The origin is one this relying party accepts (blocks a phishing site
   relaying a response).
4. ``rpIdHash`` equals SHA-256 of the relying party id (binds the credential to
   this application).
5. The User Present flag is set — someone physically touched the key.
6. User Verified, when the policy requires it (PIN or biometric).
7. The signature verifies against the registered public key.
8. The signature counter has increased, when the authenticator uses one. A
   counter that goes backwards means the credential has been cloned.

Binary layout of ``authenticatorData``::

    rpIdHash (32) | flags (1) | signCount (4, big-endian)
                  | [aaguid (16) | credIdLen (2) | credId | COSE key]   if AT
                  | [extensions CBOR]                                   if ED
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntFlag
from typing import Any, Iterable

from ..core.errors import AuthenticatorError, SecurityError
from ..core.events import Topic, publish
from ..core.logging_setup import get_logger
from .keys import (
    CoseAlgorithm,
    VerifyKey,
    b64url_decode,
    b64url_encode,
    constant_time_equal,
    random_challenge,
    sha256,
)

_log = get_logger("security.webauthn")


class AuthenticatorFlags(IntFlag):
    """Flag bits in ``authenticatorData``."""

    USER_PRESENT = 0x01
    USER_VERIFIED = 0x04
    BACKUP_ELIGIBLE = 0x08
    BACKUP_STATE = 0x10
    ATTESTED_CREDENTIAL_DATA = 0x40
    EXTENSION_DATA = 0x80


@dataclass
class AuthenticatorData:
    """Parsed ``authenticatorData``."""

    rp_id_hash: bytes
    flags: AuthenticatorFlags
    sign_count: int
    aaguid: bytes | None = None
    credential_id: bytes | None = None
    credential_public_key: dict[int, Any] | None = None
    extensions: dict[str, Any] | None = None
    raw: bytes = b""

    @property
    def user_present(self) -> bool:
        return bool(self.flags & AuthenticatorFlags.USER_PRESENT)

    @property
    def user_verified(self) -> bool:
        return bool(self.flags & AuthenticatorFlags.USER_VERIFIED)

    @property
    def has_credential_data(self) -> bool:
        return bool(self.flags & AuthenticatorFlags.ATTESTED_CREDENTIAL_DATA)


def parse_authenticator_data(data: bytes) -> AuthenticatorData:
    """Parse the binary ``authenticatorData`` structure.

    Raises
    ------
    AuthenticatorError
        The buffer is truncated or internally inconsistent.
    """
    if len(data) < 37:
        raise AuthenticatorError(
            "authenticatorData is shorter than its 37-byte fixed header", length=len(data)
        )

    rp_id_hash = data[:32]
    flags = AuthenticatorFlags(data[32])
    (sign_count,) = struct.unpack(">I", data[33:37])
    offset = 37

    aaguid: bytes | None = None
    credential_id: bytes | None = None
    credential_public_key: dict[int, Any] | None = None

    if flags & AuthenticatorFlags.ATTESTED_CREDENTIAL_DATA:
        if len(data) < offset + 18:
            raise AuthenticatorError("Attested credential data is truncated")
        aaguid = data[offset : offset + 16]
        (credential_id_length,) = struct.unpack(">H", data[offset + 16 : offset + 18])
        offset += 18

        if credential_id_length == 0 or credential_id_length > 1023:
            # The spec caps credential ids at 1023 bytes; anything else means we
            # have lost sync with the buffer rather than found a huge id.
            raise AuthenticatorError(
                "Implausible credential id length", length=credential_id_length
            )
        if len(data) < offset + credential_id_length:
            raise AuthenticatorError("Credential id is truncated")

        credential_id = data[offset : offset + credential_id_length]
        offset += credential_id_length

        credential_public_key, offset = _decode_cbor_prefix(data, offset)

    if flags & AuthenticatorFlags.EXTENSION_DATA:
        extensions, offset = _decode_cbor_prefix(data, offset)
    else:
        extensions = None

    return AuthenticatorData(
        rp_id_hash=rp_id_hash,
        flags=flags,
        sign_count=sign_count,
        aaguid=aaguid,
        credential_id=credential_id,
        credential_public_key=credential_public_key,
        extensions=extensions,
        raw=data,
    )


def _decode_cbor_prefix(data: bytes, offset: int) -> tuple[Any, int]:
    """Decode one CBOR item starting at ``offset``; return it and the new offset.

    ``authenticatorData`` concatenates CBOR items with no length prefix, so the
    decoder has to report how much it consumed.
    """
    try:
        import cbor2
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise SecurityError("WebAuthn parsing needs cbor2 (pip install cbor2)") from exc

    import io

    stream = io.BytesIO(data[offset:])
    try:
        value = cbor2.load(stream)
    except Exception as exc:  # noqa: BLE001 - malformed authenticator output
        raise AuthenticatorError(f"Could not decode CBOR at offset {offset}: {exc}") from exc
    return value, offset + stream.tell()


# --------------------------------------------------------------------------- #
# Relying party policy
# --------------------------------------------------------------------------- #

@dataclass
class RelyingParty:
    """Identity and policy of the application performing the ceremonies."""

    rp_id: str = "cad.system.local"
    name: str = "ProfileOS"
    #: Origins accepted in ``clientDataJSON``. A local application typically
    #: allows its own scheme; a hosted one allows its https origin.
    allowed_origins: tuple[str, ...] = ("https://cad.system.local",)
    require_user_presence: bool = True
    require_user_verification: bool = False
    #: How long an issued challenge stays valid.
    challenge_timeout_s: float = 120.0
    challenge_bytes: int = 32
    #: Algorithms offered at registration, most preferred first.
    algorithms: tuple[CoseAlgorithm, ...] = (CoseAlgorithm.ES256, CoseAlgorithm.EDDSA)

    @property
    def rp_id_hash(self) -> bytes:
        return sha256(self.rp_id.encode("utf-8"))

    def accepts_origin(self, origin: str) -> bool:
        return origin in self.allowed_origins


@dataclass
class RegisteredCredential:
    """A hardware key bound to this installation."""

    credential_id: bytes
    public_key: VerifyKey
    sign_count: int = 0
    aaguid: bytes | None = None
    user_handle: str | None = None
    label: str | None = None
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    revoked: bool = False
    #: True when the authenticator reported the credential is backed up
    #: (a passkey synced to a cloud), which is weaker evidence of physical
    #: presence than a device-bound key.
    backed_up: bool = False

    @property
    def credential_id_b64(self) -> str:
        return b64url_encode(self.credential_id)

    @property
    def is_usable(self) -> bool:
        return not self.revoked

    def describe(self) -> str:
        return (
            f"{self.label or 'security key'} "
            f"[{self.credential_id_b64[:12]}...] "
            f"{self.public_key.algorithm.label}"
        )


class ChallengeStore:
    """Issued challenges, held until used or expired.

    Single-use is the point: a challenge that can be answered twice lets a
    captured response be replayed.
    """

    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._issued: dict[bytes, float] = {}

    def issue(self, length: int = 32) -> bytes:
        self.purge()
        challenge = random_challenge(length)
        self._issued[challenge] = time.monotonic()
        return challenge

    def consume(self, challenge: bytes) -> bool:
        """Accept a challenge exactly once, if it is known and unexpired."""
        self.purge()
        issued_at = self._issued.pop(challenge, None)
        if issued_at is None:
            return False
        return (time.monotonic() - issued_at) <= self.timeout_s

    def purge(self) -> int:
        now = time.monotonic()
        expired = [c for c, t in self._issued.items() if now - t > self.timeout_s]
        for challenge in expired:
            del self._issued[challenge]
        return len(expired)

    def __len__(self) -> int:
        return len(self._issued)


# --------------------------------------------------------------------------- #
# Ceremonies
# --------------------------------------------------------------------------- #

class WebAuthnServer:
    """Issues challenges and verifies authenticator responses."""

    def __init__(self, relying_party: RelyingParty | None = None) -> None:
        self.rp = relying_party or RelyingParty()
        self.challenges = ChallengeStore(self.rp.challenge_timeout_s)
        self.credentials: dict[bytes, RegisteredCredential] = {}

    # -- option generation --------------------------------------------------- #
    def registration_options(
        self, user_id: str, user_name: str, display_name: str | None = None
    ) -> dict[str, Any]:
        """Options to pass to ``navigator.credentials.create()``."""
        challenge = self.challenges.issue(self.rp.challenge_bytes)
        return {
            "challenge": b64url_encode(challenge),
            "rp": {"id": self.rp.rp_id, "name": self.rp.name},
            "user": {
                "id": b64url_encode(user_id.encode("utf-8")),
                "name": user_name,
                "displayName": display_name or user_name,
            },
            "pubKeyCredParams": [
                {"type": "public-key", "alg": int(algorithm)}
                for algorithm in self.rp.algorithms
            ],
            "timeout": int(self.rp.challenge_timeout_s * 1000),
            "attestation": "none",
            "authenticatorSelection": {
                "userVerification": "required" if self.rp.require_user_verification else "preferred",
                # A removable security key is the point: the licence travels
                # with the dongle, not with the workstation.
                "authenticatorAttachment": "cross-platform",
                "residentKey": "discouraged",
            },
            "excludeCredentials": [
                {"type": "public-key", "id": credential.credential_id_b64}
                for credential in self.credentials.values()
                if credential.is_usable
            ],
        }

    def authentication_options(self) -> dict[str, Any]:
        """Options to pass to ``navigator.credentials.get()``."""
        challenge = self.challenges.issue(self.rp.challenge_bytes)
        return {
            "challenge": b64url_encode(challenge),
            "rpId": self.rp.rp_id,
            "timeout": int(self.rp.challenge_timeout_s * 1000),
            "userVerification": "required" if self.rp.require_user_verification else "preferred",
            "allowCredentials": [
                {"type": "public-key", "id": credential.credential_id_b64}
                for credential in self.credentials.values()
                if credential.is_usable
            ],
        }

    # -- shared checks -------------------------------------------------------- #
    def _check_client_data(self, client_data_json: bytes, expected_type: str) -> dict[str, Any]:
        try:
            client_data = json.loads(client_data_json.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AuthenticatorError("clientDataJSON is not valid JSON") from exc

        if client_data.get("type") != expected_type:
            raise AuthenticatorError(
                "clientDataJSON is for the wrong ceremony",
                expected=expected_type,
                received=client_data.get("type"),
            )

        challenge = b64url_decode(client_data.get("challenge", ""))
        if not self.challenges.consume(challenge):
            raise AuthenticatorError(
                "Challenge is unknown, already used, or expired — possible replay"
            )

        origin = client_data.get("origin", "")
        if not self.rp.accepts_origin(origin):
            raise AuthenticatorError(
                "Origin is not accepted by this relying party",
                origin=origin,
                allowed=list(self.rp.allowed_origins),
            )
        return client_data

    def _check_authenticator_data(self, auth_data: AuthenticatorData) -> None:
        if not constant_time_equal(auth_data.rp_id_hash, self.rp.rp_id_hash):
            raise AuthenticatorError(
                "rpIdHash does not match this relying party — credential is for another application"
            )
        if self.rp.require_user_presence and not auth_data.user_present:
            raise AuthenticatorError(
                "User Present flag is not set — nobody touched the security key"
            )
        if self.rp.require_user_verification and not auth_data.user_verified:
            raise AuthenticatorError(
                "User Verified flag is not set but the policy requires a PIN or biometric"
            )

    # -- registration ---------------------------------------------------------- #
    def verify_registration(
        self,
        attestation_object: bytes,
        client_data_json: bytes,
        *,
        label: str | None = None,
        user_handle: str | None = None,
    ) -> RegisteredCredential:
        """Verify a registration response and record the credential.

        Attestation statements are not verified: this application cares that a
        hardware key is present and consistent, not which manufacturer made it.
        Requesting ``attestation: "none"`` and skipping the statement is the
        documented behaviour for that stance, rather than an omission.
        """
        try:
            import cbor2
        except ImportError as exc:  # pragma: no cover
            raise SecurityError("WebAuthn needs cbor2 (pip install cbor2)") from exc

        self._check_client_data(client_data_json, "webauthn.create")

        try:
            attestation = cbor2.loads(attestation_object)
        except Exception as exc:  # noqa: BLE001
            raise AuthenticatorError("attestationObject is not valid CBOR") from exc
        if not isinstance(attestation, dict) or "authData" not in attestation:
            raise AuthenticatorError("attestationObject has no authData")

        auth_data = parse_authenticator_data(attestation["authData"])
        self._check_authenticator_data(auth_data)

        if not auth_data.has_credential_data or auth_data.credential_public_key is None:
            raise AuthenticatorError("Registration response carries no credential public key")

        public_key = VerifyKey.from_cose(auth_data.credential_public_key)
        if public_key.algorithm not in self.rp.algorithms:
            raise AuthenticatorError(
                "Authenticator used an algorithm this relying party does not accept",
                algorithm=public_key.algorithm.label,
            )

        assert auth_data.credential_id is not None
        if auth_data.credential_id in self.credentials:
            raise AuthenticatorError("This credential is already registered")

        credential = RegisteredCredential(
            credential_id=auth_data.credential_id,
            public_key=public_key,
            sign_count=auth_data.sign_count,
            aaguid=auth_data.aaguid,
            user_handle=user_handle,
            label=label,
            backed_up=bool(auth_data.flags & AuthenticatorFlags.BACKUP_STATE),
        )
        self.credentials[credential.credential_id] = credential

        _log.info("Registered %s", credential.describe())
        publish(
            Topic.LICENSE_GRANTED,
            source="webauthn",
            event="registered",
            credential=credential.credential_id_b64,
        )
        return credential

    # -- authentication --------------------------------------------------------- #
    def verify_assertion(
        self,
        credential_id: bytes,
        authenticator_data: bytes,
        client_data_json: bytes,
        signature: bytes,
    ) -> RegisteredCredential:
        """Verify an authentication assertion.

        Raises
        ------
        AuthenticatorError
            Any check failed. The message names which one, because "login
            failed" is useless when diagnosing a key that has been cloned
            versus one that was simply not touched.
        """
        credential = self.credentials.get(credential_id)
        if credential is None:
            raise AuthenticatorError(
                "Unknown credential", credential=b64url_encode(credential_id)[:16]
            )
        if credential.revoked:
            raise AuthenticatorError("This credential has been revoked")

        self._check_client_data(client_data_json, "webauthn.get")
        auth_data = parse_authenticator_data(authenticator_data)
        self._check_authenticator_data(auth_data)

        # The signed message is the raw authenticatorData concatenated with the
        # hash of clientDataJSON — not the JSON itself.
        signed_message = authenticator_data + sha256(client_data_json)
        if not credential.public_key.verify(signature, signed_message):
            publish(
                Topic.LICENSE_DENIED,
                source="webauthn",
                reason="bad signature",
                credential=credential.credential_id_b64,
            )
            raise AuthenticatorError("Signature does not verify against the registered key")

        # A counter that fails to advance means two authenticators share one
        # private key, which is exactly what a cloned dongle looks like.
        # Authenticators that do not implement a counter report 0 forever, and
        # that is legitimate.
        if auth_data.sign_count != 0 or credential.sign_count != 0:
            if auth_data.sign_count <= credential.sign_count:
                publish(
                    Topic.LICENSE_DENIED,
                    source="webauthn",
                    reason="counter regression",
                    credential=credential.credential_id_b64,
                )
                raise AuthenticatorError(
                    "Signature counter did not advance — the security key may have been cloned",
                    received=auth_data.sign_count,
                    stored=credential.sign_count,
                )
            credential.sign_count = auth_data.sign_count

        credential.last_used_at = datetime.now(timezone.utc)
        _log.info("Authenticated %s", credential.describe())
        publish(
            Topic.LICENSE_GRANTED,
            source="webauthn",
            event="authenticated",
            credential=credential.credential_id_b64,
        )
        return credential

    # -- management ------------------------------------------------------------- #
    def revoke(self, credential_id: bytes) -> bool:
        credential = self.credentials.get(credential_id)
        if credential is None:
            return False
        credential.revoked = True
        _log.warning("Revoked %s", credential.describe())
        return True

    def active_credentials(self) -> list[RegisteredCredential]:
        return [c for c in self.credentials.values() if c.is_usable]

    def export_credentials(self) -> list[dict[str, Any]]:
        """Serialise registered credentials for persistence."""
        return [
            {
                "credential_id": c.credential_id_b64,
                "public_key_pem": c.public_key.to_pem().decode("ascii"),
                "sign_count": c.sign_count,
                "aaguid": c.aaguid.hex() if c.aaguid else None,
                "label": c.label,
                "user_handle": c.user_handle,
                "registered_at": c.registered_at.isoformat(),
                "revoked": c.revoked,
                "backed_up": c.backed_up,
            }
            for c in self.credentials.values()
        ]

    def import_credentials(self, entries: Iterable[dict[str, Any]]) -> int:
        """Restore credentials produced by :meth:`export_credentials`."""
        count = 0
        for entry in entries:
            try:
                credential_id = b64url_decode(entry["credential_id"])
                credential = RegisteredCredential(
                    credential_id=credential_id,
                    public_key=VerifyKey.from_pem(entry["public_key_pem"].encode("ascii")),
                    sign_count=int(entry.get("sign_count", 0)),
                    aaguid=bytes.fromhex(entry["aaguid"]) if entry.get("aaguid") else None,
                    label=entry.get("label"),
                    user_handle=entry.get("user_handle"),
                    revoked=bool(entry.get("revoked", False)),
                    backed_up=bool(entry.get("backed_up", False)),
                )
            except Exception as exc:  # noqa: BLE001 - skip a bad row, keep the rest
                _log.warning("Skipping malformed credential record: %s", exc)
                continue
            self.credentials[credential_id] = credential
            count += 1
        return count


__all__ = [
    "AuthenticatorFlags",
    "AuthenticatorData",
    "parse_authenticator_data",
    "RelyingParty",
    "RegisteredCredential",
    "ChallengeStore",
    "WebAuthnServer",
]
