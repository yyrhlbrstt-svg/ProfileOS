"""The installation week by crew: a crew in two places, or out on Shabbat, is said."""

from __future__ import annotations

from datetime import date, timedelta

from profileos.insights import _clashes
from profileos.projects.agenda import Agenda, Appointment, VisitKind, clashes, closed_day

# Sunday 15 November 2026.
SUNDAY = date(2026, 11, 15)


def _visit(on, crew, who, kind=VisitKind.INSTALLATION, time=""):
    return Appointment(on=on, kind=kind, crew=crew, customer_name=who, time=time)


def test_one_crew_two_day_long_jobs_is_a_clash():
    visits = [_visit(SUNDAY, "יוסי", "לוי"), _visit(SUNDAY, "יוסי", "כהן"),
              _visit(SUNDAY, "משה", "דוד"),
              _visit(SUNDAY, "יוסי", "ברק", kind=VisitKind.SURVEY)]   # a measurement is short
    found = clashes(visits, SUNDAY, 7)
    assert len(found) == 1
    assert found[0].crew == "יוסי" and len(found[0].visits) == 2
    assert "⁦2⁩ עבודות באותו יום" in found[0].describe()


def test_shabbat_and_yom_kippur_are_closed():
    assert closed_day(date(2026, 11, 14)) == "שבת"
    assert closed_day(date(2027, 10, 11))            # Yom Kippur
    assert closed_day(SUNDAY) == ""
    found = clashes([_visit(date(2026, 11, 21), "משה", "לוי")], SUNDAY, 7)
    assert found and "יום סגור (שבת)" in found[0].reason


def test_done_visits_and_other_weeks_are_left_alone():
    done = _visit(SUNDAY, "יוסי", "לוי")
    done.done = True
    later = SUNDAY + timedelta(days=20)
    assert not clashes([done, _visit(SUNDAY, "יוסי", "כהן"),
                        _visit(later, "יוסי", "א"), _visit(later, "יוסי", "ב")], SUNDAY, 7)


def test_the_home_page_is_told_while_there_is_time(tmp_path):
    agenda = Agenda(tmp_path / "agenda.json")
    agenda.add(_visit(SUNDAY + timedelta(days=1), "יוסי", "לוי"))
    agenda.add(_visit(SUNDAY + timedelta(days=1), "יוסי", "כהן"))
    found = _clashes({"agenda": agenda}, SUNDAY)
    assert len(found) == 1 and found[0].severity == "danger"
    assert found[0].title.startswith("ביומן:")


def test_the_board_shows_crews_by_day_and_marks_the_clash():
    from PySide6.QtWidgets import QApplication

    from profileos.ui.dialogs import CrewWeekDialog
    from profileos.ui.theme import LIGHT

    QApplication.instance() or QApplication([])
    visits = [_visit(SUNDAY, "יוסי", "לוי", time="07:30"), _visit(SUNDAY, "יוסי", "כהן"),
              _visit(SUNDAY + timedelta(days=1), "משה", "דוד")]
    dialog = CrewWeekDialog(visits=visits, palette=LIGHT, start=SUNDAY)
    table = dialog.table
    crews = [table.verticalHeaderItem(row).text() for row in range(table.rowCount())]
    assert crews == ["יוסי", "משה"]
    cell = table.cellWidget(0, 0)
    assert "לוי" in cell.text() and "כהן" in cell.text()
    assert cell.property("state") == "danger" and "התנגשות" in cell.toolTip()
    assert "דוד" in table.cellWidget(1, 1).text()
    assert len(dialog.clashes) == 1
