"""The live 3D view: geometry handed to the GPU, and leaves that know how to open."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
np = pytest.importorskip("numpy")

from profileos.elements import Cell, ElementBuilder, Opening, OpeningType, Sash  # noqa: E402
from profileos.viz3d import build_element_scene  # noqa: E402


def _build():
    opening = Opening(name="W-01", width=2400, height=1500)
    opening.divide_evenly(3, 1)
    opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.CASEMENT)))
    opening.set_cell(Cell(column=2, row=0,
                          sash=Sash(opening_type=OpeningType.TOP_HUNG, hinge_side="top")))
    return ElementBuilder().build(opening)


def test_every_leaf_says_how_it_opens():
    scene = build_element_scene(_build())
    leaves = {
        mesh.metadata["leaf"]["cell"]: mesh.metadata["leaf"]
        for mesh in scene.meshes if (mesh.metadata or {}).get("leaf")
    }
    assert leaves["0-0"]["type"] == "casement"
    assert leaves["2-0"]["type"] == "top_hung"
    x, y, w, h = leaves["0-0"]["rect"]
    assert w > 0 and h > 0
    # The fixed middle light carries no leaf.
    assert "1-0" not in leaves


def test_geometry_is_flat_shaded_and_centred():
    from profileos.ui.viewer3d import flat_shaded

    scene = build_element_scene(_build())
    centre = scene.centre
    data, low, high = flat_shaded(scene.meshes, centre)
    assert data.dtype == np.float32 and data.shape[1] == 6
    assert len(data) == 3 * scene.triangle_count
    # Normals are unit length, positions are around the origin.
    lengths = np.linalg.norm(data[:, 3:], axis=1)
    assert np.allclose(lengths[lengths > 0], 1.0, atol=1e-4)
    assert abs((low[0] + high[0]) / 2) < 1.0 and abs((low[1] + high[1]) / 2) < 1.0


def test_a_machine_without_3d_keeps_the_ordinary_render():
    from PySide6.QtWidgets import QApplication

    from profileos.ui.viewer3d import Viewer3D, supported

    QApplication.instance() or QApplication([])
    assert not supported()  # the offscreen test platform has no GPU
    viewer = Viewer3D()
    assert viewer.supported is False
    assert viewer.show_build(_build()) is False
