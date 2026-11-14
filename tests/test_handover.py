"""The folder handed over with the building, and the warranty that starts there."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.delivery.handover import (
    Cover,
    HandoverPack,
    InstalledUnit,
    Warranty,
    pack_from_job,
    render_handover,
    standard_warranties,
    write_handover,
)
from profileos.elements import Opening, build_elements
from profileos.projects.model import JobFile


@pytest.fixture
def builds():
    return build_elements([
        Opening(name="W1", width=1500, height=1400, quantity=2,
                finish="אנודייז טבעי"),
        Opening(name="D1", width=1000, height=2200, finish="אנודייז טבעי"),
    ])


@pytest.fixture
def job() -> JobFile:
    return JobFile(
        job_id="2026-114", name="וילה בבית אל",
        customer_name="אבי כהן", site_address="סולם יעקב 1",
    )


class TestTheWarrantyClock:
    def test_a_period_runs_from_the_handover_date(self):
        entry = Warranty(Cover.PROFILE, months=24, starts=date(2026, 3, 15))
        assert entry.expires == date(2028, 3, 15)

    def test_a_period_that_crosses_a_year_lands_right(self):
        entry = Warranty(Cover.GLASS, months=18, starts=date(2026, 9, 1))
        assert entry.expires == date(2028, 3, 1)

    def test_a_start_on_the_thirty_first_clamps_in_the_customer_s_favour(self):
        """Ending on the 30th is shorter than rolling into the next month."""
        entry = Warranty(Cover.PROFILE, months=1, starts=date(2026, 1, 31))
        assert entry.expires == date(2026, 2, 28)

    def test_a_live_warranty_is_live_and_an_expired_one_is_not(self):
        entry = Warranty(Cover.HARDWARE, months=12, starts=date.today())
        assert entry.is_live()
        assert not entry.is_live(date.today() + timedelta(days=400))

    def test_a_period_nobody_stated_is_not_a_period(self):
        """A commercial promise this software has no way to know."""
        entry = Warranty(Cover.FINISH, starts=date.today())
        assert not entry.is_stated
        assert entry.expires is None
        assert not entry.is_live()
        assert "לא נקבעה" in entry.describe()

    def test_the_standard_set_leaves_every_period_to_the_shop(self):
        entries = standard_warranties(starts=date.today())
        assert len(entries) == len(list(Cover))
        assert all(not entry.is_stated for entry in entries)

    def test_the_shop_s_own_periods_are_taken_where_it_states_them(self):
        entries = standard_warranties(
            starts=date(2026, 3, 1),
            months={Cover.PROFILE: 120, Cover.HARDWARE: 24},
        )
        by_cover = {entry.cover: entry for entry in entries}
        assert by_cover[Cover.PROFILE].months == 120
        assert by_cover[Cover.HARDWARE].months == 24
        assert not by_cover[Cover.GLASS].is_stated


class TestAnsweringTheCallThreeYearsLater:
    def test_it_says_whether_a_part_is_still_covered(self):
        pack = HandoverPack(handed_over_on=date.today())
        pack.warranties = standard_warranties(
            starts=date.today(),
            months={Cover.HARDWARE: 24, Cover.SEALING: 12},
        )
        assert pack.covers(Cover.HARDWARE)
        assert not pack.covers(
            Cover.SEALING, on=date.today() + timedelta(days=400)
        )

    def test_an_unstated_period_never_reads_as_covered(self):
        pack = HandoverPack(handed_over_on=date.today())
        pack.warranties = standard_warranties(starts=date.today())
        assert not pack.covers(Cover.GLASS)

    def test_asking_about_something_never_warranted_is_not_an_error(self):
        assert not HandoverPack().covers(Cover.INSTALLATION)


class TestBuildingThePack:
    def test_a_unit_fitted_twice_appears_twice(self, job, builds):
        """The occupier has two of them, and both may be called about."""
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        assert len(pack.units) == 3

    def test_the_glazing_that_was_actually_fitted_is_recorded(self, job, builds):
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        assert all(unit.glass for unit in pack.units)

    def test_the_customer_and_site_come_from_the_job(self, job, builds):
        pack = pack_from_job(job, builds=builds)
        assert pack.customer_name == "אבי כהן"
        assert pack.site_address == "סולם יעקב 1"


class TestCareInstructions:
    def test_only_the_finishes_on_this_job_are_explained(self, job, builds):
        """Anodising advice on a painted job teaches people to ignore the page."""
        pack = pack_from_job(job, builds=builds)
        titles = [title for title, _lines in pack.care_notes()]
        assert "אנודייז" in titles
        assert "צבע" not in titles

    def test_a_painted_job_gets_the_paint_page(self, job):
        pack = pack_from_job(job)
        pack.units = [InstalledUnit(mark="W1", finish="צבע RAL 9016")]
        titles = [title for title, _lines in pack.care_notes()]
        assert "צבע" in titles

    def test_drainage_is_always_explained(self, job, builds):
        """The blocked sill drain is the most common call a shop gets."""
        pack = pack_from_job(job, builds=builds)
        assert "ניקוז" in [title for title, _lines in pack.care_notes()]


class TestChecking:
    def test_an_unsigned_pack_says_so(self, job, builds):
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        assert not pack.is_signed
        assert any("נחתם" in p for p in pack.problems())

    def test_a_signed_pack_is_signed(self, job, builds):
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        pack.received_by = "אבי כהן"
        assert pack.is_signed

    def test_unstated_periods_are_named_rather_than_filled_in(self, job, builds):
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        assert any("תקופת אחריות" in p for p in pack.problems())

    def test_no_service_contact_is_a_problem(self, job, builds):
        pack = pack_from_job(job, builds=builds)
        assert any("קריאת שירות" in p for p in pack.problems())


class TestTheDocument:
    def test_an_unstated_period_prints_as_unstated_not_as_a_common_figure(
        self, job, builds
    ):
        pack = pack_from_job(job, builds=builds, handed_over_on=date.today())
        assert "לא נקבעה" in render_handover(pack)

    def test_a_stated_period_prints_its_end_date(self, job, builds):
        pack = pack_from_job(
            job, builds=builds, handed_over_on=date(2026, 3, 15),
            warranty_months={Cover.PROFILE: 120},
        )
        assert "15/03/2036" in render_handover(pack)

    def test_it_carries_a_place_for_both_signatures(self, job, builds):
        document = render_handover(pack_from_job(job, builds=builds))
        assert "נמסר על ידי" in document
        assert "התקבל על ידי" in document

    def test_it_is_written_right_to_left(self, job, builds):
        assert 'dir="rtl"' in render_handover(pack_from_job(job, builds=builds))

    def test_it_is_written_where_it_was_asked_for(self, job, builds, tmp_path):
        pack = pack_from_job(job, builds=builds)
        target = write_handover(pack, tmp_path / "out" / "handover.html")
        assert target.exists()
        assert "תיק מסירה" in target.read_text(encoding="utf-8")
