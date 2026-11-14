"""The order that goes to the glazier, and what it must not get wrong."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.elements import Opening, build_elements
from profileos.glazing.order import (
    LIFTING_GEAR_KG,
    TWO_PERSON_LIFT_KG,
    GlassOrder,
    OrderedPane,
    order_from_builds,
    render_glass_order,
    write_glass_order,
)


@pytest.fixture
def builds():
    return build_elements([
        Opening(name="W1", width=1500, height=1400, quantity=2),
        Opening(name="D1", width=1000, height=2200),
        Opening(name="W4", width=2400, height=1600),
    ])


@pytest.fixture
def order(builds) -> GlassOrder:
    return order_from_builds(
        builds, job_id="2026-114", job_name="וילה",
        supplier="זכוכית הרים", wanted_by=date.today() + timedelta(days=12),
    )


class TestBuildingTheOrder:
    def test_an_element_ordered_twice_needs_two_sets_of_panes(self, order):
        """A job that arrives three windows short starts exactly here."""
        assert order.pane_count == 4

    def test_every_pane_carries_its_make_up_in_words(self, order):
        assert all(pane.build_up for pane in order.panes)

    def test_the_area_totals_across_quantities(self, order):
        by_hand = sum(pane.area * pane.quantity for pane in order.panes)
        assert order.total_area == pytest.approx(round(by_hand, 2))

    def test_the_mass_of_each_pane_is_carried_for_the_people_lifting_it(self, order):
        assert all(pane.mass_each and pane.mass_each > 0 for pane in order.panes)

    def test_a_pane_beyond_one_person_says_so(self):
        pane = OrderedPane(mark="W1", width=1500.0, height=1400.0,
                           mass_each=TWO_PERSON_LIFT_KG + 1)
        assert pane.lift == "שני אנשים"

    def test_a_pane_beyond_two_people_says_that_instead(self):
        pane = OrderedPane(mark="W4", width=2400.0, height=1600.0,
                           mass_each=LIFTING_GEAR_KG + 1)
        assert pane.lift == "ציוד הרמה"

    def test_a_pane_within_one_person_says_nothing(self):
        assert OrderedPane(mark="W9", mass_each=8.0).lift == ""

    def test_the_safety_reason_is_in_the_language_of_the_document(self, order):
        """A Hebrew order with an English clause on it is a half-finished order."""
        reasons = {pane.safety_reason for pane in order.panes if pane.safety_reason}
        assert reasons
        assert not any(
            any("a" <= character.lower() <= "z" for character in reason)
            for reason in reasons
        )


class TestWhatItRefusesToGuess:
    def test_unconfirmed_series_figures_leave_the_sizes_blank(self, builds):
        """A pane four millimetres too big is paid for twice."""
        order = order_from_builds(
            builds, supplier="זכוכית הרים", sizes_confirmed=False,
            provisional_reason="הסדרה טרם אושרה",
        )
        assert all(not pane.is_sized for pane in order.panes)
        assert not order.may_be_sent
        assert any("אין מידת הזמנה" in p for p in order.problems())

    def test_the_provisional_reason_reaches_the_document(self, builds):
        order = order_from_builds(
            builds, supplier="ס", sizes_confirmed=False,
            provisional_reason="הסדרה טרם אושרה",
        )
        assert "הסדרה טרם אושרה" in render_glass_order(order)

    def test_an_unsized_pane_contributes_no_area_rather_than_a_guess(self):
        order = GlassOrder(supplier="ס")
        order.panes = [OrderedPane(mark="W1", quantity=2)]
        assert order.total_area == pytest.approx(0.0)


class TestChecking:
    def test_safety_glass_required_but_not_specified_stops_the_order(self, order):
        assert not order.may_be_sent
        assert any("זכוכית בטיחות" in p for p in order.problems())

    def test_a_safety_specification_satisfies_the_requirement(self):
        order = GlassOrder(supplier="ס")
        order.panes = [OrderedPane(
            mark="W1", width=1000.0, height=1000.0,
            safety_reason="זיגוג בכנף דלת", toughened=True,
        )]
        assert order.may_be_sent

    def test_no_supplier_is_a_problem(self):
        order = GlassOrder()
        order.panes = [OrderedPane(mark="W1", width=1.0, height=1.0)]
        assert any("ספק" in p for p in order.problems())

    def test_a_delivery_date_before_the_order_date_is_refused(self):
        order = GlassOrder(
            supplier="ס", issued=date(2026, 8, 27), wanted_by=date(2026, 8, 20)
        )
        order.panes = [OrderedPane(mark="W1", width=1.0, height=1.0)]
        assert any("מועד האספקה" in p for p in order.problems())

    def test_an_empty_order_says_so_rather_than_passing(self):
        assert GlassOrder(supplier="ס").problems() == ["אין שמשות בהזמנה"]


class TestTheDocument:
    def test_it_groups_the_area_by_make_up_the_way_a_glazier_prices_it(self, order):
        rows = order.by_build_up()
        assert rows
        assert sum(row["area"] for row in rows) == pytest.approx(
            sum(pane.total_area for pane in order.panes), abs=0.01
        )

    def test_a_blocked_order_prints_the_banner_rather_than_looking_normal(self, order):
        document = render_glass_order(order)
        assert "לא לשליחה" in document

    def test_a_clean_order_prints_no_banner(self):
        order = GlassOrder(supplier="ס", wanted_by=date.today() + timedelta(days=5))
        order.panes = [OrderedPane(mark="W1", width=900.0, height=1100.0,
                                   build_up="6/16/4", mass_each=14.0)]
        assert "לא לשליחה" not in render_glass_order(order)

    def test_the_document_states_that_a_unit_cannot_be_recut(self, order):
        document = " ".join(render_glass_order(order).split())
        assert "אינה ניתנת לחיתוך מחדש" in document

    def test_it_is_written_right_to_left(self, order):
        assert 'dir="rtl"' in render_glass_order(order)

    def test_it_is_written_where_it_was_asked_for(self, order, tmp_path):
        target = write_glass_order(order, tmp_path / "out" / "glass.html")
        assert target.exists()
        assert "הזמנת זכוכית" in target.read_text(encoding="utf-8")
