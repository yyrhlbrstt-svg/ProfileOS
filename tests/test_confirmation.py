"""Turning a series into one the shop may actually cut to."""

from __future__ import annotations

import json

import pytest

from profileos.core.errors import ProfileOSError
from profileos.systems import (
    FIGURES,
    Confirmation,
    ConfirmationBook,
    read_confirmation,
    template,
    write_template,
)


def _filled(entry_id: str = "klil-7300", **overrides) -> Confirmation:
    confirmation = Confirmation(
        entry_id=entry_id,
        source="קטלוג קליל 7300, מהדורת 2026, עמ׳ 41",
        entered_by="דאדי",
    )
    for figure in FIGURES:
        if figure.default is not None:
            confirmation.values[figure.key] = figure.default
    confirmation.values.update(overrides)
    return confirmation


@pytest.fixture(autouse=True)
def clean_directory(tmp_path, monkeypatch):
    """A confirmation is global state; put the directory back afterwards.

    Confirming a series changes what every later build in the process may be
    cut to, so a test that confirms one and walks away has changed the answer
    for the next test — exactly as a shop that deletes a catalogue entry
    changes it for the next job.
    """
    from profileos.core.config import reload_settings
    from profileos.systems import DIRECTORY

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    before = {entry.id: entry for entry in DIRECTORY}
    yield
    DIRECTORY._entries.update(before)
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


class TestWhatHasToBeEntered:
    def test_the_figures_are_the_ones_a_catalogue_prints(self):
        keys = {figure.key for figure in FIGURES}
        for expected in (
            "frame_face", "sash_overlap", "glass_edge_cover",
            "max_glass_thickness", "mullion_face",
        ):
            assert expected in keys

    def test_every_figure_says_where_to_look_for_it(self):
        assert all(figure.where for figure in FIGURES)

    def test_it_is_a_short_form_because_a_long_one_is_never_finished(self):
        assert len([f for f in FIGURES if f.required]) <= 12

    def test_a_value_outside_what_is_physically_possible_is_refused(self):
        confirmation = _filled(frame_face=900.0)
        assert any("מחוץ לתחום" in problem for problem in confirmation.problems())

    def test_a_transposed_glass_pair_is_caught(self):
        """The mistake somebody typing eleven numbers off a page actually makes."""
        confirmation = _filled(glass_edge_cover=3.0, glass_clearance=15.0)
        assert any("הוחלפו" in problem for problem in confirmation.problems())

    def test_a_transposed_sash_pair_is_caught_too(self):
        confirmation = _filled(sash_overlap=2.0, sash_clearance=8.0)
        assert any("לא תיסגר" in problem for problem in confirmation.problems())

    def test_figures_with_no_source_are_not_confirmed_they_are_asserted(self):
        confirmation = _filled()
        confirmation.source = ""
        assert any("מקור" in problem for problem in confirmation.problems())
        assert not confirmation.is_complete

    def test_a_half_filled_form_names_what_is_missing_and_where_it_is(self):
        confirmation = Confirmation("klil-7300", source="קטלוג")
        problems = confirmation.problems()
        assert len(problems) == len(confirmation.missing())
        assert all("חסר:" in problem for problem in problems)

    def test_a_complete_form_becomes_a_rule_set(self):
        rules = _filled(frame_face=52.0).to_rules(name="קליל 7300")
        assert rules.frame.face_width == 52.0
        assert rules.id == "klil-7300"

    def test_an_incomplete_one_refuses_to_rather_than_filling_gaps(self):
        with pytest.raises(ProfileOSError):
            Confirmation("klil-7300").to_rules()


