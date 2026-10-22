"""The program keeps itself up to date: found, downloaded, checked, put in place.

Every change to ProfileOS goes through the same gate before it reaches the
shop: the whole test suite runs on GitHub, and only when every test passes
is a new version published there — one zip, its SHA-256 fingerprint, and
what is new in it, in Hebrew. Nobody runs a command at the shop.

While the program is open it looks for a newer version (shortly after it
starts, then every few hours). One that is found is downloaded, its
fingerprint checked against the published one, unpacked next to the program
— never over it — and started once on its own to see that it opens. Only
then is it marked ready. It is put in place the next time the program starts
(:mod:`launcher`), after a backup of the shop's data, and if it does not open
the previous version comes back by itself.

What this deliberately does not do:

* replace the program while it is running — files in use, half a version;
* touch the shop's data folder — it is elsewhere and nothing here writes it;
* take a version that did not pass its tests — only published ones exist;
* update a developer's checkout — a folder under version control is left
  to version control, and says so.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from .errors import ProfileOSError
from .logging_setup import get_logger

_log = get_logger("core.selfupdate")

REPOSITORY = "yyrhlbrstt-svg/ProfileOS"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
USER_AGENT = "ProfileOS self-update"
TIMEOUT_S = 30
#: A whole version is a few megabytes; anything near this is not one.
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_UNPACKED_BYTES = 600 * 1024 * 1024
#: How often the open program looks for a newer version.
CHECK_EVERY_S = 6 * 3600
#: The exit code that asks the launcher to put the update in place and start again.
RESTART_CODE = 75
#: The exit code of a sign-in the person cancelled: not a version that failed.
CANCELLED_CODE = 76
#: Seconds the window must stay open before this version counts as working.
PROVEN_AFTER_S = 45

#: ``fetch(url, limit) -> bytes``; injectable for tests and odd proxies.
Fetcher = Callable[[str, int], bytes]


def install_root() -> Path:
    """The folder the program runs from (holds ``profileos/`` and ``launcher.py``)."""
    return Path(__file__).resolve().parent.parent.parent


def updates_dir(root: Path | None = None) -> Path:
    return (root or install_root()) / ".updates"


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

def installed(root: Path | None = None) -> dict[str, str]:
    """The version running: from ``profileos/_release.json`` in a published
    build, ``dev`` in a checkout nobody published."""
    path = (root or install_root()) / "profileos" / "_release.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version"):
            return {key: str(value) for key, value in data.items()}
    except (OSError, ValueError):
        pass
    from .. import __version__

    found = {"version": "dev", "base": __version__}
    if under_version_control(root):
        try:
            head = _git(root or install_root(), "rev-parse", "--short", "HEAD", timeout=10)
            if head.returncode == 0:
                found["commit"] = head.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return found


def version_key(version: str) -> tuple[int, ...] | None:
    """'2026.10.22.14' → (2026, 10, 22, 14); None for anything else."""
    try:
        parts = tuple(int(part) for part in version.strip().lstrip("v").split("."))
    except (AttributeError, ValueError):
        return None
    return parts or None


def is_newer(candidate: str, current: str) -> bool:
    new, old = version_key(candidate), version_key(current)
    if new is None:
        return False
    return old is None or new > old


def under_version_control(root: Path | None = None) -> bool:
    return ((root or install_root()) / ".git").exists()


# --------------------------------------------------------------------------- #
# Finding a release
# --------------------------------------------------------------------------- #

def http_fetch(url: str, limit: int = 5 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json, */*",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - https only
        declared = int(response.headers.get("content-length") or 0)
        if declared > limit:
            raise ProfileOSError("קובץ העדכון גדול מהצפוי — לא הורד")
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ProfileOSError("קובץ העדכון גדול מהצפוי — לא הורד")
    return body


@dataclass
class Release:
    version: str
    zip_url: str
    sha256: str
    size: int
    commit: str = ""
    published: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)
    branch: str = ""


def latest_release(fetch: Fetcher | None = None) -> Release:
    """The newest published version, with its fingerprint."""
    fetch = fetch or http_fetch
    raw = json.loads(fetch(LATEST_URL, 2 * 1024 * 1024).decode("utf-8"))
    assets = {asset.get("name", ""): asset.get("browser_download_url", "")
              for asset in raw.get("assets", []) or []}
    manifest_url = assets.get("release.json", "")
    if not manifest_url:
        raise ProfileOSError("בגרסה שפורסמה אין קובץ אימות — היא לא תותקן")
    manifest = json.loads(fetch(manifest_url, 1024 * 1024).decode("utf-8"))
    name = str(manifest.get("zip", ""))
    if version_key(str(manifest.get("version", ""))) is None:
        raise ProfileOSError("לגרסה שפורסמה אין מספר גרסה תקין — היא לא תותקן")
    if name not in assets:
        raise ProfileOSError("קובץ הגרסה חסר בפרסום — היא לא תותקן")
    return Release(
        version=str(manifest.get("version", "")),
        zip_url=assets[name],
        sha256=str(manifest.get("sha256", "")).lower(),
        size=int(manifest.get("size", 0) or 0),
        commit=str(manifest.get("commit", "")),
        published=str(raw.get("published_at", "")),
        notes=list(manifest.get("notes", []) or []),
        branch=str(manifest.get("branch", "")),
    )


# --------------------------------------------------------------------------- #
# Downloading and checking
# --------------------------------------------------------------------------- #

def _unpack(archive: Path, target: Path) -> Path:
    """Unpack safely; returns the folder holding ``profileos/``."""
    with zipfile.ZipFile(archive) as pack:
        members = pack.infolist()
        if sum(info.file_size for info in members) > MAX_UNPACKED_BYTES:
            raise ProfileOSError("קובץ העדכון גדול מהצפוי — לא נפתח")
        for info in members:
            posix, windows = PurePosixPath(info.filename), PureWindowsPath(info.filename)
            if (posix.is_absolute() or windows.is_absolute() or windows.drive
                    or ".." in posix.parts or ".." in windows.parts):
                raise ProfileOSError(f"שם לא תקין בקובץ העדכון: {info.filename}")
        pack.extractall(target)
    if (target / "profileos" / "__init__.py").is_file():
        return target
    inner = [p for p in target.iterdir() if p.is_dir()]
    if len(inner) == 1 and (inner[0] / "profileos" / "__init__.py").is_file():
        return inner[0]
    raise ProfileOSError("בקובץ העדכון אין את התוכנה — הוא לא יותקן")


def _requirement_name(line: str) -> str:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return ""
    for mark in "<>=!~;[ @":
        line = line.split(mark, 1)[0]
    return line.strip()


def missing_components(staged: Path) -> list[str]:
    """The lines of the new version's ``requirements.txt`` naming a component
    this computer does not have at all."""
    from importlib import metadata

    try:
        lines = (staged / "requirements.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    missing = []
    for line in lines:
        name = _requirement_name(line)
        if not name:
            continue
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(line.split("#", 1)[0].strip())
    return missing


def self_check(staged: Path) -> str:
    """Start the new version's own code once, apart from everything: empty
    if it loads, else what went wrong.

    A component the new version needs and this computer lacks is installed
    for the check only, into a scratch folder — so a version that adds one
    can pass. Without internet for it, the check waits (raises) rather than
    calling the version broken.
    """
    launcher = staged / "launcher.py"
    if launcher.is_file():
        # The launcher is the one file the window never runs: read it here.
        try:
            compile(launcher.read_text(encoding="utf-8"), str(launcher), "exec")
        except (SyntaxError, ValueError, OSError) as exc:
            return f"launcher.py: {exc}"
    with tempfile.TemporaryDirectory(prefix="profileos-check-") as scratch:
        paths = [str(staged)]
        missing = missing_components(staged)
        if missing:
            components = Path(scratch) / "components"
            try:
                done = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet", "--target",
                     str(components), *missing],
                    capture_output=True, text=True, timeout=1800)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ProfileOSError(
                    "הגרסה החדשה צריכה רכיב חדש, ולא ניתן היה להוריד אותו — ננסה שוב מאוחר יותר"
                ) from exc
            if done.returncode != 0:
                raise ProfileOSError(
                    "הגרסה החדשה צריכה רכיב חדש, ולא ניתן היה להוריד אותו — ננסה שוב מאוחר יותר"
                )
            paths.append(str(components))
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(paths),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PROFILEOS_CONFIG_DIR": str(Path(scratch) / "config"),
            "PROFILEOS_DATA_DIR": str(Path(scratch) / "data"),
            "HOME": str(Path(scratch) / "home"),
            "USERPROFILE": str(Path(scratch) / "home"),
            "QT_QPA_PLATFORM": "offscreen",
        }
        # os._exit: what is being checked is that the window builds; Qt's
        # tear-down at interpreter exit is not, and can crash on its own.
        code = (
            "import os, sys;"
            "import profileos, profileos.core.storage, profileos.insights, profileos.ui.app;"
            "from profileos.ui.main_window import MainWindow;"
            "from PySide6.QtWidgets import QApplication;"
            "app = QApplication([]); MainWindow(); print('ok', flush=True);"
            "sys.stderr.flush(); os._exit(0)"
        )
        try:
            done = subprocess.run([sys.executable, "-c", code], cwd=staged, env=env,
                                  capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)
    if done.returncode != 0 or "ok" not in done.stdout:
        said = (done.stderr or done.stdout).strip()
        return said.splitlines()[-1] if said else f"exit {done.returncode}"
    return ""


def _zip_fingerprints(archive: Path, target: Path, staged: Path) -> dict[str, str]:
    """Every file the verified zip put into ``staged``, with its SHA-256 — the
    launcher checks them again right before it moves anything."""
    files: dict[str, str] = {}
    with zipfile.ZipFile(archive) as pack:
        for info in pack.infolist():
            if info.is_dir():
                continue
            try:
                name = (target / info.filename).relative_to(staged).as_posix()
            except ValueError:
                continue
            files[name] = hashlib.sha256(pack.read(info)).hexdigest()
    return files


def stage(release: Release, *, fetch: Fetcher | None = None, root: Path | None = None,
          check: Callable[[Path], str] = self_check) -> Path:
    """Download, verify, unpack and test a release next to the program.

    Marks it ready for the launcher only when every step passed. A failure at
    any step leaves nothing marked and the running version untouched.
    """
    root = root or install_root()
    fetch = fetch or http_fetch
    protected = guard_data(root)
    folder = updates_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    if not release.sha256 or len(release.sha256) != 64:
        raise ProfileOSError("לגרסה אין טביעת אצבע תקינה — היא לא תותקן")

    body = fetch(release.zip_url, MAX_ZIP_BYTES)
    if release.size and len(body) != release.size:
        raise ProfileOSError("קובץ העדכון לא ירד במלואו — ננסה שוב מאוחר יותר")
    if hashlib.sha256(body).hexdigest() != release.sha256:
        raise ProfileOSError(
            "טביעת האצבע של קובץ העדכון לא תואמת למה שפורסם — הוא לא יותקן"
        )
    archive = folder / f"profileos-{release.version}.zip"
    archive.write_bytes(body)

    _drop_old_staged(folder)
    # A name of its own: a second check running meanwhile never deletes it.
    target = folder / f"staged-{release.version}-{time.time_ns()}"
    try:
        staged = _unpack(archive, target)
        files = _zip_fingerprints(archive, target, staged)
        stamped = installed(staged).get("version")
        if stamped != release.version:
            raise ProfileOSError("הגרסה שבקובץ לא תואמת לגרסה שפורסמה — היא לא תותקן")
        problem = check(staged)
        if problem:
            raise DidNotOpen(f"הגרסה החדשה לא נפתחה בבדיקה, ולא תותקן: {problem}")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)
    if staged != target:
        # The launcher only takes a folder directly inside .updates.
        moved = folder / f"{target.name}-x"
        os.replace(staged, moved)
        shutil.rmtree(target, ignore_errors=True)
        staged = moved

    needs = _differs(root / "requirements.txt", staged / "requirements.txt")
    _write_json(folder / "ready.json", {
        "version": release.version,
        "path": str(staged),
        "needs_components": needs,
        "from_version": installed(root).get("version", ""),
        "commit": release.commit,
        "notes": release.notes,
        "staged_at": time.time(),
        # The launcher checks these again before it moves anything.
        "protect": protected,
        "files": files,
    })
    return staged


class DidNotOpen(ProfileOSError):
    """A version that failed its start check: remembered, not downloaded again."""


def _drop_old_staged(folder: Path) -> None:
    """Folders of versions no longer waiting; a fresh one may be another
    check's, still at work, and is left alone."""
    ready = _read_json(folder / "ready.json", {})
    keep = str(ready.get("path", "")) if isinstance(ready, dict) else ""
    for old in folder.glob("staged-*"):
        try:
            if str(old) == keep or time.time() - old.stat().st_mtime < 3600:
                continue
        except OSError:
            continue
        shutil.rmtree(old, ignore_errors=True)


