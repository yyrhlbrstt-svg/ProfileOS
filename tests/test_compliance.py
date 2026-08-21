"""What the window has to satisfy — heat, sound, wind — and how honestly."""

from __future__ import annotations

import math

import pytest

from profileos.compliance import (
    STANDARDS,
    Confidence,
    FacadeZone,
    FrameClass,
    SealClass,
    Site,
    Spacer,
    Terrain,
    Verdict,
    check_compliance,
    design_pressure,
    estimate_acoustic,
    pane_reduction,
    peak_velocity_pressure,
    required_classes,
    standard,
    standards_for,
    window_u_value,
)
from profileos.core.errors import ProfileOSError
from profileos.elements import Cell, ElementBuilder, Opening, OpeningType, Sash


def _build(width=1800, height=1400, columns=2, glass="dgu-6-16-6", sash=None):
    opening = Opening(name="W-01", width=width, height=height, glass_spec_id=glass)
    opening.divide_evenly(columns, 1)
    if sash:
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType(sash))))
    return ElementBuilder().build(opening, sill_height=900)


class TestStandards:
    def test_the_standards_a_facade_job_meets_are_named(self):
        numbers = {entry.number for entry in STANDARDS}
        assert "ת״י 1068" in numbers
        assert "ת״י 1099" in numbers
        assert "ת״י 414" in numbers

    def test_each_one_says_what_it_cannot_answer(self):
        assert all(entry.not_covered for entry in STANDARDS)

    def test_a_standard_is_found_however_it_is_typed(self):
        assert standard("1068") is standard("ת״י 1068")
        assert standard('ת"י 1068') is standard("ת״י 1068")
        assert standard("no such thing") is None

    def test_searching_finds_by_subject(self):
        assert standard("414") in standards_for("רוח")

    def test_nothing_unconfirmed_claims_to_be_certifiable(self):
        for entry in STANDARDS:
            if entry.confidence is not Confidence.CONFIRMED:
                assert not entry.confidence.may_be_certified


class TestWholeWindowU:
    def test_the_window_is_worse_than_its_glass(self):
        """The frame and the edge are the parts a glass quote leaves out."""
        result = window_u_value(_build(), frame_class=FrameClass.THERMAL_BREAK)
        assert result.u_window > result.u_glass

    def test_more_divisions_make_a_worse_window(self):
        plain = window_u_value(_build(columns=1), frame_class=FrameClass.THERMAL_BREAK)
        divided = window_u_value(_build(columns=6), frame_class=FrameClass.THERMAL_BREAK)
        assert divided.u_window > plain.u_window
        assert divided.frame_fraction > plain.frame_fraction

    def test_a_small_window_is_worse_than_a_large_one_of_the_same_build(self):
        small = window_u_value(_build(600, 600, 1), frame_class=FrameClass.THERMAL_BREAK)
        large = window_u_value(_build(2400, 2000, 1), frame_class=FrameClass.THERMAL_BREAK)
        assert small.u_window > large.u_window

    def test_a_thermal_break_beats_a_plain_frame(self):
        plain = window_u_value(_build(), frame_class=FrameClass.PLAIN)
        broken = window_u_value(_build(), frame_class=FrameClass.THERMAL_BREAK)
        assert broken.u_window < plain.u_window

    def test_a_warm_edge_beats_an_aluminium_spacer(self):
        cold = window_u_value(_build(), spacer=Spacer.ALUMINIUM)
        warm = window_u_value(_build(), spacer=Spacer.WARM_EDGE)
        assert warm.u_window < cold.u_window

    def test_the_area_weighting_is_the_one_the_standard_uses(self):
        result = window_u_value(_build(), frame_class=FrameClass.THERMAL_BREAK)
        expected = (
            result.glass_area * result.u_glass
            + result.frame_area * result.u_frame
            + result.glass_perimeter * result.psi
        ) / result.total_area
        assert result.u_window == pytest.approx(expected)

    def test_the_areas_add_up_to_the_opening(self):
        result = window_u_value(_build())
        assert result.glass_area + result.frame_area == pytest.approx(result.total_area)

    def test_a_supplied_frame_figure_is_used_and_said_to_be(self):
        result = window_u_value(_build(), u_frame=1.4)
        assert result.u_frame == 1.4
        assert "הוזן" in result.source

    def test_typical_frame_values_never_claim_to_be_the_makers(self):
        assert "לא מנתוני היצרן" in window_u_value(_build()).source


