"""Who changed the price, in a file that cannot quietly lose a line."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from profileos.core.audit import (
    GENESIS,
    Action,
    AuditLog,
    Entry,
    current_person,
)


@pytest.fixture
def log(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl")


class TestRecording:
    def test_an_entry_records_the_value_not_just_the_event(self, log):
        entry = log.record(
            Action.CHANGED, "quote:2026-114",
            field_name="net_price", before=96_000.0, after=86_000.0,
        )
        assert "96,000" in entry.describe()
        assert "86,000" in entry.describe()

    def test_the_first_entry_points_at_the_genesis_value(self, log):
        assert log.record(Action.CREATED, "job:A").previous == GENESIS

    def test_each_entry_points_at_the_one_before_it(self, log):
        first = log.record(Action.CREATED, "job:A")
        second = log.record(Action.CHANGED, "job:A", field_name="name")
        assert second.previous == first.digest()

    def test_the_person_is_recorded_as_whoever_the_machine_says(self, log, monkeypatch):
        monkeypatch.setenv("PROFILEOS_USER", "דנה")
        assert log.record(Action.CHANGED, "job:A").person == "דנה"

    def test_an_explicit_person_wins_over_the_machine(self, log):
        assert log.record(Action.CHANGED, "job:A", person="יוסי").person == "יוסי"

    def test_writing_never_rewrites_what_is_already_there(self, log):
        log.record(Action.CREATED, "job:A")
        first_line = log.path.read_text(encoding="utf-8").splitlines()[0]
        log.record(Action.CHANGED, "job:A")
        assert log.path.read_text(encoding="utf-8").splitlines()[0] == first_line


class TestRecordingChanges:
    def test_only_the_fields_that_moved_are_recorded(self):
        """A log that repeats the unchanged is a log nobody reads."""
        before = {"name": "וילה", "price": 96_000, "customer": "אבי"}
        after = {"name": "וילה", "price": 86_000, "customer": "אבי"}
        log = AuditLog(_path())
        written = log.record_changes("quote:A", before, after)
        assert [entry.field_name for entry in written] == ["price"]

    def test_it_works_on_records_as_well_as_dictionaries(self, log):
        from profileos.projects.model import JobFile, JobStatus

        before = JobFile(job_id="A", name="וילה", quote_total=96_000.0)
        after = before.model_copy(update={"quote_total": 86_000.0})
        written = log.record_changes("job:A", before, after)
        assert [entry.field_name for entry in written] == ["quote_total"]
        assert written[0].after == pytest.approx(86_000.0)

    def test_a_new_field_appearing_counts_as_a_change(self, log):
        written = log.record_changes("job:A", {"a": 1}, {"a": 1, "b": 2})
        assert [entry.field_name for entry in written] == ["b"]

    def test_nothing_moving_records_nothing(self, log):
        assert log.record_changes("job:A", {"a": 1}, {"a": 1}) == []
        assert len(log) == 0

    def test_a_value_json_cannot_hold_is_reduced_rather_than_dropped(self, log):
        from datetime import date

        written = log.record_changes(
            "job:A", {"due": date(2026, 1, 1)}, {"due": date(2026, 2, 1)}
        )
        assert written[0].after == "2026-02-01"


class TestReading:
    def test_the_story_of_one_record_is_readable_on_its_own(self, log):
        log.record(Action.CREATED, "job:A")
        log.record(Action.CHANGED, "job:B")
        log.record(Action.CHANGED, "job:A", field_name="price")
        assert len(log.for_subject("job:A")) == 2

    def test_entries_by_one_person_are_findable(self, log):
        log.record(Action.CHANGED, "job:A", person="דנה")
        log.record(Action.CHANGED, "job:B", person="יוסי")
        assert len(log.by_person("דנה")) == 1

    def test_recent_comes_back_newest_first(self, log):
        log.record(Action.CREATED, "job:A")
        log.record(Action.CREATED, "job:B")
        assert [entry.subject for entry in log.recent()] == ["job:B", "job:A"]

    def test_a_time_window_can_be_asked_for(self, log):
        log.record(Action.CREATED, "job:A")
        now = datetime.now(timezone.utc)
        assert len(log.between(now - timedelta(hours=1), now + timedelta(hours=1))) == 1
        assert len(log.between(now + timedelta(days=1), now + timedelta(days=2))) == 0

    def test_an_empty_log_reads_as_empty_rather_than_failing(self, log):
        assert log.all() == []
        assert len(log) == 0


class TestTheChain:
    def test_an_untouched_log_verifies(self, log):
        for index in range(5):
            log.record(Action.CHANGED, f"job:{index}")
        result = log.verify()
        assert result.ok
        assert result.entries == 5

    def test_a_line_removed_from_the_middle_is_caught(self, log):
        """The whole point: this is what a log file cannot normally tell you."""
        for index in range(5):
            log.record(Action.CHANGED, f"job:{index}")
        lines = log.path.read_text(encoding="utf-8").splitlines()
        del lines[2]
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = log.verify()
        assert not result.ok
        assert result.broken_at == 3
        assert "נמחקה" in result.reason

    def test_a_figure_edited_in_place_is_caught(self, log):
        log.record(Action.CHANGED, "quote:A", field_name="price", after=96_000.0)
        log.record(Action.CHANGED, "quote:A", field_name="price", after=86_000.0)

        lines = log.path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["after"] = 86_000.0
        lines[0] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = log.verify()
        assert not result.ok
        assert result.broken_at == 1
        assert "נערך" in result.reason

    def test_a_line_removed_from_the_front_is_caught(self, log):
        """Genesis is a fixed value so a truncated file does not verify."""
        for index in range(3):
            log.record(Action.CHANGED, f"job:{index}")
        lines = log.path.read_text(encoding="utf-8").splitlines()
        log.path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
        assert not log.verify().ok

    def test_appending_after_a_verified_log_keeps_it_verified(self, log):
        log.record(Action.CREATED, "job:A")
        assert log.verify().ok
        log.record(Action.CHANGED, "job:A")
        assert log.verify().ok

    def test_a_missing_file_verifies_as_empty_not_as_broken(self, log):
        result = log.verify()
        assert result.ok
        assert result.entries == 0

    def test_the_verdict_reads_as_a_sentence(self, log):
        log.record(Action.CREATED, "job:A")
        assert "שלמה" in log.verify().describe()


class TestWhoIsAtTheKeyboard:
    def test_the_environment_names_the_person(self, monkeypatch):
        monkeypatch.setenv("PROFILEOS_USER", "מאיה")
        assert current_person() == "מאיה"

    def test_with_nothing_to_go_on_it_says_so_rather_than_guessing(self, monkeypatch):
        for variable in ("PROFILEOS_USER", "USER", "USERNAME", "LOGNAME"):
            monkeypatch.delenv(variable, raising=False)
        assert current_person() == "לא ידוע"


def _path():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "audit.jsonl"
