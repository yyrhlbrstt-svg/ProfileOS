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


def self_check(staged: Path) -> str:
    """Start the new version's own code once, apart from everything: empty
    if it loads, else what went wrong."""
    with tempfile.TemporaryDirectory(prefix="profileos-check-") as scratch:
        env = {
            **os.environ,
            "PYTHONPATH": str(staged),
            "PROFILEOS_CONFIG_DIR": str(Path(scratch) / "config"),
            "PROFILEOS_DATA_DIR": str(Path(scratch) / "data"),
            "QT_QPA_PLATFORM": "offscreen",
        }
        code = (
            "import profileos, profileos.core.storage, profileos.insights, profileos.ui.app;"
            "from profileos.ui.main_window import MainWindow;"
            "from PySide6.QtWidgets import QApplication;"
            "app = QApplication([]); MainWindow(); print('ok')"
        )
        try:
            done = subprocess.run([sys.executable, "-c", code], cwd=staged, env=env,
                                  capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)
    if done.returncode != 0 or "ok" not in done.stdout:
        return (done.stderr or done.stdout).strip().splitlines()[-1:][0] if (
            done.stderr or done.stdout).strip() else f"exit {done.returncode}"
    return ""


def stage(release: Release, *, fetch: Fetcher | None = None, root: Path | None = None,
          check: Callable[[Path], str] = self_check) -> Path:
    """Download, verify, unpack and test a release next to the program.

    Marks it ready for the launcher only when every step passed. A failure at
    any step leaves nothing marked and the running version untouched.
    """
    root = root or install_root()
    fetch = fetch or http_fetch
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

    target = folder / f"staged-{release.version}"
    if target.exists():
        shutil.rmtree(target)
    try:
        staged = _unpack(archive, target)
        stamped = installed(staged).get("version")
        if stamped != release.version:
            raise ProfileOSError("הגרסה שבקובץ לא תואמת לגרסה שפורסמה — היא לא תותקן")
        problem = check(staged)
        if problem:
            raise ProfileOSError(f"הגרסה החדשה לא נפתחה בבדיקה, ולא תותקן: {problem}")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        archive.unlink(missing_ok=True)

    needs = _differs(root / "requirements.txt", staged / "requirements.txt")
    _write_json(folder / "ready.json", {
        "version": release.version,
        "path": str(staged),
        "needs_components": needs,
        "from_version": installed(root).get("version", ""),
        "commit": release.commit,
        "notes": release.notes,
        "staged_at": time.time(),
    })
    return staged


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
        raise ProfileOSError(f"הגרסה החדשה לא נפתחה בבדיקה, ולא תותקן: {problem}")
    _write_json(folder / "ready.json", {
        "version": release.version, "mode": "git", "commit": release.commit,
        "previous_commit": head, "needs_components": needs,
        "from_version": installed(root).get("version", ""), "notes": release.notes,
        "staged_at": time.time(),
    })
    return release.commit


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

    def describe(self) -> str:
        if self.ready:
            return f"גרסה {self.ready} הורדה ונבדקה — תותקן בהפעלה הבאה"
        if self.error:
            return f"בדיקת העדכונים נכשלה: {self.error}"
        if self.available and is_newer(self.available, self.installed):
            return f"גרסה {self.available} זמינה — מורידים ובודקים"
        if self.checked_at:
            return "התוכנה מעודכנת"
        return "עוד לא נבדק"


def _state_path(root: Path | None = None) -> Path:
    return updates_dir(root) / "state.json"


def status(root: Path | None = None) -> Status:
    root = root or install_root()
    state = _read_json(_state_path(root), {})
    ready = _read_json(updates_dir(root) / "ready.json", {})
    return Status(
        installed=installed(root).get("version", "dev"),
        available=str(state.get("available", "")),
        ready=str(ready.get("version", "")) if isinstance(ready, dict) else "",
        checked_at=float(state.get("checked_at", 0.0) or 0.0),
        error=str(state.get("error", "")),
        last=_read_json(updates_dir(root) / "last_result.json", {}) or {},
        managed=True,
    )


def check_and_stage(*, fetch: Fetcher | None = None, root: Path | None = None,
                    check: Callable[[Path], str] = self_check) -> Status:
    """Look for a newer version and, when there is one, make it ready.

    Never raises: what went wrong is kept for the window to say.
    """
    root = root or install_root()
    state: dict[str, Any] = {"checked_at": time.time(), "error": ""}
    try:
        git = under_version_control(root)
        current = installed(root).get("version", "dev")
        release = latest_release(fetch)
        state["available"] = release.version
        bad = set(_read_json(updates_dir(root) / "bad_versions.json", []) or [])
        ready = _read_json(updates_dir(root) / "ready.json", {})
        already = isinstance(ready, dict) and ready.get("version") == release.version
        if git:
            # A checkout knows its version by its commit, not by a stamp.
            head = _git(root, "rev-parse", "HEAD").stdout.strip()
            behind = bool(release.commit) and head != release.commit
            if behind and release.version not in bad and not already:
                stage_git(release, root=root, check=check)
        elif is_newer(release.version, current) and release.version not in bad and not already:
            stage(release, fetch=fetch, root=root, check=check)
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


def mark_started(root: Path | None = None) -> None:
    """The window opened: this version works (the launcher's proof)."""
    root = root or install_root()
    try:
        folder = updates_dir(root)
        if folder.is_dir():
            _write_json(folder / "started.json",
                        {"version": installed(root).get("version", ""), "at": time.time()})
    except Exception as exc:  # noqa: BLE001 - a missing proof only means a cautious launcher
        _log.warning("Could not mark the start: %s", exc)


def launched_by_launcher() -> bool:
    return os.environ.get("PROFILEOS_LAUNCHER") == "1"


# --------------------------------------------------------------------------- #
# What is new
# --------------------------------------------------------------------------- #

def whats_new(since: str = "", *, root: Path | None = None) -> list[dict[str, Any]]:
    """Entries of ``profileos/data/whats_new.json`` newer than ``since`` (a date)."""
    path = (root or install_root()) / "profileos" / "data" / "whats_new.json"
    entries = _read_json(path, []) or []
    return [entry for entry in entries if str(entry.get("date", "")) > since]


def unannounced(root: Path | None = None) -> dict[str, Any] | None:
    """The launcher's result, once, for the window to tell: installed or rolled back."""
    root = root or install_root()
    path = updates_dir(root) / "last_result.json"
    result = _read_json(path, None)
    if not isinstance(result, dict) or result.get("announced"):
        return None
    if result.get("outcome") not in ("installed", "rolled_back"):
        return None
    result["announced"] = True
    try:
        _write_json(path, result)
    except Exception:  # noqa: BLE001 - told twice is better than not told
        pass
    result["announced"] = False
    return result


__all__ = [
    "CHECK_EVERY_S",
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
