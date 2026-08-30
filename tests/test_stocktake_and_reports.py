"""Counting the racks, and the numbers the owner asks for on a Sunday."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.erp.stock import StockItem, StockLedger, Valuation
from profileos.erp.stocktake import (
    Status,
    Stocktake,
    StocktakeBook,
    open_stocktake,
)
from profileos.projects.model import JobFile, JobStatus


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def ledger() -> StockLedger:
    book = StockLedger([
        StockItem("KL-7300-F", "משקוף קליל 7300", unit="m", category="profile"),
        StockItem("KL-7300-S", "כנף קליל 7300", unit="m", category="profile"),
        StockItem("HW-ROLLER", "גלגלת", unit="pcs", category="hardware"),
    ])
    # The stock ledger prices in agorot: 3450 agorot is ⁦34.50⁩ ₪ a metre.
    book.receive("KL-7300-F", 120.0, 3450.0, on=date(2026, 1, 5))
    book.receive("KL-7300-S", 90.0, 4100.0, on=date(2026, 1, 5))
    book.receive("HW-ROLLER", 200.0, 1200.0, on=date(2026, 1, 5))
    return book


def _job(job_id, *, status, total=0.0, quoted="", customer="לקוח", due="") -> JobFile:
    return JobFile(
        job_id=job_id, name=f"עבודה {job_id}", customer_id=customer,
        customer_name=customer, status=status, quote_total=total,
        quoted_on=quoted, due_date=due,
    )


# --------------------------------------------------------------------------- #
# The sheet
# --------------------------------------------------------------------------- #
class TestOpeningASheet:
    def test_the_sheet_freezes_what_the_book_claims(self, ledger):
        sheet = open_stocktake(ledger, scope="מחסן פרופילים", by="יוסי")
        assert len(sheet) == 3
        assert sheet.line("KL-7300-F").book == pytest.approx(120.0)
        assert sheet.line("KL-7300-F").unit_cost == pytest.approx(34.5)

    def test_the_frozen_cost_does_not_move_when_the_book_does(self, ledger):
        sheet = open_stocktake(ledger)
        ledger.receive("KL-7300-F", 60.0, 99.0, on=date(2026, 2, 1))
        assert sheet.line("KL-7300-F").unit_cost == pytest.approx(34.5)
        assert sheet.line("KL-7300-F").book == pytest.approx(120.0)

    def test_a_sheet_may_be_narrowed_to_one_area(self, ledger):
        sheet = open_stocktake(ledger, codes=["HW-ROLLER"])
        assert [line.code for line in sheet.lines] == ["HW-ROLLER"]

    def test_the_printed_sheet_does_not_carry_the_answer(self, ledger):
        """A counter who can see the expected figure copies it."""
        sheet = open_stocktake(ledger)
        printed = sheet.count_sheet_rows()
        assert all("120" not in "".join(row) for row in printed)
        assert Stocktake.COUNT_SHEET_HEADERS[-1] == "נספר"


class TestCounting:
    def test_a_count_records_who_counted_it(self, ledger):
        sheet = open_stocktake(ledger)
        line = sheet.enter("KL-7300-F", 111.0, by="דנה")
        assert line.counted_by == "דנה"
        assert line.difference == pytest.approx(-9.0)
        assert line.value_difference == pytest.approx(-310.5)

    def test_an_uncounted_line_is_not_a_zero(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 120.0)
        assert [line.code for line in sheet.uncounted] == ["HW-ROLLER", "KL-7300-S"]
        assert sheet.uncounted[0].counted is None
        assert sheet.uncounted[0].difference == 0.0

    def test_clearing_a_count_returns_it_to_uncounted(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("HW-ROLLER", 0.0)
        assert sheet.line("HW-ROLLER").is_counted
        sheet.clear("HW-ROLLER")
        assert not sheet.line("HW-ROLLER").is_counted

    def test_zero_is_a_legitimate_count(self, ledger):
        """The rack being empty is a finding, not a missing entry."""
        sheet = open_stocktake(ledger)
        sheet.enter("HW-ROLLER", 0.0, by="מאיה")
        line = sheet.line("HW-ROLLER")
        assert line.is_counted
        assert line.difference == pytest.approx(-200.0)

    def test_a_negative_count_is_refused(self, ledger):
        sheet = open_stocktake(ledger)
        with pytest.raises(ProfileOSError):
            sheet.enter("HW-ROLLER", -3.0)

    def test_counting_an_item_not_on_the_sheet_is_refused(self, ledger):
        sheet = open_stocktake(ledger, codes=["HW-ROLLER"])
        with pytest.raises(ProfileOSError):
            sheet.enter("KL-7300-F", 10.0)


class TestWhatTheSheetSays:
    def test_shortage_and_surplus_are_reported_apart(self, ledger):
        """Two mistakes that cancel out are two mistakes, not none."""
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 110.0)   # −10 × 34.5 = −345
        sheet.enter("KL-7300-S", 100.0)   # +10 × 41.0 = +410
        assert sheet.shrinkage == pytest.approx(-345.0)
        assert sheet.surplus == pytest.approx(410.0)
        assert sheet.net_value == pytest.approx(65.0)

    def test_accuracy_counts_only_the_lines_somebody_counted(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 120.0)
        sheet.enter("KL-7300-S", 88.0)
        assert sheet.accuracy == pytest.approx(50.0)
        assert sheet.progress == pytest.approx(200 / 3, abs=0.1)

    def test_uncounted_lines_are_warned_about_before_posting(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 120.0)
        warnings = " ".join(sheet.warnings())
        assert "לא נספרו" in warnings

    def test_a_wild_difference_is_flagged_as_a_unit_mistake(self, ledger):
        """Counting bars where the book holds metres looks exactly like this."""
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 20.0)
        assert any("היחידה הנכונה" in w for w in sheet.warnings())

    def test_differences_are_ordered_by_money_not_by_quantity(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("HW-ROLLER", 180.0)    # −20 × 12 = −240
        sheet.enter("KL-7300-S", 80.0)     # −10 × 41 = −410
        assert [line.code for line in sheet.differences] == [
            "KL-7300-S", "HW-ROLLER",
        ]


class TestPosting:
    def test_posting_moves_the_book_to_the_counted_figure(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 111.0, by="דנה")
        movements = sheet.post(ledger, by="דנה")
        assert len(movements) == 1
        assert ledger.state("KL-7300-F").on_hand == pytest.approx(111.0)

    def test_posting_never_touches_a_line_nobody_counted(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 111.0)
        sheet.post(ledger)
        assert ledger.state("HW-ROLLER").on_hand == pytest.approx(200.0)
        assert ledger.state("KL-7300-S").on_hand == pytest.approx(90.0)

    def test_a_line_that_agreed_writes_no_movement(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 120.0)
        sheet.enter("KL-7300-S", 85.0)
        movements = sheet.post(ledger)
        assert [m.item for m in movements] == ["KL-7300-S"]

    def test_a_posted_sheet_cannot_be_posted_twice(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 119.0)
        sheet.post(ledger)
        with pytest.raises(ProfileOSError):
            sheet.post(ledger)

    def test_a_posted_sheet_cannot_be_edited(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("KL-7300-F", 119.0)
        sheet.post(ledger)
        with pytest.raises(ProfileOSError):
            sheet.enter("KL-7300-S", 90.0)

    def test_an_empty_sheet_refuses_to_post(self, ledger):
        sheet = open_stocktake(ledger)
        with pytest.raises(ProfileOSError):
            sheet.post(ledger)

    def test_the_movement_names_the_sheet_that_caused_it(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("HW-ROLLER", 195.0)
        movements = sheet.post(ledger)
        assert movements[0].reference == sheet.sheet_id

    def test_an_abandoned_sheet_leaves_the_book_alone(self, ledger):
        sheet = open_stocktake(ledger)
        sheet.enter("HW-ROLLER", 3.0)
        sheet.abandon()
        assert sheet.status is Status.ABANDONED
        assert ledger.state("HW-ROLLER").on_hand == pytest.approx(200.0)


class TestKeepingSheets:
    def test_a_sheet_survives_a_round_trip_through_disk(self, ledger, tmp_path):
        sheet = open_stocktake(ledger, scope="פרזול", by="יוסי")
        sheet.enter("HW-ROLLER", 190.0, by="מאיה", note="מדף עליון")

        book = StocktakeBook(tmp_path / "st.json")
        book.add(sheet)

        again = StocktakeBook(tmp_path / "st.json").load().get(sheet.sheet_id)
        assert again.scope == "פרזול"
        assert again.line("HW-ROLLER").counted == pytest.approx(190.0)
        assert again.line("HW-ROLLER").note == "מדף עליון"
        assert not again.line("KL-7300-F").is_counted

    def test_an_uncounted_line_stays_uncounted_across_a_save(self, ledger, tmp_path):
        """The one thing serialisation must not turn into a zero."""
        sheet = open_stocktake(ledger)
        book = StocktakeBook(tmp_path / "st.json")
        book.add(sheet)
        again = StocktakeBook(tmp_path / "st.json").load().get(sheet.sheet_id)
        assert all(line.counted is None for line in again.lines)

    def test_history_shows_only_sheets_that_were_posted(self, ledger, tmp_path):
        posted = open_stocktake(ledger, scope="פרופילים")
        posted.enter("KL-7300-F", 119.0)
        posted.post(ledger)
        pending = open_stocktake(ledger, scope="פרזול")

        book = StocktakeBook(tmp_path / "st.json")
        book.add(posted)
        book.add(pending)
        assert [row["sheet_id"] for row in book.history()] == [posted.sheet_id]
        assert len(book.open_sheets) == 1


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
from profileos import reports  # noqa: E402


class TestSales:
    def test_a_period_counts_what_was_quoted_inside_it(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=90_000, quoted="2026-03-04"),
            _job("B", status=JobStatus.LOST, total=30_000, quoted="2026-05-20"),
            _job("C", status=JobStatus.WON, total=45_000, quoted="2025-11-02"),
        ]
        report = reports.sales(jobs, reports.year(2026))
        assert report.quoted_count == 2
        assert report.won_value == pytest.approx(90_000)
        assert report.lost_value == pytest.approx(30_000)

    def test_win_rate_by_count_and_by_money_can_disagree(self):
        """Winning most jobs and losing most money is a real shop's year."""
        jobs = [
            _job("A", status=JobStatus.WON, total=10_000, quoted="2026-02-01"),
            _job("B", status=JobStatus.WON, total=12_000, quoted="2026-02-01"),
            _job("C", status=JobStatus.WON, total=8_000, quoted="2026-02-01"),
            _job("D", status=JobStatus.LOST, total=400_000, quoted="2026-02-01"),
        ]
        report = reports.sales(jobs, reports.year(2026))
        assert report.win_rate().value == pytest.approx(75.0)
        assert report.value_win_rate().value < 10.0

    def test_a_thin_sample_says_so_instead_of_printing_a_percentage(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=10_000, quoted="2026-02-01"),
            _job("B", status=JobStatus.LOST, total=10_000, quoted="2026-02-01"),
        ]
        rate = reports.sales(jobs, reports.year(2026)).win_rate()
        assert rate.is_thin
        assert "בלבד" in rate.format()

    def test_jobs_with_no_price_are_counted_not_estimated(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=50_000, quoted="2026-02-01"),
            _job("B", status=JobStatus.WON, total=0.0, quoted="2026-02-01"),
        ]
        report = reports.sales(jobs, reports.year(2026))
        assert report.unpriced == 1
        assert report.won_value == pytest.approx(50_000)
        assert any("בלי סכום" in w for w in report.warnings())

    def test_an_enquiry_that_was_never_quoted_is_not_a_lost_quotation(self):
        jobs = [_job("A", status=JobStatus.ENQUIRY, total=0.0, quoted="2026-02-01")]
        report = reports.sales(jobs, reports.year(2026))
        assert report.quoted_count == 0

    def test_a_year_reads_as_twelve_months(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=10_000, quoted="2026-03-10"),
            _job("B", status=JobStatus.WON, total=20_000, quoted="2026-03-25"),
        ]
        months = reports.by_month(jobs, 2026)
        assert len(months) == 12
        assert months[2]["won"] == 2
        assert months[2]["won_value"] == pytest.approx(30_000)
        assert months[0]["won"] == 0


