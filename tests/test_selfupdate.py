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
        pack.writestr("profileos/__init__.py", "__version__ = '0.1.0'\n")
        pack.writestr("profileos/_release.json", json.dumps({"version": version}))
        pack.writestr("launcher.py", "# new launcher\n")
        pack.writestr("requirements.txt", "pydantic\n")
        for name, text in (extra or {}).items():
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
