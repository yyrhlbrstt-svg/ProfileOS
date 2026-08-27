"""A copy of the shop, in one file, that somebody can actually restore from.

Everything this software knows lives in one folder: the jobs, the customers,
the confirmed supplier figures somebody spent an evening typing, the service
calls, the cheque book. That folder is on one computer in an office, and the
day it stops working is the day the shop discovers whether anybody ever
copied it.

So backup is not an export. An export is something you find later and cannot
open. This writes one dated zip that contains everything, with a manifest
saying what is in it and which version wrote it, and it can be read back —
into the same installation or a different one — with a check first that says
what will be replaced before anything is.

Restoring is the dangerous half, so it never overwrites in place. The current
folder is moved aside first, and its location is reported, so a restore of the
wrong file is undone by moving one directory back.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .. import __version__
from .errors import ProfileOSError
from .logging_setup import get_logger

_log = get_logger("core.backup")

MANIFEST_NAME = "profileos-backup.json"

#: Never copied into a backup: caches, locks, and the half-written files a
#: crash leaves behind. Restoring a stale lock would greet somebody with a
#: message about a machine that no longer exists.
SKIP_SUFFIXES = (".tmp", ".lock", ".pyc")
SKIP_DIRECTORIES = ("__pycache__", "output", "logs")


@dataclass
class Manifest:
    """What a backup contains, written inside it."""

    created: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    version: str = __version__
    brand: str = ""
    files: int = 0
    bytes: int = 0
    #: A line per thing the shop would want to see before restoring.
    contents: dict[str, int] = field(default_factory=dict)

    def describe(self) -> str:
        when = self.created[:16].replace("T", " ")
        return (
            f"גיבוי מ-⁦{when}⁩ · גרסה ⁦{self.version}⁩ · "
            f"⁦{self.files}⁩ קבצים · ⁦{self.bytes / 1_048_576:.1f}⁩ MB"
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        labels = {
            "jobs": "תיקי עבודה",
            "customers": "לקוחות",
            "system_confirmations": "סדרות מאושרות",
            "service_calls": "קריאות שירות",
            "hardware": "פריטי פרזול",
            "price_list": "שורות מחירון",
            "files": "מסמכים וצילומים",
        }
        rows = [("נוצר", self.created[:16].replace("T", " ")), ("גרסה", self.version)]
        rows.extend(
            (labels.get(key, key), f"⁦{count}⁩")
            for key, count in sorted(self.contents.items())
        )
        return rows


def _should_skip(path: Path, root: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts)


def _count_contents(root: Path) -> dict[str, int]:
    """What is in the folder, in the shop's own terms rather than file counts."""
    contents: dict[str, int] = {}

    jobs = root / "jobs"
    if jobs.is_dir():
        contents["jobs"] = len(list(jobs.glob("*.json")))
        files = list(jobs.rglob("files/*"))
        if files:
            contents["files"] = len([item for item in files if item.is_file()])

    for name, key in (
        ("customers.json", "customers"),
        ("system_confirmations.json", "system_confirmations"),
        ("service_calls.json", "service_calls"),
        ("hardware.json", "hardware"),
        ("price_list.json", "price_list"),
    ):
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a count is not worth a failed backup
            contents[key] = 1
            continue
        if isinstance(data, dict):
            for value in data.values():
                if isinstance(value, list):
                    contents[key] = len(value)
                    break
            else:
                contents[key] = len(data)
        elif isinstance(data, list):
            contents[key] = len(data)
    return contents


def default_backup_folder() -> Path:
    """Where backups go unless somebody says otherwise.

    Beside the data folder rather than inside it: a backup written inside the
    folder it copies grows with every run, and is lost with the folder it was
    meant to survive.
    """
    from .config import get_settings

    return get_settings().data_dir.parent / "גיבויים"


def _dated_name(folder: Path) -> str:
    """A filename for today's backup that never lands on an existing one.

    The stamp is only accurate to the second, and two backups a second apart
    are not a strange thing — somebody clicks the button twice, or a nightly
    task and a person both run one. Silently overwriting the first would mean
    a folder that says it holds a week of copies while holding one.
    """
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    candidate = folder / f"profileos-{stamp}.zip"
    attempt = 2
    while candidate.exists() or candidate.with_suffix(".zip.part").exists():
        candidate = folder / f"profileos-{stamp}-{attempt}.zip"
        attempt += 1
    return candidate.name


def write_backup(destination: Path, *, root: Path | None = None) -> Path:
    """Write one dated zip of everything the shop's data folder holds."""
    from .config import get_settings

    root = Path(root) if root is not None else get_settings().data_dir
    if not root.is_dir():
        raise ProfileOSError(f"אין תיקיית נתונים ב-{root}")

    # Anything that is not named like an archive is a folder to put one in —
    # including a folder that does not exist yet. Treating it as a filename
    # would write a backup with no extension that nothing later finds, which
    # is the exact failure this module exists to prevent.
    destination = Path(destination)
    if destination.is_dir() or destination.suffix.lower() != ".zip":
        destination.mkdir(parents=True, exist_ok=True)
        destination = destination / _dated_name(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    from ..branding import active_brand

    manifest = Manifest(brand=active_brand().display_name)
    manifest.contents = _count_contents(root)

    # Written beside the target and renamed, so an interrupted backup never
    # replaces yesterday's good one with half a file.
    temporary = destination.with_suffix(destination.suffix + ".part")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or _should_skip(path, root):
                continue
            archive.write(path, path.relative_to(root).as_posix())
            manifest.files += 1
            manifest.bytes += path.stat().st_size
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(manifest.__dict__, ensure_ascii=False, indent=2),
        )
    temporary.replace(destination)
    _log.info("Backup written to %s: %s", destination, manifest.describe())
    return destination