# --------------------------------------------------------------------------- #
# A folder under version control
# --------------------------------------------------------------------------- #

def _git(root: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          timeout=timeout)


def git_ready(root: Path) -> str:
    """Empty when this checkout can be moved forward safely, else why not."""
    try:
        if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
            return "התיקייה אינה מאגר git תקין"
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "git לא מותקן במחשב"
    if dirty:
        return "יש שינויים מקומיים בקבצי התוכנה — העדכון לא ייגע בהם"
    return ""


def stage_git(release: Release, *, root: Path | None = None,
              check: Callable[[Path], str] = self_check) -> str:
    """Bring the published commit in, test it apart, and mark it ready.

    Only the commit that passed the tests (from the published release) is
    fetched; the checkout moves to it only by fast-forward, at the next start,
    and only if nothing was changed locally.
    """
    root = root or install_root()
    if not release.commit:
        raise ProfileOSError("בגרסה שפורסמה אין מזהה גרסה — לא ניתן לעדכן דרך git")
    if version_key(release.version) is None:
        raise ProfileOSError("לגרסה שפורסמה אין מספר גרסה תקין — היא לא תותקן")
    problem = git_ready(root)
    if problem:
        raise ProfileOSError(problem)
    fetched = _git(root, "fetch", "--quiet", "origin", release.commit, timeout=600)
    if fetched.returncode != 0:
        raise ProfileOSError("ההורדה מ־GitHub נכשלה — ננסה שוב מאוחר יותר")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    ancestor = _git(root, "merge-base", "--is-ancestor", head, release.commit)
    if ancestor.returncode != 0:
        raise ProfileOSError("הגרסה המקומית התפצלה מהגרסה שפורסמה — העדכון לא יבוצע אוטומטית")
    folder = updates_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    checkout = folder / f"check-{release.version}"
    if checkout.exists():
        _git(root, "worktree", "remove", "--force", str(checkout))
        shutil.rmtree(checkout, ignore_errors=True)
    added = _git(root, "worktree", "add", "--detach", str(checkout), release.commit)
    if added.returncode != 0:
        raise ProfileOSError("לא ניתן היה להכין את הגרסה לבדיקה")
    try:
        problem = check(checkout)
        needs = _differs(root / "requirements.txt", checkout / "requirements.txt")
    finally:
        _git(root, "worktree", "remove", "--force", str(checkout))
        shutil.rmtree(checkout, ignore_errors=True)
    if problem:
        raise DidNotOpen(f"הגרסה החדשה לא נפתחה בבדיקה, ולא תותקן: {problem}")
    _write_json(folder / "ready.json", {
        "version": release.version, "mode": "git", "commit": release.commit,
        "branch": release.branch,
        "previous_commit": head, "needs_components": needs,
        "from_version": installed(root).get("version", ""), "notes": release.notes,
        "staged_at": time.time(),
    })
    return release.commit


