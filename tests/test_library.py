"""The library a fabricator searches: profiles to open, openings to build."""

from __future__ import annotations

import pytest

from profileos.core.config import bundled_data_dir, samples_dir
from profileos.elements import Cell, ElementBuilder, ElementKind, Opening, OpeningType, Sash
from profileos.library import (
    FAMILIES,
    catalogue_size,
    families,
    folder_profiles,
    opening,
    opening_library,
    parse_query,
    profile_library,
    sample_profiles,
    search_openings,
    search_profiles,
    to_millimetres,
)


class TestBundledData:
    def test_the_data_ships_inside_the_package(self):
        """An installed copy has no repository root to look in."""
        from profileos.core.config import PACKAGE_ROOT

        assert bundled_data_dir() == PACKAGE_ROOT / "data"

    def test_the_samples_are_there(self):
        assert samples_dir().is_dir()
        assert (samples_dir() / "mullion_mb70.dxf").is_file()


class TestProfileLibrary:
    def test_every_sample_is_listed_and_present(self):
        profiles = sample_profiles()
        assert profiles
        assert all(profile.exists for profile in profiles)

    def test_samples_are_named_in_hebrew(self):
        for profile in sample_profiles():
            assert any("֐" <= ch <= "ת" for ch in profile.hebrew)

    def test_a_drawing_folder_becomes_a_library(self, tmp_path):
        (tmp_path / "KLIL-7300-frame.dxf").write_text("0\nEOF\n")
        (tmp_path / "notes.txt").write_text("not a drawing")
        found = folder_profiles(tmp_path)
        assert [p.profile_id for p in found] == ["KLIL-7300-frame"]
        assert found[0].origin == "התיקייה שלך"

    def test_searching_matches_hebrew_and_the_file_name(self):
        assert search_profiles("זקף")
        assert search_profiles("mullion")
        assert not search_profiles("קורקינט")

    def test_an_empty_search_returns_everything(self):
        assert len(search_profiles("")) == len(profile_library())


class TestReadingWhatWasTyped:
    """The search has to hear a size the way the trade says it."""

    def test_four_digits_are_millimetres(self):
        assert to_millimetres("6000") == 6000

    def test_two_or_three_digits_are_centimetres(self):
        """Nobody orders a 240 millimetre window; everybody orders a 240 one."""
        assert to_millimetres("240") == 2400
        assert to_millimetres("90") == 900

    def test_a_decimal_point_means_metres(self):
        assert to_millimetres("2.4") == 2400
        assert to_millimetres("2,4") == 2400

    def test_an_explicit_unit_always_wins(self):
        assert to_millimetres("3", "מטר") == 3000
        assert to_millimetres("240", "cm") == 2400

    @pytest.mark.parametrize("text", ["6000x2200", "6000/2200", "6000 על 2200", "6000×2200"])
    def test_a_pair_is_read_as_width_and_height(self, text):
        query = parse_query(text)
        assert (query.width, query.height) == (6000, 2200)

    def test_a_small_whole_number_is_a_leaf_count(self):
        query = parse_query("הזזה 3")
        assert query.leaves == 3
        assert query.width is None

    def test_a_leaf_count_is_still_a_leaf_count_when_it_is_said_in_full(self):
        query = parse_query("הזזה 4 כנפיים 6000")
        assert query.leaves == 4
        assert query.width == 6000

    def test_a_series_named_by_number_is_not_mistaken_for_a_size(self):
        query = parse_query("הזזה 3 קליל 9000")
        assert query.system_id == "klil-9000"
        assert query.leaves == 3
        assert query.width is None

    def test_every_way_of_naming_one_series_lands_on_it(self):
        for text in ("קליל 7300", "בלגי קליל 7300", "7300"):
            assert parse_query(text).system_id == "klil-7300"