class TestAcoustics:
    def test_a_thicker_pane_stops_more_noise(self):
        assert pane_reduction(10) > pane_reduction(6) > pane_reduction(4)

    def test_the_mass_law_is_six_decibels_per_doubling(self):
        assert pane_reduction(8) - pane_reduction(4) == pytest.approx(6.0)

    def test_two_identical_panes_are_penalised_for_dipping_together(self):
        symmetric = estimate_acoustic(_build(glass="dgu-6-16-6"))
        asymmetric = estimate_acoustic(_build(glass="dgu-6-16-4"))
        assert asymmetric.r_glass > symmetric.r_glass
        assert any("עובי זהה" in note for note in symmetric.notes)

    def test_a_slider_is_the_worst_thing_in_the_catalogue_for_noise(self):
        slider = estimate_acoustic(_build(sash="sliding"))
        casement = estimate_acoustic(_build(sash="casement"))
        assert slider.r_window < casement.r_window

    def test_the_seal_is_worth_decibels(self):
        tight = estimate_acoustic(_build(), seal=SealClass.TRIPLE)
        loose = estimate_acoustic(_build(), seal=SealClass.NONE)
        assert tight.r_window > loose.r_window

    def test_a_built_in_shutter_box_gives_back_what_the_glass_bought(self):
        from profileos.accessories import AccessorySpec, ShutterSpec

        plain = _build()
        with_box = _build()
        with_box.opening.metadata["accessories"] = AccessorySpec(
            shutter=ShutterSpec()
        ).to_dict()
        assert estimate_acoustic(with_box).r_window < estimate_acoustic(plain).r_window

    def test_the_estimate_never_pretends_to_be_a_test_report(self):
        assert "דוח בדיקה" in estimate_acoustic(_build()).source


class TestWind:
    def test_pressure_grows_with_height(self):
        low = peak_velocity_pressure(30, 5, Terrain.SUBURBAN)
        high = peak_velocity_pressure(30, 50, Terrain.SUBURBAN)
        assert high > low

    def test_the_open_coast_is_windier_than_the_middle_of_a_city(self):
        sea = peak_velocity_pressure(30, 10, Terrain.SEA)
        urban = peak_velocity_pressure(30, 10, Terrain.URBAN)
        assert sea > urban

    def test_pressure_grows_with_the_square_of_the_velocity(self):
        single = peak_velocity_pressure(20, 10, Terrain.OPEN)
        double = peak_velocity_pressure(40, 10, Terrain.OPEN)
        assert double / single == pytest.approx(4.0, rel=1e-6)

    def test_a_corner_takes_far_more_than_the_middle_of_the_wall(self):
        field = design_pressure(30, zone=FacadeZone.FIELD)
        corner = design_pressure(30, zone=FacadeZone.CORNER)
        assert corner.pressure > field.pressure * 1.5

    def test_a_pressure_with_no_source_is_not_something_to_build_to(self):
        assert not design_pressure(30).is_verified
        assert design_pressure(30, source="ת״י 414 מפה, אזור א׳").is_verified

    def test_a_velocity_of_nothing_is_refused(self):
        with pytest.raises(ProfileOSError):
            design_pressure(0)

    def test_the_classes_are_what_to_ask_for_not_what_was_achieved(self):
        classes = required_classes(design_pressure(30, source="map"))
        assert any("מעבדה" in note for note in classes.notes)

    def test_a_harder_case_asks_for_a_higher_class(self):
        easy = required_classes(design_pressure(25, height=6, zone=FacadeZone.FIELD))
        hard = required_classes(design_pressure(35, height=60, zone=FacadeZone.CORNER))
        assert hard.pressure_pa > easy.pressure_pa
        assert hard.air == "Class 4"


class TestTheWholeSheet:
    def test_a_requirement_that_is_met_passes_and_one_that_is_not_fails(self):
        met = check_compliance(_build(), Site(required_u=3.0))
        missed = check_compliance(_build(), Site(required_u=1.0))
        assert any(f.verdict is Verdict.PASS for f in met.findings)
        assert missed.failures

    def test_with_no_requirement_the_number_is_reported_not_judged(self):
        report = check_compliance(_build(), Site())
        thermal = [f for f in report.findings if f.subject == "בידוד תרמי"]
        assert thermal and thermal[0].verdict is Verdict.CHECK

    def test_nothing_is_certifiable_while_a_figure_is_unconfirmed(self):
        """The whole point: no compliance statement resting on a typical value."""
        report = check_compliance(_build(), Site(required_u=3.0))
        assert not report.may_be_certified

    def test_unsafe_glazing_fails_against_the_glazing_standard(self):
        """A pane a person can walk into, specified as ordinary float."""
        from profileos.glazing import make_double_glazing

        plain = make_double_glazing(6.0, 16.0, 6.0, toughened=False)
        opening = Opening(name="W-01", width=2400, height=2400, glass_spec_id=plain.id)
        opening.divide_evenly(1, 1)
        build = ElementBuilder(glass_catalogue={plain.id: plain}).build(
            opening, sill_height=0
        )
        report = check_compliance(build)
        failures = [f for f in report.failures if f.subject == "זיגוג בטיחותי"]
        assert failures
        assert failures[0].citation == "ת״י 1099"

    def test_a_protected_space_is_never_answered_by_calculation(self):
        opening = Opening(name="חלון ממ״ד", width=1000, height=1000)
        opening.divide_evenly(1, 1)
        build = ElementBuilder().build(opening, sill_height=1100)
        report = check_compliance(build)
        assert any("פיקוד העורף" in f.text for f in report.findings)

    def test_the_verdict_says_what_to_do_next(self):
        assert "לאימות" in check_compliance(_build(), Site()).verdict()

    def test_every_finding_carries_how_far_it_can_be_trusted(self):
        report = check_compliance(_build(), Site(required_u=2.0, required_rw=30))
        assert all(isinstance(f.confidence, Confidence) for f in report.findings)
