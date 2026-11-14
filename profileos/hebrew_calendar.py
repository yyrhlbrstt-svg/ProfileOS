"""The Hebrew calendar, because a delivery date in Israel is not arithmetic.

A scheduler that counts working days Monday to Friday will promise a shop in
Bet El a delivery on Yom Kippur, and quietly lose a week every autumn when
Tishrei takes most of the month out. No foreign package knows that, and it is
not a setting somebody can be expected to type in every year: the festivals
move against the Gregorian calendar, so they have to be computed.

The arithmetic is the standard fixed-date algorithm: the molad of Tishrei
gives a candidate new year, the four postponement rules move it, and the
lengths of Heshvan and Kislev follow from how long the year turned out to be.
Everything else — every festival, every eve, every intermediate day — is a
count from there.

Python's ``date.toordinal`` is the same fixed-day count the algorithm uses, so
conversion in and out is exact and needs no epoch of its own beyond the one
constant below.

A Hebrew day begins at sunset. What a business calendar cares about is the
daytime, so a festival is placed on the Gregorian day it is kept, and the
afternoon before it is an eve — which in Israel is a short working day, and
is exactly the thing that makes a Thursday delivery fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import lru_cache

#: Fixed day number of 1 Tishrei, year 1 — the one constant the rest rests on.
HEBREW_EPOCH = -1373427

NISAN, IYAR, SIVAN, TAMMUZ, AV, ELUL = 1, 2, 3, 4, 5, 6
TISHREI, HESHVAN, KISLEV, TEVET, SHEVAT, ADAR, ADAR_II = 7, 8, 9, 10, 11, 12, 13

MONTH_NAMES: dict[int, str] = {
    1: "ניסן", 2: "אייר", 3: "סיוון", 4: "תמוז", 5: "אב", 6: "אלול",
    7: "תשרי", 8: "חשוון", 9: "כסלו", 10: "טבת", 11: "שבט",
    12: "אדר", 13: "אדר ב׳",
}


def is_leap_year(year: int) -> bool:
    """Whether this Hebrew year carries a second Adar.

    Seven leap years in every nineteen, in a fixed pattern — the cycle that
    keeps Pesach in the spring.
    """
    return ((year * 7 + 1) % 19) < 7


def months_in_year(year: int) -> int:
    return 13 if is_leap_year(year) else 12


def _elapsed_days(year: int) -> int:
    """Days from the epoch to the molad-based new year, before postponement."""
    months = (
        235 * ((year - 1) // 19)          # whole nineteen-year cycles
        + 12 * ((year - 1) % 19)          # ordinary years in this cycle
        + (7 * ((year - 1) % 19) + 1) // 19  # leap years so far in this cycle
    )
    parts = 12084 + 13753 * months
    day = 29 * months + parts // 25920
    # If the molad falls late in the day, the year starts the next day.
    if (3 * (day + 1)) % 7 < 3:
        day += 1
    return day


def _year_correction(year: int) -> int:
    """The postponement that keeps a year a legal length.

    A year may not be 356 or 382 days long, so when the raw arithmetic
    produces one, the new year moves by a day or two.
    """
    this_year = _elapsed_days(year)
    if _elapsed_days(year + 1) - this_year == 356:
        return 2
    if this_year - _elapsed_days(year - 1) == 382:
        return 1
    return 0


@lru_cache(maxsize=512)
def new_year(year: int) -> int:
    """Fixed day of 1 Tishrei of a Hebrew year."""
    return HEBREW_EPOCH + _elapsed_days(year) + _year_correction(year)


@lru_cache(maxsize=512)
def year_length(year: int) -> int:
    """How many days this Hebrew year runs to — 353 to 385."""
    return new_year(year + 1) - new_year(year)


def days_in_month(year: int, month: int) -> int:
    """Length of one Hebrew month.

    Two of them are variable: Heshvan gains a day in a full year and Kislev
    loses one in a deficient year, which is how the calendar absorbs the
    postponements without moving any festival relative to its own month.
    """
    if month in (NISAN, SIVAN, AV, TISHREI, SHEVAT):
        return 30
    if month in (IYAR, TAMMUZ, ELUL, TEVET, ADAR_II):
        return 29
    if month == ADAR:
        return 30 if is_leap_year(year) else 29
    if month == HESHVAN:
        return 30 if year_length(year) in (355, 385) else 29
    if month == KISLEV:
        return 29 if year_length(year) in (353, 383) else 30
    raise ValueError(f"No Hebrew month {month}")


def to_gregorian(year: int, month: int, day: int) -> date:
    """One Hebrew date as the Gregorian day it is kept on."""
    if month == ADAR_II and not is_leap_year(year):
        month = ADAR
    if month < TISHREI:
        # The year runs Tishrei to Elul, so a spring month is counted after
        # the whole autumn of the same Hebrew year.
        elapsed = sum(
            days_in_month(year, m) for m in range(TISHREI, months_in_year(year) + 1)
        ) + sum(days_in_month(year, m) for m in range(NISAN, month))
    else:
        elapsed = sum(days_in_month(year, m) for m in range(TISHREI, month))
    return date.fromordinal(new_year(year) + elapsed + day - 1)


def from_gregorian(day: date) -> tuple[int, int, int]:
    """The Hebrew date kept on this Gregorian day."""
    fixed = day.toordinal()
    # Start close and walk: the estimate is never more than a year out.
    year = (fixed - HEBREW_EPOCH) // 366 + 1
    while new_year(year + 1) <= fixed:
        year += 1
    while new_year(year) > fixed:
        year -= 1

    month = TISHREI
    remaining = fixed - new_year(year)
    order = list(range(TISHREI, months_in_year(year) + 1)) + list(range(NISAN, TISHREI))
    for candidate in order:
        length = days_in_month(year, candidate)
        if remaining < length:
            month = candidate
            break
        remaining -= length
    return year, month, remaining + 1


def hebrew_year_for(day: date) -> int:
    return from_gregorian(day)[0]


def describe(day: date) -> str:
    """The Hebrew date in Hebrew, as it would be written on a document."""
    year, month, number = from_gregorian(day)
    return f"⁦{number}⁩ ב{MONTH_NAMES[month]} ⁦{year}⁩"


# --------------------------------------------------------------------------- #
# The working year
# --------------------------------------------------------------------------- #

class HolidayKind(StrEnum):
    """What a day off actually is, because they are not all the same."""

    #: Nobody works. Yom Kippur, the first day of Pesach.
    FULL = "full"
    #: The afternoon before a festival: shops close early, deliveries do not run.
    EVE = "eve"
    #: חול המועד — the shop is open, thin, and nobody accepts a delivery.
    INTERMEDIATE = "intermediate"
    #: A memorial day: work happens, but not on a customer's site.
    MEMORIAL = "memorial"
    #: Widely taken but not statutory — Purim, Tisha B'Av.
    CUSTOMARY = "customary"

    @property
    def hebrew(self) -> str:
        return {
            "full": "חג — לא עובדים",
            "eve": "ערב חג — יום קצר",
            "intermediate": "חול המועד",
            "memorial": "יום זיכרון",
            "customary": "נהוג לא לעבוד",
        }[self.value]

    @property
    def working_fraction(self) -> float:
        """How much of an ordinary day's work actually gets done."""
        return {
            "full": 0.0,
            "eve": 0.5,
            "intermediate": 0.6,
            "memorial": 0.5,
            "customary": 0.5,
        }[self.value]