def read_manifest(archive_path: Path) -> Manifest:
    """What is inside a backup, without unpacking it."""
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ProfileOSError(f"אין קובץ גיבוי ב-{archive_path}")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            raw = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    except KeyError as exc:
        raise ProfileOSError(
            f"{archive_path.name} אינו גיבוי של ProfileOS — אין בו מניפסט"
        ) from exc
    except zipfile.BadZipFile as exc:
        raise ProfileOSError(f"{archive_path.name} פגום ואי אפשר לקרוא אותו") from exc

    manifest = Manifest()
    for key, value in raw.items():
        if hasattr(manifest, key):
            setattr(manifest, key, value)
    return manifest


@dataclass
class RestorePlan:
    """What restoring this backup would do to the folder that is there now."""

    archive: Path
    manifest: Manifest
    into: Path
    #: What is in the folder now, so somebody can see what they are replacing.
    current: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def replaces_existing(self) -> bool:
        return bool(self.current)

    def describe(self) -> str:
        if not self.replaces_existing:
            return f"שחזור אל תיקייה ריקה: {self.manifest.describe()}"
        return (
            f"שחזור יחליף את הנתונים הקיימים. הגיבוי: {self.manifest.describe()}"
        )

    def comparison(self) -> list[tuple[str, str, str]]:
        """Side by side: what is there now, and what the backup holds."""
        keys = sorted(set(self.current) | set(self.manifest.contents))
        labels = {
            "jobs": "תיקי עבודה", "customers": "לקוחות",
            "system_confirmations": "סדרות מאושרות",
            "service_calls": "קריאות שירות", "hardware": "פריטי פרזול",
            "price_list": "שורות מחירון", "files": "מסמכים",
        }
        return [
            (
                labels.get(key, key),
                f"⁦{self.current.get(key, 0)}⁩",
                f"⁦{self.manifest.contents.get(key, 0)}⁩",
            )
            for key in keys
        ]


def plan_restore(archive_path: Path, *, root: Path | None = None) -> RestorePlan:
    """Read a backup and say what restoring it would replace. Changes nothing."""
    from .config import get_settings

    root = Path(root) if root is not None else get_settings().data_dir
    manifest = read_manifest(archive_path)
    plan = RestorePlan(
        archive=Path(archive_path), manifest=manifest, into=root,
        current=_count_contents(root) if root.is_dir() else {},
    )

    if manifest.version != __version__:
        plan.warnings.append(
            f"הגיבוי נכתב בגרסה ⁦{manifest.version}⁩ והתוכנה היא ⁦{__version__}⁩ — "
            "בדקו את הנתונים אחרי השחזור"
        )
    for key, current in plan.current.items():
        inside = manifest.contents.get(key, 0)
        if current > inside:
            plan.warnings.append(
                f"בתיקייה יש כרגע ⁦{current}⁩ {key} ובגיבוי ⁦{inside}⁩ — "
                "שחזור יחזיר אתכם אחורה"
            )
    return plan


def restore(
    archive_path: Path, *, root: Path | None = None, keep_current: bool = True
) -> tuple[Path, Path | None]:
    """Restore a backup, moving the current folder aside rather than deleting it.

    Returns the folder restored into and where the previous one was put, so a
    restore of the wrong file is undone by moving one directory back.
    """
    from .config import get_settings

    root = Path(root) if root is not None else get_settings().data_dir
    manifest = read_manifest(archive_path)

    aside: Path | None = None
    if root.is_dir() and any(root.iterdir()):
        if keep_current:
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            aside = root.with_name(f"{root.name}-לפני-שחזור-{stamp}")
            shutil.move(str(root), str(aside))
            _log.warning("Moved the current data folder aside to %s", aside)
        else:
            shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if name == MANIFEST_NAME:
                continue
            # A zip can name a path outside the folder it is unpacked into;
            # anything that tries is refused rather than written.
            target = (root / name).resolve()
            if not str(target).startswith(str(root.resolve())):
                raise ProfileOSError(
                    f"הגיבוי מכיל נתיב חורג ({name}) — הוא לא נפרס"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)

    _log.info("Restored %s into %s", manifest.describe(), root)
    return root, aside


def list_backups(folder: Path) -> list[tuple[Path, Manifest]]:
    """Every readable backup in a folder, newest first."""
    found: list[tuple[Path, Manifest]] = []
    for path in sorted(Path(folder).glob("*.zip")):
        try:
            found.append((path, read_manifest(path)))
        except ProfileOSError:
            continue
    # The stamp inside a backup is only accurate to the second, so two written
    # in the same second tie. The name breaks the tie the same way it was made:
    # the suffixed one came second.
    return sorted(
        found, key=lambda pair: (pair[1].created, pair[0].name), reverse=True
    )


def prune(folder: Path, *, keep: int = 14) -> list[Path]:
    """Delete the oldest backups past ``keep``, and say which went.

    A backup folder that fills the disk stops the shop working, which is the
    opposite of what backups are for.
    """
    backups = list_backups(folder)
    removed: list[Path] = []
    for path, _manifest in backups[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:  # pragma: no cover - permissions
            _log.warning("Could not remove old backup %s", path)
    return removed


def _unused(_: Iterable[Any]) -> None:  # pragma: no cover - typing helper
    return None


__all__ = [
    "MANIFEST_NAME",
    "default_backup_folder",
    "Manifest",
    "RestorePlan",
    "list_backups",
    "plan_restore",
    "prune",
    "read_manifest",
    "restore",
    "write_backup",
]
