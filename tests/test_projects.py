"""Job files: the record the shop keeps of the work it has taken on."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.core.errors import ProfileOSError
from profileos.elements.model import ElementSchedule, Opening
from profileos.projects import (
    CustomerBook,
    JobError,
    JobFile,
    JobStatus,
    JobStore,
)


@pytest.fixture
def store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "jobs")


@pytest.fixture
def book(tmp_path) -> CustomerBook:
    return CustomerBook(tmp_path / "customers.json")


class TestStatus:
    def test_every_status_has_a_hebrew_name(self):
        for status in JobStatus:
            assert status.hebrew and status.hebrew != status.value

    def test_installed_and_lost_are_not_open_work(self):
        assert not JobStatus.INSTALLED.is_open
        assert not JobStatus.LOST.is_open
        assert JobStatus.WON.is_open

    def test_a_job_cannot_skip_to_installed(self):
        job = JobFile(job_id="J-1", name="x")
        ok, reason = job.can_advance(JobStatus.INSTALLED)
        assert not ok and "אי אפשר לעבור" in reason

    def test_advancing_records_when_it_happened(self):
        job = JobFile(job_id="J-1", name="x")
        job.advance(JobStatus.QUOTED, "נשלח במייל")
        assert job.status is JobStatus.QUOTED
        assert job.history[-1].note == "נשלח במייל"
        assert job.history[-1].at.startswith(str(date.today().year))

    def test_a_lost_job_reopens_as_an_enquiry_not_as_won(self):
        job = JobFile(job_id="J-1", name="x", status=JobStatus.LOST)
        with pytest.raises(JobError):
            job.advance(JobStatus.WON)
        assert job.advance(JobStatus.ENQUIRY).status is JobStatus.ENQUIRY

    def test_the_same_status_twice_is_refused(self):
        job = JobFile(job_id="J-1", name="x")
        with pytest.raises(JobError):
            job.advance(JobStatus.ENQUIRY)


class TestStore:
    def test_a_saved_job_reads_back_identical(self, store):
        job = store.create("וילה", system_id="klil-7300")
        job.schedule = ElementSchedule(
            name="וילה",
            openings=[Opening(element_id="W-01", name="W-01", width=1200, height=1400)],
        )
        store.save(job)
        again = store.load(job.job_id)
        assert again.model_dump() == job.model_dump()
        assert again.schedule.openings[0].element_id == "W-01"

    def test_numbers_run_by_year_and_do_not_repeat(self, store):
        first = store.create("א")
        second = store.create("ב")
        year = date.today().year
        assert first.job_id == f"J-{year}-0001"
        assert second.job_id == f"J-{year}-0002"
        assert store.next_id() == f"J-{year}-0003"

    def test_an_unreadable_file_does_not_hide_the_others(self, store):
        store.create("טוב")
        (store.root / "broken.json").write_text("{not json", encoding="utf-8")
        assert [job.name for job in store.all()] == ["טוב"]

    def test_missing_job_is_reported_in_hebrew(self, store):
        with pytest.raises(ProfileOSError) as error:
            store.load("J-nope")
        assert "לא נמצא" in str(error.value)

    def test_open_jobs_exclude_the_finished_ones(self, store):
        live = store.create("חי")
        done = store.create("גמור")
        done.status = JobStatus.INSTALLED
        store.save(done)
        assert [job.job_id for job in store.open_jobs()] == [live.job_id]

    def test_backlog_counts_only_work_won_and_not_yet_installed(self, store):
        quoted = store.create("בהצעה")
        quoted.advance(JobStatus.QUOTED)
        quoted.record_quote(10_000)
        store.save(quoted)

        won = store.create("הוזמן")
        won.advance(JobStatus.QUOTED)
        won.record_quote(50_000)
        won.advance(JobStatus.WON)
        store.save(won)

        assert store.backlog_value() == 50_000
        assert store.pipeline()["quoted"] == 1

    def test_a_half_written_file_never_replaces_a_good_one(self, store, monkeypatch):
        """The save is atomic: a crash mid-write leaves the old version intact."""
        job = store.create("שמור")
        original = store.path_for(job.job_id).read_text(encoding="utf-8")

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("profileos.projects.store.os.replace", explode)
        job.name = "חדש"
        with pytest.raises(OSError):
            store.save(job)
        assert store.path_for(job.job_id).read_text(encoding="utf-8") == original

    def test_area_and_opening_count_follow_the_schedule(self, store):
        job = store.create("שטח")
        job.schedule = ElementSchedule(
            name="שטח",
            openings=[
                Opening(element_id="W-01", name="W-01", width=2000, height=1500, quantity=2),
                Opening(element_id="W-02", name="W-02", width=1000, height=1000),
            ],
        )
        assert job.opening_count == 2, "two drawings"
        assert job.unit_count == 3, "three windows to make"
        assert job.total_area == pytest.approx(2 * 3.0 + 1.0, abs=0.01)


class TestCustomers:
    def test_customers_are_numbered_and_sorted_by_name(self, book):
        book.add("ריבלין")
        book.add("אבני דרך")
        names = [customer.name for customer in book.all()]
        assert names == ["אבני דרך", "ריבלין"]
        assert {c.customer_id for c in book.all()} == {"C-0001", "C-0002"}

    def test_a_customer_without_a_name_is_refused(self, book):
        with pytest.raises(ProfileOSError):
            book.add("   ")

    def test_details_survive_a_round_trip(self, book):
        book.add("כהן בנייה", phone="02-9971234", city="עפרה", tax_id="514882201")
        customer = book.all()[0]
        assert customer.phone == "02-9971234"
        assert "עפרה" in customer.describe()

    def test_updating_a_customer_replaces_rather_than_duplicates(self, book):
        customer = book.add("לוי")
        customer.phone = "050-1112222"
        book.update(customer)
        assert len(book.all()) == 1
        assert book.get(customer.customer_id).phone == "050-1112222"