class TestCustomers:
    def test_customers_are_ranked_by_what_they_ordered(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=10_000, customer="אבי"),
            _job("B", status=JobStatus.WON, total=80_000, customer="בני"),
            _job("C", status=JobStatus.LOST, total=500_000, customer="גדי"),
        ]
        lines = reports.by_customer(jobs)
        assert [line.name for line in lines][:2] == ["בני", "אבי"]

    def test_a_customer_never_costed_has_an_unknown_margin_not_a_zero(self):
        jobs = [_job("A", status=JobStatus.WON, total=10_000, customer="אבי")]
        assert reports.by_customer(jobs)[0].margin is None

    def test_margin_appears_once_hours_and_materials_are_booked(self):
        jobs = [_job("A", status=JobStatus.WON, total=10_000, customer="אבי")]
        lines = reports.by_customer(jobs, costs={"A": 7_500.0})
        assert lines[0].margin == pytest.approx(25.0)

    def test_a_customer_not_seen_for_a_year_is_dormant(self):
        job = _job("A", status=JobStatus.WON, total=1_000, customer="אבי")
        job.updated = (date.today() - timedelta(days=400)).isoformat()
        assert reports.by_customer([job])[0].is_dormant


class TestPipeline:
    def test_late_jobs_are_listed_worst_first(self):
        today = date(2026, 6, 1)
        jobs = [
            _job("A", status=JobStatus.IN_PRODUCTION, due="2026-05-01"),
            _job("B", status=JobStatus.WON, due="2026-05-25"),
            _job("C", status=JobStatus.WON, due="2026-06-03"),
        ]
        live = reports.pipeline(jobs, today=today)
        assert [row[0] for row in live.overdue] == ["A", "B"]
        assert [row[0] for row in live.due_this_week] == ["C"]

    def test_a_job_with_no_date_is_counted_rather_than_assumed_on_time(self):
        jobs = [_job("A", status=JobStatus.WON, due="")]
        assert reports.pipeline(jobs, today=date(2026, 6, 1)).undated == 1

    def test_finished_work_is_not_in_the_pipeline(self):
        jobs = [
            _job("A", status=JobStatus.INSTALLED, total=50_000),
            _job("B", status=JobStatus.LOST, total=50_000),
            _job("C", status=JobStatus.WON, total=20_000),
        ]
        live = reports.pipeline(jobs, today=date(2026, 6, 1))
        assert live.open_value == pytest.approx(20_000)


