"""Measuring the hole in the wall, and turning it into a frame size."""

from __future__ import annotations

from datetime import date

import pytest

from profileos.core.errors import ProfileOSError
from profileos.delivery.survey import (
    SQUARE_TOLERANCE_MM,
    OpeningSurvey,
    Survey,
    SurveyBook,
    survey_for_job,
)


def _measured(**overrides) -> OpeningSurvey:
    values = dict(
        reference="W1", room="סלון",
        width_head=1500.0, width_middle=1500.0, width_sill=1500.0,
        height_left=1200.0, height_middle=1200.0, height_right=1200.0,
        diagonal_a=1921.0, diagonal_b=1921.0,
        clearance_per_side=10.0,
        measured_by="יוסי", measured_on=date(2026, 8, 27),
    )
    values.update(overrides)
    return OpeningSurvey(**values)


class TestTheMethod:
    def test_the_frame_passes_through_the_smallest_width_not_the_average(self):
        """A frame does not fit an average."""
        entry = _measured(width_head=1500.0, width_middle=1494.0, width_sill=1488.0)
        assert entry.smallest_width == pytest.approx(1488.0)

    def test_the_same_holds_for_height(self):
        entry = _measured(height_left=1200.0, height_middle=1197.0, height_right=1204.0)
        assert entry.smallest_height == pytest.approx(1197.0)

    def test_the_frame_size_is_the_opening_less_clearance_both_sides(self):
        entry = _measured(clearance_per_side=10.0)
        assert entry.frame_size() == (1480.0, 1180.0)

    def test_without_a_clearance_from_the_system_there_is_no_frame_size(self):
        """The most expensive invention this software could make."""
        entry = _measured(clearance_per_side=None)
        assert entry.frame_size() is None
        assert any("מרווח התקנה" in p for p in entry.problems())

    def test_an_unmeasured_opening_is_not_measured(self):
        assert not OpeningSurvey(reference="W1").is_measured

    def test_a_date_alone_does_not_make_it_measured(self):
        entry = OpeningSurvey(reference="W1", measured_on=date(2026, 8, 27))
        assert not entry.is_measured


class TestWhatGoesWrong:
    def test_a_non_parallel_opening_is_caught_by_the_spread(self):
        entry = _measured(width_head=1500.0, width_middle=1494.0, width_sill=1480.0)
        assert entry.width_spread == pytest.approx(20.0)
        assert any("אינו מקביל" in p for p in entry.problems())

    def test_one_width_measured_is_flagged_as_not_enough(self):
        entry = _measured(width_middle=None, width_sill=None)
        assert any("שלושה רוחבים" in p for p in entry.problems())

    def test_matching_diagonals_mean_square(self):
        assert _measured().out_of_square == pytest.approx(0.0)

    def test_diagonals_that_disagree_are_reported_in_millimetres(self):
        entry = _measured(diagonal_a=1921.0, diagonal_b=1939.0)
        assert entry.out_of_square == pytest.approx(18.0)
        assert any("מחוץ לזווית" in p for p in entry.problems())

    def test_a_small_racking_is_within_what_packers_hide(self):
        entry = _measured(diagonal_b=1921.0 + SQUARE_TOLERANCE_MM - 1)
        assert not any("מחוץ לזווית" in p for p in entry.problems())

    def test_missing_diagonals_are_a_problem_not_an_assumption_of_square(self):
        entry = _measured(diagonal_a=None, diagonal_b=None)
        assert entry.out_of_square is None
        assert any("אלכסונים" in p for p in entry.problems())

    def test_the_lean_recovers_the_sideways_displacement_that_caused_it(self):
        """A diagonal difference is easy to measure and hard to picture."""
        import math

        width, height, lean = 1500.0, 1200.0, 15.0
        entry = _measured(
            width_head=width, width_middle=width, width_sill=width,
            height_left=height, height_middle=height, height_right=height,
            diagonal_a=math.hypot(width + lean, height),
            diagonal_b=math.hypot(width - lean, height),
        )
        assert entry.implied_lean == pytest.approx(lean, abs=0.5)

    def test_a_sill_measured_off_an_unfinished_floor_says_so(self):
        entry = _measured(sill_above_floor=900.0, floor_is_finished=False)
        assert any("רצפה שאינה גמורה" in p for p in entry.problems())

    def test_a_finished_floor_raises_nothing(self):
        entry = _measured(sill_above_floor=900.0, floor_is_finished=True)
        assert not any("רצפה" in p for p in entry.problems())

    def test_nobody_named_as_the_measurer_is_a_problem(self):
        entry = _measured(measured_by="")
        assert any("מי מדד" in p for p in entry.problems())

    def test_a_clean_measurement_may_be_made(self):
        assert _measured().may_be_made

    def test_measured_is_not_the_same_as_makeable(self):
        entry = _measured(diagonal_b=1960.0)
        assert entry.is_measured
        assert not entry.may_be_made