class TestTheFormOnPaper:
    def test_the_template_carries_its_own_instructions(self, tmp_path):
        form = template("klil-7300")
        assert form["entry_id"] == "klil-7300"
        assert form["source"] == ""
        assert any("קטלוג" in str(value) for value in form.values())
        assert set(form["values"]) == {figure.key for figure in FIGURES}

    def test_it_round_trips_through_a_file(self, tmp_path):
        path = write_template("klil-7300", tmp_path / "figures.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["source"] = "קטלוג 2026"
        data["values"]["frame_face"] = 52.0
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        confirmation = read_confirmation(path)
        assert confirmation.source == "קטלוג 2026"
        assert confirmation.values["frame_face"] == 52.0

    def test_a_blank_field_is_left_out_rather_than_read_as_zero(self, tmp_path):
        path = write_template("klil-7300", tmp_path / "figures.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["values"]["frame_face"] = ""
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert "frame_face" not in read_confirmation(path).values


class TestItSurvivesTheProgramClosing:
    def test_a_confirmation_is_still_there_next_morning(self, tmp_path):
        path = tmp_path / "confirmations.json"
        ConfirmationBook(path)._entries["x"] = _filled()
        ConfirmationBook(path)  # a second reader must not disturb the first

        book = ConfirmationBook(path)
        book.record(_filled())
        assert len(ConfirmationBook(path)) == 1

    def test_an_unreadable_file_does_not_lose_the_application(self, tmp_path):
        path = tmp_path / "confirmations.json"
        path.write_text("{ not json", encoding="utf-8")
        assert len(ConfirmationBook(path)) == 0

    def test_one_bad_row_does_not_lose_the_good_ones(self, tmp_path):
        path = tmp_path / "confirmations.json"
        path.write_text(json.dumps({
            "confirmations": [
                {"nonsense": True},
                _filled().as_dict(),
            ]
        }, ensure_ascii=False), encoding="utf-8")
        assert len(ConfirmationBook(path)) == 1

    def test_an_incomplete_confirmation_is_never_stored(self, tmp_path):
        book = ConfirmationBook(tmp_path / "confirmations.json")
        incomplete = _filled()
        incomplete.source = ""
        with pytest.raises(ProfileOSError):
            book.record(incomplete)
        assert len(book) == 0


class TestWhatItUnlocks:
    def test_a_series_with_no_figures_may_not_be_cut_to(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.elements import ElementBuilder, Opening

        opening = Opening(name="W-01", width=1800, height=1400, system_id="klil-4300")
        opening.divide_evenly(2, 1)
        build = ElementBuilder.for_system("klil-4300").build(opening, sill_height=900)
        assert not build.may_be_cut
        assert "לא לייצור" in build.production_banner

    def test_entering_the_figures_takes_the_banner_off(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.elements import ElementBuilder, Opening

        book = ConfirmationBook(tmp_path / "confirmations.json")
        book.record(_filled("klil-7300", frame_face=52.0))

        opening = Opening(name="W-01", width=1800, height=1400, system_id="klil-7300")
        opening.divide_evenly(2, 1)
        build = ElementBuilder.for_system("klil-7300").build(opening, sill_height=900)
        assert build.may_be_cut
        assert build.production_banner is None

    def test_the_supplier_s_own_face_width_is_the_one_that_is_cut_to(
        self, tmp_path, monkeypatch
    ):
        """Not a detail: it decides every daylight opening in the job."""
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.elements import ElementBuilder, Opening

        book = ConfirmationBook(tmp_path / "confirmations.json")
        book.record(_filled("klil-7300", frame_face=52.0, mullion_face=64.0))

        opening = Opening(name="W-01", width=1800, height=1400, system_id="klil-7300")
        opening.divide_evenly(2, 1)
        build = ElementBuilder.for_system("klil-7300").build(opening, sill_height=900)
        assert build.rules.frame.face_width == 52.0
        assert build.rules.mullion.face_width == 64.0

    def test_the_article_numbers_reach_the_cut_list(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        from profileos.elements import ElementBuilder, Opening

        confirmation = _filled("klil-7300")
        confirmation.profiles = {"frame": "KL-7300-F", "sash": "KL-7300-S"}
        ConfirmationBook(tmp_path / "confirmations.json").record(confirmation)

        opening = Opening(name="W-01", width=1800, height=1400, system_id="klil-7300")
        opening.divide_evenly(1, 1)
        build = ElementBuilder.for_system("klil-7300").build(opening, sill_height=900)
        assert any(cut.profile_id == "KL-7300-F" for cut in build.cuts)

    def test_the_readiness_report_changes_its_answer(self, tmp_path, monkeypatch):
        from profileos.core.config import reload_settings
        from profileos.readiness import readiness

        monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
        reload_settings()
        assert not readiness().may_cut

        from profileos.systems import default_confirmations

        default_confirmations().record(_filled("klil-7300"))
        assert readiness().may_cut


class TestTakingItBack:
    def test_forgetting_a_confirmation_stops_the_cutting(self):
        """Delete a wrongly typed catalogue and the saw must stop with it."""
        from profileos.systems import DIRECTORY, default_confirmations

        book = default_confirmations()
        book.record(_filled("klil-7300"))
        assert DIRECTORY.readiness("klil-7300").may_cut

        book.forget("klil-7300")
        assert not DIRECTORY.readiness("klil-7300").may_cut
        assert len(book) == 0

    def test_a_revoked_series_can_still_be_quoted_on_typical_figures(self):
        from profileos.systems import DIRECTORY, default_confirmations

        book = default_confirmations()
        book.record(_filled("klil-7300"))
        book.forget("klil-7300")
        assert DIRECTORY.readiness("klil-7300").may_quote

    def test_forgetting_something_never_confirmed_is_harmless(self):
        from profileos.systems import default_confirmations

        default_confirmations().forget("klil-7300")