def protected_paths() -> list[str]:
    """The shop's folders an update must never move: data, backups, mirror."""
    try:
        from .backup import default_backup_folder
        from .config import get_settings

        settings = get_settings()
        paths = [settings.data_dir, default_backup_folder()]
        if settings.backup_mirror:
            paths.append(Path(settings.backup_mirror))
        config_dir = getattr(settings, "config_dir", None)
        if config_dir:
            paths.append(Path(config_dir))
    except Exception as exc:  # noqa: BLE001 - unknown means refuse, not guess
        raise ProfileOSError(f"לא ניתן לדעת איפה הנתונים — העדכון לא יוכן ({exc})") from exc
    return [str(Path(path).resolve()) for path in paths]


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def guard_data(root: Path) -> list[str]:
    """Refuse to prepare an update that would move the shop's data with the program."""
    protected = protected_paths()
    inside = [path for path in protected if _inside(path, root)]
    if inside:
        raise ProfileOSError(
            "תיקיית הנתונים או הגיבויים נמצאת בתוך תיקיית התוכנה ("
            + ", ".join(inside) + "). כדי לא להזיז אותה, העדכון האוטומטי לא יבוצע — "
            "העבירו את הנתונים לתיקייה אחרת במסך מערכת."
        )
    return protected


def _differs(old: Path, new: Path) -> bool:
    try:
        return new.is_file() and (not old.is_file() or old.read_bytes() != new.read_bytes())
    except OSError:
        return True


