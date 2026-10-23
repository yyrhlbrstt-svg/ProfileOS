"""Openings that are not rectangles, which is most of the interesting ones."""

from __future__ import annotations

import math

import pytest

from profileos.core.errors import ProfileOSError
from profileos.elements.model import Opening
from profileos.elements.shapes import (
    Bending,
    Corner,
    Shape,
    arc_length,
    arc_radius,
    outline,
)


class TestArcSetOut:
    def test_the_radius_follows_the_chord_and_the_rise(self):
        """R = (c²/4 + r²)/2r — what every arch on a drawing is set out from."""
        assert arc_radius(1600.0, 400.0) == pytest.approx(1000.0)

    def test_a_half_circle_has_the_radius_of_its_half_chord(self):
        assert arc_radius(1200.0, 600.0) == pytest.approx(600.0)

    def test_a_half_circle_arc_is_half_a_circumference(self):
        assert arc_length(1200.0, 600.0) == pytest.approx(math.pi * 600.0)

    def test_a_shallow_arc_is_a_little_longer_than_its_chord(self):
        length = arc_length(2000.0, 100.0)
        assert 2000.0 < length < 2050.0

    def test_an_arc_with_no_rise_is_not_an_arc(self):
        with pytest.raises(ProfileOSError):
            arc_radius(1200.0, 0.0)


class TestAreaIsTheRealArea:
    def test_a_triangle_is_half_its_box(self):
        """Priced on its box, a gable is priced at twice what it is."""
        shaped = outline(Shape.TRIANGLE, width=2000, height=1800)
        assert shaped.area == pytest.approx(1.8)
        assert shaped.bounding_area == pytest.approx(3.6)
        assert shaped.waste_against_box == pytest.approx(50.0)

    def test_a_raked_opening_is_the_mean_of_its_two_heights(self):
        shaped = outline(Shape.RAKED, width=2000, height=1200, height_right=1800)
        assert shaped.area == pytest.approx(3.0)

    def test_a_half_round_is_the_rectangle_plus_the_semicircle(self):
        shaped = outline(Shape.HALF_ROUND, width=1200, height=1800)
        expected = (1200 * 1200 + math.pi * 600 ** 2 / 2) / 1e6
        assert shaped.area == pytest.approx(expected, abs=0.002)

    def test_a_segmental_arch_is_the_rectangle_plus_the_segment(self):
        shaped = outline(Shape.ARCHED, width=1600, height=2000, rise=400)
        radius, half = 1000.0, math.asin(0.8)
        segment = radius ** 2 * (half - math.sin(half) * math.cos(half))
        assert shaped.area == pytest.approx((1600 * 1600 + segment) / 1e6, abs=0.002)

    def test_a_circle_is_a_circle(self):
        shaped = outline(Shape.CIRCLE, width=1000, height=1000)
        assert shaped.area == pytest.approx(math.pi * 0.5 ** 2, abs=0.001)

    def test_a_rectangle_wastes_nothing_against_its_own_box(self):
        shaped = outline(Shape.RECTANGLE, width=1500, height=1200)
        assert shaped.waste_against_box == pytest.approx(0.0)


class TestCornersAndTheSaw:
    def test_a_square_corner_is_two_forty_five_degree_cuts(self):
        assert Corner("x", 90.0).mitre == pytest.approx(45.0)

    def test_a_raked_head_meets_its_two_jambs_at_different_angles(self):
        """Cutting both the same is how a raked head arrives short on one side."""
        shaped = outline(Shape.RAKED, width=2000, height=1200, height_right=1800)
        tops = [c for c in shaped.corners if "עליון" in c.name]
        assert len(tops) == 2
        assert tops[0].included != pytest.approx(tops[1].included)
        assert sum(c.included for c in tops) == pytest.approx(180.0)

    def test_the_head_of_a_raked_opening_is_the_hypotenuse(self):
        shaped = outline(Shape.RAKED, width=2000, height=1200, height_right=1800)
        assert shaped.member("משקוף משופע").length == pytest.approx(
            math.hypot(2000, 600), abs=0.1
        )

    def test_a_gable_apex_narrows_as_the_pitch_steepens(self):
        shallow = outline(Shape.TRIANGLE, width=2000, height=500)
        steep = outline(Shape.TRIANGLE, width=2000, height=2500)
        apex = lambda s: next(c for c in s.corners if c.name == "קודקוד")
        assert apex(steep).included < apex(shallow).included

    def test_a_cut_the_saw_cannot_swing_is_refused_by_name(self):
        """Discovered here rather than at the saw."""
        shaped = outline(
            Shape.TRIANGLE, width=4000, height=400, min_mitre=22.5
        )
        assert any("מתחת למינימום" in w for w in shaped.warnings)

    def test_a_shop_with_a_better_saw_may_say_so(self):
        shaped = outline(
            Shape.TRIANGLE, width=4000, height=400, min_mitre=5.0
        )
        assert not any("מתחת למינימום" in w for w in shaped.warnings)

    def test_a_rectangle_raises_nothing(self):
        assert outline(Shape.RECTANGLE, width=1500, height=1200).warnings == []


