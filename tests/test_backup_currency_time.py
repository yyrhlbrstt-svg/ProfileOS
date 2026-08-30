"""A copy of the shop, the rate it buys at, and the hours it really worked."""

from __future__ import annotations

import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from profileos.core.backup import (
    list_backups,
    plan_restore,
    prune,
    read_manifest,
    restore,
    write_backup,
)
from profileos.core.errors import ProfileOSError
from profileos.erp.currency import HOME, Rate, RateBook
from profileos.erp.timesheets import Entry, TimeBook, minutes_between


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    data = tmp_path / "data"
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(data))
    reload_settings()
    data.mkdir(parents=True, exist_ok=True)
    yield data
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _populate(data: Path) -> None:
    from profileos.projects import default_customers, default_store

    store = default_store()
    store.save(store.create(name="דירה בבית אל"))
    default_customers().add(name="משה כהן")


class TestBackup:
    def test_everything_the_shop_has_goes_in_one_file(self, shop, tmp_path):
        _populate(shop)
        archive = write_backup(tmp_path / "backups")
        manifest = read_manifest(archive)
        assert manifest.contents["jobs"] == 1
        assert manifest.contents["customers"] == 1

    def test_a_folder_that_does_not_exist_yet_is_a_folder(self, shop, tmp_path):
        """Not a filename — that writes a backup nothing can ever find."""
        archive = write_backup(tmp_path / "not-created-yet")
        assert archive.suffix == ".zip"
        assert archive.parent.is_dir()
        assert list_backups(archive.parent)

    def test_locks_and_half_written_files_are_left_out(self, shop, tmp_path):
        _populate(shop)
        (shop / "customers.json.lock").write_text("{}")
        (shop / "half.tmp").write_text("x")
        names = zipfile.ZipFile(write_backup(tmp_path / "b")).namelist()
        assert not any(name.endswith((".lock", ".tmp")) for name in names)

    def test_an_interrupted_backup_never_replaces_a_good_one(self, shop, tmp_path):
        _populate(shop)
        folder = tmp_path / "b"
        first = write_backup(folder)
        assert not list(folder.glob("*.part"))
        assert first.is_file()

    def test_a_file_that_is_not_a_backup_is_refused_by_name(self, tmp_path):
        stranger = tmp_path / "holiday.zip"
        with zipfile.ZipFile(stranger, "w") as archive:
            archive.writestr("photo.jpg", "x")
        with pytest.raises(ProfileOSError):
            read_manifest(stranger)

    def test_a_corrupt_file_is_refused_by_name(self, tmp_path):
        broken = tmp_path / "broken.zip"
        broken.write_text("not a zip at all")
        with pytest.raises(ProfileOSError):
            read_manifest(broken)


class TestRestoring:
    def test_a_plan_says_what_would_be_replaced_and_changes_nothing(self, shop, tmp_path):
        _populate(shop)
        archive = write_backup(tmp_path / "b")

        from profileos.projects import default_store

        store = default_store()
        store.save(store.create(name="נוסף אחרי הגיבוי"))

        plan = plan_restore(archive)
        assert plan.replaces_existing
        assert any("אחורה" in warning for warning in plan.warnings)
        assert len(default_store().all()) == 2

    def test_the_current_folder_is_moved_aside_not_deleted(self, shop, tmp_path):
        _populate(shop)
        archive = write_backup(tmp_path / "b")

        from profileos.projects import default_store

        store = default_store()
        store.save(store.create(name="נוסף אחרי הגיבוי"))

        _root, aside = restore(archive)
        assert aside is not None and aside.is_dir()
        assert len(default_store().all()) == 1

    def test_what_was_in_the_backup_comes_back(self, shop, tmp_path):
        _populate(shop)
        archive = write_backup(tmp_path / "b")

        from profileos.projects import default_customers, default_store

        restore(archive)
        assert [job.name for job in default_store().all()] == ["דירה בבית אל"]
        assert [c.name for c in default_customers().all()] == ["משה כהן"]

    def test_a_backup_naming_a_path_outside_the_folder_is_refused(self, shop, tmp_path):
        """A zip can name ../../etc; this one does not get to write there."""
        hostile = tmp_path / "hostile.zip"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr("../escaped.txt", "x")
            archive.writestr(
                "profileos-backup.json",
                json.dumps({"created": "2026-01-01T00:00:00", "version": "0.1.0"}),
            )
        with pytest.raises(ProfileOSError):
            restore(hostile)

    def test_old_backups_are_pruned_so_the_disk_does_not_fill(self, shop, tmp_path):
        _populate(shop)
        folder = tmp_path / "b"
        for _ in range(5):
            write_backup(folder)
        assert len(prune(folder, keep=2)) == 3
        assert len(list_backups(folder)) == 2


