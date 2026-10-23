"""Security engine: hardware authentication and offline licensing."""

from __future__ import annotations

from .hwid import HardwareFingerprint, Trait, collect_traits, current_fingerprint
from .keys import (
    CoseAlgorithm,
    CoseKeyType,
    KeyPurpose,
    SigningKey,
    VerifyKey,
    b64url_decode,
    b64url_encode,
    random_challenge,
    sha256,
)
from .license import (
    LicenseStatus,
    LicenseTerms,
    issue_license,
    load_license,
    load_license_file,
    save_license,
)
from .softkey import SoftwareAuthenticator
from .webauthn import (
    AuthenticatorData,
    AuthenticatorFlags,
    ChallengeStore,
    RegisteredCredential,
    RelyingParty,
    WebAuthnServer,
    parse_authenticator_data,
)

__all__ = [
    "CoseAlgorithm", "CoseKeyType", "KeyPurpose", "SigningKey", "VerifyKey",
    "b64url_encode", "b64url_decode", "sha256", "random_challenge",
    "AuthenticatorFlags", "AuthenticatorData", "parse_authenticator_data",
    "RelyingParty", "RegisteredCredential", "ChallengeStore", "WebAuthnServer",
    "SoftwareAuthenticator",
    "Trait", "HardwareFingerprint", "collect_traits", "current_fingerprint",
    "LicenseTerms", "LicenseStatus", "issue_license", "load_license",
    "load_license_file", "save_license",
]
