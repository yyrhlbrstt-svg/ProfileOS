"""Start ProfileOS, putting a downloaded update in place first.

This file is the one thing ``ProfileOS.bat`` runs. It uses the standard
library only and never imports the program it is about to replace, so it can
swap the program's files while nothing holds them open.

What happens on each start:

0. If a swap was cut short last time (power cut, a locked file), the old
   version is put back before anything else.
1. If an update was downloaded and checked while the program ran
   (``.updates/ready.json``), the shop's data is backed up with the version
   still installed, then each of the program's folders and files is moved
   aside to ``.updates/previous/`` and the new one moved into its place —
   every step written down as it happens, so it can always be undone. The
   data folder lives elsewhere and is never touched; nothing that is not in
   the update is moved, and nothing that holds the data is moved at all.
2. If the new version needs a component the old one did not, it is
   installed; if that cannot be done (no internet), the old version is put
   back and the update waits — and is not retried for a few hours.
3. The program is started. If a version that was just put in place fails to
   open, the previous one is put back automatically, started instead, and
   the version is not tried again. The program says so when it opens.
"""

from __future__ import annotations

import hashlib
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

#: Never moved by an update: the launcher script Windows is running right now
#: (it reads a .bat as it goes), our own folder, version control.
KEEP = {"ProfileOS.bat", ".updates", ".git", ".github"}

#: The program asks to be started again (``profileos.core.selfupdate.RESTART_CODE``):
#: "an update is ready, put it in place now".
RESTART_CODE = 75
#: The person cancelled the sign-in: not a version that failed to open.
CANCELLED_CODE = 76
#: After a failed component install, wait this long before trying again,
#: so a computer without internet does not spend every start on it.
RETRY_AFTER_S = 6 * 3600


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
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _python() -> str:
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


# --------------------------------------------------------------------------- #
# Moving files, always undoable
# --------------------------------------------------------------------------- #