def _write_json(path: Path, payload: Any) -> None:
    from .storage import write_json

    write_json(path, payload)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# What the window asks
# --------------------------------------------------------------------------- #

def _v(text: str) -> str:
    """A version number inside Hebrew text, kept in its order."""
    return f"\u2066{text}\u2069"


#: The launcher's reasons, said to the person.
WAITING_REASONS = {
    "backup": "הגיבוי לפני העדכון לא הצליח — העדכון יחכה להפעלה שבה הגיבוי יצליח",
    "components": "אין חיבור לאינטרנט להתקנת רכיב חדש — ננסה שוב בהפעלה הבאה",
    "running": "ProfileOS פתוחה בחלון נוסף (או טרמינל הטלפון) — העדכון יותקן כשכולם ייסגרו",
}
FAILED_REASONS = {
    "interrupted": "ההתקנה נקטעה באמצע והגרסה הקודמת הוחזרה — העדכון יורד מחדש",
    "damaged": "קובץ העדכון השתנה מאז שנבדק — הוא יורד מחדש",
    "merge": "לא ניתן היה לקדם את התיקייה לגרסה החדשה — יש בה שינויים מקומיים",
    "rollback": "הגרסה החדשה לא נפתחה ולא ניתן היה להחזיר את הקודמת אוטומטית — פנו לתמיכה",
    "not_the_update": "גם הגרסה הקודמת לא נפתחה — הבעיה אינה בעדכון. פנו לתמיכה",
}


