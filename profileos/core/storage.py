"""How every record this program keeps reaches the disk — and comes back.

The question a shop asks is simple: *once I have saved it, is it there?* The
answer has to be yes after a power cut, after a crash halfway through a
save, after a file is damaged by a dying disk, and after a second window
saved something a moment earlier. This module is the one place that
answer is earned, and every store in the program writes and reads through
it.

**Writing** (:func:`atomic_write_text`):

1. The new content goes into a temporary file *beside* the target, which
   is flushed and ``fsync``-ed — pushed through the operating system's
   cache to the physical disk.
2. The current version, if there is one, is first copied to
   ``<name>.bak``. One generation back is always on disk.
3. The temporary file is renamed over the target in one step. A crash at
   any moment leaves either the old file or the new one, never half.
4. The folder itself is ``fsync``-ed where the system supports it, so the
   rename survives a power cut too.

**Reading** (:func:`read_json`):

* A missing file is the honest "nothing yet" — the caller's default.
* A file that is empty or does not parse is **never** treated as empty. It
  is set aside as ``<name>.damaged-<time>``, the ``.bak`` is restored in its
  place, and the caller gets the previous version. If there is no usable
  ``.bak`` either, :class:`StorageError` is raised. A store that read a
  damaged file as "no records" would save that emptiness over the shop's
  real data the next time anything was added — the one failure this module
  exists to make impossible.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .errors import ProfileOSError
from .logging_setup import get_logger

_log = get_logger("core.storage")


class StorageError(ProfileOSError):
    """A record could not be read back, and no safe previous version exists."""

    def __str__(self) -> str:
        # The Hebrew message is what a person reads; the file and the parser's
        # complaint stay on ``context`` and in the log.
        return self.message


def backup_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".bak")


def _fsync_folder(folder: Path) -> None:
    """Make a rename durable. POSIX only; Windows commits renames itself."""
    if os.name == "nt":
        return
    try:
        handle = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _write_durably(target: Path, data: bytes) -> None:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if target.is_file() and target.stat().st_size > 0:
        bak = backup_path(target)
        try:
            shutil.copy2(target, bak)
            with open(bak, "rb+") as handle:
                os.fsync(handle.fileno())
        except OSError as exc:  # a missing .bak must not stop the save itself
            _log.warning("Could not keep the previous version of %s: %s", target.name, exc)
    os.replace(temporary, target)
    _fsync_folder(target.parent)


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write ``data`` to ``path`` durably, keeping the previous version."""
    _write_durably(Path(path), data)


def atomic_write_text(path: Path | str, text: str) -> None:
    """Write ``text`` (UTF-8) to ``path`` durably, keeping the previous version."""
    _write_durably(Path(path), text.encode("utf-8"))


def write_json(path: Path | str, payload: Any) -> None:
    """Serialise and write durably — the way every JSON store saves."""
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


class _Damaged(ValueError):
    """The bytes on disk are not a complete file: empty, cut off, not text."""


def _read_bytes(path: Path) -> bytes:
    """Read, waiting out the brief locks antivirus and cloud sync take.

    A file that cannot be *opened* is busy, not damaged — swapping in the
    previous version would quietly roll the record back one save.
    """
    import time

    last: OSError | None = None
    for attempt in range(6):
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise
        except OSError as exc:
            last = exc
            time.sleep(0.05 * (attempt + 1))
    raise StorageError(
        f"הקובץ ⁦{path.name}⁩ תפוס כרגע על ידי תוכנה אחרת (גיבוי ענן, אנטי-וירוס). "
        "שום דבר לא שונה — נסו שוב בעוד רגע.",
        file=str(path), reason=str(last),
    ) from last


def _parse(path: Path, parse: Callable[[str], Any]) -> Any:
    raw = _read_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Damaged(f"not UTF-8 text: {exc}") from exc
    if not text.strip():
        raise _Damaged("the file is empty")
    # Damage is a file that is not well-formed JSON — what a crash or a
    # half-written copy leaves. Well-formed JSON the caller cannot accept is
    # something else (a newer version's file, a hand edit) and is refused
    # without touching anything: the previous version is not "the same
    # record, intact", it is an older one.
    try:
        json.loads(text)
    except ValueError as exc:
        raise _Damaged(str(exc)) from exc
    try:
        return parse(text)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(
            f"הקובץ ⁦{path.name}⁩ תקין, אבל הגרסה הזאת של התוכנה לא יודעת לקרוא אותו "
            "(אולי נכתב בגרסה חדשה יותר). הוא לא שונה — עדכנו את התוכנה ונסו שוב.",
            file=str(path), reason=str(exc),
        ) from exc