def _holds(item: Path, protected: list[str]) -> bool:
    """Whether moving ``item`` would move one of the shop's folders with it."""
    for path in protected:
        try:
            Path(path).resolve().relative_to(item.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def _journal(previous: Path) -> Path:
    return previous / ".moved.json"


def _in_progress(previous: Path) -> Path:
    return previous / ".swapping"


def swap_in(staged: Path, root: Path | None = None, previous: Path | None = None,
            protected: list[str] | None = None, *, finish: bool = True,
            version: str = "") -> list[str]:
    """Move each item of ``staged`` into ``root``, the old one aside.

    Every move is written to a journal before the next, so whatever stops
    it — an error, a power cut — :func:`swap_back` can undo exactly what was
    done. A folder under version control is never swapped file by file.
    """
    root = ROOT if root is None else root
    previous = PREVIOUS if previous is None else previous
    if (root / ".git").exists():
        raise OSError("תיקייה תחת git לא מתעדכנת בהחלפת קבצים")
    if _in_progress(previous).exists():
        # The last swap never finished: its copy of the old version is the
        # only one there is. Undo it first (recover()), never delete it.
        raise OSError("החלפה קודמת לא הסתיימה — מחזירים אותה קודם")
    if not (staged / "profileos" / "__init__.py").is_file():
        raise OSError("בתיקיית העדכון אין את התוכנה — לא יוזז דבר")
    for item in staged.iterdir():
        if _holds(root / item.name, protected or []):
            raise OSError(f"{item.name} מכיל את תיקיית הנתונים — לא יוזז")
    if previous.exists():
        shutil.rmtree(previous)
    previous.mkdir(parents=True)
    _write(_in_progress(previous), {"at": time.time(), "version": version})
    moved: list[str] = []
    journal: list[dict] = []
    _write(_journal(previous), journal)
    try:
        for item in sorted(staged.iterdir(), key=lambda p: p.name):
            name = item.name
            if name in KEEP or name.startswith("."):
                continue
            current = root / name
            # Written down before anything moves: a failure — or a power
            # cut — at any point still knows what is where.
            journal.append({"name": name, "had_old": current.exists()})
            _write(_journal(previous), journal)
            moved.append(name)
            if current.exists():
                os.replace(current, previous / name)
            os.replace(item, current)
    except OSError:
        swap_back(journal, root, previous, keep_new_in=staged)
        raise
    if finish:
        _in_progress(previous).unlink(missing_ok=True)
    return moved


def swap_back(moved: list | None = None, root: Path | None = None,
              previous: Path | None = None, *, keep_new_in: Path | None = None) -> None:
    """Put back what :func:`swap_in` moved aside.

    The version being taken out is deleted — or, with ``keep_new_in``, moved
    back there, to be put in place again on a later start. An item whose old
    copy never reached the side folder is the old one itself, and stays.
    """
    root = ROOT if root is None else root
    previous = PREVIOUS if previous is None else previous
    journal = _read(_journal(previous), [])
    had_old = {entry.get("name"): bool(entry.get("had_old")) for entry in journal
               if isinstance(entry, dict)} if isinstance(journal, list) else {}
    if moved is None:
        moved = list(had_old)
    names = [entry.get("name") if isinstance(entry, dict) else entry for entry in moved]
    if keep_new_in is not None:
        keep_new_in.mkdir(parents=True, exist_ok=True)
    for name in reversed(names):
        current, old = root / name, previous / name
        existed = had_old.get(name, old.exists())
        if existed and not old.exists():
            continue  # never moved aside: what is in place is the old one
        if current.exists():
            if keep_new_in is not None:
                if not (keep_new_in / name).exists():
                    os.replace(current, keep_new_in / name)
            else:
                # Aside first, deleted after: a folder that cannot be fully
                # deleted never blocks the old one from coming back.
                discard = previous / f".discard-{name}-{time.time_ns()}"
                os.replace(current, discard)
                if discard.is_dir():
                    shutil.rmtree(discard, ignore_errors=True)
                else:
                    discard.unlink(missing_ok=True)
        if existed:
            os.replace(old, current)
    _in_progress(previous).unlink(missing_ok=True)


def recover() -> None:
    """An install or a rollback that a power cut, a closed window or a locked
    file stopped half-way: put the old version back, whole.

    The half-moved update is not trusted again: it is dropped and the program
    downloads and checks it afresh. Components installed for it go back too.
    """
    marker = _read(_in_progress(PREVIOUS), None)
    if marker is None and not _in_progress(PREVIOUS).exists():
        return
    _say("  עדכון קודם לא הסתיים — מחזירים את הגרסה הקודמת…")
    swap_back()
    ready = _read(READY, {})
    ready = ready if isinstance(ready, dict) else {}
    version = str((marker or {}).get("version", "") if isinstance(marker, dict) else "") \
        or str(ready.get("version", ""))
    staged = _staged_path(ready)
    if staged is not None:
        shutil.rmtree(staged, ignore_errors=True)
    READY.unlink(missing_ok=True)
    if version:
        _restore_components(version)
    _result(version, "failed", "interrupted")


def _mark_bad(version: str) -> None:
    bad = set(_read(BAD, []))  # type: ignore[arg-type]
    bad.add(version)
    _write(BAD, sorted(bad))


def _unmark_bad(version: str) -> None:
    bad = set(_read(BAD, []))  # type: ignore[arg-type]
    bad.discard(version)
    _write(BAD, sorted(bad))


def _waiting_recently(version: str) -> bool:
    last = _read(RESULT, {})
    return (isinstance(last, dict) and last.get("version") == version
            and last.get("outcome") == "waiting" and last.get("reason") == "components"
            and time.time() - float(last.get("at", 0) or 0) < RETRY_AFTER_S)


def _result(version: str, outcome: str, reason: str = "", **more: object) -> None:
    try:
        _write(RESULT, {"version": version, "outcome": outcome, "reason": reason,
                        "at": time.time(), **more})
    except OSError:
        pass  # the console line already said it


# --------------------------------------------------------------------------- #
# Who else is running
# --------------------------------------------------------------------------- #

#: Held for as long as one launcher is putting an update in place.
LOCK = UPDATES / "launcher.lock"
#: Every running copy of the program (window or phone terminal) holds one
#: ``instance-<pid>.lock`` open (``profileos.core.selfupdate.hold_instance``).
INSTANCE_GLOB = "instance-*.lock"


def _holder_gone(path: Path) -> bool:
    """True — and the file removed — when no running process holds ``path``.

    On Windows a file another process has open cannot be deleted, which is
    exactly the question. Elsewhere the process number inside is asked.
    """
    if os.name == "nt":
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except PermissionError:
            return False
        return True
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        pid = int(text.split()[0])
    except (ValueError, IndexError):
        pid = 0
    if pid <= 0:
        try:  # just created, its number not written yet
            if time.time() - path.stat().st_mtime < 10:
                return False
        except OSError:
            return True
    else:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
    path.unlink(missing_ok=True)
    return True


def running_copies(updates: Path | None = None) -> int:
    """How many copies of the program are open right now."""
    updates = UPDATES if updates is None else updates
    if not updates.is_dir():
        return 0
    return sum(1 for path in updates.glob(INSTANCE_GLOB) if not _holder_gone(path))


#: How long a second launcher waits for the first to finish installing.
LOCK_WAIT_S = 600


def take_lock(wait_s: float | None = None, lock: Path | None = None) -> int | None:
    """The right to put an update in place; None when another launcher keeps it."""
    lock = LOCK if lock is None else lock
    wait_s = LOCK_WAIT_S if wait_s is None else wait_s
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline, told = time.time() + wait_s, False
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if _holder_gone(lock):
                continue
            if not told:
                _say("  עדכון מותקן כרגע מחלון אחר — ממתינים שיסתיים…")
                told = True
            if time.time() >= deadline:
                return None
            time.sleep(1)
            continue
        os.write(handle, str(os.getpid()).encode("ascii"))
        return handle


def release_lock(handle: int | None, lock: Path | None = None) -> None:
    if handle is None:
        return
    lock = LOCK if lock is None else lock
    try:
        os.close(handle)
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Checking what is about to go in
# --------------------------------------------------------------------------- #

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_staged(staged: Path, files: dict) -> str:
    """Empty when every file of the published version is in ``staged``,
    byte for byte; else the first one that is not."""
    if not (staged / "profileos" / "__init__.py").is_file():
        return "profileos"
    if not files:
        return "רשימת הקבצים"
    for name, digest in files.items():
        path = staged / name
        try:
            if _sha256(path) != digest:
                return name
        except OSError:
            return name
    return ""


def _staged_path(ready: dict) -> Path | None:
    """The folder the update waits in — only ever one of ours inside ``.updates``."""
    raw = str(ready.get("path", "") or "")
    if not raw:
        return None
    path = Path(raw)
    try:
        if (not path.is_absolute() or path.resolve().parent != UPDATES.resolve()
                or not path.name.startswith("staged-")):
            return None
    except OSError:
        return None
    return path


def _freeze() -> str:
    try:
        done = subprocess.run([_python(), "-m", "pip", "freeze"], cwd=ROOT,
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _restore_components(version: str) -> None:
    """Components installed for a version that is being taken out go back to
    what they were — best effort, it needs the internet."""
    saved = _read(COMPONENTS_BEFORE, {})
    if not isinstance(saved, dict) or saved.get("version") != version or not saved.get("freeze"):
        return
    listing = UPDATES / "components-before.txt"
    lines = [line for line in str(saved["freeze"]).splitlines()
             if line and not line.startswith(("-e ", "#")) and " @ " not in line]
    _say("  מחזירים את הרכיבים לגרסאות הקודמות…")
    try:
        listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run([_python(), "-m", "pip", "install", "-r", str(listing), "--quiet",
                        "--retries", "1", "--timeout", "15"],
                       cwd=ROOT, timeout=900)
        COMPONENTS_BEFORE.unlink(missing_ok=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


#: ``pip freeze`` from before a version installed components, to undo them.
COMPONENTS_BEFORE = UPDATES / "components_before.json"


def _remember_components(version: str) -> None:
    try:
        _write(COMPONENTS_BEFORE, {"version": version, "freeze": _freeze()})
    except OSError:
        pass  # without it a rollback keeps the new components: not fatal


# --------------------------------------------------------------------------- #
# Putting an update in place
# --------------------------------------------------------------------------- #

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=600)


def apply_git(ready: dict) -> str:
    """A checkout under version control: fast-forward to the tested commit,
    only on the branch that is published, never over local changes."""
    version, commit = str(ready.get("version", "")), str(ready.get("commit", ""))
    if not commit or version in set(_read(BAD, [])):  # type: ignore[arg-type]
        READY.unlink(missing_ok=True)
        return ""
    if _waiting_recently(version):
        return ""
    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if ready.get("branch") and branch != ready.get("branch"):
        READY.unlink(missing_ok=True)
        return ""
    _say(f"  מתקין עדכון — גרסה {version}…")
    if not _backup():
        _say("  הגיבוי לפני העדכון לא הצליח — העדכון יחכה להפעלה הבאה.")
        _result(version, "waiting", "backup")
        return ""
    try:
        merged = _git("merge", "--ff-only", "--quiet", commit)
    except (OSError, subprocess.TimeoutExpired) as exc:
        merged = None
        _say(f"  העדכון לא הותקן ({exc}).")
    if merged is None or merged.returncode != 0:
        _result(version, "failed", "merge")
        READY.unlink(missing_ok=True)
        return ""
    if ready.get("needs_components"):
        _remember_components(version)
        if not _install_requirements():
            _git("reset", "--keep", str(ready.get("previous_commit", "")))
            _restore_components(version)
            _say("  לא ניתן היה להתקין רכיבים (אין אינטרנט?) — העדכון יחכה.")
            _result(version, "waiting", "components")
            return ""
    READY.unlink(missing_ok=True)
    _write(PREVIOUS_COMMIT, {"commit": ready.get("previous_commit", "")})
    _result(version, "installed", **{"from": ready.get("from_version", "")})
    return version


def _apply_pending() -> str:
    recover()
    ready = _read(READY, None)
    if not isinstance(ready, dict):
        return ""
    if ready.get("mode") == "git":
        if running_copies():
            _result(str(ready.get("version", "")), "waiting", "running")
            return ""
        return apply_git(ready)
    version = str(ready.get("version", ""))
    staged = _staged_path(ready)
    if (not version or staged is None or not staged.is_dir()
            or version in set(_read(BAD, []))):  # type: ignore[arg-type]
        READY.unlink(missing_ok=True)
        return ""
    if _waiting_recently(version):
        return ""
    if running_copies():
        # Files moved under a running copy would mix two versions in it.
        _say("  ProfileOS פתוחה כבר במחשב הזה (או טרמינל הטלפון) — "
             "העדכון יותקן כשכל החלונות ייסגרו.")
        _result(version, "waiting", "running")
        return ""
    damaged = verify_staged(staged, dict(ready.get("files", {}) or {}))
    if damaged:
        # Changed since it was checked (a deleted file, an antivirus): not
        # this version any more. It is dropped and downloaded afresh.
        _say(f"  קובץ העדכון השתנה מאז שנבדק ({damaged}) — יורד מחדש בהמשך.")
        READY.unlink(missing_ok=True)
        shutil.rmtree(staged, ignore_errors=True)
        _result(version, "failed", "damaged")
        return ""
    _say(f"  מתקין עדכון — גרסה {version}…")
    if not _backup():
        # No backup, no swap: the update waits for a start that has one.
        _say("  הגיבוי לפני העדכון לא הצליח — העדכון יחכה להפעלה הבאה.")
        _result(version, "waiting", "backup")
        return ""
    try:
        moved = swap_in(staged, protected=list(ready.get("protect", []) or []),
                        finish=False, version=version)
    except OSError as exc:
        _say(f"  העדכון לא הותקן ({exc}) — ממשיכים בגרסה הקיימת.")
        _result(version, "failed", str(exc))
        return ""
    if ready.get("needs_components"):
        _say("  מתקין רכיבים חדשים שהגרסה צריכה…")
        _remember_components(version)
        if not _install_requirements():
            # The old version goes back; the new one returns to where it waits.
            swap_back(moved, keep_new_in=staged)
            _restore_components(version)
            _say("  לא ניתן היה להתקין רכיבים (אין אינטרנט?) — העדכון יחכה.")
            _result(version, "waiting", "components")
            return ""
    READY.unlink(missing_ok=True)
    # Only now is the swap finished: until here a closed window or a power
    # cut is undone at the next start.
    _in_progress(PREVIOUS).unlink(missing_ok=True)
    shutil.rmtree(staged, ignore_errors=True)
    _result(version, "installed", **{"from": ready.get("from_version", "")})
    return version


def apply_pending() -> str:
    """Put a downloaded, checked update in place. Returns its version, or ''.

    Never stops the program from starting: whatever goes wrong here, the
    version that is in place is started.
    """
    try:
        handle = take_lock()
    except OSError:
        handle = None
    if handle is None:
        return ""
    try:
        return _apply_pending()
    except Exception as exc:  # noqa: BLE001 - the program must still start
        _say(f"  העדכון לא הותקן ({exc}) — ממשיכים בגרסה הקיימת.")
        return ""
    finally:
        release_lock(handle)


def run_program() -> int:
    return subprocess.call([_python(), "-c",
                            "from profileos.ui.app import run; raise SystemExit(run())"],
                           cwd=ROOT, env={**os.environ, "PROFILEOS_LAUNCHER": "1"})


def _roll_back(installed: str) -> bool:
    """Put the previous version back. False when it could not be done now.

    In a folder of files the marker goes down first: a rollback that stops
    half-way (a locked file, another window) is finished by :func:`recover`
    at the next start, and nothing deletes the old copy before that.
    """
    handle = None
    try:
        _mark_bad(installed)
    except OSError:
        pass
    try:
        previous = _read(PREVIOUS_COMMIT, {})
        git = (ROOT / ".git").exists() and isinstance(previous, dict) and previous.get("commit")
        if not git:
            if not _journal(PREVIOUS).exists():
                return False
            _write(_in_progress(PREVIOUS), {"at": time.time(), "version": installed})
        handle = take_lock()
        if handle is None:
            return False
        if git:
            done = _git("reset", "--keep", str(previous["commit"]))
            if done.returncode != 0:
                return False
        else:
            swap_back()
        _restore_components(installed)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        release_lock(handle)
    return True


def _cannot_start() -> str:
    """Why the program must not be started now, in words; empty when it may."""
    if _in_progress(PREVIOUS).exists():
        return ("  ההחלפה לגרסה הקודמת לא הסתיימה (קובץ נעול?). סגרו את כל חלונות ProfileOS "
                "ופתחו שוב — היא תושלם אוטומטית. אם זה חוזר, הפעילו מחדש את המחשב.")
    try:
        busy = LOCK.exists() and not _holder_gone(LOCK)
    except OSError:
        busy = False
    if busy:
        return "  עדכון מותקן כרגע מחלון אחר. המתינו שיסתיים ופתחו שוב."
    return ""


def _opened() -> bool:
    return isinstance(_read(STARTED, None), dict)


def start_once() -> int:
    """Put a ready update in place (if any), start the program, and roll the
    update back if the new version could not open its window."""
    installed = apply_pending()
    # Never on a half-swapped folder, nor while another window installs.
    problem = _cannot_start()
    if problem:
        _say(problem)
        return 1
    if not installed:
        return run_program()
    try:
        STARTED.unlink(missing_ok=True)
    except OSError:
        pass
    code = run_program()
    if code in (0, RESTART_CODE, CANCELLED_CODE) or _opened():
        return code
    # The new version did not open its window: the old one goes back,
    # this version is not tried again, and the program says so.
    _say("  הגרסה החדשה לא נפתחה — חוזרים לגרסה הקודמת.")
    if not _roll_back(installed):
        _say("  לא ניתן היה להחזיר את הגרסה הקודמת כרגע — היא תוחזר בפתיחה הבאה.")
        _say("  הנתונים לא נפגעו. סגרו את כל חלונות ProfileOS ופתחו שוב.")
        _result(installed, "failed", "rollback")
        return 1
    _result(installed, "rolled_back")
    code = run_program()
    if code not in (0, RESTART_CODE, CANCELLED_CODE) and not _opened():
        # The previous version does not open either: the update was not the
        # cause (the data, the computer). It is not held against it.
        try:
            _unmark_bad(installed)
        except OSError:
            pass
        _result(installed, "failed", "not_the_update")
    return code


def main() -> int:
    code = start_once()
    while code == RESTART_CODE:
        code = start_once()
    return 0 if code == CANCELLED_CODE else code


if __name__ == "__main__":
    raise SystemExit(main())
