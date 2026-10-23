"""Key material: generation, encoding and storage.

Two algorithm families are supported, because they are the two FIDO2
authenticators actually emit and the two the update signer needs:

``ES256`` (COSE -7)
    ECDSA over NIST P-256 with SHA-256. What almost every hardware security key
    produces.

``EdDSA`` (COSE -8)
    Ed25519. Faster, smaller signatures, no parameter choices to get wrong —
    the default for signing update packages.

Public keys are exchanged in COSE_Key form when they come from an
authenticator, and in PEM when they are configuration. Both are handled here so
nothing else has to think about encoding.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from ..core.errors import SecurityError


class CoseAlgorithm(IntEnum):
    """COSE algorithm identifiers used by WebAuthn."""

    ES256 = -7
    EDDSA = -8
    RS256 = -257

    @property
    def label(self) -> str:
        return {-7: "ES256 (ECDSA P-256)", -8: "EdDSA (Ed25519)", -257: "RS256 (RSA)"}[
            self.value
        ]


class CoseKeyType(IntEnum):
    OKP = 1  # Octet Key Pair — Ed25519
    EC2 = 2  # Two-coordinate elliptic curve — P-256


class KeyPurpose(StrEnum):
    """What a key is allowed to be used for.

    Separating purposes means a compromised update-signing key cannot be
    replayed as a license key, and vice versa.
    """

    UPDATE_SIGNING = "update_signing"
    LICENSE_SIGNING = "license_signing"
    AUTHENTICATOR = "authenticator"


# --------------------------------------------------------------------------- #
# base64url — WebAuthn uses it everywhere, unpadded
# --------------------------------------------------------------------------- #

def b64url_encode(data: bytes) -> str:
    """Encode to unpadded base64url, as WebAuthn transmits it."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str | bytes) -> bytes:
    """Decode unpadded (or padded) base64url.

    Browsers strip the padding; some libraries do not. Restoring it before
    decoding accepts both rather than failing on a technicality.
    """
    if isinstance(value, bytes):
        value = value.decode("ascii")
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # noqa: BLE001 - malformed input from a client
        raise SecurityError("Value is not valid base64url", value=value[:32]) from exc


# --------------------------------------------------------------------------- #
# Key pairs
# --------------------------------------------------------------------------- #

