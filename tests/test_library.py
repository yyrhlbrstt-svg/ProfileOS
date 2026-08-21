"""The library a fabricator searches: profiles to open, openings to build."""

from __future__ import annotations

import pytest

from profileos.core.config import bundled_data_dir, samples_dir
from profileos.elements import Cell, ElementBuilder, ElementKind, Opening, OpeningType, Sash
from profileos.library import (
    OPENINGS,
    folder_profiles,
    opening,
    opening_library,
    profile_library,
    sample_profiles,
    search_openings,
    search_profiles,
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


class TestOpeningLibrary:
    def test_the_shop_gets_the_openings_it_actually_makes(self):
        names = {preset.preset_id for preset in opening_library()}
        for expected in ("sliding_2", "tilt_turn", "entry_door", "curtain_wall", "mamad_window"):
            assert expected in names

    def test_words_match_in_any_order(self):
        """Order of words never changes the answer; a digit also matches a size."""
        assert search_openings("הזזה 3") == search_openings("3 הזזה")
        found = [preset.preset_id for preset in search_openings("הזזה 3")]
        assert "sliding_3" in found
        assert "tilt_turn" not in found

    def test_a_search_that_finds_nothing_says_so_rather_than_guessing(self):
        assert search_openings("קורקינט") == []

    @pytest.mark.parametrize("preset", OPENINGS, ids=lambda p: p.preset_id)
    def test_every_preset_builds(self, preset):
        """A preset that cannot be made is worse than no preset at all."""
        unit = Opening(
            name=preset.preset_id,
            kind=ElementKind(preset.kind),
            width=preset.width,
            height=preset.height,
            quantity=preset.quantity,
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
        assert build.cuts
        assert build.glass

    def test_a_preset_is_found_by_its_identifier(self):
        assert opening("sliding_2").hebrew.startswith("חלון הזזה")
        assert opening("no_such_thing") is None
