"""Where job files and customers live on disk.

One JSON file per job, in a folder — not a database. A shop that has to
recover a job from a backup, mail it to their engineer, or read it five years
from now on a machine that no longer runs this software can do all three with
a text editor. The cost is that listing jobs reads the folder; for the number
of jobs a fabricator has open at once that is nothing.

Writes are atomic: the file is written beside its target and renamed over it,
so a power cut during a save leaves the previous version intact rather than
half a job.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .model import Customer, JobFile, JobStatus

_log = get_logger("projects.store")


def _atomic_write(path: Path, text: str) -> None:
    """Write durably (see :mod:`profileos.core.storage`), holding the lock.

    Durable on its own is only half of it: two people on a shared folder can
    each write safely and still have the second one erase the first. The
    lock makes the pair of writes take turns, and a machine that went away
    without releasing it does not hold the shop up — see
    :mod:`profileos.core.sharing`.
    """
    from ..core.sharing import Locked, locked
    from ..core.storage import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with locked(path):
            atomic_write_text(path, text)
    except Locked:
        # Never lose somebody's work to a lock: say who has it, and write
        # anyway. Refusing here would mean an estimator loses what they typed
        # because a colleague left a window open, which is a worse trade.
        _log.warning("Wrote %s while another machine held the lock", path.name)
        atomic_write_text(path, text)


def _read_job(path: Path) -> JobFile:
    from ..core.storage import read_text_checked

    return read_text_checked(path, JobFile.model_validate_json)


class JobStore:
    """The job folder: read, write, list and number the shop's jobs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        #: Job files that could not be read on the last listing.
        self.unreadable: list[Path] = []

    # -- paths -------------------------------------------------------------- #
    def path_for(self, job_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)
        return self.root / f"{safe}.json"

    # -- reading ------------------------------------------------------------ #
    def load(self, job_id: str) -> JobFile:
        path = self.path_for(job_id)
        if not path.is_file():
            raise ProfileOSError(f"לא נמצא פרויקט {job_id}", job_id=job_id)
        return _read_job(path)

    def all(self) -> list[JobFile]:
        """Every job, newest change first. Unreadable files are skipped loudly."""
        jobs: list[JobFile] = []
        if not self.root.is_dir():
            return jobs
        self.unreadable: list[Path] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                jobs.append(_read_job(path))
            except Exception:  # noqa: BLE001 - one bad file must not hide the rest
                # Recorded, not forgotten: the screen says a job could not be
                # read, and numbering steps around it (see ``next_id``).
                _log.exception("Could not read job file %s", path)
                self.unreadable.append(path)
        jobs.sort(key=lambda job: (job.updated, job.job_id), reverse=True)
        return jobs

    def open_jobs(self) -> list[JobFile]:
        return [job for job in self.all() if job.status.is_open]

    def __iter__(self) -> Iterator[JobFile]:
        return iter(self.all())

    # -- writing ------------------------------------------------------------ #
    def save(self, job: JobFile) -> Path:
        job.touch()
        path = self.path_for(job.job_id)
        _atomic_write(path, job.model_dump_json(indent=2, exclude_none=False))
        _log.info("Saved job %s to %s", job.job_id, path)
        return path

    def delete(self, job_id: str) -> Path | None:
        """Move a job to the folder's recycle bin — never erase it outright.

        A job deleted by a slip of the mouse is a job somebody needs back the
        same afternoon. It goes to ``.trash`` beside the jobs, timestamped,
        where a backup still carries it and a person can still find it.
        """
        from datetime import datetime

        path = self.path_for(job_id)
        if not path.is_file():
            return None
        trash = self.root / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        target = trash / f"{path.stem}-{stamp}{path.suffix}"
        os.replace(path, target)
        # Its previous version and its documents go with it, so a new job
        # never inherits a deleted customer's site photos.
        for companion, name in (
            (path.with_name(path.name + ".bak"), f"{path.stem}-{stamp}{path.suffix}.bak"),
            (self.root / path.stem, f"{path.stem}-{stamp}"),
        ):
            if companion.exists():
                os.replace(companion, trash / name)
        _log.info("Deleted job %s (kept in %s)", job_id, target)
        return target

    # -- numbering ---------------------------------------------------------- #
    def next_id(self, today: date | None = None) -> str:
        """The next job number, ``J-<year>-<serial>``.

        Serials restart each year, which is how a shop refers to a job out
        loud — "the third one this year" — and it keeps the number short
        enough to write on a job card by hand.
        """
        year = (today or date.today()).year
        prefix = f"J-{year}-"
        used = 0
        # Counted from the file names as well as the readable jobs: a job file
        # that cannot be read right now still owns its number, and handing that
        # number out again would save a new job over one still recoverable.
        # Deleted jobs in the recycle bin keep theirs too: a number reused is
        # a paper job card that now points at two different customers.
        import re

        ids = {job.job_id for job in self.all()}
        if self.root.is_dir():
            ids |= {path.name for path in self.root.glob("*.json*")}
            ids |= {path.name for path in (self.root / ".trash").glob("*")}
        for job_id in ids:
            if job_id.startswith(prefix):
                serial = re.match(r"\d+", job_id[len(prefix):])
                if serial:
                    used = max(used, int(serial.group(0)))
        return f"{prefix}{used + 1:04d}"

    def create(self, name: str, *, customer: Customer | None = None, **fields) -> JobFile:
        job = JobFile(
            job_id=self.next_id(),
            name=name,
            customer_id=customer.customer_id if customer else "",
            customer_name=customer.name if customer else "",
            **fields,
        )
        self.save(job)
        return job

    # -- reporting ---------------------------------------------------------- #
    def pipeline(self) -> dict[str, int]:
        """How many jobs sit at each status — the shop's order book at a glance."""
        counts = {status.value: 0 for status in JobStatus}
        for job in self.all():
            counts[job.status.value] += 1
        return counts

    def backlog_value(self) -> float:
        """Quoted value of everything won but not yet installed."""
        return round(
            sum(
                job.quote_total
                for job in self.all()
                if job.status in (JobStatus.WON, JobStatus.IN_PRODUCTION)
            ),
            2,
        )