class TestBendingIsSomebodyElsesMachine:
    def test_a_curved_member_has_no_length_without_confirmed_figures(self):
        """A bar cut to a guessed developed length is a bar in the skip."""
        shaped = outline(Shape.HALF_ROUND, width=1200, height=1800)
        assert not shaped.member("קשת").is_orderable
        assert not shaped.is_orderable
        assert any("רדיוס כיפוף" in w for w in shaped.warnings)

    def test_the_computed_length_is_still_reported_as_a_figure_not_an_order(self):
        shaped = outline(Shape.HALF_ROUND, width=1200, height=1800)
        assert any("אין להזמין לפיו" in w for w in shaped.warnings)

    def test_confirmed_figures_make_it_orderable(self):
        bending = Bending(
            minimum_radius=400.0, grip_allowance=250.0, source="כופף הרים",
        )
        shaped = outline(
            Shape.ARCHED, width=1600, height=2000, rise=400, bending=bending
        )
        member = shaped.member("קשת")
        assert member.is_orderable
        assert shaped.is_orderable

    def test_the_grip_the_bender_needs_is_added_to_what_is_ordered(self):
        bending = Bending(
            minimum_radius=400.0, grip_allowance=250.0, source="כופף הרים",
        )
        shaped = outline(
            Shape.ARCHED, width=1600, height=2000, rise=400, bending=bending
        )
        bare = arc_length(1600.0, 400.0)
        assert shaped.member("קשת").length == pytest.approx(bare + 500.0, abs=0.2)

    def test_a_radius_tighter_than_the_profile_stands_is_refused(self):
        bending = Bending(
            minimum_radius=900.0, grip_allowance=250.0, source="כופף הרים",
        )
        shaped = outline(
            Shape.HALF_ROUND, width=1200, height=1800, bending=bending
        )
        assert not shaped.member("קשת").is_orderable
        assert any("ייסדק" in w for w in shaped.warnings)

    def test_a_partial_bending_record_is_not_a_confirmed_one(self):
        assert not Bending(minimum_radius=400.0).is_confirmed
        assert not Bending(
            minimum_radius=400.0, grip_allowance=250.0
        ).is_confirmed

    def test_a_circle_is_one_bar_closed_on_itself(self):
        bending = Bending(
            minimum_radius=100.0, grip_allowance=200.0, source="כופף",
        )
        shaped = outline(
            Shape.CIRCLE, width=1000, height=1000, bending=bending
        )
        assert len(shaped.members) == 1
        assert shaped.members[0].length == pytest.approx(
            math.pi * 1000.0 + 400.0, abs=0.2
        )


class TestRefusals:
    def test_a_raked_opening_needs_its_second_height(self):
        with pytest.raises(ProfileOSError):
            outline(Shape.RAKED, width=2000, height=1200)

    def test_an_arch_needs_a_rise(self):
        with pytest.raises(ProfileOSError):
            outline(Shape.ARCHED, width=1600, height=2000)

    def test_an_arch_taller_than_its_opening_is_refused(self):
        with pytest.raises(ProfileOSError):
            outline(Shape.ARCHED, width=1600, height=300, rise=400)

    def test_a_negative_size_is_refused(self):
        with pytest.raises(ProfileOSError):
            outline(Shape.RECTANGLE, width=-1, height=1200)

    def test_a_circle_given_unequal_sides_takes_the_smaller_and_says_so(self):
        shaped = outline(Shape.CIRCLE, width=1200, height=900)
        assert shaped.width == pytest.approx(900.0)
        assert any("הקטן" in w for w in shaped.warnings)


class TestOnTheOpeningItself:
    def test_a_plain_opening_is_still_a_rectangle(self):
        opening = Opening(name="W1", width=2000, height=1800)
        assert not opening.is_shaped
        assert opening.outline is None
        assert opening.area == pytest.approx(3.6)

    def test_a_shaped_opening_is_priced_on_its_real_area(self):
        opening = Opening(name="G1", width=2000, height=1800, shape="triangle")
        assert opening.is_shaped
        assert opening.area == pytest.approx(1.8)

    def test_an_arched_opening_carries_its_rise(self):
        opening = Opening(
            name="A1", width=1600, height=2000, shape="arched", rise=400
        )
        assert opening.outline.member("סף").length == pytest.approx(1600.0)
        assert opening.area < 3.2

    def test_a_shape_that_cannot_be_worked_out_does_not_break_the_list(self):
        """A bad shape must not take a whole schedule down with it."""
        opening = Opening(name="A1", width=1600, height=2000, shape="arched")
        assert opening.outline is None
        assert opening.area == pytest.approx(3.2)

    def test_a_schedule_of_shaped_openings_totals_their_real_areas(self):
        from profileos.elements import ElementSchedule

        schedule = ElementSchedule(name="s", openings=[
            Opening(name="W1", width=2000, height=1800),
            Opening(name="G1", width=2000, height=1800, shape="triangle"),
        ])
        assert sum(o.area for o in schedule.openings) == pytest.approx(5.4)


class TestTheGlassConsequence:
    def test_a_shaped_opening_says_its_panes_are_not_rectangles(self):
        """A glazier sent a width and a height for an arch sends back a rectangle."""
        from profileos.elements import Opening, build_elements
        from profileos.glazing.order import order_from_builds

        builds = build_elements([
            Opening(name="A1", width=1600, height=2000, shape="arched", rise=400),
        ])
        order = order_from_builds(builds, supplier="זכוכית הרים")
        assert "תבנית" in order.note
        assert all("לפי תבנית" in pane.note for pane in order.panes)

    def test_a_plain_opening_carries_no_such_note(self):
        from profileos.elements import Opening, build_elements
        from profileos.glazing.order import order_from_builds

        builds = build_elements([Opening(name="W1", width=1500, height=1200)])
        order = order_from_builds(builds, supplier="זכוכית הרים")
        assert "תבנית" not in order.note
