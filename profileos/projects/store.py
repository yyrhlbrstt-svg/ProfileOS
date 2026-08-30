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
from typing import Iterable, Iterator

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger
from .model import Customer, JobFile, JobStatus

_log = get_logger("projects.store")


def _atomic_write(path: Path, text: str) -> None:
    """Write beside the target and rename over it, holding the lock.

    Atomic on its own is only half of it: two people on a shared folder can
    each write atomically and still have the second one erase the first. The
    lock makes the pair of writes take turns, and a machine that went away
    without releasing it does not hold the shop up — see
    :mod:`profileos.core.sharing`.
    """
    from ..core.sharing import Locked, locked

    path.parent.mkdir(parents=True, exist_ok=True)

    def write() -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)

    try:
        with locked(path):
            write()
    except Locked:
        # Never lose somebody's work to a lock: say who has it, and write
        # anyway. Refusing here would mean an estimator loses what they typed
        # because a colleague left a window open, which is a worse trade.
        _log.warning("Wrote %s while another machine held the lock", path.name)
        write()


class JobStore:
    """The job folder: read, write, list and number the shop's jobs."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------- #
    def path_for(self, job_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_id)
        return self.root / f"{safe}.json"

    # -- reading ------------------------------------------------------------ #
    def load(self, job_id: str) -> JobFile:
        path = self.path_for(job_id)
        if not path.is_file():
            raise ProfileOSError(f"לא נמצא פרויקט {job_id}", job_id=job_id)
        return JobFile.model_validate_json(path.read_text(encoding="utf-8"))

    def all(self) -> list[JobFile]:
        """Every job, newest change first. Unreadable files are skipped loudly."""
        jobs: list[JobFile] = []
        if not self.root.is_dir():
            return jobs
        for path in sorted(self.root.glob("*.json")):
            try:
                jobs.append(JobFile.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 - one bad file must not hide the rest
                _log.exception("Could not read job file %s", path)
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

    def delete(self, job_id: str) -> None:
        path = self.path_for(job_id)
        if path.is_file():
            path.unlink()
            _log.info("Deleted job %s", job_id)

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
        for job in self.all():
            if job.job_id.startswith(prefix):
                tail = job.job_id[len(prefix):]
                if tail.isdigit():
                    used = max(used, int(tail))
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
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - shown, never raised at the user
            _log.exception("Could not read the customer book at %s", self.path)
            return []
        customers = [Customer.model_validate(entry) for entry in raw.get("customers", [])]
        customers.sort(key=lambda customer: customer.name)
        return customers

    def get(self, customer_id: str) -> Customer | None:
        return next((c for c in self.all() if c.customer_id == customer_id), None)

    def save_all(self, customers: Iterable[Customer]) -> None:
        payload = {"customers": [c.model_dump() for c in customers]}
        _atomic_write(self.path, json.dumps(payload, indent=2, ensure_ascii=False))

    def add(self, name: str, **fields) -> Customer:
        """Add a customer, numbering them so two of the same name stay apart."""
        if not name.strip():
            raise ProfileOSError("ללקוח חייב להיות שם")
        existing = self.all()
        serial = len(existing) + 1
        used = {c.customer_id for c in existing}
        while f"C-{serial:04d}" in used:
            serial += 1
        customer = Customer(customer_id=f"C-{serial:04d}", name=name.strip(), **fields)
        self.save_all(existing + [customer])
        return customer

    def update(self, customer: Customer) -> Customer:
        others = [c for c in self.all() if c.customer_id != customer.customer_id]
        self.save_all(others + [customer])
        return customer


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