class TestDashboard:
    def test_growth_against_last_year_is_reported_when_there_is_a_last_year(self):
        jobs = [
            _job("A", status=JobStatus.WON, total=100_000, quoted="2025-04-01"),
            _job("B", status=JobStatus.WON, total=150_000, quoted="2026-04-01"),
        ]
        page = reports.dashboard(jobs, on=date(2026, 8, 1))
        assert page["growth_pct"] == pytest.approx(50.0)

    def test_a_first_year_reports_no_growth_rather_than_infinity(self):
        jobs = [_job("B", status=JobStatus.WON, total=150_000, quoted="2026-04-01")]
        page = reports.dashboard(jobs, on=date(2026, 8, 1))
        assert page["growth_pct"] is None

    def test_the_headline_figures_carry_their_sample(self):
        jobs = [_job("B", status=JobStatus.WON, total=150_000, quoted="2026-04-01")]
        page = reports.dashboard(jobs, on=date(2026, 8, 1))
        assert all(isinstance(f, reports.Figure) for f in page["headlines"])
        assert page["headlines"][0].sample == 1


class TestWhereTheTimeWent:
    def test_rework_is_the_figure_the_report_exists_for(self, tmp_path):
        from profileos.erp.timesheets import TimeBook

        book = TimeBook(tmp_path / "t.json")
        book.book("יוסי", "A", 480, operation="חיתוך")
        book.book("דנה", "A", 120, operation="תיקון", rework=True)
        found = reports.where_the_time_went(book)
        assert found["total_hours"] == pytest.approx(10.0)
        assert found["rework_pct"] == pytest.approx(20.0)
        assert found["heaviest_jobs"][0]["job_id"] == "A"
