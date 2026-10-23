"""A software authenticator, for tests and for development without a dongle.

It produces byte-for-byte correct WebAuthn structures — the same CBOR
attestation object, the same ``authenticatorData`` layout, the same signature
construction — so :class:`~profileos.security.webauthn.WebAuthnServer` cannot
tell it apart from hardware by inspection. That is exactly what makes it useful
for testing the verifier, and exactly why it is **not** a security boundary.

.. warning::
   A software key offers no protection at all: its private key is in ordinary
   process memory and can be copied. It exists so the verification path can be
   tested and so developers can work without a physical key. Production
   licensing must set ``enforce_hardware_key`` and register a real
   authenticator; :meth:`SoftwareAuthenticator.is_hardware` returns ``False``
   so callers can refuse it.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import AuthenticatorError
from ..core.logging_setup import get_logger
from .keys import CoseAlgorithm, SigningKey, b64url_encode, sha256

_log = get_logger("security.softkey")

#: AAGUID identifying this as a software authenticator rather than a device.
#: All-zero is the value real authenticators use when they decline to identify
#: themselves, so it never collides with a genuine vendor AAGUID.
SOFTWARE_AAGUID = b"\x00" * 16


@dataclass
class _Credential:
    credential_id: bytes
    signing_key: SigningKey
    sign_count: int = 0


@dataclass
class SoftwareAuthenticator:
    """A simulated FIDO2 authenticator."""

    algorithm: CoseAlgorithm = CoseAlgorithm.ES256
    #: Whether the simulated user touches the key.
    user_present: bool = True
    #: Whether the simulated user verifies with a PIN or biometric.
    user_verified: bool = False
    #: Some authenticators do not implement a counter and report 0 forever.
    use_sign_counter: bool = True
    credentials: dict[bytes, _Credential] = field(default_factory=dict)

    @property
    def is_hardware(self) -> bool:
        """Always False. A production policy should refuse this."""
        return False

    # -- registration --------------------------------------------------------- #
    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        """Answer a registration request, as ``navigator.credentials.create()`` would."""
        import cbor2

        rp_id = options["rp"]["id"]
        challenge = options["challenge"]
        origin = options.get("_origin") or f"https://{rp_id}"

        signing_key = SigningKey.generate(algorithm=self.algorithm)
        credential_id = os.urandom(32)
        credential = _Credential(credential_id=credential_id, signing_key=signing_key)
        self.credentials[credential_id] = credential

        client_data = self._client_data("webauthn.create", challenge, origin)
        auth_data = self._authenticator_data(
            rp_id, credential.sign_count, credential=credential, include_credential=True
        )
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})

        return {
            "id": b64url_encode(credential_id),
            "rawId": credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": client_data,
                "attestationObject": attestation_object,
            },
        }

    # -- authentication -------------------------------------------------------- #
    def get(self, options: dict[str, Any], credential_id: bytes | None = None) -> dict[str, Any]:
        """Answer an authentication request, as ``navigator.credentials.get()`` would."""
        rp_id = options["rpId"]
        challenge = options["challenge"]
        origin = options.get("_origin") or f"https://{rp_id}"

        if credential_id is None:
            if not self.credentials:
                raise AuthenticatorError("This authenticator holds no credentials")
            credential_id = next(iter(self.credentials))
        credential = self.credentials.get(credential_id)
        if credential is None:
            raise AuthenticatorError("Unknown credential on this authenticator")

        if self.use_sign_counter:
            credential.sign_count += 1

        client_data = self._client_data("webauthn.get", challenge, origin)
        auth_data = self._authenticator_data(rp_id, credential.sign_count)
        signature = credential.signing_key.sign(auth_data + sha256(client_data))

        return {
            "id": b64url_encode(credential_id),
            "rawId": credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": client_data,
                "authenticatorData": auth_data,
                "signature": signature,
                "userHandle": None,
            },
        }

    # -- structure building ------------------------------------------------------ #
    def _client_data(self, ceremony: str, challenge: str, origin: str) -> bytes:
        import json

        # Key order is not significant to the verifier (it re-parses the JSON),
        # but matching the browser's order keeps captured samples comparable.
        return json.dumps(
            {
                "type": ceremony,
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _authenticator_data(
        self,
        rp_id: str,
        sign_count: int,
        *,
        credential: _Credential | None = None,
        include_credential: bool = False,
    ) -> bytes:
        import cbor2

        from .webauthn import AuthenticatorFlags

        flags = AuthenticatorFlags(0)
        if self.user_present:
            flags |= AuthenticatorFlags.USER_PRESENT
        if self.user_verified:
            flags |= AuthenticatorFlags.USER_VERIFIED
        if include_credential:
            flags |= AuthenticatorFlags.ATTESTED_CREDENTIAL_DATA

        data = sha256(rp_id.encode("utf-8")) + bytes([int(flags)]) + struct.pack(">I", sign_count)

        if include_credential and credential is not None:
            cose_key = self._cose_key(credential.signing_key)
            data += (
                SOFTWARE_AAGUID
                + struct.pack(">H", len(credential.credential_id))
                + credential.credential_id
                + cbor2.dumps(cose_key)
            )
        return data

    def _cose_key(self, signing_key: SigningKey) -> dict[int, Any]:
        """Encode the public key as a COSE_Key map."""
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519
        from cryptography.hazmat.primitives import serialization

        public_key = signing_key.key.public_key()

        if signing_key.algorithm is CoseAlgorithm.ES256:
            numbers = public_key.public_numbers()
            return {
                1: 2,  # kty: EC2
                3: int(CoseAlgorithm.ES256),
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }

        raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        return {
            1: 1,  # kty: OKP
            3: int(CoseAlgorithm.EDDSA),
            -1: 6,  # crv: Ed25519
            -2: raw,
        }

    # -- test helpers -------------------------------------------------------------- #
    def clone(self) -> "SoftwareAuthenticator":
        """A copy sharing the same private keys but with counters rewound.

        This is what a cloned dongle looks like to the server, and is how the
        counter-regression check is tested.
        """
        copy = SoftwareAuthenticator(
            algorithm=self.algorithm,
            user_present=self.user_present,
            user_verified=self.user_verified,
            use_sign_counter=self.use_sign_counter,
        )
        copy.credentials = {
            cid: _Credential(credential_id=cid, signing_key=c.signing_key, sign_count=0)
            for cid, c in self.credentials.items()
        }
        return copy


__all__ = ["SoftwareAuthenticator", "SOFTWARE_AAGUID"]