class TestOpeningLibrary:
    def test_the_shop_gets_the_types_it_actually_makes(self):
        names = {fam.family_id for fam in families()}
        for expected in (
            "sliding", "lift_slide", "casement", "tilt_turn", "top_hung",
            "door_single", "door_double", "shopfront", "curtain_wall",
            "partition", "mamad", "louvre", "skylight",
        ):
            assert expected in names

    def test_the_catalogue_is_not_a_list_of_sixteen(self):
        assert catalogue_size() > 100_000

    def test_browsing_shows_every_type_before_a_second_of_any(self):
        """A list that opens with eleven sliders looks like a library of sliders."""
        first = [preset.family_id for preset in opening_library()[: len(FAMILIES)]]
        assert len(set(first)) == len(FAMILIES)

    def test_a_size_nobody_stored_is_still_found(self):
        found = search_openings("הזזה 5350/2130")
        assert found
        assert all((p.width, p.height) == (5350, 2130) for p in found)

    def test_a_six_metre_slider_is_there_at_every_leaf_count(self):
        leaves = {p.columns for p in search_openings("הזזה 6000") if p.family_id == "sliding"}
        assert leaves == {2, 3, 4, 6}

    def test_a_type_is_not_offered_at_a_size_it_is_the_wrong_choice_for(self):
        """A six-metre tilt-turn leaf does not exist, so it is not offered."""
        found = search_openings("נטוי 6000")
        assert all(p.family_id != "tilt_turn" for p in found)

    def test_words_match_in_any_order(self):
        assert search_openings("הזזה 4 כנפיים") == search_openings("4 כנפיים הזזה")

    def test_a_search_that_finds_nothing_says_so_rather_than_guessing(self):
        assert search_openings("קורקינט") == []

    def test_a_series_carries_through_to_the_opening(self):
        found = search_openings("הזזה 6000 קליל 9000")
        assert found
        assert all(p.system_id == "klil-9000" for p in found)
        assert "קליל" in found[0].system_hebrew

    def test_a_quantity_can_be_said_in_the_same_breath(self):
        found = search_openings("הזזה 2400 1400 12")
        assert found and all(p.quantity == 12 for p in found)

    def test_an_opening_is_rebuilt_from_its_identifier(self):
        preset = search_openings("הזזה 3 6000/2200")[0]
        again = opening(preset.preset_id)
        assert again is not None
        assert (again.width, again.height, again.columns) == (
            preset.width, preset.height, preset.columns
        )
        assert opening("no-such-thing") is None

    @pytest.mark.parametrize("fam", FAMILIES, ids=lambda f: f.family_id)
    def test_every_type_builds_across_its_whole_envelope(self, fam):
        """A type offered at a size it cannot be made at is worse than none."""
        from profileos.library import _make

        for leaves in fam.leaves:
            for width in (fam.min_width, fam.widths[-1], fam.max_width):
                for height in (fam.min_height, fam.heights[-1], fam.max_height):
                    preset = _make(fam, leaves, width, height)
                    unit = Opening(
                        name=preset.preset_id,
                        kind=ElementKind(preset.kind),
                        width=preset.width,
                        height=preset.height,
                        glass_spec_id=preset.glass,
                    )
                    unit.divide_evenly(preset.columns, preset.rows)
                    sash_type = OpeningType(preset.sash_type)
                    if sash_type is not OpeningType.FIXED:
                        unit.set_cell(Cell(
                            column=min(preset.sash_column, unit.column_count - 1),
                            row=min(preset.sash_row, unit.row_count - 1),
                            sash=Sash(opening_type=sash_type),
                        ))
                    build = ElementBuilder().build(unit, sill_height=preset.sill)
                    assert build.cuts and build.glass

    def test_a_family_word_is_never_mistaken_for_a_series(self):
        """Classifying a series must not make its family word name it."""
        from profileos.systems import DIRECTORY, SystemFamily

        DIRECTORY.classify("klil-7000", SystemFamily.SLIDING, source="בדיקה")
        assert parse_query("הזזה 2400/1400 קליל 7300").system_id == "klil-7300"
        assert parse_query("הזזה 6000").system_id == ""

    def test_a_series_known_by_its_hebrew_alias_is_found(self):
        assert parse_query("אלומייל הזזה").system_id == "alubin-alumeal"