@dataclass
class SigningKey:
    """A private signing key with its purpose."""

    key: Any
    algorithm: CoseAlgorithm
    purpose: KeyPurpose
    key_id: str

    @classmethod
    def generate(
        cls,
        purpose: KeyPurpose = KeyPurpose.UPDATE_SIGNING,
        algorithm: CoseAlgorithm = CoseAlgorithm.EDDSA,
    ) -> "SigningKey":
        if algorithm is CoseAlgorithm.EDDSA:
            key: Any = ed25519.Ed25519PrivateKey.generate()
        elif algorithm is CoseAlgorithm.ES256:
            key = ec.generate_private_key(ec.SECP256R1())
        else:
            raise SecurityError("Unsupported signing algorithm", algorithm=algorithm.label)

        instance = cls(key=key, algorithm=algorithm, purpose=purpose, key_id="")
        instance.key_id = instance.public_key().key_id
        return instance

    def sign(self, message: bytes) -> bytes:
        """Sign ``message``. ECDSA signatures are DER-encoded, as WebAuthn expects."""
        if self.algorithm is CoseAlgorithm.EDDSA:
            return self.key.sign(message)
        return self.key.sign(message, ec.ECDSA(hashes.SHA256()))

    def public_key(self) -> "VerifyKey":
        return VerifyKey(key=self.key.public_key(), algorithm=self.algorithm)

    def to_pem(self, password: bytes | None = None) -> bytes:
        encryption = (
            serialization.BestAvailableEncryption(password)
            if password
            else serialization.NoEncryption()
        )
        return self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=encryption,
        )

    @classmethod
    def from_pem(
        cls,
        data: bytes,
        purpose: KeyPurpose = KeyPurpose.UPDATE_SIGNING,
        password: bytes | None = None,
    ) -> "SigningKey":
        try:
            key = serialization.load_pem_private_key(data, password=password)
        except Exception as exc:  # noqa: BLE001
            raise SecurityError("Could not load the private key") from exc

        if isinstance(key, ed25519.Ed25519PrivateKey):
            algorithm = CoseAlgorithm.EDDSA
        elif isinstance(key, ec.EllipticCurvePrivateKey):
            algorithm = CoseAlgorithm.ES256
        else:
            raise SecurityError("Unsupported private key type", type=type(key).__name__)

        instance = cls(key=key, algorithm=algorithm, purpose=purpose, key_id="")
        instance.key_id = instance.public_key().key_id
        return instance

    def save(self, path: str | os.PathLike[str], password: bytes | None = None) -> Path:
        """Write the key with owner-only permissions."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.to_pem(password))
        # A signing key readable by other users on the machine is not a secret.
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        return target


@dataclass
class VerifyKey:
    """A public key that can verify signatures."""

    key: Any
    algorithm: CoseAlgorithm

    @property
    def key_id(self) -> str:
        """A short, stable identifier: the first 16 hex of SHA-256 over the DER."""
        digest = hashes.Hash(hashes.SHA256())
        digest.update(self.to_der())
        return digest.finalize().hex()[:16]

    def verify(self, signature: bytes, message: bytes) -> bool:
        """Return True when ``signature`` is valid for ``message``.

        Returns a bool rather than raising, because a failed verification is an
        expected outcome — an attacker's forgery, a stale key — not an error in
        the program.
        """
        try:
            if self.algorithm is CoseAlgorithm.EDDSA:
                self.key.verify(signature, message)
            elif self.algorithm is CoseAlgorithm.ES256:
                self.key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            else:
                raise SecurityError(
                    "Unsupported verification algorithm", algorithm=self.algorithm.label
                )
        except InvalidSignature:
            return False
        except Exception as exc:  # noqa: BLE001 - malformed signature bytes
            raise SecurityError(f"Signature could not be checked: {exc}") from exc
        return True

    def to_der(self) -> bytes:
        return self.key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def to_pem(self) -> bytes:
        return self.key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @classmethod
    def from_pem(cls, data: bytes) -> "VerifyKey":
        try:
            key = serialization.load_pem_public_key(data)
        except Exception as exc:  # noqa: BLE001
            raise SecurityError("Could not load the public key") from exc
        return cls(key=key, algorithm=_algorithm_for(key))

    @classmethod
    def from_cose(cls, cose_key: dict[int, Any]) -> "VerifyKey":
        """Decode a COSE_Key, the form an authenticator returns its key in.

        Only the two curve types WebAuthn authenticators actually use are
        accepted; anything else is rejected rather than guessed at.
        """
        key_type = cose_key.get(1)
        algorithm_id = cose_key.get(3)

        if key_type == CoseKeyType.EC2:
            if cose_key.get(-1) != 1:  # crv must be P-256
                raise SecurityError("Only the P-256 curve is supported", curve=cose_key.get(-1))
            x, y = cose_key.get(-2), cose_key.get(-3)
            if not isinstance(x, bytes) or not isinstance(y, bytes):
                raise SecurityError("COSE EC2 key is missing its coordinates")
            public_numbers = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
            )
            return cls(key=public_numbers.public_key(), algorithm=CoseAlgorithm.ES256)

        if key_type == CoseKeyType.OKP:
            if cose_key.get(-1) != 6:  # crv must be Ed25519
                raise SecurityError("Only the Ed25519 curve is supported", curve=cose_key.get(-1))
            x = cose_key.get(-2)
            if not isinstance(x, bytes):
                raise SecurityError("COSE OKP key is missing its public value")
            return cls(
                key=ed25519.Ed25519PublicKey.from_public_bytes(x),
                algorithm=CoseAlgorithm.EDDSA,
            )

        raise SecurityError(
            "Unsupported COSE key type", key_type=key_type, algorithm=algorithm_id
        )


def _algorithm_for(key: Any) -> CoseAlgorithm:
    if isinstance(key, ed25519.Ed25519PublicKey):
        return CoseAlgorithm.EDDSA
    if isinstance(key, ec.EllipticCurvePublicKey):
        return CoseAlgorithm.ES256
    raise SecurityError("Unsupported public key type", type=type(key).__name__)


def sha256(data: bytes) -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(data)
    return digest.finalize()


def constant_time_equal(a: bytes, b: bytes) -> bool:
    """Compare without leaking where the first difference is."""
    import hmac

    return hmac.compare_digest(a, b)


def random_challenge(length: int = 32) -> bytes:
    """A cryptographically random challenge.

    32 bytes is the WebAuthn recommendation: enough that an attacker cannot
    predict or precompute a response.
    """
    if length < 16:
        raise SecurityError("A challenge shorter than 16 bytes is not safe", length=length)
    return os.urandom(length)


__all__ = [
    "CoseAlgorithm",
    "CoseKeyType",
    "KeyPurpose",
    "SigningKey",
    "VerifyKey",
    "b64url_encode",
    "b64url_decode",
    "sha256",
    "constant_time_equal",
    "random_challenge",
]
