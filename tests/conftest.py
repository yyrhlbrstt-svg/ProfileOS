"""Shared pytest fixtures."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

# Every test run gets its own settings folder: a test that restores a
# backup or saves settings must never touch the real installation's.
import os as _os
import tempfile as _tempfile

_os.environ.setdefault(
    "PROFILEOS_CONFIG_DIR", _tempfile.mkdtemp(prefix="profileos-test-config-")
)

from profileos.core.logging_setup import configure_logging  # noqa: E402

# The FEA backend emits numpy deprecation chatter that drowns the test output.
warnings.filterwarnings("ignore", category=DeprecationWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "profileos" / "data" / "samples"


@pytest.fixture(scope="session", autouse=True)
def _quiet_logging() -> None:
    configure_logging("ERROR", use_rich=False, force=True)


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path, monkeypatch) -> None:
    """Tests write audit entries as a side effect of doing real work.

    Confirming a series, posting a stocktake and taking a quotation revision
    all record who did it. That is the point of them — but a test run must not
    append hundreds of lines to the chain of whatever installation happens to
    be on this machine.
    """
    monkeypatch.setenv("PROFILEOS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    """Directory holding the generated sample DXF drawings.

    The samples are build artefacts; regenerate them if missing so a fresh
    checkout can run the suite without a separate step.
    """
    if not SAMPLE_DIR.is_dir() or not any(SAMPLE_DIR.glob("*.dxf")):
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "tools" / "generate_sample_dxf.py")],
            check=True,
            capture_output=True,
        )
    return SAMPLE_DIR


@pytest.fixture(scope="session")
def mullion_dxf(sample_dir: Path) -> Path:
    return sample_dir / "mullion_mb70.dxf"


@pytest.fixture(scope="session")
def bead_dxf(sample_dir: Path) -> Path:
    return sample_dir / "glazing_bead.dxf"


@pytest.fixture(scope="session")
def gapped_dxf(sample_dir: Path) -> Path:
    return sample_dir / "gapped_box.dxf"


@pytest.fixture(scope="session")
def thermal_dxf(sample_dir: Path) -> Path:
    return sample_dir / "frame_thermal.dxf"


@pytest.fixture
def rect_polygon():
    """Factory for an axis-aligned rectangle placed at the origin."""
    from shapely.geometry import Polygon

    def _make(width: float, height: float, x: float = 0.0, y: float = 0.0):
        return Polygon(
            [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        )

    return _make


@pytest.fixture(autouse=True)
def _forget_process_wide_notes():
    """Recoveries and backup outcomes are remembered for the whole run of
    the program; one test's must not show up in the next one's sidebar."""
    from profileos.core import backup, storage

    yield
    storage.RECOVERED.clear()
    backup._STATUS.update(error="", last="", mirror_error="")


@pytest.fixture(autouse=True)
def _no_price_lists_left_behind():
    """A supplier price list taken in by one test is put in force process-wide;
    the next test must price from its own figures, not that one's."""
    yield
    from profileos.core.registry import SUPPLIERS

    SUPPLIERS.remove_by_source("shop")


@pytest.fixture(autouse=True)
def _no_message_box_waits_for_a_click(monkeypatch):
    """A message box nobody will click would hang the run: it fails the test
    at once, with what it said. A test that expects one patches it itself."""
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        yield
        return
    from PySide6.QtWidgets import QMessageBox

    def refuse(box, *args, **kwargs):
        raise AssertionError(f"a message box would wait for a click: {box.windowTitle()} — {box.text()}")

    monkeypatch.setattr(QMessageBox, "exec", refuse)
    yield


@pytest.fixture(autouse=True)
def _windows_are_really_destroyed():
    """Every window a test opened is destroyed when it ends.

    ``deleteLater`` only queues the deletion, and ``processEvents`` does not
    deliver it outside an event loop — so every test left its windows alive,
    and every theme switch restyled all of them: the suite grew slower with
    each window test added, until switching the theme took minutes.
    """
    yield
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if isinstance(widget, (QMainWindow, QDialog)):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# Qt can crash in its own tear-down at interpreter exit, after every test has
# passed and the summary is printed — and the crash becomes the run's exit
# code, failing CI for nothing. Once the results are in, the process leaves
# with them, before Qt's destructors run.
_RESULT: dict[str, int] = {}


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus) -> None:
    _RESULT["status"] = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config) -> None:
    import sys

    if "status" in _RESULT and "PySide6.QtWidgets" in sys.modules:
        sys.stdout.flush()
        sys.stderr.flush()
        _os._exit(_RESULT["status"])
