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


def _parse(path: Path, parse: Callable[[str], Any]) -> Any:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("the file is empty")
    return parse(text)


def read_text_checked(
    path: Path | str, parse: Callable[[str], Any] = json.loads, *, default: Any = None,
) -> Any:
    """Read and parse ``path``, recovering from ``.bak`` rather than ever
    returning "empty" for a file that exists but cannot be read.

    ``parse`` turns the text into the caller's value and must raise on bad
    content. ``default`` is returned only when the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return _parse(path, parse)
    except Exception as first:  # noqa: BLE001 - every failure takes the same road
        bak = backup_path(path)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        damaged = path.with_name(f"{path.name}.damaged-{stamp}")
        try:
            value = _parse(bak, parse) if bak.is_file() else None
        except Exception:  # noqa: BLE001
            value = None
        if value is None:
            raise StorageError(
                f"הקובץ {path.name} פגום ואין עותק קודם תקין. "
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


def read_json(path: Path | str, *, default: Any = None) -> Any:
    """:func:`read_text_checked` for JSON — what every store loads with."""
    return read_text_checked(path, json.loads, default=default)


#: Files restored from their previous version during this run, for the UI to
#: tell the person rather than leave it in a log.
RECOVERED: list[tuple[Path, Path]] = []


def _note_recovery(path: Path, damaged: Path) -> None:
    RECOVERED.append((path, damaged))


__all__ = [
    "RECOVERED",
    "StorageError",
    "atomic_write_bytes",
    "atomic_write_text",
    "backup_path",
    "read_json",
    "read_text_checked",
    "write_json",
]