@dataclass
class Status:
    installed: str
    available: str = ""
    ready: str = ""
    checked_at: float = 0.0
    error: str = ""
    #: The launcher's last word: installed, rolled_back, waiting, failed.
    last: dict[str, Any] = field(default_factory=dict)
    managed: bool = True
    #: Versions that failed to open, here or in the start check.
    blocked: list[str] = field(default_factory=list)
    git: bool = False

    def problem(self) -> str:
        """What stops the program from updating, in words; empty when nothing."""
        last = self.last if isinstance(self.last, dict) else {}
        reason = str(last.get("reason", ""))
        if self.ready and last.get("version") == self.ready:
            if last.get("outcome") == "waiting":
                return WAITING_REASONS.get(reason, "")
            if last.get("outcome") == "failed":
                return FAILED_REASONS.get(reason, f"העדכון לא הותקן: {reason}")
        if last.get("outcome") == "failed" and reason in ("rollback", "not_the_update"):
            return FAILED_REASONS[reason]
        return self.error

    def describe(self) -> str:
        if self.ready:
            text = f"גרסה {_v(self.ready)} הורדה ונבדקה — תותקן בהפעלה הבאה"
            problem = self.problem()
            return f"{text}. {problem}" if problem else text
        if self.error:
            return f"בדיקת העדכונים נכשלה: {self.error}"
        if self.available and self.available in self.blocked:
            return (f"גרסה {_v(self.available)} לא נפתחה בבדיקה ולא תותקן — "
                    "ממתינים לגרסה המתוקנת הבאה")
        if self.available and is_newer(self.available, self.installed) and not self.git:
            return f"גרסה {_v(self.available)} זמינה — מורידים ובודקים"
        if self.checked_at:
            return "התוכנה מעודכנת"
        return "עוד לא נבדק"


def _state_path(root: Path | None = None) -> Path:
    return updates_dir(root) / "state.json"


def status(root: Path | None = None) -> Status:
    root = root or install_root()
    state = _read_json(_state_path(root), {})
    state = state if isinstance(state, dict) else {}
    ready = _read_json(updates_dir(root) / "ready.json", {})
    bad = _read_json(updates_dir(root) / "bad_versions.json", []) or []
    failed = [str(state["failed"])] if state.get("failed") else []
    return Status(
        installed=installed(root).get("version", "dev"),
        available=str(state.get("available", "")),
        ready=str(ready.get("version", "")) if isinstance(ready, dict) else "",
        checked_at=float(state.get("checked_at", 0.0) or 0.0),
        error=str(state.get("error", "")),
        last=_read_json(updates_dir(root) / "last_result.json", {}) or {},
        managed=True,
        blocked=sorted({*map(str, bad), *failed}),
        git=under_version_control(root),
    )


