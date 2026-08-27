"""What changed between one price and the next, and what the shop is waiting for."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.erp.order_confirmation import (
    Need,
    OrderConfirmation,
    Prerequisite,
    confirm_order,
)
from profileos.erp.scheduling import Calendar
from profileos.quoting.pricing import QuoteLine, Quotation
from profileos.quoting.revisions import (
    QuoteHistory,
    RevisionBook,
    compare,
)


def _quote(*lines, net: float | None = None) -> Quotation:
    quote = Quotation(project_name="וילה", currency="ILS")
    quote.lines = list(lines)
    total = sum(line.total for line in quote.lines)
    quote.material_cost = net if net is not None else total
    quote.policy.margin_pct = 0.0
    quote.policy.overhead_pct = 0.0
    quote.policy.contingency_pct = 0.0
    quote.policy.delivery_pct = 0.0
    return quote


def _line(code, description, quantity, price, unit="יח׳") -> QuoteLine:
    return QuoteLine(
        description=description, quantity=quantity, unit=unit,
        unit_price=price, code=code,
    )


# --------------------------------------------------------------------------- #
# Revisions
# --------------------------------------------------------------------------- #
class TestTakingRevisions:
    def test_the_first_revision_needs_no_reason(self):
        history = QuoteHistory(job_id="2026-114")
        first = history.take(_quote(_line("W1", "חלון סלון", 2, 4_000)))
        assert first.number == 1
        assert first.net_price == pytest.approx(8_000)

    def test_a_later_revision_without_a_reason_is_refused(self):
        """A version nobody can explain is a version nobody can defend."""
        history = QuoteHistory(job_id="2026-114")
        history.take(_quote(_line("W1", "חלון סלון", 2, 4_000)))
        with pytest.raises(ProfileOSError):
            history.take(_quote(_line("W1", "חלון סלון", 3, 4_000)))

    def test_a_sent_revision_is_the_one_the_customer_holds(self):
        history = QuoteHistory(job_id="2026-114")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.mark_sent(1)
        history.take(
            _quote(_line("W1", "חלון", 3, 4_000)), reason="הוסף חלון"
        )
        assert history.last_sent.number == 1
        assert history.current.number == 2

    def test_a_revision_cannot_be_sent_twice(self):
        history = QuoteHistory(job_id="2026-114")
        history.take(_quote(_line("W1", "חלון", 1, 4_000)))
        history.mark_sent(1)
        with pytest.raises(ProfileOSError):
            history.mark_sent(1)


class TestComparing:
    def test_an_added_line_shows_what_it_added(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.take(
            _quote(_line("W1", "חלון", 2, 4_000), _line("D1", "דלת", 1, 9_000)),
            reason="הוספת דלת ממ״ד",
        )
        diff = history.compare(1, 2)
        added = diff.of_kind("added")
        assert [change.key for change in added] == ["D1"]
        assert added[0].effect == pytest.approx(9_000)

    def test_a_removed_line_shows_what_it_saved(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000), _line("W2", "אשנב", 2, 900)))
        history.take(_quote(_line("W1", "חלון", 2, 4_000)), reason="ביטול אשנבים")
        removed = history.compare(1, 2).of_kind("removed")
        assert removed[0].effect == pytest.approx(-1_800)

    def test_quantity_and_price_moving_together_are_two_separate_changes(self):
        """Otherwise nobody can say whether it was the count or the rate."""
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.take(_quote(_line("W1", "חלון", 3, 4_500)), reason="עדכון")
        diff = history.compare(1, 2)
        assert len(diff.of_kind("requantified")) == 1
        assert len(diff.of_kind("repriced")) == 1
        assert diff.of_kind("requantified")[0].effect == pytest.approx(4_000)
        assert diff.of_kind("repriced")[0].effect == pytest.approx(1_500)

    def test_the_changes_add_up_to_the_move_in_the_price(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000), _line("W2", "אשנב", 4, 900)))
        history.take(
            _quote(_line("W1", "חלון", 3, 4_500), _line("D1", "דלת", 1, 9_000)),
            reason="שינוי תכנית",
        )
        diff = history.compare(1, 2)
        assert diff.reconciles
        assert diff.line_difference == pytest.approx(diff.price_difference)

    def test_a_price_that_moved_without_any_line_moving_says_so(self):
        """The awkward answer — margin changed — is printed, not hidden."""
        first = _quote(_line("W1", "חלון", 2, 4_000))
        second = _quote(_line("W1", "חלון", 2, 4_000))
        second.policy.margin_pct = 20.0

        history = QuoteHistory(job_id="J")
        history.take(first)
        history.take(second, reason="עדכון רווח")
        diff = history.compare(1, 2)
        assert not diff.reconciles
        assert diff.unexplained > 0
        assert "לא מוסברים" in diff.describe()

    def test_lines_without_a_code_are_never_paired_on_a_resemblance(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line(None, "עבודות שונות", 1, 5_000)))
        history.take(_quote(_line(None, "עבודות נוספות", 1, 5_100)), reason="עדכון")
        diff = history.compare(1, 2)
        assert len(diff.of_kind("added")) == 1
        assert len(diff.of_kind("removed")) == 1
        assert not diff.of_kind("repriced")

    def test_identical_revisions_compare_to_nothing(self):
        quote = _quote(_line("W1", "חלון", 2, 4_000))
        history = QuoteHistory(job_id="J")
        history.take(quote)
        history.take(quote, reason="עותק")
        assert "לא השתנה דבר" in history.compare(1, 2).describe()

    def test_since_sent_answers_is_that_still_the_price(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.mark_sent(1)
        assert history.since_sent() is None
        history.take(_quote(_line("W1", "חלון", 4, 4_000)), reason="הוכפל")
        moved = history.since_sent()
        assert moved.price_difference == pytest.approx(8_000)

    def test_the_biggest_change_comes_first_regardless_of_direction(self):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("A", "א", 1, 1_000), _line("B", "ב", 1, 500)))
        history.take(
            _quote(_line("A", "א", 1, 1_000)), reason="ביטול"
        )
        assert history.compare(1, 2).biggest[0].key == "B"


class TestKeepingRevisions:
    def test_a_history_survives_a_round_trip_through_disk(self, tmp_path):
        book = RevisionBook(tmp_path / "r.json")
        history = book.for_job("2026-114")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.mark_sent(1)
        history.take(_quote(_line("W1", "חלון", 3, 4_000)), reason="תוספת")
        book.save()

        again = RevisionBook(tmp_path / "r.json").load().for_job("2026-114")
        assert len(again) == 2
        assert again.last_sent.number == 1
        assert again.get(2).reason == "תוספת"
        assert again.compare(1, 2).price_difference == pytest.approx(4_000)

    def test_the_trail_shows_each_step_and_what_it_moved(self, tmp_path):
        history = QuoteHistory(job_id="J")
        history.take(_quote(_line("W1", "חלון", 2, 4_000)))
        history.take(_quote(_line("W1", "חלון", 3, 4_000)), reason="תוספת")
        trail = history.trail()
        assert trail[0]["change"] == pytest.approx(0.0)
        assert trail[1]["change"] == pytest.approx(4_000)
        assert trail[1]["reason"] == "תוספת"


# --------------------------------------------------------------------------- #
# Order confirmation
# --------------------------------------------------------------------------- #
class _Job:
    job_id = "2026-114"
    name = "וילה בבית אל"
    customer_name = "אבי כהן"
    reference = "PO-88"
    site_address = "סולם יעקב 1"
    quote_total = 0.0


def _confirmation(**overrides) -> OrderConfirmation:
    values = dict(net=120_000.0, lead_working_days=15, deposit_pct=30.0,
                  on=date(2026, 8, 27))
    values.update(overrides)
    return confirm_order(_Job(), **values)


class TestWhatIsOutstanding:
    def test_the_standard_list_is_asked_for_without_being_remembered(self):
        confirmation = _confirmation()
        needs = {item.need for item in confirmation.prerequisites}
        assert Need.MEASUREMENT in needs
        assert Need.DEPOSIT in needs
        assert Need.COLOUR in needs

    def test_no_deposit_asked_means_no_deposit_line(self):
        confirmation = _confirmation(deposit_pct=0.0)
        assert all(
            item.need is not Need.DEPOSIT for item in confirmation.prerequisites
        )

    def test_site_access_does_not_stop_a_bar_being_cut(self):
        confirmation = _confirmation(needs=[Need.SITE_ACCESS], deposit_pct=0.0)
        assert confirmation.outstanding
        assert confirmation.is_clear_to_start

    def test_a_missing_measurement_does_stop_it(self):
        confirmation = _confirmation(needs=[Need.MEASUREMENT], deposit_pct=0.0)
        assert not confirmation.is_clear_to_start

    def test_receiving_an_item_clears_it(self):
        confirmation = _confirmation(needs=[Need.MEASUREMENT], deposit_pct=0.0)
        confirmation.receive(Need.MEASUREMENT, on=date(2026, 9, 1))
        assert confirmation.is_clear_to_start

    def test_receiving_something_never_asked_for_is_refused(self):
        confirmation = _confirmation(needs=[Need.MEASUREMENT], deposit_pct=0.0)
        with pytest.raises(ProfileOSError):
            confirmation.receive(Need.PERMIT)

    def test_the_deposit_answers_for_itself_once_the_money_is_in(self):
        confirmation = _confirmation(needs=[Need.DEPOSIT])
        assert not confirmation.is_clear_to_start
        confirmation.deposit_received = confirmation.deposit_due
        assert confirmation.is_clear_to_start


class TestThePromisedDate:
    def test_a_date_is_provisional_while_anything_is_outstanding(self):
        confirmation = _confirmation()
        assert not confirmation.date_is_firm
        assert "משוער" in confirmation.date_line(on=date(2026, 8, 27))

    def test_a_date_is_firm_once_nothing_is_waited_on(self):
        confirmation = _confirmation(needs=[], deposit_pct=0.0)
        line = confirmation.date_line(on=date(2026, 8, 27))
        assert confirmation.date_is_firm
        assert "משוער" not in line

    def test_the_clock_starts_when_the_last_blocker_is_due_not_today(self):
        early = _confirmation(needs=[], deposit_pct=0.0)
        late = _confirmation(needs=[], deposit_pct=0.0)
        late.prerequisites.append(Prerequisite(
            need=Need.MEASUREMENT, due=date(2026, 9, 20)
        ))
        assert late.promised_date(on=date(2026, 8, 27)) > early.promised_date(
            on=date(2026, 8, 27)
        )

    def test_the_lead_time_is_counted_in_working_days_not_calendar_days(self):
        confirmation = _confirmation(needs=[], deposit_pct=0.0,
                                     lead_working_days=10)
        promised = confirmation.promised_date(on=date(2026, 8, 27))
        assert (promised - date(2026, 8, 27)).days > 10

    def test_a_promised_day_is_never_a_day_the_shop_is_shut(self):
        confirmation = _confirmation(needs=[], deposit_pct=0.0)
        calendar = Calendar.israeli()
        assert calendar.is_working(
            confirmation.promised_date(calendar=calendar, on=date(2026, 8, 27))
        )

    def test_no_lead_time_means_the_start_day_itself(self):
        confirmation = _confirmation(needs=[], deposit_pct=0.0,
                                     lead_working_days=0)
        calendar = Calendar.israeli()
        assert confirmation.promised_date(
            calendar=calendar, on=date(2026, 8, 27)
        ) == calendar.next_working_day(date(2026, 8, 27))


class TestChecking:
    def test_a_confirmation_with_no_quote_revision_is_flagged(self):
        assert any(
            "גרסת הצעת מחיר" in problem
            for problem in _confirmation().problems()
        )

    def test_a_blocker_with_no_date_makes_the_promise_meaningless(self):
        confirmation = _confirmation(needs=[], deposit_pct=0.0)
        confirmation.prerequisites.append(
            Prerequisite(need=Need.COLOUR, due=None)
        )
        assert any("אין לו תאריך יעד" in p for p in confirmation.problems())

    def test_no_lead_time_is_a_problem_worth_naming(self):
        confirmation = _confirmation(lead_working_days=0)
        assert any("זמן ייצור" in p for p in confirmation.problems())

    def test_the_money_adds_up(self):
        confirmation = _confirmation()
        assert confirmation.vat == pytest.approx(21_600)
        assert confirmation.gross == pytest.approx(141_600)
        assert confirmation.deposit_due == pytest.approx(42_480)
        assert confirmation.deposit_outstanding == pytest.approx(42_480)

    def test_an_overdue_prerequisite_says_it_is_overdue(self):
        item = Prerequisite(
            need=Need.COLOUR, due=date.today() - timedelta(days=3)
        )
        assert item.is_overdue
        assert "באיחור" in item.describe()

    def test_a_confirmation_survives_a_round_trip(self):
        confirmation = _confirmation()
        confirmation.receive(Need.MEASUREMENT, on=date(2026, 9, 2))
        again = OrderConfirmation.from_dict(confirmation.as_dict())
        assert again.gross == pytest.approx(confirmation.gross)
        assert len(again.outstanding) == len(confirmation.outstanding)
        assert again.prerequisites[1].received_on == date(2026, 9, 2)
