"""Tests for the storage layer: the guarantee that a save is never lost."""

from __future__ import annotations

import json
import os

import pytest

from profileos.core import storage
from profileos.core.storage import (
    StorageError,
    atomic_write_text,
    backup_path,
    read_json,
    write_json,
)


class TestWriting:
    def test_a_save_lands_and_keeps_the_previous_version(self, tmp_path):
        target = tmp_path / "book.json"
        write_json(target, {"v": 1})
        write_json(target, {"v": 2})
        assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
        assert json.loads(backup_path(target).read_text(encoding="utf-8")) == {"v": 1}

    def test_a_crash_before_the_rename_leaves_the_old_file_whole(self, tmp_path, monkeypatch):
        """Power cut between writing the new copy and putting it in place."""
        target = tmp_path / "book.json"
        write_json(target, {"customers": ["כהן"]})

        def power_cut(*_args, **_kwargs):
            raise OSError("power cut")

        monkeypatch.setattr(storage.os, "replace", power_cut)
        with pytest.raises(OSError):
            write_json(target, {"customers": []})
        monkeypatch.undo()
        assert read_json(target) == {"customers": ["כהן"]}

    def test_the_new_content_is_forced_to_disk(self, tmp_path, monkeypatch):
        synced: list[int] = []
        real = os.fsync
        monkeypatch.setattr(storage.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
        atomic_write_text(tmp_path / "x.json", "{}")
        assert synced, "a save that never reaches the disk is not a save"

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        write_json(tmp_path / "x.json", [1, 2, 3])
        assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


class TestReading:
    def test_a_missing_file_is_the_default(self, tmp_path):
        assert read_json(tmp_path / "nothing.json", default={"a": []}) == {"a": []}

    def test_a_damaged_file_comes_back_from_the_previous_version(self, tmp_path):
        target = tmp_path / "customers.json"
        write_json(target, {"customers": ["כהן"]})
        write_json(target, {"customers": ["כהן", "לוי"]})
        target.write_text('{"customers": ["כהן", "ל', encoding="utf-8")  # torn
        assert read_json(target) == {"customers": ["כהן"]}
        # The damaged copy is kept, not deleted — somebody may want it.
        assert any(p.name.startswith("customers.json.damaged-") for p in tmp_path.iterdir())
        # And the good version is back in place for the next reader.
        assert json.loads(target.read_text(encoding="utf-8")) == {"customers": ["כהן"]}

    def test_an_empty_file_is_damage_not_an_empty_list(self, tmp_path):
        target = tmp_path / "tasks.json"
        write_json(target, {"tasks": [1]})
        write_json(target, {"tasks": [1, 2]})
        target.write_bytes(b"")
        assert read_json(target) == {"tasks": [1]}

    def test_damage_with_no_previous_version_refuses_rather_than_empties(self, tmp_path):
        target = tmp_path / "jobs.json"
        target.write_text("not json", encoding="utf-8")
        with pytest.raises(StorageError):
            read_json(target, default={})
        # Refusing must not touch the file: it may still be recoverable.
        assert target.read_text(encoding="utf-8") == "not json"


class TestStoresNeverSaveEmptinessOverData:
    """The failure this layer exists to make impossible, store by store."""

    def test_customers(self, tmp_path):
        from profileos.projects.store import CustomerBook

        book = CustomerBook(tmp_path / "customers.json")
        book.add("משה כהן", phone="054-1234567")
        book.add("דנה לוי")
        (tmp_path / "customers.json").write_text("{broken", encoding="utf-8")
        book.add("יוסי")
        names = sorted(c.name for c in book.all())
        assert names == ["יוסי", "משה כהן"], (
            "a damaged file must be recovered from the previous version, "
            "not read as empty and saved over"
        )

    def test_customers_with_no_previous_version_refuse_to_add(self, tmp_path):
        from profileos.projects.store import CustomerBook

        path = tmp_path / "customers.json"
        path.write_text("{broken", encoding="utf-8")
        with pytest.raises(StorageError):
            CustomerBook(path).add("יוסי")
        assert path.read_text(encoding="utf-8") == "{broken"