def _http_problem(exc: urllib.error.HTTPError) -> str:
    if exc.code == 404:
        return ""  # nothing published yet: not a problem
    if exc.code in (403, 429):
        return "GitHub הגביל זמנית את הבדיקות — ננסה שוב בעוד כמה שעות"
    return f"שרת העדכונים לא ענה כרגיל (קוד {_v(str(exc.code))}) — ננסה שוב מאוחר יותר"


#: One check at a time in this program: the timer and the button share it.
_CHECKING = threading.Lock()


def check_and_stage(*, fetch: Fetcher | None = None, root: Path | None = None,
                    check: Callable[[Path], str] = self_check) -> Status:
    """Look for a newer version and, when there is one, make it ready.

    Never raises: what went wrong is kept for the window to say. A second
    call while one is at work returns the state as it is.
    """
    root = root or install_root()
    if not _CHECKING.acquire(blocking=False):
        return status(root)
    try:
        return _check_and_stage(fetch, root, check)
    finally:
        _CHECKING.release()


def _check_and_stage(fetch: Fetcher | None, root: Path,
                     check: Callable[[Path], str]) -> Status:
    before = _read_json(_state_path(root), {})
    before = before if isinstance(before, dict) else {}
    state: dict[str, Any] = {"checked_at": time.time(), "error": "",
                             "failed": before.get("failed", "")}
    try:
        git = under_version_control(root)
        current = installed(root).get("version", "dev")
        release = latest_release(fetch)
        state["available"] = release.version
        bad = set(_read_json(updates_dir(root) / "bad_versions.json", []) or [])
        if state["failed"]:
            bad.add(state["failed"])
        ready = _read_json(updates_dir(root) / "ready.json", {})
        already = isinstance(ready, dict) and ready.get("version") == release.version
        try:
            if git:
                if _git_wants(root, release) and release.version not in bad and not already:
                    stage_git(release, root=root, check=check)
            elif is_newer(release.version, current) and release.version not in bad and not already:
                stage(release, fetch=fetch, root=root, check=check)
        except DidNotOpen:
            # Not downloaded again every few hours: the next version replaces it.
            state["failed"] = release.version
            raise
    except urllib.error.HTTPError as exc:
        state["error"] = _http_problem(exc)
    except urllib.error.URLError:
        state["error"] = "אין חיבור לאינטרנט — ננסה שוב מאוחר יותר"
    except Exception as exc:  # noqa: BLE001 - said in the window, never raised
        _log.warning("Update check failed: %s", exc)
        state["error"] = str(exc)
    try:
        updates_dir(root).mkdir(parents=True, exist_ok=True)
        _write_json(_state_path(root), state)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Could not record the update check: %s", exc)
    return status(root)


