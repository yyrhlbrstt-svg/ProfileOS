"""What happens after the window is in: calls back, cheques, and the margin."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from profileos.core.errors import ProfileOSError
from profileos.erp.collection import Cheque, ChequeBook, ChequeState
from profileos.projects import JobFile
from profileos.projects.costing import cost_job, portfolio
from profileos.service import (
    CallState,
    Cause,
    ServiceCall,
    ServiceRegister,
    Severity,
    Symptom,
    warranty_expires,
)


@pytest.fixture
def register(tmp_path):
    return ServiceRegister(tmp_path / "service.json")


class TestWarranty:
    def test_cover_runs_from_handover_not_from_the_call(self):
        assert warranty_expires(date(2025, 3, 1), "fabrication") == date(2027, 3, 1)

    def test_a_sealed_unit_is_covered_far_longer_than_the_frame(self):
        assert (
            warranty_expires(date(2025, 1, 1), "sealed_unit")
            > warranty_expires(date(2025, 1, 1), "fabrication")
        )

    def test_breakage_is_never_covered_and_says_so(self):
        assert warranty_expires(date(2025, 1, 1), "glass_breakage") is None

    def test_the_end_of_a_long_month_does_not_overflow(self):
        assert warranty_expires(date(2025, 8, 31), "screen") == date(2026, 8, 31)

    def test_a_call_claims_against_the_right_component(self):
        misted = ServiceCall(symptom=Symptom.MISTED_UNIT, delivered=date(2022, 1, 1))
        assert misted.component == "sealed_unit"
        assert misted.under_warranty is True

    def test_an_unknown_handover_date_is_not_the_same_as_no_cover(self):
        """Three-valued on purpose: a shop that says no here argues with a customer."""
        assert ServiceCall(symptom=Symptom.WATER).under_warranty is None


class TestCalls:
    def test_the_symptom_sets_how_fast_somebody_goes_out(self):
        assert ServiceCall(symptom=Symptom.BROKEN_GLASS).severity is Severity.BLOCKING
        assert ServiceCall(symptom=Symptom.FINISH).severity is Severity.COSMETIC

    def test_a_blocking_call_is_due_sooner_than_a_cosmetic_one(self):
        opened = date(2026, 1, 5)
        blocking = ServiceCall(symptom=Symptom.BROKEN_GLASS, opened=opened)
        cosmetic = ServiceCall(symptom=Symptom.FINISH, opened=opened)
        assert blocking.due_by() < cosmetic.due_by()

    def test_an_open_call_past_its_date_is_overdue(self):
        call = ServiceCall(symptom=Symptom.WATER, opened=date(2026, 1, 1))
        assert call.is_overdue(date(2026, 1, 20))

    def test_a_closed_call_is_never_overdue(self):
        call = ServiceCall(symptom=Symptom.WATER, opened=date(2026, 1, 1))
        call.close(date(2026, 1, 2), Cause.INSTALLATION)
        assert not call.is_overdue(date(2027, 1, 1))
        assert call.state is CallState.DONE

    def test_closing_records_what_it_turned_out_to_be(self):
        call = ServiceCall(symptom=Symptom.DROPPED)
        call.close(date(2026, 1, 2), Cause.MANUFACTURE, minutes=90, engineer="דני")
        assert call.cause.is_ours
        assert call.minutes_spent == 90

    def test_who_pays_is_decided_by_the_cause(self):
        assert Cause.MANUFACTURE.is_ours and not Cause.MANUFACTURE.is_chargeable
        assert Cause.CUSTOMER.is_chargeable and not Cause.CUSTOMER.is_ours


class TestTheRegister:
    def _log(self, register, symptom, cause, *, minutes=60, charged=0.0, day=None):
        call = ServiceCall(
            job_id="J-1", customer_name="לקוח", symptom=Symptom(symptom),
            opened=day or date(2026, 1, 1),
        )
        call.close(
            (day or date(2026, 1, 1)) + timedelta(days=1), Cause(cause),
            minutes=minutes, charged=charged,
        )
        return register.add(call)

    def test_calls_survive_the_program_closing(self, tmp_path):
        first = ServiceRegister(tmp_path / "s.json")
        first.add(ServiceCall(customer_name="משה", symptom=Symptom.WATER))
        assert len(ServiceRegister(tmp_path / "s.json")) == 1

    def test_a_fault_seen_three_times_becomes_a_pattern(self, register):
        for _ in range(3):
            self._log(register, "dropped", "manufacture")
        self._log(register, "water", "building")
        recurring = register.recurring(minimum=3)
        assert len(recurring) == 1
        assert "כנף צנחה" in recurring[0][0]

    def test_going_back_is_counted_by_whose_fault_it_was(self, register):
        self._log(register, "dropped", "manufacture", minutes=120)
        self._log(register, "water", "customer", minutes=60, charged=350)
        quality = register.cost_of_quality()
        assert quality["hours_our_fault"] == 2.0
        assert quality["hours_chargeable"] == 1.0
        assert quality["recovered"] == 350

    def test_response_is_measured_against_what_was_promised(self, register):
        self._log(register, "water", "installation")
        performance = register.response_performance()
        assert performance["closed"] == 1
        assert performance["within_target"] == 100.0

    def test_open_and_overdue_are_separate_questions(self, register):
        register.add(ServiceCall(symptom=Symptom.WATER, opened=date(2026, 1, 1)))
        self._log(register, "finish", "wear")
        assert len(register.open_calls()) == 1
        assert len(register.overdue(date(2026, 2, 1))) == 1

    def test_calls_can_be_read_back_by_job_and_by_customer(self, register):
        self._log(register, "water", "installation")
        assert register.for_job("J-1")
        assert register.for_customer("לקוח")
        assert not register.for_job("J-999")


class TestCheques:
    def test_a_post_dated_cheque_cannot_be_banked_yet(self):
        cheque = Cheque(customer="כהן", amount=1000, due=date(2026, 12, 1))
        assert not cheque.is_bankable(date(2026, 8, 21))
        with pytest.raises(ProfileOSError):
            cheque.deposit(date(2026, 8, 21))

    def test_it_can_be_banked_on_the_day_written_on_it(self):
        cheque = Cheque(customer="כהן", amount=1000, due=date(2026, 8, 21))
        cheque.deposit(date(2026, 8, 21))
        assert cheque.state is ChequeState.DEPOSITED

    def test_a_cheque_is_not_money_until_it_clears(self):
        cheque = Cheque(customer="כהן", amount=1000, due=date(2026, 1, 1))
        assert not cheque.state.is_money and cheque.state.is_expected
        cheque.deposit(date(2026, 1, 2)).clear(date(2026, 1, 3))
        assert cheque.state.is_money

    def test_a_cheque_of_nothing_is_refused(self):
        with pytest.raises(ProfileOSError):
            Cheque(customer="כהן", amount=0)

    def test_the_drawer_knows_what_could_be_banked_this_morning(self):
        today = date(2026, 8, 21)
        book = ChequeBook([
            Cheque(customer="א", amount=1000, due=today - timedelta(days=1)),
            Cheque(customer="ב", amount=2000, due=today + timedelta(days=40)),
        ])
        assert len(book.bankable(today)) == 1
        assert book.in_hand == 3000

    def test_a_bounced_cheque_leaves_the_expected_money(self):
        book = ChequeBook([Cheque(customer="א", amount=1000, due=date(2026, 1, 1))])
        assert book.in_hand == 1000
        list(book)[0].bounce(date(2026, 1, 5), "אין כיסוי")
        assert book.in_hand == 0
        assert book.summary()["bounced"] == 1000

    def test_a_customer_whose_cheques_come_back_is_named(self):
        book = ChequeBook()
        for _ in range(2):
            book.add(Cheque(customer="קבלן", amount=5000, due=date(2026, 1, 1))).bounce()
        book.add(Cheque(customer="כהן", amount=5000, due=date(2026, 1, 1)))
        risky = book.risky_customers(minimum=2)
        assert risky == [("קבלן", 2, 100.0)]
        assert book.bounce_rate("כהן") == 0.0

    def test_the_forecast_buckets_by_week_and_stops_at_the_horizon(self):
        today = date(2026, 8, 23)
        book = ChequeBook([
            Cheque(customer="א", amount=1000, due=today + timedelta(days=30 * i),
                   received=today)
            for i in range(6)
        ])
        flow = book.cash_flow(today, weeks=12)
        assert all(day.weekday() == 6 for day, _ in flow)  # Sundays
        assert len(flow) < 6

    def test_how_long_the_shop_waits_is_measured(self):
        today = date(2026, 8, 21)
        book = ChequeBook([
            Cheque(customer="א", amount=1000, received=today,
                   due=today + timedelta(days=60)),
            Cheque(customer="א", amount=1000, received=today,
                   due=today + timedelta(days=120)),
        ])
        assert book.average_days_out() == 90.0


class TestJobCosting:
    class _Quote:
        net_price = 48000.0
        cost = 31000.0
        material_cost = 22000.0
        labour_cost = 9000.0

    def _job(self):
        return JobFile(job_id="J-2026-0007", name="דירה", customer_name="כהן")

    def test_a_job_with_no_movement_says_so_rather_than_reporting_zero(self):
        costing = cost_job(self._job())
        assert "אין תנועה" in costing.verdict()

    def test_the_quoted_margin_comes_from_the_quotation(self):
        costing = cost_job(self._job(), quotation=self._Quote())
        assert costing.quoted == 48000.0
        assert costing.quoted_margin == 17000.0
        assert costing.quoted_margin_pct == pytest.approx(35.4, abs=0.1)

    def test_estimates_standing_in_for_actuals_are_declared(self):
        costing = cost_job(self._job(), quotation=self._Quote())
        assert costing.has_estimates_standing_in
        assert any("אומדן" in warning for warning in costing.warnings)

    def test_going_back_to_site_comes_off_the_margin(self, register):
        call = ServiceCall(job_id="J-2026-0007", symptom=Symptom.DROPPED)
        call.close(date(2026, 3, 3), Cause.MANUFACTURE, minutes=240)
        register.add(call)

        without = cost_job(self._job(), quotation=self._Quote())
        with_service = cost_job(self._job(), quotation=self._Quote(), service=register)
        assert with_service.actual_cost > without.actual_cost

    def test_a_visit_the_customer_pays_for_does_not(self, register):
        call = ServiceCall(job_id="J-2026-0007", symptom=Symptom.SCREEN)
        call.close(date(2026, 3, 3), Cause.CUSTOMER, minutes=240, charged=400)
        register.add(call)
        costing = cost_job(self._job(), quotation=self._Quote(), service=register)
        assert costing.invoiced == 400.0
        assert "חזרות" not in " ".join(line.hebrew for line in costing.lines)

    def test_a_job_in_the_red_is_said_plainly(self):
        costing = cost_job(self._job(), quotation=self._Quote())
        costing.invoiced = 20000.0
        assert costing.is_losing
        assert "הפסד" in costing.verdict()

    def test_the_portfolio_puts_the_worst_job_first(self):
        jobs = [self._job(), JobFile(job_id="J-2", name="שנייה", customer_name="לוי")]
        ranked = portfolio(jobs)
        assert len(ranked) == 2
        assert ranked[0].margin_pct <= ranked[1].margin_pct
