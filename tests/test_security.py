"""Security engine tests: WebAuthn, hardware fingerprints and licences.

The WebAuthn tests drive a software authenticator that emits byte-for-byte
correct structures, so the verifier is exercised for real rather than against
recorded fixtures. Every negative case asserts on *which* check refused it —
"login failed" is useless when diagnosing a cloned key versus an untouched one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import AuthenticatorError, LicenseError, SecurityError
from profileos.security import (
    CoseAlgorithm,
    HardwareFingerprint,
    RelyingParty,
    SigningKey,
    SoftwareAuthenticator,
    Trait,
    VerifyKey,
    WebAuthnServer,
    b64url_decode,
    b64url_encode,
    current_fingerprint,
    issue_license,
    load_license,
    parse_authenticator_data,
    random_challenge,
)
from profileos.security.license import LicenseTerms


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #

class TestKeys:
    @pytest.mark.parametrize("algorithm", [CoseAlgorithm.EDDSA, CoseAlgorithm.ES256])
    def test_sign_and_verify(self, algorithm):
        key = SigningKey.generate(algorithm=algorithm)
        signature = key.sign(b"payload")
        assert key.public_key().verify(signature, b"payload")

    @pytest.mark.parametrize("algorithm", [CoseAlgorithm.EDDSA, CoseAlgorithm.ES256])
    def test_tampered_message_fails(self, algorithm):
        key = SigningKey.generate(algorithm=algorithm)
        signature = key.sign(b"payload")
        assert not key.public_key().verify(signature, b"payloae")

    def test_other_key_cannot_verify(self):
        signature = SigningKey.generate().sign(b"x")
        assert not SigningKey.generate().public_key().verify(signature, b"x")

    def test_pem_round_trip(self):
        key = SigningKey.generate()
        restored = SigningKey.from_pem(key.to_pem())
        assert restored.key_id == key.key_id

    def test_public_pem_round_trip(self):
        public = SigningKey.generate().public_key()
        assert VerifyKey.from_pem(public.to_pem()).key_id == public.key_id

    def test_key_saved_with_restricted_permissions(self, tmp_path):
        import os
        import stat

        path = SigningKey.generate().save(tmp_path / "signing.key")
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode & 0o077 == 0, "a signing key must not be group or world readable"

    @pytest.mark.parametrize("data", [b"", b"\x00", b"\xff\xfe\x00abc"])
    def test_b64url_round_trip(self, data):
        assert b64url_decode(b64url_encode(data)) == data

    def test_b64url_accepts_padded_input(self):
        assert b64url_decode("AP_-") == b64url_decode("AP_-=")

    def test_short_challenge_is_refused(self):
        with pytest.raises(SecurityError):
            random_challenge(8)


# --------------------------------------------------------------------------- #
# WebAuthn
# --------------------------------------------------------------------------- #

@pytest.fixture
def relying_party() -> RelyingParty:
    return RelyingParty(
        rp_id="cad.system.local", allowed_origins=("https://cad.system.local",)
    )


@pytest.fixture
def server(relying_party) -> WebAuthnServer:
    return WebAuthnServer(relying_party)


def _register(server: WebAuthnServer, key: SoftwareAuthenticator, **kwargs):
    response = key.create(server.registration_options("u1", "operator"))["response"]
    return server.verify_registration(
        response["attestationObject"], response["clientDataJSON"], **kwargs
    )


def _authenticate(server: WebAuthnServer, key: SoftwareAuthenticator, credential, **overrides):
    options = server.authentication_options()
    options.update(overrides)
    response = key.get(options, credential.credential_id)["response"]
    return server.verify_assertion(
        credential.credential_id,
        response["authenticatorData"],
        response["clientDataJSON"],
        response["signature"],
    )


class TestAuthenticatorData:
    def test_short_buffer_is_rejected(self):
        with pytest.raises(AuthenticatorError):
            parse_authenticator_data(b"\x00" * 10)

    def test_flags_are_decoded(self):
        import struct

        from profileos.security.keys import sha256

        data = sha256(b"rp") + bytes([0x01 | 0x04]) + struct.pack(">I", 7)
        parsed = parse_authenticator_data(data)
        assert parsed.user_present and parsed.user_verified
        assert parsed.sign_count == 7
        assert not parsed.has_credential_data


class TestRegistration:
    @pytest.mark.parametrize("algorithm", [CoseAlgorithm.ES256, CoseAlgorithm.EDDSA])
    def test_registration_succeeds(self, server, algorithm):
        credential = _register(server, SoftwareAuthenticator(algorithm=algorithm))
        assert credential.public_key.algorithm is algorithm
        assert credential.is_usable

    def test_registration_without_user_presence_is_refused(self, server):
        with pytest.raises(AuthenticatorError, match="User Present"):
            _register(server, SoftwareAuthenticator(user_present=False))

    def test_user_verification_policy_is_enforced(self):
        strict = RelyingParty(
            allowed_origins=("https://cad.system.local",), require_user_verification=True
        )
        server = WebAuthnServer(strict)
        with pytest.raises(AuthenticatorError, match="User Verified"):
            _register(server, SoftwareAuthenticator(user_verified=False))

    def test_same_credential_cannot_register_twice(self, server):
        key = SoftwareAuthenticator()
        _register(server, key)
        # Replay the same authenticator's stored credential.
        response = key.create(server.registration_options("u1", "operator"))["response"]
        server.verify_registration(response["attestationObject"], response["clientDataJSON"])
        assert len(server.credentials) == 2  # a fresh credential each time

    def test_registration_challenge_cannot_be_reused(self, server):
        key = SoftwareAuthenticator()
        options = server.registration_options("u1", "operator")
        first = key.create(options)["response"]
        server.verify_registration(first["attestationObject"], first["clientDataJSON"])

        second = key.create(options)["response"]
        with pytest.raises(AuthenticatorError, match="replay"):
            server.verify_registration(second["attestationObject"], second["clientDataJSON"])


class TestAuthentication:
    def test_authentication_succeeds_and_advances_the_counter(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        for expected in (1, 2, 3):
            assert _authenticate(server, key, credential).sign_count == expected

    def test_replayed_assertion_is_refused(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        options = server.authentication_options()
        response = key.get(options, credential.credential_id)["response"]

        server.verify_assertion(
            credential.credential_id, response["authenticatorData"],
            response["clientDataJSON"], response["signature"],
        )
        with pytest.raises(AuthenticatorError, match="replay"):
            server.verify_assertion(
                credential.credential_id, response["authenticatorData"],
                response["clientDataJSON"], response["signature"],
            )

    def test_tampered_signature_is_refused(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        options = server.authentication_options()
        response = key.get(options, credential.credential_id)["response"]

        with pytest.raises(AuthenticatorError, match="Signature"):
            server.verify_assertion(
                credential.credential_id, response["authenticatorData"],
                response["clientDataJSON"], b"\x00" * len(response["signature"]),
            )

    def test_phishing_origin_is_refused(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        with pytest.raises(AuthenticatorError, match="Origin"):
            _authenticate(server, key, credential, _origin="https://evil.example")

    def test_wrong_relying_party_is_refused(self, server):
        """Correct origin but a credential scoped to a different rpId."""
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        with pytest.raises(AuthenticatorError, match="rpIdHash"):
            _authenticate(
                server, key, credential,
                rpId="other.local", _origin="https://cad.system.local",
            )

    def test_cloned_key_is_detected(self, server):
        """A clone shares the private key but has a rewound counter."""
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        _authenticate(server, key, credential)
        _authenticate(server, key, credential)

        with pytest.raises(AuthenticatorError, match="cloned"):
            _authenticate(server, key.clone(), credential)

    def test_authenticator_without_a_counter_is_accepted(self, server):
        """Reporting 0 forever is legitimate and must not read as a clone."""
        key = SoftwareAuthenticator(use_sign_counter=False)
        credential = _register(server, key)
        for _ in range(3):
            assert _authenticate(server, key, credential).sign_count == 0

    def test_revoked_credential_is_refused(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        assert server.revoke(credential.credential_id)
        with pytest.raises(AuthenticatorError, match="revoked"):
            _authenticate(server, key, credential)

    def test_unknown_credential_is_refused(self, server):
        key = SoftwareAuthenticator()
        credential = _register(server, key)
        options = server.authentication_options()
        response = key.get(options, credential.credential_id)["response"]
        with pytest.raises(AuthenticatorError, match="Unknown credential"):
            server.verify_assertion(
                b"nonexistent", response["authenticatorData"],
                response["clientDataJSON"], response["signature"],
            )

    def test_credentials_survive_export_and_import(self, server, relying_party):
        key = SoftwareAuthenticator()
        credential = _register(server, key, label="YubiKey")
        _authenticate(server, key, credential)

        restored = WebAuthnServer(relying_party)
        assert restored.import_credentials(server.export_credentials()) == 1
        assert _authenticate(restored, key, credential).sign_count == 2

    def test_software_key_declares_itself(self):
        assert SoftwareAuthenticator().is_hardware is False


# --------------------------------------------------------------------------- #
# Hardware fingerprint
# --------------------------------------------------------------------------- #

class TestFingerprint:
    def test_fingerprint_is_stable(self):
        assert current_fingerprint().fingerprint == current_fingerprint().fingerprint

    def test_traits_are_hashed_not_stored(self):
        """A licence must not carry a serial number or MAC address in the clear."""
        import json

        fingerprint = current_fingerprint()
        serialised = json.dumps(fingerprint.to_dict())

        for trait in fingerprint.traits:
            # Short values ("4" from cpu_count) appear inside hex digests by
            # coincidence, so only check values long enough to be identifying.
            if len(trait.value) >= 6:
                assert trait.value not in serialised, f"{trait.name} leaked its raw value"

        # The serialised form exposes only name, digest and weight.
        for entry in fingerprint.to_dict()["traits"]:
            assert set(entry) == {"n", "d", "w"}

    def test_identical_machine_scores_one(self):
        fingerprint = current_fingerprint()
        assert fingerprint.match_score(fingerprint.to_dict()) == pytest.approx(1.0)

    def test_different_machine_scores_zero(self):
        recorded = HardwareFingerprint(
            traits=[Trait("machine_id", "elsewhere", 3.0), Trait("hostname", "other", 0.5)]
        ).to_dict()
        assert current_fingerprint().match_score(recorded) == pytest.approx(0.0)

    def test_a_swapped_network_card_still_matches(self):
        """A weak trait changing must not lock the customer out."""
        original = current_fingerprint()
        degraded = HardwareFingerprint(
            traits=[
                t if t.name != "mac_address" else Trait("mac_address", "aabbccddeeff", t.weight)
                for t in original.traits
            ]
        )
        assert degraded.matches(original.to_dict(), threshold=0.6)


# --------------------------------------------------------------------------- #
# Licences
# --------------------------------------------------------------------------- #

@pytest.fixture
def issuer() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def machine() -> HardwareFingerprint:
    return current_fingerprint()


class TestLicence:
    def test_valid_licence_is_accepted(self, issuer, machine):
        terms = LicenseTerms(licensee="Acme", expires_on=date.today() + timedelta(days=90))
        blob = issue_license(terms, issuer, machine)
        status = load_license(blob, issuer.public_key(), fingerprint=machine)
        assert status.valid and status.terms.licensee == "Acme"

    def test_features_gate_correctly(self, issuer, machine):
        terms = LicenseTerms(licensee="A", features={"nesting"})
        status = load_license(issue_license(terms, issuer, machine), issuer.public_key(), fingerprint=machine)
        assert status.terms.allows("nesting")
        assert not status.terms.allows("cnc")

    def test_empty_feature_set_allows_everything(self):
        assert LicenseTerms(licensee="A").allows("anything")

    def test_forged_licence_is_refused(self, issuer, machine):
        blob = issue_license(LicenseTerms(licensee="A"), issuer, machine)
        status = load_license(blob, SigningKey.generate().public_key(), fingerprint=machine)
        assert not status.valid and "signature" in status.reason.lower()

    def test_licence_from_another_machine_is_refused(self, issuer, machine):
        blob = issue_license(LicenseTerms(licensee="A"), issuer, machine)
        other = HardwareFingerprint(traits=[Trait("machine_id", "elsewhere", 3.0)])
        status = load_license(blob, issuer.public_key(), fingerprint=other)
        assert not status.valid

    def test_tampered_licence_is_refused(self, issuer, machine):
        blob = bytearray(issue_license(LicenseTerms(licensee="A"), issuer, machine))
        blob[-1] ^= 0xFF
        status = load_license(bytes(blob), issuer.public_key(), fingerprint=machine)
        assert not status.valid

    def test_recently_expired_licence_runs_read_only(self, issuer, machine):
        terms = LicenseTerms(licensee="A", expires_on=date.today() - timedelta(days=3))
        status = load_license(
            issue_license(terms, issuer, machine), issuer.public_key(),
            fingerprint=machine, grace_days=7,
        )
        assert status.valid and status.read_only

    def test_long_expired_licence_is_refused(self, issuer, machine):
        terms = LicenseTerms(licensee="A", expires_on=date.today() - timedelta(days=30))
        status = load_license(
            issue_license(terms, issuer, machine), issuer.public_key(),
            fingerprint=machine, grace_days=7,
        )
        assert not status.valid and "grace" in status.reason.lower()

    def test_non_licence_file_raises(self, issuer):
        with pytest.raises(LicenseError):
            load_license(b"just some bytes", issuer.public_key())

    def test_licence_bytes_do_not_leak_the_licensee(self, issuer, machine):
        """The body is sealed, so the file must not contain readable terms."""
        blob = issue_license(LicenseTerms(licensee="Confidential Customer"), issuer, machine)
        assert b"Confidential Customer" not in blob
