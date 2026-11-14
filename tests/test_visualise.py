"""The quoted window on the customer's own house: drawn from the design, kept in the job."""

from __future__ import annotations

import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def build():
    from profileos.elements import ElementBuilder
    from profileos.elements.model import Opening

    opening = Opening(element_id="EL-1", name="W1", width=1500, height=1200, system_id="generic")
    return ElementBuilder().build(opening)


def _facade(tmp_path, colour="#E9DFCF"):
    from PySide6.QtGui import QColor, QImage

    image = QImage(800, 600, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    path = tmp_path / "facade.png"
    image.save(str(path))
    return path


def test_the_element_is_drawn_at_its_own_proportions_on_a_clear_ground(app, build):
    from profileos.ui.visualise import front_image

    image = front_image(build, "anthracite", 600)
    assert image.width() == 600 and image.height() == 480          # 1500 × 1200
    # The frame reaches all four edges: four corners place it exactly.
    for x, y in ((1, 240), (598, 240), (300, 1), (300, 478)):
        assert image.pixelColor(x, y).alpha() > 0


def test_it_lands_inside_the_four_corners_and_nowhere_else(app, build, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QImage

    from profileos.ui.visualise import composite, front_image

    photo = QImage(str(_facade(tmp_path)))
    corners = [QPointF(500, 100), QPointF(200, 120), QPointF(200, 400), QPointF(500, 420)]
    result = composite(photo, front_image(build, "anthracite", 400), corners)
    wall = QColor("#E9DFCF")
    assert result.pixelColor(50, 50) == wall                         # the wall untouched
    assert result.pixelColor(350, 260) != wall                       # the window in the opening


def test_the_picture_is_kept_in_the_job_s_files(app, build, tmp_path, monkeypatch):
    from profileos.core.config import reload_settings
    from profileos.projects import JobFile
    from profileos.projects.attachments import AttachmentKind, attachments_for
    from profileos.ui.visualise import VisualiseDialog

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    try:
        job = JobFile(job_id="J-9", name="וילה", customer_name="לוי")
        dialog = VisualiseDialog(builds=[build], colour="white", job=job)
        assert dialog.load_photo(_facade(tmp_path))
        assert dialog.keep.isEnabled()
        dialog.save()
        kept = attachments_for("J-9").of_kind(AttachmentKind.VISUALISATION)
        assert len(kept) == 1 and kept[0].element == "W1"
        assert dialog.saved is not None and dialog.saved.exists()
    finally:
        monkeypatch.undo()
        reload_settings()


def test_without_a_job_it_shows_but_does_not_save(app, build, tmp_path):
    from profileos.ui.visualise import VisualiseDialog

    dialog = VisualiseDialog(builds=[build], job=None)
    dialog.load_photo(_facade(tmp_path))
    assert not dialog.keep.isEnabled() and "פתחו פרויקט" in dialog.problem.text()


def test_a_file_that_is_not_a_picture_is_said(app, build, tmp_path):
    from profileos.ui.visualise import VisualiseDialog

    bad = tmp_path / "notes.png"
    bad.write_text("not a picture", encoding="utf-8")
    dialog = VisualiseDialog(builds=[build], job=None)
    assert not dialog.load_photo(bad) and "לא נפתח" in dialog.problem.text()
