"""Chasing the quotation, which is where most of the money is left."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.erp.scheduling import Calendar
from profileos.projects.followups import (
    CHASE_SCHEDULE,
    Kind,
    Outcome,
    Task,
    TaskBook,
    working_days_after,
)
from profileos.projects.model import JobFile, JobStatus


@pytest.fixture
def book(tmp_path) -> TaskBook:
    return TaskBook(tmp_path / "tasks.json")


def _job(job_id="2026-114", status=JobStatus.QUOTED) -> JobFile:
    return JobFile(
        job_id=job_id, name="וילה", customer_name="אבי כהן", status=status
    )


class TestWorkingDays:
    def test_a_reminder_never_lands_on_a_day_nobody_is_there(self, book):
        """A Saturday reminder gets scrolled past on the Sunday, forever."""
        calendar = Calendar.israeli()
        for offset in range(0, 21):
            task = book.create(
                Kind.CALL_BACK, "לבדוק",
                due=date(2026, 8, 1) + timedelta(days=offset),
            )
            assert calendar.is_working(task.due)

    def test_counting_forward_counts_working_days_not_calendar_days(self):
        start = date(2026, 8, 2)
        assert (working_days_after(start, 7) - start).days > 7

    def test_zero_days_forward_is_the_next_working_day(self):
        calendar = Calendar.israeli()
        start = date(2026, 8, 1)
        assert working_days_after(start, 0) == calendar.next_working_day(start)


class TestTheChase:
    def test_sending_a_quotation_creates_the_whole_schedule(self, book):
        made = book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        assert len(made) == len(CHASE_SCHEDULE)
        assert all(task.kind is Kind.CHASE_QUOTE for task in made)

    def test_the_touches_come_in_order_and_stop(self, book):
        made = book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        dates = [task.due for task in made]
        assert dates == sorted(dates)
        assert len(dates) == 3

    def test_the_customer_is_named_so_the_list_reads_without_lookups(self, book):
        made = book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        assert all(task.subject_name == "אבי כהן" for task in made)

    def test_scheduling_twice_does_not_double_the_list(self, book):
        book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        again = book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        assert again == []
        assert len(book.open_tasks) == len(CHASE_SCHEDULE)

    def test_a_closed_touch_may_be_scheduled_again(self, book):
        made = book.chase_quote(_job(), sent_on=date(2026, 8, 2))
        book.close(made[0].task_id, Outcome.DONE, result="דיברתי")
        again = book.chase_quote(_job(), sent_on=date(2026, 9, 1))
        assert len(again) == 1


class TestWhatIsForgotten:
    def test_a_quotation_nobody_is_chasing_is_reported(self, book):
        """Not what was quoted — what was quoted and then forgotten."""
        chased, forgotten = _job("A"), _job("B")
        book.chase_quote(chased, sent_on=date(2026, 8, 2))
        found = book.unchased_quotes([chased, forgotten])
        assert [job.job_id for job in found] == ["B"]

    def test_a_job_already_won_is_not_a_forgotten_quotation(self, book):
        won = _job("C", status=JobStatus.WON)
        assert book.unchased_quotes([won]) == []

    def test_closing_every_touch_puts_the_job_back_on_the_list(self, book):
        job = _job("A")
        for task in book.chase_quote(job, sent_on=date(2026, 8, 2)):
            book.close(task.task_id, Outcome.NO_ANSWER)
        assert [j.job_id for j in book.unchased_quotes([job])] == ["A"]


class TestClosingATask:
    def test_a_task_is_closed_with_what_happened(self, book):
        task = book.create(Kind.CALL_BACK, "לחזור")
        book.close(task.task_id, Outcome.POSTPONED, result="ממתינים לאדריכל")
        assert book.get(task.task_id).result == "ממתינים לאדריכל"
        assert not book.get(task.task_id).is_open

    def test_closing_with_nothing_said_is_allowed_but_counted(self, book):
        """Forcing prose is how a list becomes a nuisance."""
        task = book.create(Kind.CALL_BACK, "לחזור")
        book.close(task.task_id)
        assert book.get(task.task_id).closed_silently
        assert book.summary()["closed_silently"] == 1

    def test_a_task_cannot_be_closed_twice(self, book):
        task = book.create(Kind.CALL_BACK, "לחזור")
        book.close(task.task_id)
        with pytest.raises(ProfileOSError):
            book.close(task.task_id)

    def test_postponing_keeps_that_it_was_postponed(self, book):
        task = book.create(Kind.CALL_BACK, "לחזור", due=date(2026, 8, 3))
        task.postpone(date(2026, 8, 20), reason="הלקוח בחו״ל")
        assert task.due == date(2026, 8, 20)
        assert "הלקוח בחו״ל" in task.result

    def test_postponing_backwards_is_refused(self, book):
        task = book.create(Kind.CALL_BACK, "לחזור", due=date(2026, 8, 20))
        with pytest.raises(ProfileOSError):
            task.postpone(date(2026, 8, 3))

    def test_a_closed_task_cannot_be_postponed(self, book):
        task = book.create(Kind.CALL_BACK, "לחזור")
        book.close(task.task_id)
        with pytest.raises(ProfileOSError):
            task.postpone(date.today() + timedelta(days=30))


class TestTheList:
    def test_overdue_is_open_and_past_not_merely_past(self, book):
        late = book.create(Kind.CALL_BACK, "ישן", due=date(2026, 1, 1))
        book.create(Kind.CALL_BACK, "עתידי", due=date(2030, 1, 1))
        done = book.create(Kind.CALL_BACK, "נעשה", due=date(2026, 1, 1))
        book.close(done.task_id)

        overdue = book.overdue(date(2026, 8, 27))
        assert [task.task_id for task in overdue] == [late.task_id]

    def test_the_list_is_ordered_by_when_it_should_have_been_done(self, book):
        book.create(Kind.CALL_BACK, "ב", due=date(2026, 9, 10))
        book.create(Kind.CALL_BACK, "א", due=date(2026, 9, 1))
        assert [task.what for task in book][:2] == ["א", "ב"]

    def test_a_week_ahead_is_askable_for(self, book):
        book.create(Kind.CALL_BACK, "קרוב", due=date(2026, 9, 2))
        book.create(Kind.CALL_BACK, "רחוק", due=date(2026, 12, 2))
        found = book.this_week(from_day=date(2026, 9, 1))
        assert [task.what for task in found] == ["קרוב"]

    def test_tasks_may_be_asked_for_by_person(self, book):
        book.create(Kind.CALL_BACK, "א", assigned_to="דנה")
        book.create(Kind.CALL_BACK, "ב", assigned_to="יוסי")
        assert [task.what for task in book.for_person("דנה")] == ["א"]

    def test_everything_about_one_job_reads_as_its_own_story(self, book):
        book.chase_quote(_job("A"), sent_on=date(2026, 8, 2))
        book.chase_quote(_job("B"), sent_on=date(2026, 8, 2))
        assert len(book.about("job:A")) == len(CHASE_SCHEDULE)


class TestKeeping:
    def test_the_list_survives_a_round_trip_through_disk(self, tmp_path):
        book = TaskBook(tmp_path / "t.json")
        task = book.create(
            Kind.CHASE_PAYMENT, "לגבות", about="job:A",
            subject_name="אבי", assigned_to="דנה",
        )
        book.close(task.task_id, Outcome.POSTPONED, result="מבטיח לשלם")

        again = TaskBook(tmp_path / "t.json").load()
        kept = again.get(task.task_id)
        assert kept.kind is Kind.CHASE_PAYMENT
        assert kept.outcome is Outcome.POSTPONED
        assert kept.result == "מבטיח לשלם"
        assert kept.assigned_to == "דנה"

    def test_the_summary_counts_what_a_person_would_ask(self, book):
        book.create(Kind.CALL_BACK, "היום", due=date.today())
        book.create(Kind.CALL_BACK, "ישן", due=date.today() - timedelta(days=5))
        assert book.summary()["open"] == 2
        assert book.summary()["overdue"] == 1
