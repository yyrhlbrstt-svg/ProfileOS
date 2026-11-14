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


class TestBusyAndForeignFiles:
    def test_a_busy_file_is_not_swapped_for_its_previous_version(self, tmp_path, monkeypatch):
        from pathlib import Path

        from profileos.core.storage import StorageError, read_json, write_json

        path = tmp_path / "book.json"
        write_json(path, {"v": 1})
        write_json(path, {"v": 2})
        real = Path.read_bytes

        def busy(self):
            if self == path:
                raise PermissionError("locked by antivirus")
            return real(self)

        monkeypatch.setattr(Path, "read_bytes", busy)
        with pytest.raises(StorageError):
            read_json(path)
        monkeypatch.setattr(Path, "read_bytes", real)
        assert read_json(path) == {"v": 2}
        assert not list(tmp_path.glob("*.damaged-*"))

    def test_a_file_from_a_newer_version_is_refused_not_rolled_back(self, tmp_path):
        from profileos.core.storage import StorageError, read_text_checked, write_json

        path = tmp_path / "job.json"
        write_json(path, {"status": "enquiry"})
        write_json(path, {"status": "a-status-from-the-future"})

        def strict(text):
            import json

            value = json.loads(text)
            if value["status"] not in ("enquiry", "quoted"):
                raise ValueError("unknown status")
            return value

        with pytest.raises(StorageError):
            read_text_checked(path, strict)
        assert "future" in path.read_text(encoding="utf-8")
        assert not list(tmp_path.glob("*.damaged-*"))


class TestTwoCopiesOfOneBook:
    def test_closing_a_task_here_keeps_the_tasks_added_there(self, tmp_path):
        from profileos.projects.followups import Kind, TaskBook

        path = tmp_path / "tasks.json"
        here = TaskBook(path).load()
        first = here.create(Kind.OTHER, "להתקשר למשה")

        there = TaskBook(path).load()
        for name in ("א", "ב", "ג"):
            there.create(Kind.CHASE_QUOTE, f"מעקב הצעה {name}")

        here.close(first.task_id)
        after = TaskBook(path).load()
        assert len(after) == 4
        assert not after.get(first.task_id).is_open
        # And this copy now sees them too.
        assert len(here) == 4

    def test_a_row_this_version_cannot_read_survives_a_save(self, tmp_path):
        import json

        from profileos.projects.followups import Kind, TaskBook

        path = tmp_path / "tasks.json"
        book = TaskBook(path).load()
        book.create(Kind.OTHER, "רגיל")
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["tasks"].append({"task_id": "TK-NEW", "kind": "kind-from-the-future", "what": "x"})
        path.write_text(json.dumps(raw), encoding="utf-8")

        book = TaskBook(path).load()
        book.create(Kind.OTHER, "עוד אחת")
        book.create(Kind.OTHER, "ועוד")
        ids = [t["task_id"] for t in json.loads(path.read_text(encoding="utf-8"))["tasks"]]
        assert "TK-NEW" in ids

    def test_a_record_removed_elsewhere_stays_removed(self, tmp_path):
        from profileos.erp.collection import Cheque, ChequeBook

        path = tmp_path / "cheques.json"
        first = ChequeBook(path=path).load()
        cheque = first.add(Cheque(customer="משה", amount=1000))
        first.save()

        here = ChequeBook(path=path).load()
        there = ChequeBook(path=path).load()
        there._cheques.pop(cheque.cheque_id)
        there.save()
        here.add(Cheque(customer="דוד", amount=500))
        here.save()
        names = [c.customer for c in ChequeBook(path=path).load()]
        assert names == ["דוד"]


def test_a_folder_given_for_one_run_is_not_saved_as_the_shops(tmp_path, monkeypatch):
    import json

    from profileos.core.config import reload_settings, save_settings

    config = tmp_path / "config"
    config.mkdir()
    (config / "settings.json").write_text(
        json.dumps({"data_dir": str(tmp_path / "real-data")}), encoding="utf-8"
    )
    monkeypatch.setenv("PROFILEOS_CONFIG_DIR", str(config))
    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "throw-away"))
    try:
        settings = reload_settings()
        assert settings.data_dir == tmp_path / "throw-away"
        settings.theme = "dark"
        save_settings(settings)
        saved = json.loads((config / "settings.json").read_text(encoding="utf-8"))
        assert saved["data_dir"] == str(tmp_path / "real-data")
        assert saved["theme"] == "dark"
    finally:
        import profileos.core.config as config_module

        monkeypatch.delenv("PROFILEOS_CONFIG_DIR")
        monkeypatch.delenv("PROFILEOS_DATA_DIR")
        config_module._SETTINGS = None  # not reload: that keeps the temp folder
        config_module.load_settings()
