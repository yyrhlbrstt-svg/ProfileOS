"""The program keeps itself up to date — and never breaks itself doing it."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from profileos.core import selfupdate
from profileos.core.errors import ProfileOSError

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _the_real_program_is_never_touched():
    """Every test here moves program files around: none may be this tree's."""
    before = {name: (REPO / name).stat().st_mtime_ns
              for name in ("launcher.py", "requirements.txt", "profileos/__init__.py")}
    yield
    after = {name: (REPO / name).stat().st_mtime_ns for name in before}
    assert after == before, "a test changed the real program's files"
    assert not (REPO / ".updates").exists(), "a test wrote into the real program folder"


def load_launcher():
    spec = importlib.util.spec_from_file_location("profileos_launcher", REPO / "launcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def a_build(version: str, *, extra: dict[str, str] | None = None) -> bytes:
    """A published zip as the release workflow makes one."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as pack:
        files = {
            "profileos/__init__.py": "__version__ = '0.1.0'\n",
            "profileos/_release.json": json.dumps({"version": version}),
            "launcher.py": "# new launcher\n",
            "requirements.txt": "pydantic\n",
            **(extra or {}),
        }
        for name, text in files.items():
            pack.writestr(name, text)
    return buffer.getvalue()


class FakeGitHub:
    """GitHub's latest-release answer and its two assets."""

    def __init__(self, version: str, body: bytes, *, sha: str | None = None, commit: str = "c0ffee"):
        self.zip_name = f"profileos-{version}.zip"
        self.files = {
            selfupdate.LATEST_URL: json.dumps({
                "published_at": "2026-10-22T10:00:00Z",
                "assets": [
                    {"name": self.zip_name, "browser_download_url": "https://x/zip"},
                    {"name": "release.json", "browser_download_url": "https://x/manifest"},
                ],
            }).encode(),
            "https://x/manifest": json.dumps({
                "version": version, "zip": self.zip_name, "size": len(body),
                "sha256": sha or hashlib.sha256(body).hexdigest(), "commit": commit,
                "notes": [{"date": "2026-10-22", "title": "חדש", "items": ["א"]}],
            }).encode(),
            "https://x/zip": body,
        }
        self.asked: list[str] = []

    def __call__(self, url: str, limit: int = 0) -> bytes:
        self.asked.append(url)
        return self.files[url]


def an_install(tmp_path: Path, version: str = "") -> Path:
    root = tmp_path / "ProfileOS"
    (root / "profileos").mkdir(parents=True)
    (root / "profileos" / "__init__.py").write_text("__version__ = '0.1.0'\n")
    if version:
        (root / "profileos" / "_release.json").write_text(json.dumps({"version": version}))
    (root / "launcher.py").write_text("# old launcher\n")
    (root / "requirements.txt").write_text("pydantic\n")
    return root


class TestVersions:
    def test_newer_is_by_date_and_run(self):
        assert selfupdate.is_newer("2026.10.22.14", "2026.10.22.9")
        assert selfupdate.is_newer("2026.11.1.1", "2026.10.31.99")
        assert not selfupdate.is_newer("2026.10.22.9", "2026.10.22.9")
        assert selfupdate.is_newer("2026.10.22.1", "dev")
        assert not selfupdate.is_newer("garbage", "2026.10.22.1")

    def test_the_installed_version_comes_from_the_stamp(self, tmp_path):
        assert selfupdate.installed(an_install(tmp_path, "2026.10.22.3"))["version"] == "2026.10.22.3"
        assert selfupdate.installed(an_install(tmp_path / "b"))["version"] == "dev"


class TestFindingAndStaging:
    def test_a_newer_release_is_downloaded_checked_and_made_ready(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda staged: "")
        assert state.ready == "2026.10.22.5" and not state.error
        ready = json.loads((root / ".updates" / "ready.json").read_text())
        assert Path(ready["path"], "profileos", "__init__.py").is_file()
        assert ready["from_version"] == "2026.10.20.1" and not ready["needs_components"]
        assert "הורדה ונבדקה" in state.describe()
        # The running program was not touched.
        assert (root / "launcher.py").read_text() == "# old launcher\n"

    def test_nothing_newer_nothing_downloaded(self, tmp_path):
        root = an_install(tmp_path, "2026.10.22.5")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert not state.ready and "https://x/zip" not in github.asked
        assert state.describe() == "התוכנה מעודכנת"

    def test_a_fingerprint_that_does_not_match_is_refused(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"), sha="0" * 64)
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert not state.ready and "טביעת האצבע" in state.error
        assert not list((root / ".updates").glob("staged-*"))

    def test_a_zip_that_is_not_the_published_version_is_refused(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.4"))
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert not state.ready and "לא תואמת" in state.error

    def test_a_zip_that_climbs_out_is_refused(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        body = a_build("2026.10.22.5", extra={"../evil.txt": "x"})
        state = selfupdate.check_and_stage(fetch=FakeGitHub("2026.10.22.5", body), root=root,
                                           check=lambda s: "")
        assert not state.ready and not (tmp_path / "evil.txt").exists()

    def test_a_version_that_does_not_open_is_never_made_ready(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        state = selfupdate.check_and_stage(fetch=github, root=root,
                                           check=lambda staged: "ImportError: boom")
        assert not state.ready and "לא נפתחה בבדיקה" in state.error
        assert not list((root / ".updates").glob("staged-*"))

    def test_a_version_that_failed_once_is_not_tried_again(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        (root / ".updates").mkdir()
        (root / ".updates" / "bad_versions.json").write_text('["2026.10.22.5"]')
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        assert not selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "").ready

    def test_offline_is_said_in_words(self, tmp_path):
        import urllib.error

        root = an_install(tmp_path, "2026.10.20.1")

        def offline(url, limit=0):
            raise urllib.error.URLError("down")

        state = selfupdate.check_and_stage(fetch=offline, root=root)
        assert "אין חיבור" in state.error

    def test_new_components_are_noted(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        body = a_build("2026.10.22.5", extra={"requirements.txt": "pydantic\nnewthing\n"})
        selfupdate.check_and_stage(fetch=FakeGitHub("2026.10.22.5", body), root=root,
                                   check=lambda s: "")
        assert json.loads((root / ".updates" / "ready.json").read_text())["needs_components"]


class TestTheLauncher:
    def point(self, launcher, root: Path, monkeypatch):
        updates = root / ".updates"
        for name, value in {
            "ROOT": root, "UPDATES": updates, "READY": updates / "ready.json",
            "PREVIOUS": updates / "previous", "RESULT": updates / "last_result.json",
            "BAD": updates / "bad_versions.json", "STARTED": updates / "started.json",
            "PREVIOUS_COMMIT": updates / "previous_commit.json",
            "LOCK": updates / "launcher.lock",
            "COMPONENTS_BEFORE": updates / "components_before.json",
        }.items():
            monkeypatch.setattr(launcher, name, value)
        monkeypatch.setattr(launcher, "_backup", lambda: True)
        monkeypatch.setattr(launcher, "_install_requirements", lambda: True)

    def staged(self, tmp_path, monkeypatch) -> tuple[Path, object]:
        root = an_install(tmp_path, "2026.10.20.1")
        (root / "ProfileOS.bat").write_text("@echo old\n")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5", extra={"ProfileOS.bat": "new"}))
        selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        launcher = load_launcher()
        self.point(launcher, root, monkeypatch)
        # The staged path recorded is under this root.
        return root, launcher

    def test_the_update_is_put_in_place_at_the_next_start(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        assert launcher.apply_pending() == "2026.10.22.5"
        assert selfupdate.installed(root)["version"] == "2026.10.22.5"
        assert (root / "launcher.py").read_text() == "# new launcher\n"
        # The running .bat is never rewritten under Windows' feet.
        assert (root / "ProfileOS.bat").read_text() == "@echo old\n"
        assert not (root / ".updates" / "ready.json").exists()
        result = json.loads((root / ".updates" / "last_result.json").read_text())
        assert result["outcome"] == "installed" and result["from"] == "2026.10.20.1"

    def test_no_backup_no_update(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        monkeypatch.setattr(launcher, "_backup", lambda: False)
        assert launcher.apply_pending() == ""
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        assert (root / ".updates" / "ready.json").exists()     # waits for a start with one

    def test_a_version_that_does_not_open_is_rolled_back(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        runs: list[str] = []

        def run_program():
            runs.append(selfupdate.installed(root)["version"])
            return 1 if len(runs) == 1 else 0      # the new one fails, the old one opens

        monkeypatch.setattr(launcher, "run_program", run_program)
        assert launcher.main() == 0
        assert runs == ["2026.10.22.5", "2026.10.20.1"]
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        assert (root / "launcher.py").read_text() == "# old launcher\n"
        assert "2026.10.22.5" in json.loads((root / ".updates" / "bad_versions.json").read_text())
        announced = selfupdate.unannounced(root)
        assert announced["outcome"] == "rolled_back" and selfupdate.unannounced(root) is None

    def test_a_version_that_opened_is_kept_even_if_it_closes_with_an_error(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)

        def run_program():
            selfupdate.mark_started(root)
            return 1

        monkeypatch.setattr(launcher, "run_program", run_program)
        launcher.main()
        assert selfupdate.installed(root)["version"] == "2026.10.22.5"

    def test_restart_now_installs_and_starts_again(self, tmp_path, monkeypatch):
        root = an_install(tmp_path, "2026.10.20.1")
        launcher = load_launcher()
        self.point(launcher, root, monkeypatch)
        codes = iter([launcher.RESTART_CODE, 0])
        started: list[str] = []

        def run_program():
            started.append(selfupdate.installed(root)["version"])
            if len(started) == 1:
                # While running, a new version was staged; the person pressed restart.
                selfupdate.check_and_stage(fetch=FakeGitHub("2026.10.22.5", a_build("2026.10.22.5")),
                                           root=root, check=lambda s: "")
            selfupdate.mark_started(root)
            return next(codes)

        monkeypatch.setattr(launcher, "run_program", run_program)
        assert launcher.main() == 0
        assert started == ["2026.10.20.1", "2026.10.22.5"]

    def test_no_internet_for_new_components_keeps_the_old_version(self, tmp_path, monkeypatch):
        root = an_install(tmp_path, "2026.10.20.1")
        body = a_build("2026.10.22.5", extra={"requirements.txt": "pydantic\nnewthing\n"})
        selfupdate.check_and_stage(fetch=FakeGitHub("2026.10.22.5", body), root=root,
                                   check=lambda s: "")
        launcher = load_launcher()
        self.point(launcher, root, monkeypatch)
        monkeypatch.setattr(launcher, "_install_requirements", lambda: False)
        assert launcher.apply_pending() == ""
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        ready = json.loads((root / ".updates" / "ready.json").read_text())
        assert Path(ready["path"], "profileos", "_release.json").is_file()   # still waiting, whole
        monkeypatch.setattr(launcher, "_install_requirements", lambda: True)
        # Not retried at every start: a computer without internet would spend
        # each morning on it.
        assert launcher.apply_pending() == ""
        monkeypatch.setattr(launcher, "RETRY_AFTER_S", 0)
        assert launcher.apply_pending() == "2026.10.22.5"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestACheckoutUnderGit:
    def repo(self, tmp_path: Path) -> tuple[Path, Path, str, str]:
        origin = tmp_path / "origin"
        origin.mkdir()

        def git(cwd, *args):
            return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                                  text=True).stdout.strip()

        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "t@t")
        git(origin, "config", "user.name", "t")
        (origin / "profileos").mkdir()
        (origin / "profileos" / "__init__.py").write_text("v = 1\n")
        git(origin, "add", ".")
        git(origin, "commit", "-qm", "one")
        first = git(origin, "rev-parse", "HEAD")
        clone = tmp_path / "shop"
        git(tmp_path, "clone", "-q", str(origin), str(clone))
        (origin / "profileos" / "__init__.py").write_text("v = 2\n")
        git(origin, "commit", "-qam", "two")
        second = git(origin, "rev-parse", "HEAD")
        return origin, clone, first, second

    def test_moves_forward_to_the_tested_commit_only(self, tmp_path, monkeypatch):
        _origin, clone, first, second = self.repo(tmp_path)
        release = selfupdate.Release(version="2026.10.22.5", zip_url="", sha256="", size=0,
                                     commit=second)
        selfupdate.stage_git(release, root=clone, check=lambda checkout: "")
        ready = json.loads((clone / ".updates" / "ready.json").read_text())
        assert ready["mode"] == "git" and ready["previous_commit"] == first
        assert (clone / "profileos" / "__init__.py").read_text() == "v = 1\n"   # not yet

        launcher = load_launcher()
        TestTheLauncher().point(launcher, clone, monkeypatch)
        monkeypatch.setattr(launcher, "_git", lambda *a: subprocess.run(
            ["git", *a], cwd=clone, capture_output=True, text=True))
        assert launcher.apply_pending() == "2026.10.22.5"
        assert (clone / "profileos" / "__init__.py").read_text() == "v = 2\n"

    def test_local_changes_are_never_overwritten(self, tmp_path):
        _origin, clone, _first, second = self.repo(tmp_path)
        (clone / "profileos" / "__init__.py").write_text("v = 'mine'\n")
        release = selfupdate.Release(version="2026.10.22.5", zip_url="", sha256="", size=0,
                                     commit=second)
        with pytest.raises(ProfileOSError, match="שינויים מקומיים"):
            selfupdate.stage_git(release, root=clone, check=lambda c: "")


def test_the_real_program_passes_its_own_start_check():
    """The check a downloaded version must pass, run on this very tree."""
    assert selfupdate.self_check(REPO) == ""


def test_a_broken_program_fails_the_start_check(tmp_path):
    broken = tmp_path / "broken"
    shutil.copytree(REPO / "profileos", broken / "profileos",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "profileos" / "insights.py").write_text("raise RuntimeError('broken build')\n")
    assert "broken build" in selfupdate.self_check(broken)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_the_release_build(tmp_path):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import build_release
    finally:
        sys.path.remove(str(REPO / "tools"))
    from datetime import datetime, timezone

    manifest = build_release.build(tmp_path, run=7, commit="abc",
                                   now=datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert manifest["version"] == "2026.10.22.7"
    archive = tmp_path / manifest["zip"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["sha256"]
    with zipfile.ZipFile(archive) as pack:
        names = pack.namelist()
        assert json.loads(pack.read("profileos/_release.json"))["version"] == "2026.10.22.7"
    assert set(names) == set(build_release.tracked_files()) | {"profileos/_release.json"}
    assert "profileos/__init__.py" in names
    assert not [n for n in names if n.startswith(("tests/", ".github/", ".claude/"))]
    assert manifest["notes"] and (tmp_path / "notes.md").is_file()
    # Never a number that sorts before what is already out there.
    with pytest.raises(SystemExit):
        build_release.build(tmp_path / "again", run=3, commit="old", after="2026.10.22.7",
                            now=datetime(2026, 10, 22, tzinfo=timezone.utc))


def test_the_system_page_shows_the_version(tmp_path):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from profileos.ui.main_window import MainWindow

    QApplication.instance() or QApplication([])
    window = MainWindow()
    page = window.go_to_page("System")
    assert "גרסה" in page.program_version.text()
    assert not page.program_restart.isEnabled()
    window.close()


def test_a_checkout_is_never_swapped_file_by_file(tmp_path):
    launcher = load_launcher()
    root = an_install(tmp_path)
    (root / ".git").mkdir()
    staged = tmp_path / "staged"
    (staged / "profileos").mkdir(parents=True)
    with pytest.raises(OSError):
        launcher.swap_in(staged, root, tmp_path / "previous")
    assert (root / "profileos" / "__init__.py").read_text() == "__version__ = '0.1.0'\n"


def test_an_old_shortcut_starts_through_the_launcher(monkeypatch):
    from profileos.ui import app

    calls: list[list[str]] = []
    monkeypatch.delenv("PROFILEOS_LAUNCHER", raising=False)
    monkeypatch.delenv("PROFILEOS_NO_LAUNCHER", raising=False)
    monkeypatch.setattr("subprocess.call", lambda args, cwd=None: calls.append(args) or 0)
    assert app._through_launcher() == 0
    assert calls and calls[0][-1].endswith("launcher.py")
    monkeypatch.setenv("PROFILEOS_LAUNCHER", "1")
    assert app._through_launcher() is None


def test_data_inside_the_program_folder_is_never_moved(tmp_path, monkeypatch):
    root = an_install(tmp_path, "2026.10.20.1")
    data = root / "profileos" / "data" / "shop"
    data.mkdir(parents=True)
    monkeypatch.setattr(selfupdate, "protected_paths", lambda: [str(data)])
    github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
    state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
    assert not state.ready and "תיקיית הנתונים" in state.error
    # And the launcher checks again, whatever ready.json says.
    launcher = load_launcher()
    staged = tmp_path / "staged"
    (staged / "profileos").mkdir(parents=True)
    with pytest.raises(OSError):
        launcher.swap_in(staged, root, tmp_path / "previous", protected=[str(data)])
    assert data.is_dir() and (root / "profileos" / "__init__.py").is_file()


# --------------------------------------------------------------------------- #
# What the audit found: every way an update could break the program
# --------------------------------------------------------------------------- #

class TestNothingIsEverHalfInstalled:
    staged = TestTheLauncher.staged
    point = TestTheLauncher.point

    def test_a_swap_that_fails_half_way_puts_everything_back(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        real = os.replace
        calls = {"n": 0}

        def flaky(source, target):
            calls["n"] += not str(source).endswith(".tmp")     # file moves only
            if calls["n"] == 4:   # the second item's new copy cannot go in
                raise PermissionError("locked by antivirus")
            return real(source, target)

        monkeypatch.setattr(launcher.os, "replace", flaky)
        assert launcher.apply_pending() == ""
        assert calls["n"] >= 4
        monkeypatch.setattr(launcher.os, "replace", real)
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        assert (root / "launcher.py").read_text() == "# old launcher\n"
        assert (root / "profileos" / "__init__.py").is_file()
        # The update is whole again where it waits, and tried at the next start.
        assert launcher.apply_pending() == "2026.10.22.5"

    def test_a_power_cut_in_the_middle_is_undone_at_the_next_start(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        real = os.replace
        calls = {"n": 0}

        def cut(source, target):
            calls["n"] += not str(source).endswith(".tmp")     # file moves only
            # The second item is aside, its new copy not yet in: the worst moment.
            if calls["n"] == 4:
                raise SystemExit("power cut")   # nothing in the launcher catches this
            return real(source, target)

        monkeypatch.setattr(launcher.os, "replace", cut)
        with pytest.raises(SystemExit):
            launcher._apply_pending()
        monkeypatch.setattr(launcher.os, "replace", real)
        assert not (root / "launcher.py").exists() or not (root / "profileos").exists()
        assert launcher.apply_pending() == ""        # recovers; does not install half a thing
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        assert (root / "launcher.py").read_text() == "# old launcher\n"
        assert (root / "profileos" / "__init__.py").is_file()
        assert not (root / ".updates" / "ready.json").exists()
        assert json.loads((root / ".updates" / "last_result.json").read_text())["reason"] == "interrupted"

    def test_closing_the_window_while_components_install_keeps_the_old_copy(self, tmp_path, monkeypatch):
        root = an_install(tmp_path, "2026.10.20.1")
        body = a_build("2026.10.22.5", extra={"requirements.txt": "pydantic\nnewthing\n"})
        selfupdate.check_and_stage(fetch=FakeGitHub("2026.10.22.5", body), root=root,
                                   check=lambda s: "")
        launcher = load_launcher()
        self.point(launcher, root, monkeypatch)
        monkeypatch.setattr(launcher, "_freeze", lambda: "")

        def closed():
            raise KeyboardInterrupt

        monkeypatch.setattr(launcher, "_install_requirements", closed)
        with pytest.raises(KeyboardInterrupt):
            launcher._apply_pending()
        # Next start: the old version is back, not deleted.
        assert launcher.apply_pending() == ""
        assert selfupdate.installed(root)["version"] == "2026.10.20.1"
        assert (root / "launcher.py").read_text() == "# old launcher\n"

    def test_a_staged_folder_changed_since_its_check_is_not_installed(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        ready = json.loads((root / ".updates" / "ready.json").read_text())
        (Path(ready["path"]) / "launcher.py").unlink()        # antivirus, OneDrive…
        assert launcher.apply_pending() == ""
        assert (root / "launcher.py").read_text() == "# old launcher\n"
        assert not (root / ".updates" / "ready.json").exists()
        assert selfupdate.status(root).last["reason"] == "damaged"

    def test_ready_pointing_outside_the_updates_folder_is_ignored(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        ready_path = root / ".updates" / "ready.json"
        ready = json.loads(ready_path.read_text())
        for bad in ("", ".", str(root), str(tmp_path)):
            ready["path"] = bad
            ready_path.write_text(json.dumps(ready))
            assert launcher.apply_pending() == ""
            assert (root / "launcher.py").read_text() == "# old launcher\n"
            assert (root / "profileos" / "__init__.py").is_file()

    def test_a_staged_folder_without_the_program_is_never_swapped_in(self, tmp_path):
        launcher = load_launcher()
        root = an_install(tmp_path)
        staged = tmp_path / "staged"
        (staged / "launcher.py").parent.mkdir(parents=True)
        (staged / "launcher.py").write_text("# only this\n")
        with pytest.raises(OSError):
            launcher.swap_in(staged, root, tmp_path / "previous")
        assert (root / "launcher.py").read_text() == "# old launcher\n"


def a_live_process():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


class TestOneAtATime:
    staged = TestTheLauncher.staged
    point = TestTheLauncher.point

    def test_a_second_launcher_waits_for_the_first(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        other = a_live_process()
        try:
            (root / ".updates" / "launcher.lock").write_text(str(other.pid))
            monkeypatch.setattr(launcher, "LOCK_WAIT_S", 0.5)
            assert launcher.apply_pending() == ""
            assert (root / "launcher.py").read_text() == "# old launcher\n"
        finally:
            other.kill()
            other.wait()
        # The first launcher is gone: its lock no longer holds anything.
        assert launcher.apply_pending() == "2026.10.22.5"

    def test_nothing_is_swapped_under_a_running_copy(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        terminal = a_live_process()     # the phone terminal, open all day
        try:
            (root / ".updates" / f"instance-{terminal.pid}.lock").write_text(str(terminal.pid))
            assert launcher.running_copies() == 1
            assert launcher.apply_pending() == ""
            assert (root / "launcher.py").read_text() == "# old launcher\n"
            state = selfupdate.status(root)
            assert "טרמינל הטלפון" in state.describe()
        finally:
            terminal.kill()
            terminal.wait()
        assert launcher.running_copies() == 0
        assert launcher.apply_pending() == "2026.10.22.5"

    def test_the_program_says_it_is_running(self, tmp_path, monkeypatch):
        root = an_install(tmp_path)
        monkeypatch.setattr(selfupdate, "_INSTANCE", None)
        launcher = load_launcher()
        selfupdate.hold_instance(root)
        try:
            assert launcher.running_copies(root / ".updates") == 1
        finally:
            handle, path = selfupdate._INSTANCE
            handle.close()
            path.unlink()

    def test_two_checks_at_once_do_not_trip_over_each_other(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        with selfupdate._CHECKING:
            selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert github.asked == []
        assert selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "").ready


class TestTheRightVerdict:
    staged = TestTheLauncher.staged
    point = TestTheLauncher.point

    def test_a_cancelled_sign_in_is_not_a_broken_version(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        monkeypatch.setattr(launcher, "run_program", lambda: launcher.CANCELLED_CODE)
        assert launcher.main() == 0
        assert selfupdate.installed(root)["version"] == "2026.10.22.5"
        assert not (root / ".updates" / "bad_versions.json").exists()
        assert launcher.CANCELLED_CODE == selfupdate.CANCELLED_CODE

    def test_when_the_old_version_fails_too_the_update_is_not_blamed(self, tmp_path, monkeypatch):
        root, launcher = self.staged(tmp_path, monkeypatch)
        monkeypatch.setattr(launcher, "run_program", lambda: 1)
        assert launcher.main() == 1
        bad = json.loads((root / ".updates" / "bad_versions.json").read_text())
        assert "2026.10.22.5" not in bad
        assert selfupdate.status(root).last["reason"] == "not_the_update"

    def test_a_version_that_failed_its_check_is_not_downloaded_again(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))
        selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "boom")
        github.asked.clear()
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert "https://x/zip" not in github.asked
        assert "לא נפתחה בבדיקה" in state.describe() and "המתוקנת" in state.describe()
        # The next version is taken as usual.
        newer = FakeGitHub("2026.10.22.6", a_build("2026.10.22.6"))
        assert selfupdate.check_and_stage(fetch=newer, root=root, check=lambda s: "").ready

    def test_github_answers_are_said_in_words(self, tmp_path):
        import urllib.error

        root = an_install(tmp_path, "2026.10.20.1")

        def answer(code):
            def fetch(url, limit=0):
                raise urllib.error.HTTPError(url, code, "x", {}, None)
            return fetch

        assert selfupdate.check_and_stage(fetch=answer(404), root=root).error == ""
        assert "הגביל" in selfupdate.check_and_stage(fetch=answer(403), root=root).error
        assert "502" in selfupdate.check_and_stage(fetch=answer(502), root=root).error

    def test_a_version_number_that_is_not_one_is_refused(self, tmp_path):
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("..\\..\\x", a_build("..\\..\\x"))
        state = selfupdate.check_and_stage(fetch=github, root=root, check=lambda s: "")
        assert not state.ready and "מספר גרסה" in state.error


class TestTheStartCheck:
    def test_a_broken_launcher_fails_it(self, tmp_path):
        staged = tmp_path / "staged"
        shutil.copytree(REPO / "profileos", staged / "profileos",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (staged / "launcher.py").write_text("def main(:\n")
        assert "launcher.py" in selfupdate.self_check(staged)

    def test_a_new_component_is_found(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(
            "# comment\npydantic>=2.6\nsurely-not-installed-xyz>=1.0  # new\n-e .\n")
        assert selfupdate.missing_components(tmp_path) == ["surely-not-installed-xyz>=1.0"]

    def test_no_internet_for_a_new_component_waits_rather_than_condemns(self, tmp_path, monkeypatch):
        staged = tmp_path / "staged"
        (staged / "profileos").mkdir(parents=True)
        (staged / "requirements.txt").write_text("surely-not-installed-xyz\n")
        real = subprocess.run

        def no_internet(args, **kw):
            if "pip" in args:
                return subprocess.CompletedProcess(args, 1, "", "no network")
            return real(args, **kw)

        monkeypatch.setattr(selfupdate.subprocess, "run", no_internet)
        with pytest.raises(ProfileOSError, match="רכיב חדש"):
            selfupdate.self_check(staged)
        root = an_install(tmp_path, "2026.10.20.1")
        github = FakeGitHub("2026.10.22.5", a_build("2026.10.22.5"))

        def check(s):
            raise ProfileOSError("הגרסה החדשה צריכה רכיב חדש")

        selfupdate.check_and_stage(fetch=github, root=root, check=check)
        state = json.loads((root / ".updates" / "state.json").read_text())
        assert not state["failed"]       # tried again later, not written off


def test_what_is_new_includes_the_same_day(tmp_path):
    (tmp_path / "profileos" / "data").mkdir(parents=True)
    (tmp_path / "profileos" / "data" / "whats_new.json").write_text(json.dumps([
        {"date": "2026-10-22", "title": "ערב"}, {"date": "2026-10-21", "title": "אתמול"}]))
    assert [e["title"] for e in selfupdate.whats_new("2026-10-22", root=tmp_path)] == ["ערב"]


OLD_SCRIPT = """@echo off
cd /d %USERPROFILE%\\Desktop
echo Checking for updates...
curl -L -o profileos_update.zip https://github.com/yyrhlbrstt-svg/ProfileOS/archive/refs/heads/claude/aluminum-cad-cam-system-dxfni8.zip
if exist profileos_update.zip (
    if exist ProfileOS-claude-aluminum-cad-cam-system-dxfni8 rmdir /s /q ProfileOS-claude-aluminum-cad-cam-system-dxfni8
    tar -xf profileos_update.zip
    del profileos_update.zip
)
cd ProfileOS-claude-aluminum-cad-cam-system-dxfni8
pip install -e . --quiet
python -m profileos.cli ui
""".replace("\n", "\r\n")


class TestTheOldDesktopShortcut:
    def test_it_is_found_and_replaced_keeping_the_old_one(self, tmp_path):
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        (desktop / "ProfileOS.bat").write_bytes(OLD_SCRIPT.encode())
        (desktop / "other.bat").write_text("@echo hello\n")
        found = selfupdate.old_desktop_scripts(tmp_path)
        assert found == [desktop / "ProfileOS.bat"]
        selfupdate.replace_old_script(found[0], root=REPO)
        new = (desktop / "ProfileOS.bat").read_bytes()
        assert b"releases/latest" in new and b"Get-FileHash" in new
        assert b"\r\n" in new and b"\n" not in new.replace(b"\r\n", b"")
        assert list(desktop.glob("ProfileOS.bat*.bak")) or list(desktop.glob("*.bak"))
        assert not selfupdate.old_desktop_scripts(tmp_path)

    def test_the_old_copy_still_running_reads_on_into_nothing_but_exit(self):
        """cmd resumes the running script at the byte where the old one ended."""
        new = (REPO / "scripts" / "launch_windows.bat").read_bytes().replace(b"\r\n", b"\n")
        new = new.replace(b"\n", b"\r\n")
        for old_length in (len(OLD_SCRIPT.encode()), len(OLD_SCRIPT.replace("\r\n", "\n").encode()),
                           len(OLD_SCRIPT.encode()) * 4):
            rest = new[old_length:]
            before_exit = rest[:rest.index(b"\r\nexit /b\r\n")]
            assert set(before_exit) <= set(b": \r\n"), old_length

    def test_a_fresh_run_skips_the_padding(self):
        text = (REPO / "scripts" / "launch_windows.bat").read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines()]
        assert lines[:2] == ["@echo off", "goto start"] and ":start" in lines


def test_files_are_saved_in_documents_not_in_the_program(monkeypatch, tmp_path):
    from profileos.ui import widgets

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert widgets.documents_dir() == tmp_path
    (tmp_path / "Documents").mkdir()
    assert widgets.suggested_path("job-card.html") == str(tmp_path / "Documents" / "job-card.html")
