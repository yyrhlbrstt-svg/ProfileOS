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

#: Where the settings folder sits inside an archive, apart from the data.
CONFIG_PREFIX = "_config/"

#: Settings that describe one computer — where its folders are — and would
#: point a restored machine at paths it does not have. Kept in the archive,
#: never written back over the new machine's own.
CONFIG_KEEP_LOCAL = ("settings.json", "settings.json.bak")

#: The folder a restore puts the settings it replaced in. Never itself
#: backed up, so copies cannot nest inside copies restore after restore.
CONFIG_ASIDE = ".לפני-שחזור"

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
            f"⁦{self.files}⁩ קבצים · ⁦{self.bytes / 1_048_576:.1f} מ״ב⁩"
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


#: How the shop calls each kind of record, for anything said about a backup.
_CONTENT_LABELS = {
    "jobs": "פרויקטים", "customers": "לקוחות",
    "system_confirmations": "סדרות מאושרות", "service_calls": "קריאות שירות",
    "hardware": "פריטי פרזול", "price_list": "שורות מחירון",
    "files": "מסמכים וצילומים",
}


def _in_words(exc: BaseException) -> str:
    """Why a backup failed, said so somebody knows what to do about it."""
    import errno

    if isinstance(exc, ProfileOSError):
        return exc.message
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return "אין מספיק מקום בדיסק. פנו מקום או בחרו תיקיית גיבוי אחרת."
        if isinstance(exc, PermissionError):
            return "אין הרשאה לכתוב לתיקיית הגיבוי. בחרו תיקייה אחרת."
        if isinstance(exc, FileNotFoundError):
            return "תיקיית הגיבוי לא נמצאה — אולי הכונן לא מחובר."
    return "הגיבוי לא נכתב. בדקו שיש מקום בדיסק ושהתיקייה זמינה, ונסו שוב."


def _should_skip(path: Path, root: Path) -> bool:
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts)


def _config_files(config: Path | None, root: Path) -> list[tuple[Path, str]]:
    """The settings folder's files, unless they are already in the data."""
    if config is None or not Path(config).is_dir():
        return []
    config = Path(config).resolve()
    resolved_root = root.resolve()
    if config == resolved_root or resolved_root in config.parents:
        return []
    found: list[tuple[Path, str]] = []
    for path in sorted(config.rglob("*")):
        if not path.is_file() or _should_skip(path, config):
            continue
        # The data folder can sit inside the settings folder on some
        # machines; it has already been written once.
        if path.resolve() == resolved_root or resolved_root in path.resolve().parents:
            continue
        relative = path.relative_to(config)
        if relative.parts[0] in (CONFIG_ASIDE, ".trash") or "לפני-שחזור" in path.name \
                or ".damaged-" in path.name:
            continue
        found.append((path, path.relative_to(config).as_posix()))
    return found


