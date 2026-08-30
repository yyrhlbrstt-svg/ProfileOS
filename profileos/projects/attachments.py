"""The photographs and papers that belong to a job.

The measurement is in the software, the drawing is in the software, and the
photograph of the opening with the old frame still in it — the one that
settles the argument about whether the wall was out of plumb before anybody
touched it — is on a fitter's phone. So is the signed approval of the
quotation, and the supplier's confirmation that the glass was ordered in the
size that was asked for.

Those are the documents that decide who pays when something goes wrong, and
they are kept in the one place nobody can search and nobody backs up.

This puts them in the job. Files are copied into the job's own folder, named
so they sort by when they were taken, and recorded with who added them, what
they are of, and a checksum — so a photograph that is later replaced can be
seen to have been replaced. Nothing is stored in a database blob: they stay
ordinary files that a person can open, mail, or hand to a lawyer.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("projects.attachments")


class AttachmentKind(StrEnum):
    """What the file is of, which is what makes it findable a year later."""

    SURVEY_PHOTO = "survey_photo"
    OPENING_PHOTO = "opening_photo"
    PROGRESS_PHOTO = "progress_photo"
    DEFECT_PHOTO = "defect_photo"
    HANDOVER_PHOTO = "handover_photo"
    SIGNED_QUOTE = "signed_quote"
    SIGNED_DELIVERY = "signed_delivery"
    SUPPLIER_CONFIRMATION = "supplier_confirmation"
    ARCHITECT_DRAWING = "architect_drawing"
    PERMIT = "permit"
    CERTIFICATE = "certificate"
    OTHER = "other"

    @property
    def hebrew(self) -> str:
        return {
            "survey_photo": "צילום מדידה",
            "opening_photo": "צילום הפתח",
            "progress_photo": "צילום התקדמות",
            "defect_photo": "צילום ליקוי",
            "handover_photo": "צילום מסירה",
            "signed_quote": "הצעה חתומה",
            "signed_delivery": "תעודת משלוח חתומה",
            "supplier_confirmation": "אישור ספק",
            "architect_drawing": "תוכנית אדריכל",
            "permit": "היתר",
            "certificate": "תעודה או אישור",
            "other": "אחר",
        }[self.value]

    @property
    def is_evidence(self) -> bool:
        """Whether this is the kind of file that settles an argument.

        Those are kept even when somebody asks to tidy up, and their removal
        is refused rather than done quietly.
        """
        return self in (
            AttachmentKind.SIGNED_QUOTE,
            AttachmentKind.SIGNED_DELIVERY,
            AttachmentKind.SURVEY_PHOTO,
            AttachmentKind.HANDOVER_PHOTO,
            AttachmentKind.DEFECT_PHOTO,
        )


#: What can be attached. Anything else is refused by name rather than stored
#: and hoped for — an executable in a job folder is not a document.
ALLOWED_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif",
    ".pdf", ".dxf", ".dwg",
    ".txt", ".csv", ".xlsx", ".docx",
    ".mp4", ".mov",
}

#: Refuse anything larger, so a job folder does not quietly become a video
#: library [bytes].
MAX_BYTES = 64 * 1024 * 1024


@dataclass
class Attachment:
    """One file kept with the job."""

    name: str
    kind: AttachmentKind = AttachmentKind.OTHER
    caption: str = ""
    added_by: str = ""
    added_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    size: int = 0
    checksum: str = ""
    #: The element this is a picture of, when it is of one.
    element: str = ""

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def is_image(self) -> bool:
        return self.suffix in {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}

    def describe(self) -> str:
        who = f" · {self.added_by}" if self.added_by else ""
        return f"{self.kind.hebrew} · {self.caption or self.name}{who}"


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:32]


class AttachmentStore:
    """The job's own folder of files, with a manifest beside them."""

    MANIFEST = "attachments.json"

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._items: list[Attachment] = []
        self.load()

    @property
    def manifest_path(self) -> Path:
        return self.folder / self.MANIFEST

    # -- persistence --------------------------------------------------------- #
    def load(self) -> "AttachmentStore":
        if not self.manifest_path.is_file():
            return self
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - the files matter more than the index
            _log.exception("Attachment manifest at %s unreadable", self.manifest_path)
            return self
        for entry in raw.get("attachments", []):
            try:
                self._items.append(Attachment(
                    name=entry["name"],
                    kind=AttachmentKind(entry.get("kind", "other")),
                    caption=entry.get("caption", ""),
                    added_by=entry.get("added_by", ""),
                    added_at=entry.get("added_at", ""),
                    size=int(entry.get("size", 0)),
                    checksum=entry.get("checksum", ""),
                    element=entry.get("element", ""),
                ))
            except Exception:  # noqa: BLE001 - one bad row, not the folder
                _log.warning("Skipping unreadable attachment entry: %s", entry)
        return self

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "attachments": [
                {**asdict(item), "kind": item.kind.value} for item in self._items
            ]
        }
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.manifest_path)

    # -- reading ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(sorted(self._items, key=lambda item: item.added_at, reverse=True))

    def all(self) -> list[Attachment]:
        return list(self)

    def of_kind(self, kind: AttachmentKind) -> list[Attachment]:
        return [item for item in self if item.kind is kind]

    def for_element(self, mark: str) -> list[Attachment]:
        return [item for item in self if item.element == mark]

    def photos(self) -> list[Attachment]:
        return [item for item in self if item.is_image]

    def path_of(self, attachment: Attachment) -> Path:
        return self.folder / attachment.name

    def missing(self) -> list[Attachment]:
        """Files the manifest lists that are no longer on disk."""
        return [item for item in self if not self.path_of(item).is_file()]

    def changed(self) -> list[Attachment]:
        """Files whose contents no longer match what was recorded.

        A signed delivery note that has been replaced since it was filed is
        exactly the thing somebody needs to know about, and a checksum is the
        cheapest way to know.
        """
        found: list[Attachment] = []
        for item in self:
            path = self.path_of(item)
            if not path.is_file() or not item.checksum:
                continue
            if _checksum(path) != item.checksum:
                found.append(item)
        return found

    # -- writing ------------------------------------------------------------- #
    def add(
        self,
        source: Path,
        *,
        kind: AttachmentKind = AttachmentKind.OTHER,
        caption: str = "",
        added_by: str = "",
        element: str = "",
    ) -> Attachment:
        """Copy a file into the job and record what it is."""
        source = Path(source)
        if not source.is_file():
            raise ProfileOSError(f"אין קובץ בשם {source}")
        suffix = source.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ProfileOSError(
                f"סוג הקובץ {suffix or '—'} לא נתמך בתיק העבודה. "
                "מותר: " + ", ".join(sorted(ALLOWED_SUFFIXES))
            )
        size = source.stat().st_size
        if size > MAX_BYTES:
            raise ProfileOSError(
                f"הקובץ במשקל ⁦{size / 1_048_576:.0f}⁩ MB — מעל המותר "
                f"(⁦{MAX_BYTES // 1_048_576}⁩ MB)"
            )

        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{kind.value}-{source.name}"
        target = self.folder / name
        counter = 1
        while target.exists():
            target = self.folder / f"{stamp}-{counter}-{kind.value}-{source.name}"
            counter += 1
        shutil.copy2(source, target)

        attachment = Attachment(
            name=target.name,
            kind=kind,
            caption=caption,
            added_by=added_by,
            size=size,
            checksum=_checksum(target),
            element=element,
        )
        self._items.append(attachment)
        self.save()
        _log.info("Attached %s to %s", target.name, self.folder)
        return attachment

    def remove(self, name: str, *, force: bool = False) -> None:
        """Take a file out of the job.

        Evidence is refused unless somebody says so explicitly: a signed
        delivery note deleted by accident is a signed delivery note that never
        existed as far as any later argument is concerned.
        """
        found = next((item for item in self._items if item.name == name), None)
        if found is None:
            raise ProfileOSError(f"אין קובץ בשם {name} בתיק")
        if found.kind.is_evidence and not force:
            raise ProfileOSError(
                f"{found.kind.hebrew} הוא מסמך ראיה — מחיקה דורשת אישור מפורש"
            )
        path = self.path_of(found)
        if path.is_file():
            path.unlink()
        self._items.remove(found)
        self.save()

    def summary(self) -> dict[str, Any]:
        return {
            "count": len(self._items),
            "photos": len(self.photos()),
            "evidence": len([item for item in self if item.kind.is_evidence]),
            "megabytes": round(sum(item.size for item in self._items) / 1_048_576, 2),
            "missing": len(self.missing()),
        }


def job_folder(job_id: str) -> Path:
    """Where one job's files live."""
    from ..core.config import get_settings

    return get_settings().data_dir / "jobs" / job_id / "files"


def attachments_for(job_id: str) -> AttachmentStore:
    return AttachmentStore(job_folder(job_id))


__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_BYTES",
    "Attachment",
    "AttachmentKind",
    "AttachmentStore",
    "attachments_for",
    "job_folder",
]
