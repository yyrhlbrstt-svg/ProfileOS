"""Start ProfileOS, putting a downloaded update in place first.

This file is the one thing ``ProfileOS.bat`` runs. It uses the standard
library only and never imports the program it is about to replace, so it can
swap the program's files while nothing holds them open.

What happens on each start:

1. If an update was downloaded and checked while the program ran
   (``.updates/ready.json``), the shop's data is backed up with the version
   still installed, then each of the program's folders and files is moved
   aside to ``.updates/previous/`` and the new one moved into its place.
   The data folder lives elsewhere (``%LOCALAPPDATA%\\ProfileOS``) and is
   never touched; nothing that is not in the update is moved.
2. If the new version needs a component the old one did not, it is
   installed; if that cannot be done (no internet), the old version is put
   back and the update waits for the next start.
3. The program is started. If a version that was just put in place fails to
   open, the previous one is put back automatically, started instead, and
   the version is not tried again. The program says so when it opens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UPDATES = ROOT / ".updates"
READY = UPDATES / "ready.json"
PREVIOUS = UPDATES / "previous"
RESULT = UPDATES / "last_result.json"
#: Versions that failed to open once and are not put in place again.
BAD = UPDATES / "bad_versions.json"
#: Written by the program when its window has opened: proof the version works.
STARTED = UPDATES / "started.json"
#: The commit a checkout was on before its last update, to go back to.
PREVIOUS_COMMIT = UPDATES / "previous_commit.json"

#: Never moved by an update: the launcher scripts that are running right now
#: (Windows reads a .bat as it goes), our own folder, version control.
KEEP = {"ProfileOS.bat", ".updates", ".git", ".github"}

def _say(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:  # a console without Hebrew still runs the program
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def _read(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _python() -> str:
    """The console-less Python on Windows when there is one, else this one."""
    return sys.executable


def _backup() -> bool:
    """Today's backup of the shop's data, taken by the version still installed."""
    code = (
        "from profileos.core.backup import backup_if_due, backup_status;"
        "backup_if_due();"
        "import sys; sys.exit(1 if backup_status().get('error') else 0)"
    )
    try:
        done = subprocess.run([_python(), "-c", code], cwd=ROOT, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _install_requirements() -> bool:
    try:
        done = subprocess.run(
            [_python(), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "--quiet"],
            cwd=ROOT, timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _holds(item: Path, protected: list[str]) -> bool:
    """Whether moving ``item`` would move one of the shop's folders with it."""
    for path in protected:
        try:
            Path(path).resolve().relative_to(item.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def swap_in(staged: Path, root: Path | None = None, previous: Path | None = None,
            protected: list[str] | None = None) -> list[str]:
    """Move each item of ``staged`` into ``root``, the old one aside.

    Returns the names moved, in order, so a failure half-way can be undone
    by :func:`swap_back` with exactly those. A folder under version control
    is never swapped file by file — version control moves it (:func:`apply_git`).
    """
    root = ROOT if root is None else root
    previous = PREVIOUS if previous is None else previous
    if (root / ".git").exists():
        raise OSError("תיקייה תחת git לא מתעדכנת בהחלפת קבצים")
    for item in staged.iterdir():
        if _holds(root / item.name, protected or []):
            raise OSError(f"{item.name} מכיל את תיקיית הנתונים — לא יוזז")
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)
    moved: list[str] = []
    try:
        for item in sorted(staged.iterdir(), key=lambda p: p.name):
            name = item.name
            if name in KEEP or name.startswith("."):
                continue
            current = root / name
            if current.exists():
                os.replace(current, previous / name)
            os.replace(item, current)
            moved.append(name)
    except OSError:
        swap_back(moved, root, previous)
        raise
    _write(previous / ".moved.json", moved)
    return moved


def swap_back(moved: list[str] | None = None, root: Path | None = None,
              previous: Path | None = None, *, keep_new_in: Path | None = None) -> None:
    """Put back what :func:`swap_in` moved aside.

    The version being taken out is deleted — or, with ``keep_new_in``, moved
    back there, to be put in place again on a later start.
    """
    root = ROOT if root is None else root
    previous = PREVIOUS if previous is None else previous
    if moved is None:
        moved = list(_read(previous / ".moved.json", []))  # type: ignore[arg-type]
    if keep_new_in is not None:
        keep_new_in.mkdir(parents=True, exist_ok=True)
    for name in reversed(moved):
        current, old = root / name, previous / name
        if current.exists():
            if keep_new_in is not None:
                os.replace(current, keep_new_in / name)
            elif current.is_dir():
                shutil.rmtree(current, ignore_errors=True)
            else:
                current.unlink(missing_ok=True)
        if old.exists():
            os.replace(old, current)


def _mark_bad(version: str) -> None:
    bad = set(_read(BAD, []))  # type: ignore[arg-type]
    bad.add(version)
    _write(BAD, sorted(bad))


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=600)