def _verify(archive_path: Path) -> None:
    """Read every member back; refuse an archive that does not survive it."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            broken = archive.testzip()
            archive.read(MANIFEST_NAME)
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        archive_path.unlink(missing_ok=True)
        raise ProfileOSError(f"הגיבוי נכתב פגום ונמחק: {exc}") from exc
    if broken is not None:
        archive_path.unlink(missing_ok=True)
        raise ProfileOSError(f"הגיבוי נכתב פגום ({broken}) ונמחק — נסו שוב")


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

    settings = get_settings()
    if settings.backup_folder:
        return Path(settings.backup_folder)
    return settings.data_dir.parent / "גיבויים"


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


def write_backup(
    destination: Path, *, root: Path | None = None, config: Path | None = None
) -> Path:
    """Write one dated zip of everything the shop's data folder holds.

    With no ``root`` given this is the installation's own data, and its
    settings folder goes in too; an explicit ``root`` backs up that folder
    alone unless ``config`` names one.
    """
    from .config import get_settings

    if root is None:
        settings = get_settings()
        root = settings.data_dir
        config = config if config is not None else settings.config_dir
    root = Path(root)
    if not root.is_dir():
        raise ProfileOSError(f"אין תיקיית נתונים ב-{root}")

    # A destination inside the folder being zipped writes the archive into
    # its own subtree while it is still being built — the walk below picks
    # the half-written file back up as one more thing to include, and the
    # zip grows into itself until something gives out. Refused up front,
    # rather than left to whoever is watching the window not respond.
    resolved_root = root.resolve()
    resolved_destination = Path(destination).resolve()
    if resolved_destination == resolved_root or resolved_root in resolved_destination.parents:
        raise ProfileOSError(
            "אי אפשר לשמור גיבוי בתוך תיקיית הנתונים עצמה — "
            "הוא יכלול את עצמו וימשיך לתפוח. בחר תיקייה מחוץ לתיקיית הנתונים."
        )

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
        # The settings and the sign-in file live in a separate folder. A new
        # computer restored from data alone would open with nobody able to
        # sign in, so they travel in the same archive under their own prefix.
        for path, name in _config_files(config, root):
            archive.write(path, f"{CONFIG_PREFIX}{name}")
            manifest.files += 1
            manifest.bytes += path.stat().st_size
        archive.writestr(
            MANIFEST_NAME,
            json.dumps(manifest.__dict__, ensure_ascii=False, indent=2),
        )
    # A backup nobody can open is discovered on the day it is needed. Each
    # one is read back in full before it is allowed to count.
    _verify(temporary)
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
            f"⁦{archive_path.name}⁩ אינו קובץ גיבוי של התוכנה"
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
                f"{_CONTENT_LABELS.get(key, key)}: יש כרגע ⁦{current}⁩ ובגיבוי ⁦{inside}⁩ — "
                "שחזור יחזיר אתכם אחורה"
            )
    return plan


def restore(
    archive_path: Path, *, root: Path | None = None, keep_current: bool = True,
    config: Path | None = None,
) -> tuple[Path, Path | None]:
    """Restore a backup, moving the current folder aside rather than deleting it.

    Returns the folder restored into and where the previous one was put, so a
    restore of the wrong file is undone by moving one directory back.
    """
    from .config import get_settings

    # The installation's own settings folder is restored only with its own
    # data; restoring into some other folder leaves the settings alone.
    restore_config = root is None
    root = Path(root) if root is not None else get_settings().data_dir
    manifest = read_manifest(archive_path)

    aside: Path | None = None
    if root.is_dir() and any(root.iterdir()):
        if keep_current:
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            aside = root.with_name(f"{root.name}-לפני-שחזור-{stamp}")
            serial = 2
            while aside.exists():  # two restores in one second
                aside = root.with_name(f"{root.name}-לפני-שחזור-{stamp}-{serial}")
                serial += 1
            shutil.move(str(root), str(aside))
            _log.warning("Moved the current data folder aside to %s", aside)
        else:
            shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    config_target = config
    if config_target is None and restore_config:
        config_target = get_settings().config_dir

    with zipfile.ZipFile(archive_path) as archive:
        for name in archive.namelist():
            if name == MANIFEST_NAME:
                continue
            if name.startswith(CONFIG_PREFIX):
                if config_target is not None:
                    _restore_config_file(archive, name, Path(config_target))
                continue
            # A zip can name a path outside the folder it is unpacked into;
            # anything that tries is refused rather than written.
            target = (root / name).resolve()
            if not str(target).startswith(str(root.resolve())):
                raise ProfileOSError(
                    f"בגיבוי יש קובץ שמחוץ לתיקיית הנתונים (⁦{name}⁩). הגיבוי לא שוחזר."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)

    from .audit import Action, try_record

    try_record(
        Action.RESTORED, f"data:{root.name}",
        before=str(aside) if aside else None,
        after=str(Path(archive_path).name),
        note=manifest.describe(),
    )
    _log.info("Restored %s into %s", manifest.describe(), root)
    return root, aside


def _restore_config_file(archive: zipfile.ZipFile, name: str, config: Path) -> None:
    """Put one settings file back, keeping whatever was there beside it."""
    relative = name[len(CONFIG_PREFIX):]
    if not relative or relative in CONFIG_KEEP_LOCAL:
        return
    target = (config / relative).resolve()
    if not str(target).startswith(str(config.resolve())):
        raise ProfileOSError(
            f"בגיבוי יש קובץ שמחוץ לתיקיית ההגדרות (⁦{name}⁩). הוא לא שוחזר."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        # Kept in one folder per restore, beside — not inside — the settings.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        aside = config / CONFIG_ASIDE / stamp / relative
        serial = 2
        while aside.exists():
            aside = config / CONFIG_ASIDE / f"{stamp}-{serial}" / relative
            serial += 1
        aside.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, aside)
    with archive.open(name) as source, target.open("wb") as sink:
        shutil.copyfileobj(source, sink)


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


def _newest_change(root: Path) -> float:
    """When anything in the data folder last changed."""
    newest = 0.0
    for path in root.rglob("*"):
        if path.is_file() and not _should_skip(path, root):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def prune_by_day(folder: Path, *, days: int = 30) -> list[Path]:
    """Keep every backup from today and the last one of each earlier day,
    for ``days`` days; delete the rest.

    Counting archives would let a busy day of hourly backups push last
    month out of the folder; counting days keeps the history the same
    length however often the program is opened.
    """
    from datetime import date

    today = date.today().isoformat()
    by_day: dict[str, list[Path]] = {}
    for path in Path(folder).glob("profileos-*.zip"):
        day = path.name[len("profileos-"):len("profileos-") + 10]
        by_day.setdefault(day, []).append(path)
    keep_days = sorted(by_day, reverse=True)[:days]
    removed: list[Path] = []
    for day, paths in by_day.items():
        paths.sort(key=lambda path: path.stat().st_mtime)
        if day == today:
            survivors = paths
        elif day in keep_days:
            survivors = paths[-1:]
        else:
            survivors = []
        for path in paths:
            if path in survivors:
                continue
            try:
                path.unlink()
                removed.append(path)
            except OSError:  # pragma: no cover - permissions
                _log.warning("Could not remove old backup %s", path)
    return removed


def backup_if_due(
    folder: Path | None = None, *, keep: int = 30, mirror: Path | None = None
) -> Path | None:
    """Write today's backup if none exists yet, and keep the last ``keep``.

    The backup a small shop has is the one that happened without anybody
    remembering to make it. Run when the application opens and closes: at
    most one a day, into the backup folder, oldest pruned. A backup beside
    the data survives a deleted file but not a dead disk, so when a second
    folder is set — a USB drive, a synced cloud folder — today's archive is
    copied there too, whenever it is reachable.

    Returns the archive written, or ``None`` when today already has one or
    there is no data yet. Never raises — a failed backup must not stop the
    window closing; it is logged and reported by :func:`backup_status`.
    """
    from datetime import date

    try:
        from .config import get_settings

        settings = get_settings()
        root = settings.data_dir
        if not root.is_dir() or not any(root.iterdir()):
            return None
        target = Path(folder) if folder is not None else default_backup_folder()
        if mirror is None:
            mirror = settings.backup_mirror
        today = date.today().isoformat()
        existing = sorted(
            target.glob(f"profileos-{today}-*.zip"), key=lambda path: path.stat().st_mtime
        ) if target.is_dir() else []
        written: Path | None = None
        # Today's first backup holds the morning. Anything changed since is
        # written again — at close, or on the hourly check while the program
        # stays open — so the newest archive is never older than the work.
        if existing and _newest_change(root) <= existing[-1].stat().st_mtime:
            archive = existing[-1]
        else:
            archive = write_backup(target)
            prune_by_day(target, days=keep)
            written = archive
        if mirror:
            copy_to_mirror(archive, Path(mirror), keep=keep)
        _STATUS.update(error="", last=str(archive))
        return written
    except Exception as exc:  # noqa: BLE001 - closing the window comes first
        _log.warning("Automatic backup skipped: %s", exc)
        _STATUS.update(error=_in_words(exc))
        return None


#: The last automatic backup's outcome, for the window to show.
_STATUS: dict[str, str] = {"error": "", "last": "", "mirror_error": ""}


def backup_status() -> dict[str, str]:
    """What the last automatic backup did — empty strings when all is well."""
    return dict(_STATUS)


def copy_to_mirror(archive: Path, mirror: Path, *, keep: int = 30) -> Path | None:
    """Copy one archive to the second folder, if that folder can be reached.

    A USB drive that is not plugged in is not an error worth stopping for,
    but it is worth saying, so the window can show that the second copy is
    behind.
    """
    mirror = Path(mirror)
    # The folder, or at least the folder it sits in, must already exist: a
    # drive letter that is gone, or a mount point with nothing mounted, would
    # otherwise quietly become a new folder on the same disk as the first copy.
    if not mirror.is_dir() and not mirror.parent.is_dir():
        _STATUS["mirror_error"] = f"הכונן של העותק השני לא מחובר ({mirror})"
        _log.warning("Backup mirror not reachable: %s", mirror)
        return None
    try:
        mirror.mkdir(exist_ok=True)
        target = mirror / archive.name
        if target.is_file() and target.stat().st_size == archive.stat().st_size:
            _STATUS["mirror_error"] = ""
            return target
        partial = target.with_suffix(target.suffix + ".part")
        shutil.copyfile(archive, partial)
        _verify(partial)
        partial.replace(target)
        prune_by_day(mirror, days=keep)
    except Exception as exc:  # noqa: BLE001 - the first copy already exists
        _STATUS["mirror_error"] = f"העתקה לעותק השני נכשלה: {_in_words(exc)}"
        _log.warning("Backup mirror failed: %s", exc)
        return None
    _STATUS["mirror_error"] = ""
    _log.info("Backup copied to %s", target)
    return target


def _unused(_: Iterable[Any]) -> None:  # pragma: no cover - typing helper
    return None


__all__ = [
    "MANIFEST_NAME",
    "backup_if_due",
    "backup_status",
    "copy_to_mirror",
    "default_backup_folder",
    "Manifest",
    "RestorePlan",
    "list_backups",
    "plan_restore",
    "prune",
    "prune_by_day",
    "read_manifest",
    "restore",
    "write_backup",
]
