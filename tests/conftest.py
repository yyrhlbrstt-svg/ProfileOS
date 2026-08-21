"""Shared pytest fixtures."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from profileos.core.logging_setup import configure_logging

# The FEA backend emits numpy deprecation chatter that drowns the test output.
warnings.filterwarnings("ignore", category=DeprecationWarning)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "profileos" / "data" / "samples"


@pytest.fixture(scope="session", autouse=True)
def _quiet_logging() -> None:
    configure_logging("ERROR", use_rich=False, force=True)


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
