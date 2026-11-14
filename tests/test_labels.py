"""A label for every piece, so nothing on the rack is anonymous."""

from __future__ import annotations

import re

import pytest

from profileos.core.errors import ProfileOSError
from profileos.mes.labels import (
    DEFAULT_STOCK,
    STOCKS,
    PieceLabel,
    labels_for_order,
    render_labels,
    write_labels,
)


def _label(**overrides) -> PieceLabel:
    values = dict(
        code="POS|J1|W1|P1|CUT", job_id="2026-114", position="W1",
        description="משקוף עליון", length_mm=1420.5,
        left_angle=45.0, right_angle=90.0, profile="7300-F",
    )
    values.update(overrides)
    return PieceLabel(**values)


class TestLayout:
    def test_a_sheet_holds_exactly_what_the_stock_says(self):
        stock = STOCKS[DEFAULT_STOCK]
        document, run = render_labels([_label()] * stock.per_sheet)
        assert run.sheets == 1
        assert document.count('class="sheet"') == 1

    def test_one_more_than_a_sheet_starts_a_second_sheet(self):
        stock = STOCKS[DEFAULT_STOCK]
        _document, run = render_labels([_label()] * (stock.per_sheet + 1))
        assert run.sheets == 2

    def test_every_requested_label_is_printed_or_the_run_says_otherwise(self):
        _document, run = render_labels([_label()] * 7)
        assert run.printed == 7
        assert run.requested == 7
        assert run.is_complete

    def test_a_part_used_sheet_can_be_started_partway_in(self):
        """A shop printing two hundred labels a week notices the waste."""
        document, run = render_labels([_label()], start_at=5)
        assert run.printed == 1
        assert document.count('class="label"') == STOCKS[DEFAULT_STOCK].per_sheet

    def test_skipping_past_the_end_of_a_sheet_is_refused(self):
        with pytest.raises(ProfileOSError):
            render_labels([_label()], start_at=STOCKS[DEFAULT_STOCK].per_sheet)

    def test_an_unknown_stock_names_the_ones_that_exist(self):
        with pytest.raises(ProfileOSError) as caught:
            render_labels([_label()], stock="a4-999")
        assert DEFAULT_STOCK in str(caught.value)

    def test_the_page_size_matches_the_stock_not_the_screen(self):
        document, _run = render_labels([_label()], stock="roll-100x50")
        assert "size: 100mm 50mm" in document

    def test_no_labels_is_reported_rather_than_an_empty_file(self):
        _document, run = render_labels([])
        assert run.printed == 0
        assert run.warnings


class TestWhatIsOnTheLabel:
    def test_the_barcode_carries_the_scannable_payload(self):
        document, _run = render_labels([_label(code="POS|A|B|C|CUT")])
        assert "<svg" in document
        assert document.count('class="code"') == 1

    def test_the_length_is_printed_where_a_person_reads_it_first(self):
        document, _run = render_labels([_label(length_mm=1420.5)])
        assert "1,420.5" in document

    def test_each_angle_is_drawn_at_its_own_end_of_the_bar(self):
        """A pair of numbers in a corner is what gets cut backwards."""
        document, _run = render_labels([_label(left_angle=30.0, right_angle=60.0)])
        assert "30°" in document and "60°" in document
        assert 'class="cut"' in document

    def test_a_square_cut_draws_a_rectangle(self):
        document, _run = render_labels([_label(left_angle=90.0, right_angle=90.0)])
        polygon = re.search(r'points="([^"]+)"', document).group(1)
        xs = [float(pair.split(",")[0]) for pair in polygon.split()]
        assert xs[0] == pytest.approx(xs[3])
        assert xs[1] == pytest.approx(xs[2])

    def test_an_obtuse_cut_leans_the_other_way_from_an_acute_one(self):
        """⁦135°⁩ is not ⁦45°⁩ drawn backwards, and it is not a sliver either."""
        def skew(angle: float) -> float:
            document, _run = render_labels([_label(left_angle=angle, right_angle=90.0)])
            polygon = re.search(r'points="([^"]+)"', document).group(1)
            xs = [float(pair.split(",")[0]) for pair in polygon.split()]
            top, bottom = xs[1] - xs[0], xs[2] - xs[3]
            return top - bottom

        assert skew(45.0) < skew(90.0) < skew(135.0)
        assert skew(90.0) == pytest.approx(0.0)

    def test_a_piece_with_no_angles_draws_nothing_rather_than_a_guess(self):
        document, _run = render_labels(
            [_label(left_angle=None, right_angle=None)]
        )
        assert 'class="cut"' not in document

    def test_an_unconfirmed_series_prints_the_banner_on_every_label(self):
        """A label is an instruction to cut."""
        document, _run = render_labels([_label(provisional=True)])
        assert "לא לייצור" in document

    def test_a_confirmed_piece_carries_no_banner(self):
        document, _run = render_labels([_label()])
        assert "לא לייצור" not in document

    def test_a_quantity_of_one_is_not_printed(self):
        document, _run = render_labels([_label(quantity=1)])
        assert "×1" not in document

    def test_a_larger_quantity_is(self):
        document, _run = render_labels([_label(quantity=4)])
        assert "×4" in document


class TestFromAWorkOrder:
    @pytest.fixture
    def order(self):
        from profileos.elements import Opening, build_elements
        from profileos.mes import work_order_from_builds

        builds = build_elements([Opening(name="W1", width=1500, height=1200)])
        return work_order_from_builds(builds, project_id="PRJ", name="פרויקט")

    def test_a_released_order_produces_a_label_for_every_piece(self, order):
        labels = labels_for_order(order)
        expected = sum(max(1, item.quantity) for item in order.items)
        assert len(labels) == expected

    def test_four_of_the_same_piece_get_four_labels_not_one(self, order):
        """The problem is on the rack, and the rack has four bars."""
        item = order.items[0]
        item.quantity = 4
        labels = labels_for_order(order)
        matching = [
            label for label in labels if label.description == item.description
        ]
        assert len(matching) >= 4

    def test_a_provisional_run_marks_every_label(self, order):
        labels = labels_for_order(order, provisional=True)
        assert labels and all(label.provisional for label in labels)


class TestWriting:
    def test_a_sheet_is_written_where_it_was_asked_for(self, tmp_path):
        target = tmp_path / "out" / "labels.html"
        run = write_labels([_label()] * 3, target)
        assert target.exists()
        assert run.printed == 3
        assert "<!DOCTYPE html>" in target.read_text(encoding="utf-8")
