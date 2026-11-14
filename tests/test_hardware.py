"""Hardware chosen by what the sash weighs, not by what is usually used."""

from __future__ import annotations

import json

import pytest

from profileos.core.errors import ProfileOSError
from profileos.hardware import (
    REQUIREMENTS,
    Confidence,
    HardwareLibrary,
    Part,
    PartKind,
    sash_mass,
    template,
)


def _gear(code="R-100", kg=100.0, width=1200.0, height=2200.0,
          confidence=Confidence.CATALOGUE, price=200.0, **extra):
    return Part(
        code=code, hebrew=code, kind=PartKind.TILT_TURN_GEAR, maker="רוטו",
        max_sash_kg=kg, max_width=width, max_height=height,
        opening_types=("tilt_turn",), price=price,
        confidence=confidence, source="טבלת עומסים" if confidence
        is Confidence.CATALOGUE else "", **extra,
    )


def _trim(kind: PartKind, code: str, price: float = 10.0) -> Part:
    return Part(
        code=code, hebrew=code, kind=kind, price=price,
        confidence=Confidence.CATALOGUE, source="מחירון",
    )


def _full_library() -> HardwareLibrary:
    library = HardwareLibrary()
    library.add(_gear("R-100", 100.0))
    library.add(_gear("R-130", 130.0, width=1400.0, height=2400.0, price=300.0))
    for kind, code in (
        (PartKind.HANDLE, "H-1"), (PartKind.CORNER_DRIVE, "CD-1"),
        (PartKind.STRIKE_PLATE, "SP-1"), (PartKind.ESPAGNOLETTE, "ESP-1"),
    ):
        library.add(_trim(kind, code))
    return library


class TestWhatALeafWeighs:
    def test_the_frame_is_counted_as_well_as_the_glass(self):
        """A rating checked against the glass alone is checked short."""
        glass_only = 1.0 * 1.4 * 25.0
        assert sash_mass(1000, 1400) > glass_only

    def test_a_bigger_leaf_weighs_more(self):
        assert sash_mass(1400, 2200) > sash_mass(900, 1200)

    def test_heavier_glass_weighs_more(self):
        assert sash_mass(1000, 1400, 40.0) > sash_mass(1000, 1400, 25.0)

    def test_a_leaf_of_nothing_is_refused(self):
        with pytest.raises(ProfileOSError):
            sash_mass(0, 1400)


class TestChoosing:
    def test_the_lightest_part_that_carries_it_is_chosen_not_the_largest(self):
        """Over-specifying costs money on every unit."""
        library = _full_library()
        chosen = library.choose(
            PartKind.TILT_TURN_GEAR, width=900, height=1300, mass=40,
            opening_type="tilt_turn",
        )
        assert chosen.code == "R-100"

    def test_a_heavier_leaf_steps_up(self):
        library = _full_library()
        chosen = library.choose(
            PartKind.TILT_TURN_GEAR, width=1300, height=2300, mass=115,
            opening_type="tilt_turn",
        )
        assert chosen.code == "R-130"

    def test_nothing_is_returned_when_nothing_can_carry_it(self):
        """Not the biggest available — nothing."""
        library = _full_library()
        assert library.choose(
            PartKind.TILT_TURN_GEAR, width=1300, height=2300, mass=200,
            opening_type="tilt_turn",
        ) is None

    def test_an_unrated_part_never_carries_a_load(self):
        library = HardwareLibrary()
        library.add(Part("X", "ציר", PartKind.HINGE, opening_types=("casement",)))
        assert library.choose(
            PartKind.HINGE, width=800, height=1200, mass=30,
            opening_type="casement",
        ) is None

    def test_an_unrated_part_is_fine_where_no_load_is_carried(self):
        library = HardwareLibrary()
        library.add(Part("H", "ידית", PartKind.HANDLE))
        assert library.choose(
            PartKind.HANDLE, width=800, height=1200, mass=30,
        ) is not None

    def test_a_part_for_another_opening_type_is_not_offered(self):
        library = _full_library()
        assert library.choose(
            PartKind.TILT_TURN_GEAR, width=900, height=1300, mass=40,
            opening_type="casement",
        ) is None


