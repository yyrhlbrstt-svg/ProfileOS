"""System directory tests.

The directory's job is not to know things — it is to be honest about what it
does not know. So most of these tests are about the boundary between "you may
quote this" and "you may cut this", and about that boundary being impossible
to cross by accident.
"""

from __future__ import annotations

import pytest

from profileos.elements.builder import ElementBuilder
from profileos.elements.model import Opening
from profileos.elements.rules import SystemRules
from profileos.systems import (
    MANUFACTURERS,
    SERIES,
    Provenance,
    SystemDirectory,
    SystemEntry,
    SystemFamily,
    UnclassifiedSystem,
)


@pytest.fixture
def directory() -> SystemDirectory:
    """A fresh directory, so a confirmation in one test cannot leak into another."""
    return SystemDirectory(entries=SERIES, manufacturers=MANUFACTURERS)


def klil_rules() -> SystemRules:
    """Stands in for figures read out of a supplier catalogue."""
    return SystemRules(id="klil-7300-2024", name="קליל 7300", supplier="klil")


class TestDirectory:
    def test_the_israeli_manufacturers_are_all_there(self, directory):
        ids = {maker.id for maker in directory.manufacturers()}
        assert {"klil", "alubin", "extal", "alumgold", "apex", "alumgraph"} <= ids
        assert {"schuco", "reynaers", "wicona", "cortizo", "alumil"} <= ids

    def test_suppliers_holding_local_stock_come_first(self, directory):
        """Stock in the country is the difference between two days and six weeks."""
        makers = directory.manufacturers()
        local = [index for index, m in enumerate(makers) if m.local_stock]
        imported = [index for index, m in enumerate(makers) if not m.local_stock]
        assert max(local) < min(imported)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("7300", "klil-7300"),
            ("בלגי", "klil-7300"),
            ("E50", "extal-e50"),
            ("אקסטל", "extal-e19"),
        ],
    )
    def test_a_series_is_found_the_way_people_type_it(self, directory, text, expected):
        found = {entry.id for entry in directory.search(text)}
        assert expected in found

    def test_an_exact_series_number_ranks_above_a_partial_match(self, directory):
        assert directory.search("5500")[0].series == "5500"

    def test_searching_for_nothing_returns_everything(self, directory):
        assert len(directory.search("  ")) == len(directory)

    def test_hardware_makers_are_named_without_claiming_their_ratings(self):
        """A hinge load rating is exactly the number a weight check must not guess."""
        from profileos.systems import hardware_makers

        makers = {maker.id for maker in hardware_makers()}
        assert {"fapim", "savio", "giesse", "roto", "stublina"} <= makers


class TestProvenance:
    def test_nothing_ships_pre_confirmed(self, directory):
        """No figure in this repository claims to be a supplier's own."""
        assert all(
            directory.provenance_for(entry.id) is not Provenance.CONFIRMED
            for entry in directory
        )

    def test_an_unclassified_series_knows_nothing(self, directory):
        assert directory.provenance_for("klil-7000") is Provenance.UNKNOWN
        readiness = directory.readiness("klil-7000")
        assert not readiness.may_quote and not readiness.may_cut

    def test_a_classified_series_may_be_quoted_but_not_cut(self, directory):
        readiness = directory.readiness("klil-7300")
        assert readiness.may_quote
        assert not readiness.may_cut
        assert "קטלוג" in readiness.reasons[0] or "catalogue" in readiness.reasons[0]

    def test_the_banner_says_not_for_production_in_both_languages(self, directory):
        banner = directory.readiness("klil-7300").banner
        assert "לא לייצור" in banner
        assert "NOT FOR PRODUCTION" in banner

    def test_a_confirmed_series_has_no_banner(self, directory):
        directory.confirm("klil-7300", klil_rules(), source="Klil catalogue 2024, page 41")
        readiness = directory.readiness("klil-7300")
        assert readiness.may_cut
        assert readiness.banner is None

    def test_confirming_records_where_the_figures_came_from(self, directory):
        entry = directory.confirm("klil-7300", klil_rules(), source="klil-7300.pdf")
        assert entry.source == "klil-7300.pdf"
        assert entry.provenance is Provenance.CONFIRMED

    def test_a_confirmation_without_a_source_is_refused(self, directory):
        """A confirmed number with no record of its origin is just an assertion."""
        with pytest.raises(ValueError, match="source"):
            directory.confirm("klil-7300", klil_rules(), source="   ")

    def test_classifying_does_not_make_a_series_cuttable(self, directory):
        directory.classify("klil-7000", SystemFamily.SLIDING, source="Klil catalogue")
        assert directory.provenance_for("klil-7000") is Provenance.TYPICAL
        assert not directory.readiness("klil-7000").may_cut


