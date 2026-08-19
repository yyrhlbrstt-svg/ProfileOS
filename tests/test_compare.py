"""Tests for the capability comparison.

The comparison exists to make a claim, so the tests exist to stop the claim
drifting away from the code. The important one is
:meth:`TestClaims.test_every_claim_resolves_to_real_code`: if an engine is
deleted or renamed and the matrix still advertises it, that test fails in the
same commit.
"""

from __future__ import annotations

import json

import pytest

from profileos import compare


class TestClaims:
    def test_every_claim_resolves_to_real_code(self):
        """A capability the matrix claims must have a symbol behind it."""
        failures = compare.verify_claims()
        assert failures == {}, (
            "the comparison claims capabilities with no implementation: "
            + "; ".join(f"{k} ({v})" for k, v in sorted(failures.items()))
        )

    def test_the_matrix_admits_what_is_missing(self):
        """A comparison that claimed everything would be worth nothing."""
        gaps = compare.missing_from_profileos()
        assert gaps, "the matrix should record capabilities ProfileOS lacks"
        assert all(not capability.implemented for capability in gaps)

    def test_a_broken_probe_is_detected(self):
        """The check has to be able to fail, or it proves nothing."""
        planted = compare.Capability(
            "planted", compare.Area.PLATFORM, "Not real", "לא קיים",
            "A capability with nothing behind it.",
            probe="profileos.nowhere:missing",
        )
        assert "planted" in compare.verify_claims([planted])

    def test_a_missing_attribute_is_detected(self):
        planted = compare.Capability(
            "planted", compare.Area.PLATFORM, "Not real", "לא קיים", "",
            probe="profileos.compare:no_such_symbol",
        )
        assert "planted" in compare.verify_claims([planted])


class TestHonesty:
    def test_silence_is_not_counted_as_a_distinction(self):
        """"Nobody checked" must never be reported as "nobody has it"."""
        for capability in compare.not_documented_elsewhere():
            levels = [package.level(capability.id) for package in compare.PACKAGES]
            assert compare.Support.NOT_DOCUMENTED in levels
            assert compare.Support.FULL not in levels
            assert compare.Support.PARTIAL not in levels

    def test_unlisted_capabilities_default_to_unknown(self):
        package = compare.PACKAGES[0]
        assert package.level("no-such-capability") is compare.Support.UNKNOWN

    def test_limitations_are_part_of_the_output(self):
        assert compare.STANDING_LIMITATIONS
        joined = " ".join(compare.STANDING_LIMITATIONS).lower()
        # The two caveats that most affect whether this can replace a package.
        assert "catalogue" in joined
        assert "physical machine" in joined
        assert compare.summary()["standing_limitations"] == len(
            compare.STANDING_LIMITATIONS
        )

    def test_capability_ids_are_unique(self):
        ids = [capability.id for capability in compare.CAPABILITIES]
        assert len(ids) == len(set(ids))

    def test_package_ids_are_unique(self):
        ids = [package.id for package in compare.PACKAGES]
        assert len(ids) == len(set(ids))

    def test_packages_only_rate_capabilities_that_exist(self):
        """A rating for a capability nobody defined would never be shown."""
        known = set(compare.CAPABILITY_BY_ID)
        for package in compare.PACKAGES:
            unknown = set(package.support) - known
            assert not unknown, f"{package.id} rates unknown capabilities: {unknown}"

    def test_every_capability_is_named_in_both_languages(self):
        for capability in compare.CAPABILITIES:
            assert capability.name_en.strip()
            assert capability.name_he.strip()

    def test_every_capability_explains_itself(self):
        for capability in compare.CAPABILITIES:
            if capability.id in {"3d_view", "erp", "capacity_planning"}:
                continue
            assert len(capability.detail) > 30, capability.id


class TestReporting:
    def test_matrix_covers_every_capability_and_package(self):
        rows = compare.matrix()
        assert len(rows) == len(compare.CAPABILITIES)
        for row in rows:
            for package in compare.PACKAGES:
                assert package.id in row

    def test_coverage_adds_up(self):
        for package in (None, *compare.PACKAGES):
            counts = compare.coverage(package)
            assert sum(counts.values()) == len(compare.CAPABILITIES)

    def test_summary_is_json_safe(self):
        json.dumps(compare.summary())
        json.dumps(compare.matrix(), ensure_ascii=False)

    def test_claims_verified_flag_tracks_the_probes(self):
        assert compare.summary()["claims_verified"] is True

    @pytest.mark.parametrize("area", list(compare.Area))
    def test_every_area_has_capabilities(self, area):
        assert [c for c in compare.CAPABILITIES if c.area is area]