def _git_wants(root: Path, release: Release) -> bool:
    """Whether this checkout should move to the release: it is on the branch
    that is published, and the release is not already in its history."""
    if not release.commit:
        return False
    try:
        if release.branch:
            branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", timeout=10).stdout.strip()
            if branch != release.branch:
                return False
        head = _git(root, "rev-parse", "HEAD", timeout=10).stdout.strip()
        if head == release.commit:
            return False
        contained = _git(root, "merge-base", "--is-ancestor", release.commit, head, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # Already ahead of it (a developer's own commits on top): nothing to do.
    return contained.returncode != 0


def mark_started(root: Path | None = None) -> None:
    """The window opened and stayed open: this version works (the launcher's proof)."""
    root = root or install_root()
    try:
        folder = updates_dir(root)
        if folder.is_dir():
            _write_json(folder / "started.json",
                        {"version": installed(root).get("version", ""), "at": time.time()})
    except Exception as exc:  # noqa: BLE001 - a missing proof only means a cautious launcher
        _log.warning("Could not mark the start: %s", exc)


_INSTANCE: Any = None


def hold_instance(root: Path | None = None) -> None:
    """Say, for as long as this program runs, that it is running — so the
    launcher never moves files out from under it. The file stays open: on
    Windows nothing can delete it until this process ends."""
    global _INSTANCE
    if _INSTANCE is not None:
        return
    root = root or install_root()
    try:
        folder = updates_dir(root)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"instance-{os.getpid()}.lock"
        handle = open(path, "w", encoding="ascii")  # noqa: SIM115 - held until exit
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError as exc:
        _log.warning("Could not mark this program as running: %s", exc)
        return
    _INSTANCE = (handle, path)

    import atexit

    def _let_go() -> None:
        try:
            handle.close()
            path.unlink(missing_ok=True)
        except OSError:
            pass

    atexit.register(_let_go)


def launched_by_launcher() -> bool:
    return os.environ.get("PROFILEOS_LAUNCHER") == "1"


# --------------------------------------------------------------------------- #
# The old desktop shortcut
# --------------------------------------------------------------------------- #

#: What the first desktop script did at every start: download the branch as
#: it stood that minute (untested), delete the program folder, start that.
OLD_SCRIPT_MARK = f"github.com/{REPOSITORY}/archive/refs/heads/"


def _desktops(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    found = [home / "Desktop", home / "OneDrive" / "Desktop"]
    profile = os.environ.get("USERPROFILE")
    if profile and home == Path.home():
        found.append(Path(profile) / "Desktop")
    unique: list[Path] = []
    for path in found:
        if path.is_dir() and path not in unique:
            unique.append(path)
    return unique


def old_desktop_scripts(home: Path | None = None) -> list[Path]:
    """Copies of the first desktop script, which would keep undoing the
    updates: each start deleted the program folder and ran untested code."""
    scripts = []
    for desktop in _desktops(home):
        for path in sorted(desktop.glob("*.bat")):
            try:
                if path.stat().st_size > 64 * 1024:
                    continue
                text = path.read_bytes().decode("utf-8", "replace")
            except OSError:
                continue
            if OLD_SCRIPT_MARK in text:
                scripts.append(path)
    return scripts


def replace_old_script(path: Path, root: Path | None = None) -> None:
    """Put the new first-install script in its place; the old one is kept
    beside it (``.bak``). Safe while the old one is still running: the new
    one is padded so cmd, reading on from where the old one ended, exits."""
    from .storage import atomic_write_bytes

    new = ((root or install_root()) / "scripts" / "launch_windows.bat").read_bytes()
    if b"goto start" not in new:
        raise ProfileOSError("קובץ ההפעלה החדש חסר — קיצור הדרך לא הוחלף")
    atomic_write_bytes(path, new.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


# --------------------------------------------------------------------------- #
# What is new
# --------------------------------------------------------------------------- #

def whats_new(since: str = "", *, root: Path | None = None) -> list[dict[str, Any]]:
    """Entries of ``profileos/data/whats_new.json`` newer than ``since`` (a date)."""
    path = (root or install_root()) / "profileos" / "data" / "whats_new.json"
    entries = _read_json(path, []) or []
    # The same day counts: a version from the morning lacks the evening's news.
    return [entry for entry in entries if str(entry.get("date", "")) >= since]


def unannounced(root: Path | None = None) -> dict[str, Any] | None:
    """The launcher's result, once, for the window to tell: installed or rolled back."""
    root = root or install_root()
    path = updates_dir(root) / "last_result.json"
    result = _read_json(path, None)
    if not isinstance(result, dict) or result.get("announced"):
        return None
    if result.get("outcome") not in ("installed", "rolled_back", "failed"):
        return None
    result["announced"] = True
    try:
        _write_json(path, result)
    except Exception:  # noqa: BLE001 - told twice is better than not told
        pass
    result["announced"] = False
    return result


__all__ = [
    "CANCELLED_CODE",
    "CHECK_EVERY_S",
    "PROVEN_AFTER_S",
    "hold_instance",
    "missing_components",
    "old_desktop_scripts",
    "replace_old_script",
    "RESTART_CODE",
    "Release",
    "Status",
    "check_and_stage",
    "install_root",
    "installed",
    "is_newer",
    "latest_release",
    "launched_by_launcher",
    "mark_started",
    "self_check",
    "stage",
    "status",
    "unannounced",
    "updates_dir",
    "version_key",
    "whats_new",
]