class TestRules:
    def test_an_unclassified_series_has_no_rules_to_offer(self, directory):
        with pytest.raises(UnclassifiedSystem):
            directory.rules_for("klil-9000")

    def test_a_sliding_system_is_not_a_casement_with_other_numbers(self, directory):
        """The sash sits in a track rather than overlapping a rebate."""
        directory.classify("klil-7000", SystemFamily.SLIDING, source="test")
        sliding, _ = directory.rules_for("klil-7000")
        casement, _ = directory.rules_for("klil-7300")
        assert sliding.sash.frame_overlap == 0.0
        assert casement.sash.frame_overlap > 0.0
        assert not sliding.frame.mitred_corners
        assert casement.frame.mitred_corners

    def test_a_lift_slide_takes_thicker_glass_than_a_partition(self, directory):
        from profileos.systems import FAMILY_RULES

        assert (
            FAMILY_RULES[SystemFamily.LIFT_SLIDE].glass.max_glass_thickness
            > FAMILY_RULES[SystemFamily.PARTITION].glass.max_glass_thickness
        )

    def test_confirmed_rules_replace_the_stand_ins(self, directory):
        directory.confirm("klil-7300", klil_rules(), source="catalogue")
        rules, provenance = directory.rules_for("klil-7300")
        assert rules.id == "klil-7300-2024"
        assert provenance is Provenance.CONFIRMED

    def test_a_missing_series_is_an_error_not_a_default(self, directory):
        with pytest.raises(KeyError):
            directory.rules_for("acme-9999")


class TestItReachesTheCutList:
    """The point of all of it: a bar must not be cut to a guessed figure."""

    def _opening(self) -> Opening:
        return Opening(element_id="W1", width=1200.0, height=1400.0)

    def test_a_build_from_stand_ins_is_marked_not_for_production(self, directory):
        builder = ElementBuilder.for_system("klil-7300", directory=directory)
        build = builder.build(self._opening())
        assert build.cuts  # it still produces a full package to quote from
        assert not build.may_be_cut
        assert "NOT FOR PRODUCTION" in build.production_banner

    def test_a_build_from_a_loaded_catalogue_is_releasable(self, directory):
        directory.confirm("klil-7300", klil_rules(), source="Klil catalogue 2024")
        build = ElementBuilder.for_system("klil-7300", directory=directory).build(
            self._opening()
        )
        assert build.may_be_cut
        assert build.production_banner is None

    def test_rules_handed_in_directly_stay_cautious(self):
        """A rule set passed straight in cannot say where it came from."""
        build = ElementBuilder(klil_rules()).build(self._opening())
        assert build.provenance is Provenance.TYPICAL
        assert not build.may_be_cut

    def test_the_caller_can_still_state_a_provenance_deliberately(self):
        build = ElementBuilder(klil_rules(), provenance=Provenance.CONFIRMED).build(
            self._opening()
        )
        assert build.may_be_cut


class TestCoverage:
    def test_coverage_counts_what_is_actually_usable(self, directory):
        before = directory.coverage()
        assert before["confirmed"] == 0
        assert before["total"] == len(SERIES)
        directory.confirm("klil-7300", klil_rules(), source="catalogue")
        after = directory.coverage()
        assert after["confirmed"] == 1
        assert after["typical"] == before["typical"] - 1

    def test_a_shop_can_add_its_own_series(self, directory):
        directory.add(
            SystemEntry(
                manufacturer="klil",
                series="1234",
                hebrew="קליל",
                family=SystemFamily.CASEMENT,
                source="the shop's own list",
            )
        )
        assert directory.get("klil-1234") is not None
        assert directory.search("1234")[0].series == "1234"


class TestCatalogueDocuments:
    """A shop must be able to add series without a code change."""

    def _document(self, **overrides):
        from profileos.systems.document import SystemCatalogue

        payload = {
            "id": "shop-extra",
            "name": "תוספות המפעל",
            "source": "the shop's own list",
            "manufacturers": [
                {"id": "kav", "name": "Kav Aluminium", "hebrew": "קו אלומיניום",
                 "country": "IL", "local_stock": True}
            ],
            "series": [
                {"manufacturer": "kav", "series": "K90", "hebrew": "קו",
                 "family": "tilt_turn", "aliases": ["ק90"]}
            ],
        }
        payload.update(overrides)
        return SystemCatalogue.model_validate(payload)

    def test_a_document_adds_both_the_maker_and_the_series(self, directory):
        added = directory.load_document(self._document())
        assert added == 1
        assert directory.manufacturer("kav").hebrew == "קו אלומיניום"
        assert directory.get("kav-k90").family is SystemFamily.TILT_TURN

    def test_the_source_travels_onto_every_series_it_adds(self, directory):
        directory.load_document(self._document())
        assert directory.get("kav-k90").source == "the shop's own list"

    def test_a_document_cannot_declare_its_own_figures_confirmed(self, directory):
        """Otherwise the one number that must be traceable becomes untraceable."""
        directory.load_document(self._document())
        assert directory.provenance_for("kav-k90") is Provenance.TYPICAL
        assert not directory.readiness("kav-k90").may_cut

    def test_an_alias_is_searchable(self, directory):
        directory.load_document(self._document())
        assert directory.search("ק90")[0].id == "kav-k90"

    def test_an_empty_series_number_is_refused(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            self._document(series=[{"manufacturer": "kav", "series": "  "}])

    def test_a_stray_field_is_refused_rather_than_ignored(self):
        """A typo in a document must not silently do nothing."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            self._document(series=[{"manufacturer": "kav", "series": "K1", "familly": "sliding"}])

    def test_the_document_is_a_loadable_plugin_kind(self):
        from profileos.core.hotreload import DATA_SCHEMAS, register_builtin_schemas

        register_builtin_schemas()
        assert DATA_SCHEMAS.get("system_catalogue") is not None

    def test_the_document_has_a_published_schema(self):
        from profileos.schemas import all_schemas

        schema = all_schemas()["system_catalogue"]
        assert schema["properties"]["kind"]["const"] == "system_catalogue"