def read_text_checked(
    path: Path | str, parse: Callable[[str], Any] = json.loads, *, default: Any = None,
) -> Any:
    """Read and parse ``path``, recovering from ``.bak`` rather than ever
    returning "empty" for a file that exists but cannot be read.

    ``parse`` turns the text into the caller's value and must raise on bad
    content. ``default`` is returned only when the file does not exist. Only
    a file that is physically damaged is recovered from its previous version;
    a busy file or one this version cannot understand raises
    :class:`StorageError` and is left exactly as it is.
    """
    path = Path(path)
    if not path.exists():
        # Gone, but its previous version is still beside it — deleted by
        # hand, or lost between the rename and the crash. That is the file;
        # the default would let the next save erase it.
        bak = backup_path(path)
        if not bak.is_file():
            return default
        try:
            value = _parse(bak, parse)
        except _Damaged:
            return default  # nothing usable: the file stays missing
        # A previous version that is busy or from a newer version raises: the
        # default here would let the next save write over that .bak.
        try:
            shutil.copy2(bak, path)
        except OSError as exc:
            _log.error("Could not restore %s from its .bak: %s", path, exc)
        _log.error("%s was missing; restored its previous version", path.name)
        _note_recovery(path, bak)
        return value
    try:
        return _parse(path, parse)
    except _Damaged as first:
        bak = backup_path(path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        damaged = path.with_name(f"{path.name}.damaged-{stamp}")
        try:
            value = _parse(bak, parse) if bak.is_file() else None
        except Exception:  # noqa: BLE001
            value = None
        if value is None:
            raise StorageError(
                f"הקובץ ⁦{path.name}⁩ פגום ואין עותק קודם תקין. "
                "הנתונים לא נדרסו — הקובץ נשאר במקומו לשחזור מגיבוי.",
                file=str(path), reason=str(first),
            ) from first
        try:
            os.replace(path, damaged)
            shutil.copy2(bak, path)
        except OSError as exc:
            _log.error("Recovered %s from .bak but could not swap files: %s", path, exc)
        _log.error(
            "%s was unreadable (%s); restored the previous version, damaged copy kept as %s",
            path.name, first, damaged.name,
        )
        _note_recovery(path, damaged)
        return value


def discard(path: Path | str) -> Path | None:
    """Put a file *and its previous version* in the folder's ``.trash``.

    Unlinking only the file leaves its ``.bak`` beside it, and the next read
    brings the old contents back as if nothing had been deleted. Returns
    where the file went, or ``None`` if there was nothing to discard.
    """
    path = Path(path)
    trash = path.parent / ".trash"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    moved: Path | None = None
    for item, suffix in ((path, ""), (backup_path(path), ".bak")):
        if not item.exists():
            continue
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{path.stem}-{stamp}{path.suffix}{suffix}"
        os.replace(item, target)
        if not suffix:
            moved = target
    return moved


def stored(path: Path | str) -> bool:
    """Whether there is anything on disk for ``path`` — the file or its
    previous version. Loaders ask this, not ``exists()``, so a file lost
    with its ``.bak`` still beside it is recovered rather than skipped."""
    path = Path(path)
    return path.exists() or backup_path(path).is_file()


def read_json(path: Path | str, *, default: Any = None) -> Any:
    """:func:`read_text_checked` for JSON — what every store loads with."""
    return read_text_checked(path, json.loads, default=default)


class MergingBook:
    """Save a book of records without erasing what another writer added.

    A page keeps its book open for the whole session; another page, the
    phone app or a second computer writes to the same file meanwhile. Saving
    the whole in-memory list would erase their work. Instead each save
    re-reads the file and writes it back with only *this* copy's changes
    applied — records it added, changed or removed — and everything else as
    it now is on disk, including rows this version could not read.

    A subclass names its records' dictionary and how one is written and read.
    """

    _list_key: str = "records"
    _id_field: str = "id"
    #: Set when the file exists but could not be read: the book then opens
    #: empty so the program still starts, and every save is refused, so the
    #: file is left exactly as it is for restoring.
    damaged: Exception | None = None

    def _read_or_mark(self, path: Path) -> dict[str, Any]:
        """The file's contents, or ``{}`` with the book marked damaged."""
        try:
            raw = read_json(path, default={})
        except StorageError as exc:
            _log.error("%s unreadable, opened read-only: %s", Path(path).name, exc)
            self.damaged = exc
            return {}
        return raw if isinstance(raw, dict) else {}

    def _records(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _to_raw(self, record: Any) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _from_raw(self, raw: dict[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _remember_loaded(self) -> None:
        """Note what each record looked like when this copy last met the disk."""
        self._loaded = {key: self._to_raw(record) for key, record in self._records().items()}

    def _save_merged(self, path: Path, extra: dict[str, Any] | None = None) -> None:
        if self.damaged is not None:
            raise self.damaged
        loaded: dict[str, Any] = getattr(self, "_loaded", {})
        current = {key: self._to_raw(record) for key, record in self._records().items()}

        raw = read_json(path, default={}) or {}
        entries = raw.get(self._list_key, []) if isinstance(raw, dict) else []
        disk: dict[str, Any] = {}
        loose: list[Any] = []
        for entry in entries:
            key = entry.get(self._id_field) if isinstance(entry, dict) else None
            if key:
                disk[str(key)] = entry
            else:
                loose.append(entry)

        merged = dict(disk)
        for key in loaded:
            if key not in current:
                merged.pop(key, None)          # removed here
        for key, record in current.items():
            if loaded.get(key) != record:
                merged[key] = record           # added or changed here
            # unchanged here and gone from disk: removed elsewhere — stays gone

        payload = dict(extra or {})
        payload[self._list_key] = list(merged.values()) + loose
        write_json(path, payload)

        # Bring this copy up to date with what the others wrote.
        records = self._records()
        for key in list(records):
            if key not in merged:
                del records[key]
        for key, entry in merged.items():
            if key in current and loaded.get(key) != current[key]:
                continue
            if key not in records or current.get(key) != entry:
                try:
                    records[key] = self._from_raw(entry)
                except Exception:  # noqa: BLE001 - kept on disk, just not shown
                    records.pop(key, None)
        self._remember_loaded()


#: Files restored from their previous version during this run, for the UI to
#: tell the person rather than leave it in a log.
RECOVERED: list[tuple[Path, Path]] = []


def _note_recovery(path: Path, damaged: Path) -> None:
    RECOVERED.append((path, damaged))


__all__ = [
    "discard",
    "stored",
    "MergingBook",
    "RECOVERED",
    "StorageError",
    "atomic_write_bytes",
    "atomic_write_text",
    "backup_path",
    "read_json",
    "read_text_checked",
    "write_json",
]
