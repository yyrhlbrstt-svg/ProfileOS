"""A pasted list of openings becomes the job's openings — nothing guessed."""

from __future__ import annotations

import os

import pytest

from profileos.projects.intake import read_list


SCHEDULE = """W1\tחלון ציר\t120\t140\t2
W2 סלון הזזה 2 כנפיים 240/210
3. ח-3 חלון קבוע 60x60 מקלחת
D1 דלת כניסה 100 על 220
סלון משהו בלי מידה
4 חלונות ציר 120×140 לחדרים
W5 חלון הזזה 1.80 על 1.20 מטר, 3 יח"""


def test_a_schedule_from_a_table_and_a_message_is_read_line_by_line():
    intake = read_list(SCHEDULE)
    read = {line.mark or line.text: line for line in intake.read}
    assert (read["W1"].opening.width, read["W1"].opening.height, read["W1"].opening.quantity) \
        == (1200, 1400, 2)                                   # table: cm, then a count
    assert read["W2"].opening.width == 2400 and read["W2"].room == "סלון"
    assert read["ח-3"].opening.reference == "ח-3" and read["ח-3"].opening.notes == "מקלחת"
    assert read["D1"].opening.kind.value == "door"
    assert read["W5"].opening.width == 1800 and read["W5"].opening.quantity == 3
    plural = next(line for line in intake.read if not line.mark)
    assert plural.opening.quantity == 4                       # "4 חלונות"
    assert [line.text for line in intake.unread] == ["סלון משהו בלי מידה"]
    assert "חסרים רוחב וגובה" in intake.unread[0].problem
    assert "⁦6⁩ פתחים" in intake.describe() and "⁦12⁩ יחידות" in intake.describe()


def test_a_mark_already_in_the_job_is_not_added_twice():
    from profileos.elements.model import Opening

    intake = read_list("W1 חלון ציר 120 140", existing=[Opening(name="W1", reference="W1",
                                                                width=1000, height=1000)])
    assert not intake.read and "כבר קיים" in intake.unread[0].problem


def test_pasted_into_the_job_from_the_projects_page(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QDialog

    from profileos.core.config import reload_settings
    from profileos.projects import JobFile, default_store
    from profileos.ui import dialogs
    from profileos.ui.main_window import MainWindow
    from profileos.ui.theme import LIGHT

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    default_store().save(JobFile(job_id="J-2026-0003", name="וילה", system_id="klil-7300"))

    def paste(dialog):
        dialog.text.setPlainText(SCHEDULE)
        dialog.read()
        assert dialog.ok_button.isEnabled() and "✗" in dialog.preview.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialogs.PasteOpeningsDialog, "exec", paste)
    QApplication.instance() or QApplication([])
    window = MainWindow(LIGHT)
    page = window.go_to_page("Projects")
    monkeypatch.setattr(page, "_selected_job", lambda: default_store().load("J-2026-0003"))
    assert page.paste_openings() == 6
    job = default_store().load("J-2026-0003")
    assert len(job.schedule.openings) == 6
    assert {o.system_id for o in job.schedule.openings} == {"klil-7300"}
    window.close(); window.deleteLater(); QCoreApplication.processEvents()
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


@pytest.mark.parametrize("line, quantity", [
    ("W1 חלון ציר 120×140 2 יח׳ סלון", 2),          # the dialog's own example
    ("חלון ציר 120x140 2", 2),
    ("חלון ציר 120x140 3 יחידות", 3),
    ("חלון ציר כמות 4 120x140", 4),
    ("הזזה 240/210 2", 2),
    ("הזזה 2 כנפיים 240/210", 1),                    # leaves, not a count
])
def test_the_count_is_read_however_it_is_written(line, quantity):
    [read] = read_list(line).lines
    assert read.ok and read.opening.quantity == quantity


@pytest.mark.parametrize("line", ["100x100", "W1 120x140", "תריס 120x140", "גגון 100x100",
                                  "W2 חלון ציר 120 140 90"])
def test_a_line_that_does_not_say_what_it_is_is_not_guessed(line):
    [read] = read_list(line).lines
    assert not read.ok and read.problem