def apply_git(ready: dict) -> str:
    """A checkout under version control: fast-forward to the tested commit."""
    version, commit = str(ready.get("version", "")), str(ready.get("commit", ""))
    if not commit or version in set(_read(BAD, [])):  # type: ignore[arg-type]
        READY.unlink(missing_ok=True)
        return ""
    _say(f"  מתקין עדכון — גרסה {version}…")
    if not _backup():
        _say("  הגיבוי לפני העדכון לא הצליח — העדכון יחכה להפעלה הבאה.")
        _write(RESULT, {"version": version, "outcome": "waiting", "reason": "backup"})
        return ""
    try:
        merged = _git("merge", "--ff-only", "--quiet", commit)
    except (OSError, subprocess.TimeoutExpired) as exc:
        merged = None
        _say(f"  העדכון לא הותקן ({exc}).")
    if merged is None or merged.returncode != 0:
        _write(RESULT, {"version": version, "outcome": "failed", "reason": "merge"})
        READY.unlink(missing_ok=True)
        return ""
    if ready.get("needs_components") and not _install_requirements():
        _git("reset", "--keep", str(ready.get("previous_commit", "")))
        _say("  לא ניתן היה להתקין רכיבים (אין אינטרנט?) — העדכון יחכה להפעלה הבאה.")
        _write(RESULT, {"version": version, "outcome": "waiting", "reason": "components"})
        return ""
    READY.unlink(missing_ok=True)
    _write(PREVIOUS_COMMIT, {"commit": ready.get("previous_commit", "")})
    _write(RESULT, {"version": version, "outcome": "installed", "at": time.time(),
                    "from": ready.get("from_version", "")})
    return version


def apply_pending() -> str:
    """Put a downloaded, checked update in place. Returns its version, or ''."""
    ready = _read(READY, None)
    if not isinstance(ready, dict):
        return ""
    if ready.get("mode") == "git":
        return apply_git(ready)
    version = str(ready.get("version", ""))
    staged = Path(str(ready.get("path", "")))
    if not version or not staged.is_dir() or version in set(_read(BAD, [])):  # type: ignore[arg-type]
        READY.unlink(missing_ok=True)
        return ""
    _say(f"  מתקין עדכון — גרסה {version}…")
    if not _backup():
        # No backup, no swap: the update waits for a start that has one.
        _say("  הגיבוי לפני העדכון לא הצליח — העדכון יחכה להפעלה הבאה.")
        _write(RESULT, {"version": version, "outcome": "waiting", "reason": "backup"})
        return ""
    try:
        moved = swap_in(staged, protected=list(ready.get("protect", []) or []))
    except OSError as exc:
        _say(f"  העדכון לא הותקן ({exc}) — ממשיכים בגרסה הקיימת.")
        _write(RESULT, {"version": version, "outcome": "failed", "reason": str(exc)})
        return ""
    if ready.get("needs_components"):
        _say("  מתקין רכיבים חדשים שהגרסה צריכה…")
        if not _install_requirements():
            # The old version goes back; the new one returns to where it waits.
            swap_back(moved, keep_new_in=staged)
            _say("  לא ניתן היה להתקין רכיבים (אין אינטרנט?) — העדכון יחכה להפעלה הבאה.")
            _write(RESULT, {"version": version, "outcome": "waiting", "reason": "components"})
            return ""
    READY.unlink(missing_ok=True)
    shutil.rmtree(staged, ignore_errors=True)
    _write(RESULT, {"version": version, "outcome": "installed", "at": time.time(),
                    "from": ready.get("from_version", "")})
    return version


def run_program() -> int:
    return subprocess.call([_python(), "-c",
                            "from profileos.ui.app import run; raise SystemExit(run())"],
                           cwd=ROOT, env={**os.environ, "PROFILEOS_LAUNCHER": "1"})


#: The program asks to be started again (``profileos.core.selfupdate.RESTART_CODE``):
#: "an update is ready, put it in place now".
RESTART_CODE = 75


def start_once() -> int:
    """Put a ready update in place (if any), start the program, and roll the
    update back if the new version could not open its window."""
    installed = apply_pending()
    if not installed:
        return run_program()
    STARTED.unlink(missing_ok=True)
    code = run_program()
    opened = isinstance(_read(STARTED, None), dict)
    if code not in (0, RESTART_CODE) and not opened:
        # The new version did not open its window: the old one goes back,
        # this version is not tried again, and the program says so.
        _say("  הגרסה החדשה לא נפתחה — חוזרים לגרסה הקודמת.")
        previous = _read(PREVIOUS_COMMIT, {})
        if (ROOT / ".git").exists() and isinstance(previous, dict) and previous.get("commit"):
            _git("reset", "--keep", str(previous["commit"]))
        else:
            swap_back()
        _mark_bad(installed)
        _write(RESULT, {"version": installed, "outcome": "rolled_back", "at": time.time()})
        return run_program()
    return code


def main() -> int:
    code = start_once()
    while code == RESTART_CODE:
        code = start_once()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
