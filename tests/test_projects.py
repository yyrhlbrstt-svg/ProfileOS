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


class TestDossier:
    """The print pack: the whole job on one page a workshop can carry."""

    @pytest.fixture
    def builds(self):
        from profileos.elements.builder import ElementBuilder
        from profileos.elements.model import Cell, HingeSide, OpeningType, Sash

        builder = ElementBuilder()
        return [
            builder.build(Opening(
                element_id="W-01", name="W-01", width=2400, height=1800, quantity=4,
                mullion_positions=[800, 1600],
                cells=[Cell(column=1, row=0, sash=Sash(
                    opening_type=OpeningType.TILT_TURN, hinge_side=HingeSide.LEFT))],
            )),
            builder.build(Opening(
                element_id="D-01", name="D-01", width=1000, height=2200,
                cells=[Cell(column=0, row=0, sash=Sash(
                    opening_type=OpeningType.DOOR, hinge_side=HingeSide.RIGHT))],
            )),
        ]

    def test_the_pack_is_one_self_contained_page(self, builds):
        from profileos.projects import render_dossier

        job = JobFile(job_id="J-2026-0009", name="בדיקה", customer_name="לקוח")
        html = render_dossier(job, builds)
        assert html.startswith("<!doctype html>") and html.rstrip().endswith("</html>")
        # Nothing may be fetched at open time: no scripts, no remote stylesheets.
        # The SVG namespace is an identifier, not a request, so it does not count.
        assert "<script" not in html
        without_namespaces = html.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in without_namespaces
        assert "https://" not in without_namespaces

    def test_quantities_multiply_through_to_the_cut_list(self, builds):
        from profileos.projects.dossier import _cut_rows

        rows = _cut_rows(builds)
        single = _cut_rows(builds[1:])
        assert rows and single
        # The window is ordered four times; its bars appear four times over.
        total = sum(quantity for _p, _l, quantity, _m in rows)
        assert total > sum(quantity for _p, _l, quantity, _m in single) * 4

    def test_identical_cuts_from_two_openings_become_one_line(self):
        from profileos.elements.builder import ElementBuilder
        from profileos.projects.dossier import _cut_rows

        builder = ElementBuilder()
        same = [
            builder.build(Opening(element_id=f"W-0{n}", name=f"W-0{n}",
                                  width=1200, height=1400))
            for n in (1, 2)
        ]
        rows = _cut_rows(same)
        one = _cut_rows(same[:1])
        assert len(rows) == len(one), "the saw counts one line, twice the pieces"
        assert sum(r[2] for r in rows) == 2 * sum(r[2] for r in one)

    def test_typical_figures_carry_a_not_for_production_banner(self, builds):
        from profileos.projects import render_dossier

        job = JobFile(job_id="J-1", name="x")
        html = render_dossier(job, builds)
        assert ("לא לייצור" in html) is not all(b.may_be_cut for b in builds)

    def test_a_job_with_no_openings_still_renders_and_says_so(self):
        from profileos.projects import render_dossier

        html = render_dossier(JobFile(job_id="J-1", name="ריק"), [])
        assert "עדיין אין פתחים" in html

    def test_writing_creates_the_folder(self, tmp_path, builds):
        from profileos.projects import write_dossier

        target = tmp_path / "packs" / "J-1.html"
        written = write_dossier(JobFile(job_id="J-1", name="x"), builds, target)
        assert written.is_file() and written.read_text(encoding="utf-8")

    def test_glass_and_hardware_are_gathered_by_kind(self, builds):
        from profileos.projects.dossier import _glass_rows, _hardware_rows

        panes = _glass_rows(builds)
        assert panes and all(quantity > 0 for _s, _b, quantity, _a, _sf in panes)
        hardware = _hardware_rows(builds)
        assert hardware and len({code for code, *_ in hardware}) == len(hardware)


class TestSeeding:
    """The starting order book a fresh installation opens on."""

    @pytest.fixture
    def seeded(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        yield
        monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
        reload_settings()

    def _seed(self, **kwargs):
        from typer.testing import CliRunner

        from profileos.cli import app

        args = ["seed"] + [f"--{key}" for key, value in kwargs.items() if value]
        return CliRunner().invoke(app, args)

    def test_a_fresh_installation_gets_a_believable_order_book(self, seeded):
        from profileos.projects import JobStatus, default_customers, default_store

        assert self._seed(quiet=True).exit_code == 0
        jobs = default_store().all()
        assert len(jobs) == 4
        assert default_customers().all()
        assert any(job.status is JobStatus.IN_PRODUCTION for job in jobs)
        assert default_store().backlog_value() > 0

    def test_seeding_twice_never_scribbles_over_real_work(self, seeded):
        from profileos.projects import default_store

        self._seed(quiet=True)
        store = default_store()
        job = store.all()[0]
        job.name = "עבודה אמיתית של הלקוח"
        store.save(job)

        self._seed(quiet=True)
        assert len(store.all()) == 4
        assert any(j.name == "עבודה אמיתית של הלקוח" for j in store.all())