@dataclass(frozen=True)
class Holiday:
    """One day the shop does not work normally, and why."""

    day: date
    hebrew: str
    kind: HolidayKind

    def describe(self) -> str:
        return f"{self.hebrew} · {self.kind.hebrew}"


def _independence_day(hebrew_year: int) -> date:
    """Yom Ha'atzmaut, with the postponements that keep it off Shabbat.

    Fixed at 5 Iyar, then moved so that neither it nor the memorial day
    before it falls on or beside Shabbat: forward from Friday and Saturday to
    the Thursday, and back from Monday to the Tuesday.
    """
    nominal = to_gregorian(hebrew_year, IYAR, 5)
    weekday = nominal.weekday()  # Monday is 0, Sunday is 6
    if weekday == 4:             # Friday
        return date.fromordinal(nominal.toordinal() - 1)
    if weekday == 5:             # Saturday
        return date.fromordinal(nominal.toordinal() - 2)
    if weekday == 0:             # Monday
        return date.fromordinal(nominal.toordinal() + 1)
    return nominal


def holidays_for_hebrew_year(year: int) -> list[Holiday]:
    """Every day of one Hebrew year that changes what the shop can promise."""
    found: list[Holiday] = []

    def add(hebrew: str, day: date, kind: HolidayKind) -> None:
        found.append(Holiday(day, hebrew, kind))

    def eve_of(hebrew: str, day: date) -> None:
        add(f"ערב {hebrew}", date.fromordinal(day.toordinal() - 1), HolidayKind.EVE)

    # -- Tishrei: the month that takes the autumn out of the schedule ----- #
    rosh = to_gregorian(year, TISHREI, 1)
    eve_of("ראש השנה", rosh)
    add("ראש השנה", rosh, HolidayKind.FULL)
    add("ראש השנה ב׳", to_gregorian(year, TISHREI, 2), HolidayKind.FULL)

    kippur = to_gregorian(year, TISHREI, 10)
    eve_of("יום כיפור", kippur)
    add("יום כיפור", kippur, HolidayKind.FULL)

    sukkot = to_gregorian(year, TISHREI, 15)
    eve_of("סוכות", sukkot)
    add("סוכות", sukkot, HolidayKind.FULL)
    for offset in range(16, 21):
        add("חול המועד סוכות", to_gregorian(year, TISHREI, offset),
            HolidayKind.INTERMEDIATE)
    simchat = to_gregorian(year, TISHREI, 22)
    add("הושענא רבה", to_gregorian(year, TISHREI, 21), HolidayKind.EVE)
    add("שמחת תורה", simchat, HolidayKind.FULL)

    # -- Purim: not statutory, but the shop is half empty ------------------ #
    purim_month = ADAR_II if is_leap_year(year) else ADAR
    add("פורים", to_gregorian(year, purim_month, 14), HolidayKind.CUSTOMARY)

    # -- Nisan: the spring fortnight nobody schedules through --------------- #
    pesach = to_gregorian(year, NISAN, 15)
    eve_of("פסח", pesach)
    add("פסח", pesach, HolidayKind.FULL)
    for offset in range(16, 21):
        add("חול המועד פסח", to_gregorian(year, NISAN, offset),
            HolidayKind.INTERMEDIATE)
    seventh = to_gregorian(year, NISAN, 21)
    add("שביעי של פסח", seventh, HolidayKind.FULL)

    # -- the civic days --------------------------------------------------- #
    add("יום השואה", to_gregorian(year, NISAN, 27), HolidayKind.MEMORIAL)
    independence = _independence_day(year)
    add("יום הזיכרון", date.fromordinal(independence.toordinal() - 1),
        HolidayKind.MEMORIAL)
    add("יום העצמאות", independence, HolidayKind.FULL)

    shavuot = to_gregorian(year, SIVAN, 6)
    eve_of("שבועות", shavuot)
    add("שבועות", shavuot, HolidayKind.FULL)

    add("תשעה באב", to_gregorian(year, AV, 9), HolidayKind.CUSTOMARY)

    return sorted(found, key=lambda holiday: holiday.day)