class TestExchangeRates:
    def _book(self) -> RateBook:
        book = RateBook()
        book.record(Rate("EUR", 4.05, date(2026, 3, 1), "בנק"))
        book.record(Rate("EUR", 4.22, date(2026, 6, 1), "בנק"))
        return book

    def test_a_quote_is_costed_at_the_rate_that_applied_when_it_was_priced(self):
        """Otherwise reopening an old quote silently reprices it."""
        book = self._book()
        assert book.convert(1000, "EUR", on=date(2026, 3, 15)).home_amount == 4050.0
        assert book.convert(1000, "EUR", on=date(2026, 6, 15)).home_amount == 4220.0

    def test_shekels_need_no_rate(self):
        conversion = RateBook().convert(1000, HOME)
        assert conversion.home_amount == 1000.0
        assert conversion.is_reliable

    def test_a_currency_with_no_rate_is_reported_not_guessed(self):
        conversion = RateBook().convert(1000, "EUR")
        assert conversion.home_amount == 0.0
        assert not conversion.is_reliable

    def test_an_old_rate_is_used_but_flagged(self):
        conversion = self._book().convert(1000, "EUR", on=date(2026, 12, 1))
        assert conversion.home_amount > 0
        assert any("ימים" in warning for warning in conversion.warnings)

    def test_a_rate_with_no_source_is_flagged(self):
        book = RateBook()
        book.record(Rate("EUR", 4.0, date.today()))
        assert any("מקור" in w for w in book.convert(100, "EUR").warnings)

    def test_a_negative_rate_is_refused(self):
        with pytest.raises(ProfileOSError):
            Rate("EUR", -1.0, date.today())

    def test_the_shekel_has_no_rate_against_itself(self):
        with pytest.raises(ProfileOSError):
            Rate(HOME, 1.0, date.today())

    def test_entering_a_rate_twice_for_a_day_replaces_it(self):
        book = RateBook()
        book.record(Rate("EUR", 4.0, date(2026, 6, 1), "first"))
        book.record(Rate("EUR", 4.1, date(2026, 6, 1), "corrected"))
        assert len(book.history("EUR")) == 1
        assert book.latest("EUR").per_unit == 4.1

    def test_exposure_says_how_much_moves_with_the_rate(self):
        report = self._book().exposure(
            {"ILS": 20000, "EUR": 2000}, on=date(2026, 6, 15)
        )
        assert report["foreign"] == pytest.approx(8440.0)
        assert report["share_pct"] == pytest.approx(29.7, abs=0.2)
        assert report["if_rate_moves_5pct"] == pytest.approx(422.0)

    def test_it_survives_the_program_closing(self, tmp_path):
        path = tmp_path / "rates.json"
        RateBook(path).record(Rate("EUR", 4.22, date(2026, 6, 1), "בנק"))
        assert RateBook(path).latest("EUR").per_unit == 4.22


class TestTimesheets:
    def test_a_span_across_midnight_is_not_negative(self):
        """A late installation is not a data-entry error."""
        assert minutes_between("22:00", "02:30") == 270

    def test_an_ordinary_shift_reads_as_written(self):
        assert minutes_between("07:30", "16:15") == 525

    def test_a_time_that_is_not_a_time_is_refused(self):
        with pytest.raises(ProfileOSError):
            minutes_between("morning", "16:00")

    def test_an_impossible_hour_is_refused(self):
        with pytest.raises(ProfileOSError):
            minutes_between("29:00", "30:00")

    def test_an_entry_with_nobody_behind_it_is_refused(self):
        with pytest.raises(ProfileOSError):
            Entry(person="", job_id="J-1", minutes=60)

    def test_twenty_hours_in_one_day_is_caught_as_a_typo(self):
        with pytest.raises(ProfileOSError):
            Entry(person="דני", job_id="J-1", minutes=20 * 60)

    def test_hours_add_up_by_job_and_by_operation(self):
        book = TimeBook()
        book.book("דני", "J-1", 480, operation="assembly")
        book.book("יוסי", "J-1", 240, operation="glazing")
        book.book("דני", "J-2", 60, operation="assembly")
        assert book.hours_on_job("J-1") == 12.0
        assert book.by_operation("J-1") == {"assembly": 8.0, "glazing": 4.0}

    def test_cost_uses_the_rate_recorded_or_the_default(self):
        book = TimeBook()
        book.book("דני", "J-1", 60, rate=95)
        book.book("יוסי", "J-1", 60)
        assert book.cost_of_job("J-1", default_rate=80) == 175.0

    def test_rework_is_visible_because_it_is_booked_as_rework(self):
        book = TimeBook()
        book.book("דני", "J-1", 480)
        book.book("דני", "J-1", 120, rework=True)
        assert book.rework_share("J-1") == 20.0

    def test_booked_hours_against_the_estimate_say_which_way_it_went(self):
        book = TimeBook()
        book.book("דני", "J-1", 20 * 60 // 2)
        over = book.against_estimate("J-1", 6.0)
        assert over["difference_hours"] > 0
        assert "חריגה" in over["verdict"]

        under = book.against_estimate("J-1", 20.0)
        assert "מתחת" in under["verdict"]

    def test_it_survives_the_program_closing(self, tmp_path):
        path = tmp_path / "time.json"
        TimeBook(path).book("דני", "J-1", 60)
        assert len(TimeBook(path)) == 1


class TestRealHoursReachTheMargin:
    def test_booked_hours_replace_the_estimate_rather_than_joining_it(self, shop):
        from profileos.projects import JobFile
        from profileos.projects.costing import cost_job

        class Quote:
            net_price = 48000.0
            cost = 31000.0
            material_cost = 22000.0
            labour_cost = 9000.0

        job = JobFile(job_id="J-2026-0007", name="דירה", customer_name="כהן")
        book = TimeBook()
        book.book("דני", "J-2026-0007", 600, rate=95)

        without = cost_job(job, quotation=Quote())
        with_hours = cost_job(job, quotation=Quote(), timesheets=book)

        labour = [line for line in with_hours.lines if line.category == "labour"]
        assert len(labour) == 1
        assert "שעות שנרשמו" in labour[0].hebrew
        assert with_hours.actual_cost != without.actual_cost

    def test_a_job_full_of_rework_says_so_on_the_margin(self, shop):
        from profileos.projects import JobFile
        from profileos.projects.costing import cost_job

        job = JobFile(job_id="J-1", name="דירה")
        book = TimeBook()
        book.book("דני", "J-1", 300)
        book.book("דני", "J-1", 300, rework=True)
        costing = cost_job(job, timesheets=book)
        assert any("תיקון חוזר" in warning for warning in costing.warnings)