class TestSayingWhyNot:
    def test_a_leaf_too_wide_is_told_it_is_too_wide(self):
        """Not 'too heavy' — that sends somebody to the wrong chart."""
        library = _full_library()
        selection = library.select_for(
            opening_type="tilt_turn", width=2000, height=2400,
        )
        assert any("גדולה" in reason for reason in selection.unmet)

    def test_a_leaf_too_heavy_is_told_it_is_too_heavy(self):
        library = _full_library()
        selection = library.select_for(
            opening_type="tilt_turn", width=1300, height=2300,
            glass_mass_per_m2=90.0,
        )
        assert any("ק״ג" in reason for reason in selection.unmet)

    def test_an_empty_library_says_to_enter_a_catalogue(self):
        selection = HardwareLibrary().select_for(
            opening_type="casement", width=800, height=1200,
        )
        assert selection.unmet
        assert all("הזינו" in reason for reason in selection.unmet)

    def test_a_part_of_the_right_size_with_no_rating_says_so(self):
        library = HardwareLibrary()
        library.add(Part(
            "X", "ציר", PartKind.HINGE, max_width=1200, max_height=2000,
            opening_types=("casement",),
        ))
        selection = library.select_for(
            opening_type="casement", width=800, height=1200,
        )
        assert any("דירוג עומס" in reason for reason in selection.unmet)


class TestSelectingForALeaf:
    def test_everything_a_tilt_turn_needs_is_chosen(self):
        selection = _full_library().select_for(
            opening_type="tilt_turn", width=1000, height=1600,
        )
        assert selection.is_complete
        kinds = {part.kind for part, _quantity in selection.parts}
        assert kinds == {kind for kind, _n in REQUIREMENTS["tilt_turn"]}

    def test_the_quantities_are_the_ones_it_is_sold_in(self):
        selection = _full_library().select_for(
            opening_type="tilt_turn", width=1000, height=1600,
        )
        drives = next(
            quantity for part, quantity in selection.parts
            if part.kind is PartKind.CORNER_DRIVE
        )
        assert drives == 2

    def test_a_fixed_light_needs_nothing(self):
        selection = _full_library().select_for(
            opening_type="fixed", width=1000, height=1600,
        )
        assert selection.is_complete and not selection.parts

    def test_an_unknown_opening_type_is_refused_by_name(self):
        with pytest.raises(ProfileOSError):
            _full_library().select_for(
                opening_type="teleporter", width=1000, height=1600,
            )

    def test_it_may_only_be_ordered_when_the_ratings_are_real(self):
        good = _full_library()
        assert good.select_for(
            opening_type="tilt_turn", width=1000, height=1600
        ).may_be_ordered

        typical = HardwareLibrary()
        typical.add(Part(
            "T", "מנגנון", PartKind.TILT_TURN_GEAR, max_sash_kg=100,
            opening_types=("tilt_turn",), confidence=Confidence.TYPICAL,
            source="ניסיון",
        ))
        for kind, code in (
            (PartKind.HANDLE, "H"), (PartKind.CORNER_DRIVE, "C"),
            (PartKind.STRIKE_PLATE, "S"),
        ):
            typical.add(_trim(kind, code))
        selection = typical.select_for(
            opening_type="tilt_turn", width=1000, height=1600
        )
        assert selection.is_complete
        assert not selection.may_be_ordered
        assert selection.warnings

    def test_a_very_heavy_leaf_suggests_splitting_the_opening(self):
        library = _full_library()
        library.add(_gear("R-200", 250.0, width=2000.0, height=3000.0))
        selection = library.select_for(
            opening_type="tilt_turn", width=1600, height=2600,
            glass_mass_per_m2=45.0,
        )
        assert any("לפצל" in warning for warning in selection.warnings)

    def test_the_price_is_per_leaf(self):
        selection = _full_library().select_for(
            opening_type="tilt_turn", width=1000, height=1600,
        )
        assert selection.price == pytest.approx(200 + 10 + 20 + 20)


class TestTheLibraryFile:
    def test_a_load_bearing_part_without_a_source_is_refused(self):
        library = HardwareLibrary()
        with pytest.raises(ProfileOSError):
            library.add(Part(
                "X", "ציר", PartKind.HINGE, max_sash_kg=80,
                opening_types=("casement",),
            ))

    def test_it_survives_the_program_closing(self, tmp_path):
        path = tmp_path / "hardware.json"
        HardwareLibrary(path).add(_gear())
        assert len(HardwareLibrary(path)) == 1

    def test_an_unreadable_file_does_not_empty_the_shop(self, tmp_path):
        path = tmp_path / "hardware.json"
        path.write_text("{ not json", encoding="utf-8")
        assert len(HardwareLibrary(path)) == 0

    def test_one_bad_row_does_not_lose_the_others(self, tmp_path):
        path = tmp_path / "hardware.json"
        path.write_text(json.dumps({"parts": [
            {"code": "broken"},
            {"code": "H", "hebrew": "ידית", "kind": "handle"},
        ]}, ensure_ascii=False), encoding="utf-8")
        assert len(HardwareLibrary(path)) == 1

    def test_the_template_names_every_kind_of_part(self, tmp_path):
        form = template("רוטו")
        assert set(form["_סוגים"]) == {kind.value for kind in PartKind}

    def test_searching_finds_by_code_maker_or_name(self):
        library = _full_library()
        assert library.search("R-130")
        assert library.search("רוטו")
        assert not library.search("קורקינט")