def holidays_between(start: date, end: date) -> list[Holiday]:
    """Every relevant day in a Gregorian span, in order."""
    first = hebrew_year_for(start)
    last = hebrew_year_for(end)
    found: list[Holiday] = []
    for year in range(first, last + 2):
        found.extend(
            holiday for holiday in holidays_for_hebrew_year(year)
            if start <= holiday.day <= end
        )
    # A day can be reached twice at a year boundary; keep the first reading.
    seen: set[date] = set()
    unique: list[Holiday] = []
    for holiday in sorted(found, key=lambda h: h.day):
        if holiday.day not in seen:
            seen.add(holiday.day)
            unique.append(holiday)
    return unique


def holiday_on(day: date) -> Holiday | None:
    """What, if anything, this day is."""
    for holiday in holidays_for_hebrew_year(hebrew_year_for(day)):
        if holiday.day == day:
            return holiday
    return None


__all__ = [
    "HEBREW_EPOCH",
    "Holiday",
    "HolidayKind",
    "MONTH_NAMES",
    "days_in_month",
    "describe",
    "from_gregorian",
    "hebrew_year_for",
    "holiday_on",
    "holidays_between",
    "holidays_for_hebrew_year",
    "is_leap_year",
    "months_in_year",
    "new_year",
    "to_gregorian",
    "year_length",
]
