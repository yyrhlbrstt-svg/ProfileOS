"""Making another one of those."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.elements.model import Opening
from profileos.projects.templates import (
    PRICE_STALE_DAYS,
    Template,
    TemplateBook,
    default_templates,
    template_from_opening,
)


@pytest.fixture
def opening() -> Opening:
    return Opening(
        name="הזזה שתי כנפיים קליל 7300",
        width=2400, height=1400, system_id="klil-7300",
        mullion_positions=[1200.0], finish="אנודייז טבעי",
    )


class TestSaving:
    def test_divisions_are_stored_as_fractions_not_millimetres(self, opening):
        """Absolute positions make a template useless at any other size."""
        template = template_from_opening(opening)
        assert template.mullion_fractions == [pytest.approx(0.5)]

    def test_the_system_and_finish_come_across(self, opening):
        template = template_from_opening(opening)
        assert template.system_id == "klil-7300"
        assert template.finish == "אנודייז טבעי"

    def test_the_size_it_was_saved_at_is_a_starting_point_not_a_rule(self, opening):
        template = template_from_opening(opening)
        assert template.typical_width == pytest.approx(2400.0)

    def test_where_it_came_from_survives(self, opening):
        template = template_from_opening(opening, from_job="2026-114")
        assert template.from_job == "2026-114"

    def test_an_opening_with_no_size_cannot_become_a_template(self):
        class Sizeless:
            width = 0
            height = 0

        with pytest.raises(ProfileOSError):
            template_from_opening(Sizeless())


class TestUsingOne:
    def test_a_middle_mullion_stays_in_the_middle_at_a_new_width(self, opening):
        template = template_from_opening(opening)
        made = template.apply(3000, 1600)
        assert made.width == pytest.approx(3000.0)
        assert made.mullion_positions == [pytest.approx(1500.0)]

    def test_an_off_centre_division_keeps_its_proportion(self):
        opening = Opening(name="W", width=3000, height=1400,
                          mullion_positions=[1000.0])
        template = template_from_opening(opening)
        made = template.apply(1500, 1400)
        assert made.mullion_positions == [pytest.approx(500.0)]

    def test_the_new_opening_may_be_named_for_the_job_it_is_going_into(self, opening):
        template = template_from_opening(opening)
        assert template.apply(2000, 1200, name="W7").name == "W7"

    def test_a_negative_size_is_refused(self, opening):
        with pytest.raises(ProfileOSError):
            template_from_opening(opening).apply(-100, 1200)

    def test_using_one_records_that_it_was_used(self, opening, tmp_path):
        book = TemplateBook(tmp_path / "t.json")
        template = book.add(template_from_opening(opening))
        book.use(template.template_id, 2000, 1200)
        book.use(template.template_id, 2200, 1200)
        assert book.get(template.template_id).times_used == 2
        assert book.get(template.template_id).last_used == date.today()


class TestThePrice:
    def test_a_fresh_price_is_offered_with_its_age(self, opening):
        template = template_from_opening(opening, price_per_m2=1450.0)
        assert not template.price_is_stale
        assert "1,450" in template.price_line()

    def test_an_old_price_is_marked_for_repricing_rather_than_offered(self, opening):
        """A template quoted at last year's aluminium price loses money."""
        template = template_from_opening(opening, price_per_m2=1450.0)
        template.priced_on = date.today() - timedelta(days=PRICE_STALE_DAYS + 1)
        assert template.price_is_stale
        assert "לתמחר מחדש" in template.price_line()

    def test_a_template_never_priced_says_so_rather_than_showing_zero(self, opening):
        template = template_from_opening(opening)
        assert template.last_price_per_m2 is None
        assert template.price_line() == "לא תומחר"

    def test_the_book_lists_what_needs_repricing(self, opening, tmp_path):
        book = TemplateBook(tmp_path / "t.json")
        fresh = book.add(template_from_opening(opening, price_per_m2=1450.0))
        stale = template_from_opening(opening, name="ישן", price_per_m2=1200.0)
        stale.priced_on = date.today() - timedelta(days=PRICE_STALE_DAYS + 10)
        book.add(stale)
        assert [t.template_id for t in book.needing_repricing()] == [
            stale.template_id
        ]
        assert fresh.template_id not in {
            t.template_id for t in book.needing_repricing()
        }


class TestFindingOne:
    @pytest.fixture
    def book(self, tmp_path, opening) -> TemplateBook:
        book = TemplateBook(tmp_path / "t.json")
        book.add(template_from_opening(opening, name="הזזה שתי כנפיים קליל 7300"))
        book.add(Template(name="ויטרינה קבועה קליל 9000", system_id="klil-9000"))
        book.add(Template(name="דלת ממ״ד", tags=["ממד", "פלדה"]))
        return book

    def test_words_may_come_in_any_order(self, book):
        assert len(book.search("קליל הזזה")) == 1
        assert len(book.search("הזזה קליל")) == 1

    def test_a_tag_finds_it_as_well_as_the_name(self, book):
        assert [t.name for t in book.search("ממד")] == ["דלת ממ״ד"]

    def test_an_empty_search_offers_the_shop_its_own_habits(self, book, opening):
        template = book.search("קליל הזזה")[0]
        book.use(template.template_id, 2000, 1200)
        assert book.search("")[0].template_id == template.template_id

    def test_nothing_matching_comes_back_empty_rather_than_everything(self, book):
        assert book.search("אלומיניום סגול") == []


class TestKeeping:
    def test_a_template_survives_a_round_trip_through_disk(self, tmp_path, opening):
        book = TemplateBook(tmp_path / "t.json")
        template = book.add(template_from_opening(
            opening, name="סטנדרט", price_per_m2=1450.0, from_job="2026-114"
        ))

        again = TemplateBook(tmp_path / "t.json").load().get(template.template_id)
        assert again.name == "סטנדרט"
        assert again.mullion_fractions == [pytest.approx(0.5)]
        assert again.last_price_per_m2 == pytest.approx(1450.0)
        assert again.apply(3000, 1600).mullion_positions == [pytest.approx(1500.0)]

    def test_removing_one_removes_it(self, tmp_path, opening):
        book = TemplateBook(tmp_path / "t.json")
        template = book.add(template_from_opening(opening))
        book.remove(template.template_id)
        assert len(TemplateBook(tmp_path / "t.json").load()) == 0

    def test_asking_for_one_that_is_not_there_is_refused(self, tmp_path):
        with pytest.raises(ProfileOSError):
            TemplateBook(tmp_path / "t.json").get("TP-NOPE")