class TestTheSheet:
    @pytest.fixture
    def survey(self) -> Survey:
        survey = Survey(job_id="2026-114", job_name="וילה")
        survey.openings = [
            _measured(reference="W1"),
            _measured(reference="W2"),
            OpeningSurvey(reference="W3", clearance_per_side=10.0),
        ]
        return survey

    def test_it_knows_what_has_not_been_visited(self, survey):
        assert [entry.reference for entry in survey.unmeasured] == ["W3"]
        assert survey.progress == pytest.approx(200 / 3, abs=0.1)

    def test_the_blank_sheet_carries_no_figures_to_copy(self, survey):
        rows = survey.sheet_rows()
        assert all(cell == "" for row in rows for cell in row[2:])
        assert len(rows[0]) == len(Survey.SHEET_HEADERS)

    def test_an_unmeasured_opening_blocks_the_sheet(self, survey):
        assert not survey.may_be_made
        assert any("לא נמדדו" in p for p in survey.problems())

    def test_problems_name_the_opening_they_belong_to(self, survey):
        survey.openings[0].diagonal_b = 1960.0
        assert any("W1" in p and "מחוץ לזווית" in p for p in survey.problems())

    def test_asking_for_an_opening_that_is_not_there_is_refused(self, survey):
        with pytest.raises(ProfileOSError):
            survey.opening("W9")


class TestBecomingElements:
    def test_measured_openings_become_elements_at_the_measured_size(self):
        survey = Survey(job_id="J")
        survey.openings = [_measured(reference="W1")]
        made = survey.to_openings()
        assert len(made) == 1
        assert made[0].width == pytest.approx(1480.0)
        assert made[0].height == pytest.approx(1180.0)

    def test_an_opening_out_of_square_is_left_out_rather_than_rounded_in(self):
        survey = Survey(job_id="J")
        survey.openings = [
            _measured(reference="W1"),
            _measured(reference="W2", diagonal_b=1960.0),
        ]
        assert [o.name for o in survey.to_openings()] == ["W1"]

    def test_an_unmeasured_opening_never_becomes_an_element(self):
        survey = Survey(job_id="J")
        survey.openings = [OpeningSurvey(reference="W1", clearance_per_side=10.0)]
        assert survey.to_openings() == []

    def test_a_template_carries_the_system_and_division_across(self):
        from profileos.elements.model import Opening

        template = Opening(
            name="דגם", width=1000, height=1000,
            system_id="klil-7300", mullion_positions=[500.0],
        )
        survey = Survey(job_id="J")
        survey.openings = [_measured(reference="W1")]
        made = survey.to_openings(template=template)
        assert made[0].system_id == "klil-7300"
        assert made[0].width == pytest.approx(1480.0)


class TestOpeningAndKeeping:
    def test_a_sheet_opens_with_a_blank_line_per_scheduled_opening(self):
        from profileos.elements import ElementSchedule, Opening
        from profileos.projects.model import JobFile

        job = JobFile(
            job_id="2026-114", name="וילה",
            schedule=ElementSchedule(name="וילה", openings=[
                Opening(name="W1", width=1500, height=1200),
                Opening(name="W2", width=900, height=1200),
            ]),
        )
        survey = survey_for_job(job, clearance_per_side=10.0)
        assert len(survey) == 2
        assert all(not entry.is_measured for entry in survey)
        assert survey.openings[0].clearance_per_side == pytest.approx(10.0)

    def test_a_survey_survives_a_round_trip_through_disk(self, tmp_path):
        survey = Survey(job_id="2026-114", job_name="וילה")
        survey.openings = [_measured(reference="W1", note="גליף עמוק")]
        book = SurveyBook(tmp_path / "s.json")
        book.add(survey)

        again = SurveyBook(tmp_path / "s.json").load().get(survey.survey_id)
        assert again.job_name == "וילה"
        assert again.opening("W1").smallest_width == pytest.approx(1500.0)
        assert again.opening("W1").note == "גליף עמוק"
        assert again.opening("W1").measured_on == date(2026, 8, 27)

    def test_an_unmeasured_line_stays_unmeasured_across_a_save(self, tmp_path):
        survey = Survey(job_id="J")
        survey.openings = [OpeningSurvey(reference="W1")]
        book = SurveyBook(tmp_path / "s.json")
        book.add(survey)
        again = SurveyBook(tmp_path / "s.json").load().get(survey.survey_id)
        assert not again.opening("W1").is_measured
        assert again.opening("W1").width_head is None
