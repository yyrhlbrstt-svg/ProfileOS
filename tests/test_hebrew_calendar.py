"""The Hebrew calendar, checked against days people actually kept."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.hebrew_calendar import (
    HolidayKind,
    days_in_month,
    describe,
    from_gregorian,
    hebrew_year_for,
    holiday_on,
    holidays_between,
    holidays_for_hebrew_year,
    is_leap_year,
    months_in_year,
    to_gregorian,
    year_length,
)

#: Known anchors: the Gregorian day each Hebrew date was kept on.
ANCHORS = [
    ((5784, 7, 1), date(2023, 9, 16)),
    ((5785, 7, 1), date(2024, 10, 3)),
    ((5786, 7, 1), date(2025, 9, 23)),
    ((5787, 7, 1), date(2026, 9, 12)),
    ((5786, 7, 10), date(2025, 10, 2)),
    ((5785, 1, 15), date(2025, 4, 13)),
    ((5786, 1, 15), date(2026, 4, 2)),
]


class TestConversion:
    @pytest.mark.parametrize("hebrew,gregorian", ANCHORS)
    def test_known_days_land_where_they_were_kept(self, hebrew, gregorian):
        assert to_gregorian(*hebrew) == gregorian

    @pytest.mark.parametrize("hebrew,gregorian", ANCHORS)
    def test_and_read_back_the_same(self, hebrew, gregorian):
        assert from_gregorian(gregorian) == hebrew

    def test_every_day_of_forty_years_round_trips(self):
        day = date(2000, 1, 1)
        while day < date(2040, 1, 1):
            assert to_gregorian(*from_gregorian(day)) == day
            day += timedelta(days=1)

    def test_the_hebrew_date_reads_in_hebrew(self):
        assert "בתשרי" in describe(date(2025, 9, 23))


class TestTheShapeOfTheYear:
    def test_seven_leap_years_in_nineteen(self):
        leaps = sum(1 for year in range(5780, 5799) if is_leap_year(year))
        assert leaps == 7

    def test_a_leap_year_has_a_second_adar(self):
        assert months_in_year(5784) == 13
        assert is_leap_year(5784)

    def test_a_year_is_always_a_legal_length(self):
        """353 to 385, and never the two lengths the postponements forbid."""
        for year in range(5700, 5900):
            assert year_length(year) in (353, 354, 355, 383, 384, 385)

    def test_the_months_add_up_to_the_year(self):
        for year in (5785, 5786, 5787):
            total = sum(
                days_in_month(year, month)
                for month in range(1, months_in_year(year) + 1)
            )
            assert total == year_length(year)

    def test_the_hebrew_year_turns_over_at_rosh_hashana(self):
        assert hebrew_year_for(date(2025, 9, 22)) == 5785
        assert hebrew_year_for(date(2025, 9, 23)) == 5786


class TestTheWorkingYear:
    def test_yom_kippur_is_a_day_nobody_works(self):
        festival = holiday_on(date(2025, 10, 2))
        assert festival is not None
        assert festival.kind is HolidayKind.FULL
        assert festival.kind.working_fraction == 0.0

    def test_the_eve_of_a_festival_is_a_short_day_not_a_lost_one(self):
        eve = holiday_on(date(2025, 10, 1))
        assert eve is not None and eve.kind is HolidayKind.EVE
        assert 0 < eve.kind.working_fraction < 1

    def test_the_intermediate_days_are_working_days_that_get_less_done(self):
        middle = holiday_on(date(2026, 9, 28))
        assert middle is not None and middle.kind is HolidayKind.INTERMEDIATE
        assert 0 < middle.kind.working_fraction < 1

    def test_tishrei_takes_a_fortnight_out_of_the_autumn(self):
        found = holidays_between(date(2025, 9, 1), date(2025, 10, 31))
        assert len(found) >= 12
        assert any("סוכות" in holiday.hebrew for holiday in found)
        assert any("כיפור" in holiday.hebrew for holiday in found)

    def test_independence_day_never_falls_beside_shabbat(self):
        for year in range(5780, 5800):
            day = next(
                holiday.day for holiday in holidays_for_hebrew_year(year)
                if holiday.hebrew == "יום העצמאות"
            )
            # Never Friday or Saturday, and never a Sunday memorial day before it.
            assert day.weekday() not in (4, 5)
            assert (day - timedelta(days=1)).weekday() not in (4, 5)

    def test_a_span_never_reports_the_same_day_twice(self):
        found = holidays_between(date(2024, 1, 1), date(2030, 1, 1))
        assert len({holiday.day for holiday in found}) == len(found)

    def test_an_ordinary_tuesday_is_not_a_holiday(self):
        assert holiday_on(date(2026, 8, 25)) is None


class TestTheScheduler:
    def test_an_israeli_calendar_does_not_promise_work_on_yom_kippur(self):
        from profileos.erp.scheduling import Calendar

        israeli = Calendar.israeli()
        assert israeli.hours_on(date(2025, 10, 2)) == 0.0
        assert not israeli.is_working(date(2025, 10, 2))

    def test_a_calendar_built_for_elsewhere_is_left_alone(self):
        from profileos.erp.scheduling import Calendar

        plain = Calendar()
        assert plain.hours_on(date(2025, 10, 2)) > 0

    def test_an_eve_is_scheduled_as_the_short_day_it_is(self):
        from profileos.erp.scheduling import Calendar

        israeli = Calendar.israeli()
        eve = israeli.hours_on(date(2025, 10, 1))
        ordinary = israeli.hours_on(date(2025, 10, 15))
        assert 0 < eve < ordinary

    def test_the_next_working_day_steps_over_the_festival(self):
        from profileos.erp.scheduling import Calendar

        israeli = Calendar.israeli()
        # Yom Kippur 5787 fell on a Monday; the shop opens again on the Tuesday.
        assert israeli.next_working_day(date(2026, 9, 21)) == date(2026, 9, 22)
        # Shabbat Sukkot is followed by an intermediate day, which is thin but
        # is a working day — the shop is open, and the schedule should say so.
        assert israeli.next_working_day(date(2026, 9, 26)) == date(2026, 9, 27)

    def test_the_week_still_runs_sunday_to_thursday(self):
        from profileos.erp.scheduling import Calendar

        israeli = Calendar.israeli()
        assert israeli.hours_on(date(2026, 8, 22)) == 0.0   # Saturday
        assert israeli.hours_on(date(2026, 8, 23)) > 0.0    # Sunday
        assert (
            israeli.hours_on(date(2026, 8, 21))             # Friday
            < israeli.hours_on(date(2026, 8, 20))           # Thursday
        )