class CustomerBook:
    """The customer list, in one file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def all(self) -> list[Customer]:
        from ..core.storage import read_json

        # A damaged book is recovered from its previous version or refused —
        # never read as "no customers", which the next add would save over.
        raw = read_json(self.path, default={"customers": []})
        customers = [Customer.model_validate(entry) for entry in raw.get("customers", [])]
        customers.sort(key=lambda customer: customer.name)
        return customers

    def get(self, customer_id: str) -> Customer | None:
        return next((c for c in self.all() if c.customer_id == customer_id), None)

    def save_all(self, customers: Iterable[Customer]) -> None:
        payload = {"customers": [c.model_dump() for c in customers]}
        _atomic_write(self.path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _change(self, change) -> Any:
        """Read, change and write the book while holding its lock, so two
        computers adding a customer at the same moment both keep theirs."""
        from ..core.sharing import Locked, locked
        from ..core.storage import atomic_write_text

        def apply() -> Any:
            customers, result = change(self.all())
            payload = {"customers": [c.model_dump() for c in customers]}
            atomic_write_text(self.path, json.dumps(payload, indent=2, ensure_ascii=False))
            return result

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with locked(self.path):
                return apply()
        except Locked:
            _log.warning("Wrote %s while another machine held the lock", self.path.name)
            return apply()

    def add(self, name: str, **fields) -> Customer:
        """Add a customer, numbering them so two of the same name stay apart."""
        if not name.strip():
            raise ProfileOSError("ללקוח חייב להיות שם")

        def change(existing: list[Customer]) -> tuple[list[Customer], Customer]:
            serial = len(existing) + 1
            used = {c.customer_id for c in existing}
            while f"C-{serial:04d}" in used:
                serial += 1
            customer = Customer(customer_id=f"C-{serial:04d}", name=name.strip(), **fields)
            return existing + [customer], customer

        return self._change(change)

    def update(self, customer: Customer) -> Customer:
        def change(existing: list[Customer]) -> tuple[list[Customer], Customer]:
            others = [c for c in existing if c.customer_id != customer.customer_id]
            return others + [customer], customer

        return self._change(change)


def default_store() -> JobStore:
    """The job store this installation writes to."""
    from ..core.config import get_settings

    settings = get_settings()
    return JobStore(settings.data_dir / "jobs")


def default_customers() -> CustomerBook:
    from ..core.config import get_settings

    return CustomerBook(get_settings().data_dir / "customers.json")


__all__ = [
    "CustomerBook",
    "JobStore",
    "default_customers",
    "default_store",
]
