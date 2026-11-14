"""Self-update engine tests.

The central property under test is atomicity: a batch either applies in full or
not at all. A half-applied update — new price list, old system rules — is worse
than no update, because the numbers silently stop agreeing with each other.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from profileos.core.config import load_settings
from profileos.core.errors import ProfileOSError, SecurityError
from profileos.core.hotreload import PluginLoader
from profileos.security import SigningKey
from profileos.updates import (
    DirectorySource,
    PackageKind,
    UpdateChannel,
    UpdateEngine,
    UpdateManifest,
    Version,
    build_manifest,
    build_package,
    publish_directory,
)


SYSTEM_RULES = json.dumps({
    "kind": "system_rules", "id": "test-70", "name": "Test 70 series", "version": "2.0.0",
    "frame": {"face_width": 50.0},
    "profiles": {"frame": "T70-FRAME", "sash": "T70-SASH", "mullion": "T70-MUL",
                 "transom": "T70-TRA", "bead": "T70-BEAD"},
}).encode()

PRICE_LIST = json.dumps({
    "kind": "price_list", "id": "test-prices", "name": "Test prices", "currency": "EUR",
    "categories": ["profile"], "entries": [{"code": "T70-FRAME", "price": 100.0, "unit": "pc"}],
}).encode()


@pytest.fixture
def issuer() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def workspace(tmp_path):
    settings = load_settings(config_dir=tmp_path / "cfg", data_dir=str(tmp_path / "data"))
    settings.ensure_directories()
    return settings


@pytest.fixture
def feed(tmp_path, issuer):
    packages = [
        build_package("test.systems", PackageKind.SYSTEM_RULES, SYSTEM_RULES,
                      "test_70.json", issuer, version="2.0.0"),
        build_package("test.prices", PackageKind.PRICE_LIST, PRICE_LIST,
                      "test_prices.json", issuer, version="1.4.0"),
    ]
    manifest = build_manifest(packages, issuer)
    return publish_directory(
        {"test_70.json": SYSTEM_RULES, "test_prices.json": PRICE_LIST},
        manifest, tmp_path / "feed",
    )


def _engine(feed, issuer, settings, **kwargs) -> UpdateEngine:
    return UpdateEngine(
        DirectorySource(feed), issuer.public_key(), settings,
        loader=PluginLoader(settings, strict=False), **kwargs,
    )


class TestVersion:
    @pytest.mark.parametrize("text,expected", [("1", (1, 0, 0)), ("1.2", (1, 2, 0)), ("1.2.3", (1, 2, 3))])
    def test_parse(self, text, expected):
        version = Version.parse(text)
        assert (version.major, version.minor, version.patch) == expected

    def test_ordering(self):
        assert Version.parse("1.2.3") < Version.parse("1.10.0")
        assert Version.parse("2.0.0") > Version.parse("1.99.99")

    def test_bad_version_is_rejected(self):
        with pytest.raises(ProfileOSError):
            Version.parse("not.a.version")


class TestChannels:
    def test_stable_takes_only_stable(self):
        assert UpdateChannel.STABLE.accepts(UpdateChannel.STABLE)
        assert not UpdateChannel.STABLE.accepts(UpdateChannel.BETA)

    def test_beta_takes_stable_too(self):
        assert UpdateChannel.BETA.accepts(UpdateChannel.STABLE)
        assert UpdateChannel.BETA.accepts(UpdateChannel.BETA)
        assert not UpdateChannel.BETA.accepts(UpdateChannel.CANARY)


class TestPackageSafety:
    @pytest.mark.parametrize("filename", ["../escape.json", "a/b.json", "..", ".hidden.json", "x.exe"])
    def test_unsafe_filenames_are_rejected(self, filename, issuer):
        with pytest.raises(Exception):
            build_package("p", PackageKind.SYSTEM_RULES, b"{}", filename, issuer)

    def test_size_mismatch_is_detected(self, issuer):
        package = build_package("p", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "a.json", issuer)
        with pytest.raises(SecurityError, match="size"):
            package.verify(SYSTEM_RULES + b" ", issuer.public_key())

    def test_digest_mismatch_is_detected(self, issuer):
        """Same length, different content — this must reach the digest check."""
        package = build_package("p", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "a.json", issuer)
        altered = SYSTEM_RULES.replace(b"50.0", b"99.0")
        assert len(altered) == len(SYSTEM_RULES)
        with pytest.raises(SecurityError, match="digest"):
            package.verify(altered, issuer.public_key())

    def test_signature_from_another_key_is_rejected(self, issuer):
        package = build_package("p", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "a.json", issuer)
        with pytest.raises(SecurityError, match="signature"):
            package.verify(SYSTEM_RULES, SigningKey.generate().public_key())


class TestManifest:
    def test_signed_manifest_verifies(self, issuer):
        manifest = build_manifest([], issuer)
        manifest.verify(issuer.public_key())

    def test_unsigned_manifest_is_rejected(self, issuer):
        with pytest.raises(SecurityError, match="unsigned"):
            UpdateManifest().verify(issuer.public_key())

    def test_forged_manifest_is_rejected(self, issuer):
        manifest = build_manifest([], SigningKey.generate())
        with pytest.raises(SecurityError, match="not valid"):
            manifest.verify(issuer.public_key())

    def test_tampered_package_list_is_detected(self, issuer):
        package = build_package("p", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "a.json", issuer)
        manifest = build_manifest([package], issuer)
        # Swap in a different digest without re-signing.
        manifest.packages[0].sha256 = "0" * 64
        with pytest.raises(SecurityError):
            manifest.verify(issuer.public_key())

    def test_stale_manifest_is_rejected(self, issuer):
        old = datetime.now(timezone.utc) - timedelta(days=90)
        manifest = UpdateManifest(generated_at=old, max_age_days=30).sign(issuer)
        with pytest.raises(SecurityError, match="stale"):
            manifest.verify(issuer.public_key())


class TestUpdateFlow:
    def test_check_finds_updates(self, feed, issuer, workspace):
        plan = _engine(feed, issuer, workspace).check()
        assert len(plan.packages) == 2
        assert plan.has_updates

    def test_apply_installs_and_reloads(self, feed, issuer, workspace):
        engine = _engine(feed, issuer, workspace)
        result = engine.apply(engine.check())
        assert result.ok
        assert len(result.applied) == 2
        assert result.reloaded == 2

    def test_content_is_live_without_a_restart(self, feed, issuer, workspace):
        from profileos.elements import get_system_rules

        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())
        assert get_system_rules("test-70").name == "Test 70 series"

    def test_second_check_finds_nothing(self, feed, issuer, workspace):
        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())
        assert not engine.check().has_updates

    def test_older_version_is_not_offered(self, feed, issuer, workspace, tmp_path):
        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())

        older = build_package("test.systems", PackageKind.SYSTEM_RULES, SYSTEM_RULES,
                              "test_70.json", issuer, version="1.0.0")
        downgrade = publish_directory(
            {"test_70.json": SYSTEM_RULES}, build_manifest([older], issuer), tmp_path / "old"
        )
        assert not _engine(downgrade, issuer, workspace).check().has_updates

    def test_application_version_gate(self, tmp_path, issuer, workspace):
        future = build_package("future", PackageKind.SYSTEM_RULES, SYSTEM_RULES,
                               "test_70.json", issuer, min_app_version="99.0.0")
        source = publish_directory(
            {"test_70.json": SYSTEM_RULES}, build_manifest([future], issuer), tmp_path / "future"
        )
        plan = _engine(source, issuer, workspace).check()
        assert not plan.has_updates
        assert any("needs application" in reason for _pid, reason in plan.skipped)

    def test_beta_package_is_skipped_on_stable(self, tmp_path, issuer, workspace):
        beta = build_package("beta", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "test_70.json",
                             issuer, channel=UpdateChannel.BETA)
        source = publish_directory(
            {"test_70.json": SYSTEM_RULES}, build_manifest([beta], issuer), tmp_path / "beta"
        )
        assert not _engine(source, issuer, workspace).check().has_updates
        engine = _engine(source, issuer, workspace, channel=UpdateChannel.BETA)
        assert engine.check().has_updates


class TestAtomicity:
    def test_a_tampered_package_blocks_the_whole_batch(self, tmp_path, issuer, workspace):
        """The good package must not be applied when a sibling fails."""
        packages = [
            build_package("good", PackageKind.PRICE_LIST, PRICE_LIST, "test_prices.json",
                          issuer, version="1.0.0"),
            build_package("bad", PackageKind.SYSTEM_RULES, SYSTEM_RULES, "test_70.json",
                          issuer, version="1.0.0"),
        ]
        manifest = build_manifest(packages, issuer)
        # Publish altered bytes for one package only.
        source = publish_directory(
            {"test_prices.json": PRICE_LIST, "test_70.json": SYSTEM_RULES.replace(b"50.0", b"99.0")},
            manifest, tmp_path / "tampered",
        )

        engine = _engine(source, issuer, workspace)
        result = engine.apply(engine.check())

        assert not result.ok
        assert result.applied == []
        assert engine.installed() == []
        assert not (workspace.macros_dir / "test_prices.json").exists()

    def test_a_broken_but_signed_package_is_refused(self, tmp_path, issuer, workspace):
        """A valid signature proves origin, not that the content is loadable."""
        broken = json.dumps({"kind": "system_rules", "id": "x"}).encode()  # missing required fields
        package = build_package("broken", PackageKind.SYSTEM_RULES, broken, "broken.json", issuer)
        source = publish_directory(
            {"broken.json": broken}, build_manifest([package], issuer), tmp_path / "broken"
        )
        engine = _engine(source, issuer, workspace)
        result = engine.apply(engine.check())
        assert not result.ok and result.applied == []

    def test_a_malicious_code_package_is_refused(self, tmp_path, issuer, workspace):
        """Code packages face the same AST policy as hand-installed plugins."""
        hostile = b"import subprocess\ndef register(context):\n    subprocess.run(['id'])\n"
        package = build_package("evil", PackageKind.MACRO_LIBRARY, hostile, "evil.py", issuer)
        source = publish_directory(
            {"evil.py": hostile}, build_manifest([package], issuer), tmp_path / "evil"
        )
        engine = _engine(source, issuer, workspace)
        result = engine.apply(engine.check())

        assert not result.ok
        assert any("static validation" in reason for _pid, reason in result.failed)
        assert not (workspace.macros_dir / "evil.py").exists()

    def test_a_valid_code_package_is_accepted(self, tmp_path, issuer, workspace):
        good = (
            b'"""A well-behaved macro plugin."""\n'
            b"def register(context):\n"
            b"    context.register('macros', 'test.macro', lambda p, c: [])\n"
        )
        package = build_package("good", PackageKind.MACRO_LIBRARY, good, "good_macro.py", issuer)
        source = publish_directory(
            {"good_macro.py": good}, build_manifest([package], issuer), tmp_path / "good"
        )
        engine = _engine(source, issuer, workspace)
        assert engine.apply(engine.check()).ok


class TestRollback:
    def test_rollback_removes_a_newly_added_package(self, feed, issuer, workspace):
        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())
        assert engine.rollback("test.systems")
        assert "test.systems" not in {p.package_id for p in engine.installed()}
        assert not (workspace.macros_dir / "test_70.json").exists()

    def test_rollback_restores_the_previous_version(self, feed, issuer, workspace, tmp_path):
        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())

        updated = SYSTEM_RULES.replace(b'"2.0.0"', b'"3.0.0"').replace(b"Test 70 series", b"Test 70 rev B")
        package = build_package("test.systems", PackageKind.SYSTEM_RULES, updated,
                                "test_70.json", issuer, version="3.0.0")
        source = publish_directory(
            {"test_70.json": updated}, build_manifest([package], issuer), tmp_path / "v3"
        )
        engine2 = _engine(source, issuer, workspace)
        engine2.state = engine.state
        assert engine2.apply(engine2.check()).ok
        assert b"rev B" in (workspace.macros_dir / "test_70.json").read_bytes()

        assert engine2.rollback("test.systems")
        assert b"Test 70 series" in (workspace.macros_dir / "test_70.json").read_bytes()

    def test_rollback_of_unknown_package_is_reported(self, feed, issuer, workspace):
        assert not _engine(feed, issuer, workspace).rollback("nope")


class TestSources:
    def test_directory_source_refuses_path_escape(self, feed):
        source = DirectorySource(feed)
        with pytest.raises(ProfileOSError, match="escapes"):
            source.fetch_package("../../etc/passwd", 0)

    def test_http_source_requires_https(self):
        from profileos.updates import HttpSource

        with pytest.raises(ProfileOSError, match="HTTPS"):
            HttpSource("http://updates.example.com")

    def test_http_source_allows_explicit_insecure_mirror(self):
        from profileos.updates import HttpSource

        assert HttpSource("http://mirror.local", allow_insecure=True)

    def test_chained_source_falls_through(self, feed, tmp_path):
        from profileos.updates import ChainedSource

        chain = ChainedSource(DirectorySource(tmp_path / "missing"), DirectorySource(feed))
        assert chain.available()
        assert chain.fetch_manifest()


class TestHistory:
    def test_history_records_applied_updates(self, feed, issuer, workspace):
        engine = _engine(feed, issuer, workspace)
        engine.apply(engine.check())
        history = engine.history()
        assert history and len(history[-1]["applied"]) == 2

    def test_status_reports_the_source(self, feed, issuer, workspace):
        status = _engine(feed, issuer, workspace).status()
        assert status["source_available"]
        assert status["channel"] == "stable"
