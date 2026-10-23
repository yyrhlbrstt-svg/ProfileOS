"""Two people, one shared folder, and nobody's work disappearing.

The moment a second person opens the software, the data folder stops being a
private thing. A shop puts it on the office server or in a synced folder, and
two estimators open the customer list at the same time — and whichever of them
saves last silently erases what the other typed. It is not a crash and there
is no error; the record simply reverts, and everybody blames the software
without being able to say what happened.

There is no database here to arbitrate, on purpose: the shop's data stays as
files they can read, copy and back up with anything. So the arbitration is a
lock file beside the data and a modification check before every write.

The lock is deliberately weak in the right direction. It expires, it records
who holds it and since when, and it can be broken by somebody who is told
what they are breaking — because a lock left behind by a laptop that went
flat at four o'clock must not stop the shop working at half past.
"""

from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import ProfileOSError
from .logging_setup import get_logger

_log = get_logger("core.sharing")

#: A lock older than this is assumed to belong to a machine that went away.
#: Long enough that somebody typing a long customer record keeps it; short
#: enough that a shop is never held up for more than a coffee.
LOCK_EXPIRY_SECONDS = 15 * 60
#: How long to wait for somebody else's lock before giving up.
DEFAULT_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class Holder:
    """Who has the lock, so the message can name them."""

    user: str
    machine: str
    since: str
    pid: int = 0

    @property
    def age_seconds(self) -> float:
        """How long this lock has been held.

        A lock whose timestamp cannot be read is infinitely old rather than
        brand new: we cannot say when it was taken, and the safe reading of
        "cannot say" is that nobody is coming back for it. Treating it as
        fresh would hold the shop up forever over a corrupt file.
        """
        try:
            taken = datetime.fromisoformat(self.since)
        except ValueError:
            return float("inf")
        if taken.tzinfo is None:
            taken = taken.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - taken).total_seconds()

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > LOCK_EXPIRY_SECONDS

    @property
    def is_me(self) -> bool:
        return (
            self.machine == socket.gethostname()
            and self.pid == os.getpid()
        )

    def describe(self) -> str:
        age = self.age_seconds
        minutes = 0.0 if age == float("inf") else age / 60.0
        return (
            f"{self.user} על {self.machine}, כבר ⁦{minutes:.0f}⁩ דקות"
            + (" — כנראה ננטש" if self.is_stale else "")
        )


class Locked(ProfileOSError):
    """Somebody else is holding the file."""


def _me() -> Holder:
    return Holder(
        user=os.environ.get("PROFILEOS_USER")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "מפעיל",
        machine=socket.gethostname(),
        since=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        pid=os.getpid(),
    )


def _lock_path(path: Path) -> Path:
    return Path(path).with_suffix(Path(path).suffix + ".lock")


def holder_of(path: Path) -> Holder | None:
    """Who holds the lock on this file, if anybody."""
    lock = _lock_path(path)
    if not lock.is_file():
        return None
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        return Holder(
            user=data.get("user", "?"),
            machine=data.get("machine", "?"),
            since=data.get("since", ""),
            pid=int(data.get("pid", 0)),
        )
    except Exception:  # noqa: BLE001 - an unreadable lock is a broken lock
        _log.warning("Lock file at %s is unreadable; treating it as stale", lock)
        return Holder(user="?", machine="?", since="", pid=0)


def acquire(path: Path, *, wait: float = DEFAULT_WAIT_SECONDS) -> Holder:
    """Take the lock on a file, waiting a little for somebody else to finish.

    Created with ``O_EXCL`` so that two machines racing for the same file
    cannot both believe they won — which is the whole point, and the reason
    this is not a simple "does the file exist" check.
    """
    lock = _lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    me = _me()
    deadline = time.monotonic() + max(wait, 0.0)

    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = holder_of(path)
            if current is not None and (current.is_stale or current.is_me):
                # Left behind by a machine that went away, or by this very
                # process — take it over and say so.
                _log.info("Taking over a stale lock on %s from %s",
                          path.name, current.describe())
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise Locked(
                    f"{Path(path).name} פתוח אצל " +
                    (current.describe() if current else "מישהו אחר")
                )
            time.sleep(0.1)
            continue

        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                {"user": me.user, "machine": me.machine,
                 "since": me.since, "pid": me.pid},
                stream, ensure_ascii=False,
            )
        return me


def release(path: Path) -> None:
    """Give the lock back. Never raises: a failure here must not lose work."""
    try:
        _lock_path(path).unlink()
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 - the write already happened
        _log.warning("Could not release the lock on %s", path)


def break_lock(path: Path) -> Holder | None:
    """Force a lock open, returning whose it was so somebody can be told."""
    current = holder_of(path)
    release(path)
    if current is not None:
        _log.warning("Broke the lock on %s held by %s", path, current.describe())
    return current


@contextmanager
def locked(path: Path, *, wait: float = DEFAULT_WAIT_SECONDS) -> Iterator[Holder]:
    """Hold the lock for the length of a write."""
    holder = acquire(path, wait=wait)
    try:
        yield holder
    finally:
        release(path)


# --------------------------------------------------------------------------- #
# Noticing that somebody else changed it
# --------------------------------------------------------------------------- #

def fingerprint(path: Path) -> tuple[float, int]:
    """Enough to tell whether a file changed under us."""
    try:
        stat = Path(path).stat()
    except FileNotFoundError:
        return (0.0, 0)
    return (stat.st_mtime, stat.st_size)


class Stale(ProfileOSError):
    """The file changed since it was read."""


@contextmanager
def guarded(path: Path, *, wait: float = DEFAULT_WAIT_SECONDS,
            since: tuple[float, int] | None = None) -> Iterator[Holder]:
    """Lock a file and refuse the write if it changed since it was read.

    The lock stops two writes overlapping. This stops the subtler loss: two
    people both read the customer list at nine, one saves at ten past and the
    other at quarter past, and the second write is made from data that is
    five minutes out of date. The second writer is told, rather than silently
    erasing the first one's work.
    """
    with locked(path, wait=wait) as holder:
        if since is not None and fingerprint(path) != since:
            raise Stale(
                f"{Path(path).name} השתנה על ידי מישהו אחר מאז שנקרא. "
                "רעננו ונסו שוב — אחרת השינוי שלהם יימחק"
            )
        yield holder


def shared_folder_warning(path: Path) -> str:
    """A word of warning when the data sits somewhere two people can reach it.

    Not a refusal: a shared folder is exactly where a shop with two
    estimators should keep this. But a folder that syncs in the background —
    Dropbox, OneDrive, Google Drive — resolves its own conflicts by keeping
    both copies under different names, which no amount of locking here can
    prevent.
    """
    text = str(path).lower()
    for marker, service in (
        ("dropbox", "Dropbox"), ("onedrive", "OneDrive"),
        ("google drive", "Google Drive"), ("googledrive", "Google Drive"),
        ("icloud", "iCloud"),
    ):
        if marker in text:
            return (
                f"נתוני העבודה נמצאים בתיקיית {service}. שיתוף בין שני "
                "מחשבים עובד, אבל אם שניהם עורכים באותו רגע — "
                f"{service} עלול ליצור שני עותקים במקום לאחד. "
                "עדיף תיקייה משותפת ברשת המקומית."
            )
    return ""


__all__ = [
    "DEFAULT_WAIT_SECONDS",
    "Holder",
    "LOCK_EXPIRY_SECONDS",
    "Locked",
    "Stale",
    "acquire",
    "break_lock",
    "fingerprint",
    "guarded",
    "holder_of",
    "locked",
    "release",
    "shared_folder_warning",
]
