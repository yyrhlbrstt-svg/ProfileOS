"""Two people on one folder, and the program nobody has cut yet."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from profileos.cnc.proving import Proof, ProvingRecord, dry_run
from profileos.core.errors import ProfileOSError
from profileos.core.sharing import (
    LOCK_EXPIRY_SECONDS,
    Locked,
    Stale,
    acquire,
    break_lock,
    fingerprint,
    guarded,
    holder_of,
    locked,
    release,
    shared_folder_warning,
)


@pytest.fixture
def shared(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _foreign_lock(path: Path, *, minutes_ago: float = 0.0) -> None:
    """A lock as another machine would have written it."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    path.with_suffix(path.suffix + ".lock").write_text(
        json.dumps({
            "user": "אבי", "machine": "office-pc",
            "since": since.isoformat(), "pid": 424242,
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class TestTakingTurns:
    def test_a_second_machine_is_told_who_has_it(self, shared):
        _foreign_lock(shared)
        with pytest.raises(Locked) as raised:
            acquire(shared, wait=0.1)
        assert "אבי" in str(raised.value)
        assert "office-pc" in str(raised.value)

    def test_the_lock_is_released_when_the_write_finishes(self, shared):
        with locked(shared):
            assert holder_of(shared) is not None
        assert holder_of(shared) is None

    def test_a_lock_left_by_a_machine_that_went_away_expires(self, shared):
        """A laptop that went flat at four must not stop the shop at half past."""
        _foreign_lock(shared, minutes_ago=LOCK_EXPIRY_SECONDS / 60.0 + 5)
        assert holder_of(shared).is_stale
        acquire(shared, wait=0.1)
        assert holder_of(shared).is_me
        release(shared)

    def test_a_fresh_lock_does_not(self, shared):
        _foreign_lock(shared, minutes_ago=1)
        assert not holder_of(shared).is_stale

    def test_breaking_a_lock_says_whose_it_was(self, shared):
        _foreign_lock(shared)
        broken = break_lock(shared)
        assert broken.user == "אבי"
        assert holder_of(shared) is None

    def test_an_unreadable_lock_is_treated_as_broken(self, shared):
        """A lock nobody can read holds nothing, and must not hold up the shop."""
        shared.with_suffix(shared.suffix + ".lock").write_text("{ not json")
        current = holder_of(shared)
        assert current is not None and current.is_stale
        acquire(shared, wait=0.1)
        assert holder_of(shared).is_me
        release(shared)

    def test_the_same_process_does_not_deadlock_against_itself(self, shared):
        with locked(shared):
            acquire(shared, wait=0.1)
        release(shared)

    def test_releasing_a_lock_nobody_holds_is_harmless(self, shared):
        release(shared)


class TestNotErasingSomebodyElsesWork:
    def test_a_write_from_stale_data_is_refused(self, shared):
        """Two people read at nine; the second write must not silently win."""
        mark = fingerprint(shared)
        shared.write_text('{"changed": "by the other estimator"}', encoding="utf-8")
        with pytest.raises(Stale):
            with guarded(shared, since=mark):
                pass

    def test_a_write_from_current_data_goes_through(self, shared):
        mark = fingerprint(shared)
        with guarded(shared, since=mark):
            pass

    def test_the_lock_is_released_even_when_the_write_is_refused(self, shared):
        mark = fingerprint(shared)
        shared.write_text("{}  ", encoding="utf-8")
        with pytest.raises(Stale):
            with guarded(shared, since=mark):
                pass
        assert holder_of(shared) is None


class TestWarningAboutSyncedFolders:
    @pytest.mark.parametrize("folder,expected", [
        ("/Users/x/Dropbox/ProfileOS", "Dropbox"),
        ("C:/Users/x/OneDrive/data", "OneDrive"),
        ("/home/x/Google Drive/shop", "Google Drive"),
    ])
    def test_a_synced_folder_is_named(self, folder, expected):
        assert expected in shared_folder_warning(Path(folder))

    def test_an_ordinary_folder_says_nothing(self):
        assert shared_folder_warning(Path("/srv/profileos/data")) == ""


class TestSavingWhileShared:
    def test_a_job_saves_and_leaves_no_lock_behind(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.projects import default_store

        store = default_store()
        store.save(store.create(name="בדיקה"))
        assert not list(store.root.glob("*.lock"))
        monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
        reload_settings()

    def test_work_is_never_lost_to_somebody_elses_lock(self, tmp_path, monkeypatch):
        """A colleague leaving a window open must not cost an estimator their typing."""
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.projects import default_store

        store = default_store()
        job = store.create(name="ראשון")
        _foreign_lock(store.path_for(job.job_id))

        job.name = "עודכן למרות הנעילה"
        store.save(job)
        assert store.load(job.job_id).name == "עודכן למרות הנעילה"
        monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
        reload_settings()


class TestProvingAProgramOnARealMachine:
    def test_nothing_is_proven_to_begin_with(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        assert not record.is_proven("elumatec.ncx", "SBZ-151")
        assert "מעולם לא נחתך" in record.banner("elumatec.ncx", "SBZ-151")

    def test_proving_takes_the_banner_off(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        record.record(Proof(
            driver="elumatec.ncx", machine="SBZ-151", proved_by="דאדי",
            findings="מוט פסולת, סטייה 0.2 מ״מ",
        ))
        assert record.is_proven("elumatec.ncx", "SBZ-151")
        assert record.banner("elumatec.ncx", "SBZ-151") is None

    def test_proving_one_machine_says_nothing_about_the_next(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        record.record(Proof(
            driver="elumatec.ncx", machine="SBZ-151", proved_by="דאדי",
            findings="נמדד",
        ))
        assert not record.is_proven("elumatec.ncx", "Emmegi")
        assert not record.is_proven("fom.cam", "SBZ-151")

    def test_a_proof_with_nobody_behind_it_is_refused(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        with pytest.raises(ProfileOSError):
            record.record(Proof(
                driver="d", machine="m", proved_by="", findings="נמדד",
            ))

    def test_a_proof_with_no_findings_is_refused(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        with pytest.raises(ProfileOSError):
            record.record(Proof(driver="d", machine="m", proved_by="דאדי"))

    def test_a_failed_proving_blocks_rather_than_clears(self, tmp_path):
        record = ProvingRecord(tmp_path / "proving.json")
        record.record(Proof(
            driver="d", machine="m", proved_by="דאדי",
            findings="הקדח יצא 3 מ״מ מהמקום", accepted=False,
        ))
        assert not record.is_proven("d", "m")
        assert "נדחה" in record.banner("d", "m")

    def test_it_survives_the_program_closing(self, tmp_path):
        path = tmp_path / "proving.json"
        ProvingRecord(path).record(Proof(
            driver="d", machine="m", proved_by="דאדי", findings="נמדד",
        ))
        assert ProvingRecord(path).is_proven("d", "m")


class TestTheCheapHalfThatNeedsNoMachine:
    class _Op:
        def __init__(self, x=0.0, y=0.0, z=0.0, tool_id="T1"):
            self.x, self.y, self.z, self.tool_id = x, y, z, tool_id

    class _Machine:
        id = "SBZ-151"
        post_processor = "elumatec.ncx"

        class travel:
            x, y, z = 4000.0, 300.0, 200.0

        class _Tool:
            def __init__(self, tool_id):
                self.id = tool_id

        tools = [_Tool("T1"), _Tool("T2")]

    class _Job:
        def __init__(self, operations):
            self.operations = operations

    def test_an_operation_past_the_travel_is_caught_before_the_scrap(self):
        result = dry_run(
            self._Job([self._Op(x=9000.0)]), self._Machine(),
        )
        assert not result.is_clean
        assert any("מהלך" in problem for problem in result.problems)

    def test_a_tool_that_is_not_in_the_changer_is_caught(self):
        result = dry_run(
            self._Job([self._Op(tool_id="T9")]), self._Machine(),
        )
        assert any("מחסנית" in problem for problem in result.problems)

    def test_a_program_inside_the_limits_passes_without_claiming_to_be_right(self):
        result = dry_run(
            self._Job([self._Op(x=1000.0, y=50.0, z=10.0)]), self._Machine(),
        )
        assert result.is_clean
        assert "אין הוכחה" in result.describe()

    def test_an_empty_program_is_a_problem(self):
        assert not dry_run(self._Job([]), self._Machine()).is_clean

    def test_a_machine_with_no_limits_says_it_could_not_check(self):
        class Bare:
            id = "unknown"

        result = dry_run(self._Job([self._Op()]), Bare())
        assert any("מהלך" in note for note in result.notes)
