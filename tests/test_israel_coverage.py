"""Does it actually have the Israeli repertoire, or only most of it."""

from __future__ import annotations

import pytest

from profileos.elements import Opening, build_elements
from profileos.glazing import STANDARD_BUILDUPS
from profileos.library import FAMILIES, search_openings
from profileos.systems.israel import MANUFACTURERS


def _family(family_id: str):
    for family in FAMILIES:
        if family.family_id == family_id:
            return family
    raise AssertionError(f"no family {family_id!r}")


class TestTheOpeningsAnIsraeliShopIsAskedFor:
    @pytest.mark.parametrize("family_id", [
        "sliding", "lift_slide", "casement", "tilt_turn", "tilt_slide",
        "folding", "door_single", "door_double", "curtain_wall", "shopfront",
        "mamad", "mamad_door", "balustrade", "corner", "bars", "louvre",
        "skylight", "automatic_door", "partition",
    ])
    def test_the_type_exists(self, family_id):
        assert _family(family_id)

    @pytest.mark.parametrize("words,expect", [
        ("אקורדיון", "folding"),
        ("הרמונית", "folding"),
        ("מעקה זכוכית", "balustrade"),
        ("קיפ הזזה", "tilt_slide"),
        ("סורגים", "bars"),
        ("דלת ממד", "mamad_door"),
        ("חלון פינתי", "corner"),
        ("דלת אוטומטית", "automatic_door"),
    ])
    def test_it_is_found_the_way_somebody_says_it(self, words, expect):
        found = search_openings(words, limit=6)
        assert found, words
        assert any(item.family_id == expect for item in found)


class TestWhatEachOneImplies:
    def test_a_balustrade_is_laminated_not_merely_toughened(self):
        """A barrier has to keep standing, which toughened glass does not."""
        family = _family("balustrade")
        build_up = STANDARD_BUILDUPS[family.glass]
        assert build_up.is_safety_glass
        assert any(pane.laminated for pane in build_up.panes)

    def test_a_folding_door_is_made_in_more_than_two_leaves(self):
        assert max(_family("folding").leaves) >= 4

    def test_a_mamad_door_is_not_something_this_software_fabricates(self):
        """It is an approved product of a licensed maker."""
        assert "אינה מיוצרת כאן" in _family("mamad_door").note

    def test_a_corner_window_says_the_glass_is_structural(self):
        assert "נושא" in _family("corner").note

    def test_an_automatic_door_says_the_motor_is_bought_not_derived(self):
        assert "לא נגזרים כאן" in _family("automatic_door").note

    def test_bars_are_measured_from_the_structural_opening(self):
        assert "הפתח הבנוי" in _family("bars").note


class TestGlassIsNeverSubstitutedInSilence:
    def test_a_specification_this_installation_lacks_is_reported(self):
        """A balustrade quietly built in double glazing is the failure."""
        build = build_elements([
            Opening(name="B1", width=1500, height=1100,
                    glass_spec_id="lam-99-9"),
        ])[0]
        assert any("not in this installation's catalogue" in w
                   for w in build.warnings)

    def test_a_specification_it_has_is_used_without_comment(self):
        build = build_elements([
            Opening(name="B1", width=1500, height=1100,
                    glass_spec_id="lam-66-2"),
        ])[0]
        assert build.glass[0].build_up.id == "lam-66-2"
        assert not any("catalogue" in w for w in build.warnings)

    def test_naming_nothing_is_not_a_missing_specification(self):
        build = build_elements([Opening(name="W1", width=1500, height=1100)])[0]
        assert not any("catalogue" in w for w in build.warnings)


class TestTheSystemHouses:
    @pytest.mark.parametrize("hebrew", [
        "קליל", "אלובין", "אקסטל", "אלום גולד", "אפקס", "אלום גרף",
        "אלומיניום החולה", "מטלפרס",
    ])
    def test_the_israeli_extruders_are_listed(self, hebrew):
        assert any(m.hebrew == hebrew for m in MANUFACTURERS)

    @pytest.mark.parametrize("hebrew", [
        "שוקו", "ריינרס", "ויקונה", "קורטיזו", "אלומיל", "טכנל",
    ])
    def test_the_imported_aluminium_systems_are_listed(self, hebrew):
        assert any(m.hebrew == hebrew for m in MANUFACTURERS)

    @pytest.mark.parametrize("hebrew", ["רהאו", "וקה", "דקווניק", "סלמנדר"])
    def test_the_pvc_systems_are_listed(self, hebrew):
        """A growing share of the market, and a different trade in the same shop."""
        assert any(m.hebrew == hebrew for m in MANUFACTURERS)

    def test_local_stock_is_recorded_because_it_is_the_lead_time(self):
        local = [m.hebrew for m in MANUFACTURERS if m.local_stock]
        assert "קליל" in local
        assert "רהאו" not in local
