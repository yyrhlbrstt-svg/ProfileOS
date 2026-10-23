"""Fixtures, drainage, hot water and the take-off.

These are the stages between "how many taps" and "what do I buy", which is
where a plumbing office actually spends its day.
"""

from __future__ import annotations

import pytest

from profileos.plumbing import (
    DeadLeg,
    DrainageError,
    FixtureError,
    FixtureSchedule,
    HotWaterError,
    PipeRun,
    ServiceType,
    SupplyKind,
    circulation_flow,
    demand_flow,
    design_circulation,
    design_drainage,
    get_catalogue,
    heat_loss_per_metre,
    size_horizontal_drain,
    size_stack,
    size_vent,
    take_off,
    typical_dwelling,
)


class TestDemand:
    def test_no_fixtures_draw_no_water(self):
        assert demand_flow(0) == 0.0

    def test_demand_rises_with_loading_but_far_more_slowly(self):
        """The whole point of the curve: ten times the taps is not ten times the water."""
        small = demand_flow(20)
        large = demand_flow(200)
        assert large > small
        assert large < small * 10

    def test_flush_valves_draw_more_than_cisterns_at_the_same_loading(self):
        assert demand_flow(100, SupplyKind.VALVE) > demand_flow(100, SupplyKind.TANK)

    def test_the_curve_is_monotonic(self):
        flows = [demand_flow(units) for units in range(0, 1200, 25)]
        assert flows == sorted(flows)

    def test_past_the_table_it_keeps_going_rather_than_stopping(self):
        assert demand_flow(9000) > demand_flow(5000)

    def test_negative_loading_is_refused(self):
        with pytest.raises(FixtureError):
            demand_flow(-1)


class TestSchedule:
    def test_a_typical_dwelling_has_both_sides_counted(self):
        schedule = typical_dwelling()
        assert schedule.cold_lu > 0 and schedule.hot_lu > 0 and schedule.dfu > 0

    def test_adding_the_same_fixture_twice_merges_the_line(self):
        schedule = FixtureSchedule().add("basin", 2).add("basin", 3)
        assert len(schedule.lines) == 1
        assert schedule.lines[0].quantity == 5

    def test_the_combined_main_is_not_the_two_branches_added(self):
        """Cold and hot peaks do not coincide; adding them oversizes the main."""
        schedule = typical_dwelling(6)
        assert schedule.total_demand() < schedule.cold_demand() + schedule.hot_demand()

    def test_repeating_a_flat_scales_every_line(self):
        one = typical_dwelling()
        eight = one.repeated(8)
        assert eight.dfu == pytest.approx(one.dfu * 8)
        assert eight.fixture_count == one.fixture_count * 8

    def test_the_largest_trap_governs_the_branch(self):
        assert typical_dwelling().largest_trap == 100.0

    def test_an_unknown_fixture_names_what_is_available(self):
        with pytest.raises(FixtureError) as error:
            FixtureSchedule().add("jacuzzi-for-elephants")
        assert "wc-tank" in str(error.value.context.get("known", ""))


class TestDrainage:
    def test_a_wc_branch_is_never_smaller_than_100(self):
        result = size_horizontal_drain(4.0, fall=0.02, largest_trap_mm=100.0,
                                       serves_wc=True)
        assert result.size_mm == 100.0
        assert result.governed_by != "table"

    def test_a_branch_is_never_smaller_than_its_largest_trap(self):
        result = size_horizontal_drain(2.0, fall=0.02, largest_trap_mm=75.0)
        assert result.size_mm >= 75.0

    def test_more_fall_carries_more_units(self):
        gentle = size_horizontal_drain(200.0, fall=0.01)
        steep = size_horizontal_drain(200.0, fall=0.04)
        assert steep.size_mm <= gentle.size_mm

    def test_a_fall_between_columns_is_read_down_not_up(self):
        result = size_horizontal_drain(100.0, fall=0.035)
        assert result.fall == 0.02
        assert any("צד הבטוח" in note for note in result.notes)

    def test_fifty_millimetre_drains_are_refused_at_one_percent(self):
        result = size_horizontal_drain(10.0, fall=0.01)
        assert result.size_mm and result.size_mm > 50.0

    def test_a_stack_is_limited_by_one_floor_as_well_as_the_whole_load(self):
        spread = size_stack(400.0, branch_dfu=40.0)
        concentrated = size_stack(400.0, branch_dfu=180.0)
        assert concentrated.size_mm > spread.size_mm
        assert concentrated.governed_by == "branch"

    def test_a_floor_cannot_carry_more_than_the_whole_stack(self):
        with pytest.raises(DrainageError):
            size_stack(50.0, branch_dfu=80.0)

    def test_a_vent_is_never_smaller_than_half_its_drain(self):
        result = size_vent(20.0, 10.0, drain_mm=100.0)
        assert result.size_mm >= 50.0

    def test_a_longer_vent_needs_a_bigger_pipe(self):
        short = size_vent(40.0, 20.0)
        long = size_vent(40.0, 80.0)
        assert long.size_mm > short.size_mm

    def test_a_whole_block_sizes_to_what_a_plumber_would_specify(self):
        design = design_drainage(typical_dwelling(8), floors=4, fall=0.02,
                                 vent_length_m=25.0)
        assert design.ok
        assert design.branch.size_mm == 100.0
        assert design.stack.size_mm == 100.0
        assert design.drain.size_mm >= design.stack.size_mm, "never reduce downstream"

    def test_the_house_drain_never_narrows_below_the_stack(self):
        design = design_drainage(typical_dwelling(2), floors=1, house_drain_fall=0.04)
        assert design.drain.size_mm >= design.stack.size_mm

    def test_zero_floors_is_refused(self):
        with pytest.raises(DrainageError):
            design_drainage(typical_dwelling(), floors=0)


class TestHotWater:
    def test_insulation_cuts_the_loss_by_the_order_a_plumber_expects(self):
        bare = heat_loss_per_metre(28.0, insulation_mm=0.0)
        lagged = heat_loss_per_metre(28.0, insulation_mm=25.0)
        assert lagged < bare / 3.0

    def test_thicker_insulation_always_loses_less(self):
        losses = [heat_loss_per_metre(22.0, insulation_mm=t) for t in (10, 20, 30, 40)]
        assert losses == sorted(losses, reverse=True)

    def test_a_smaller_temperature_drop_needs_more_flow(self):
        assert circulation_flow(2000.0, delta_t=2.0) > circulation_flow(2000.0, delta_t=10.0)

    def test_a_zero_drop_is_refused(self):
        with pytest.raises(HotWaterError):
            circulation_flow(1000.0, delta_t=0.0)

    def test_a_loop_sizes_its_return_and_its_pump(self):
        design = design_circulation(120.0, 28.0, get_catalogue("copper-en1057"),
                                    insulation_mm=25.0)
        assert design.return_size is not None
        assert 0 < design.flow_lps < 1.0
        assert design.pump_watts < 100.0, "a circulation pump is a small pump"

    def test_a_bare_hot_line_is_called_out_against_the_standard(self):
        design = design_circulation(60.0, 28.0, get_catalogue("copper-en1057"),
                                    insulation_mm=0.0)
        assert any("1205" in note for note in design.notes)

    def test_a_long_dead_leg_fails_the_design_and_says_how_long_the_wait_is(self):
        leg = DeadLeg("מטבח", 9.0, 20.0)
        design = design_circulation(80.0, 28.0, get_catalogue("copper-en1057"),
                                    insulation_mm=25.0, dead_legs=[leg])
        assert not leg.ok and leg.wait_seconds > 20
        assert not design.ok

    def test_a_short_dead_leg_passes(self):
        assert DeadLeg("מקלחת", 3.0, 16.0).ok


class TestTakeoff:
    @pytest.fixture
    def runs(self):
        return [
            PipeRun(ServiceType.COLD_WATER, "28 mm", 46.0, "copper",
                    fittings={"elbow_90_long": 14}, valves=3),
            PipeRun(ServiceType.HOT_WATER, "22 mm", 38.0, "copper",
                    insulation_mm=25.0),
            PipeRun(ServiceType.DRAINAGE, "110 mm", 24.0, "pvc"),
        ]

    def test_pipe_is_ordered_in_stock_lengths_not_metres(self, runs):
        takeoff = take_off(runs)
        copper = next(line for line in takeoff.lines if "28 mm" in line.description)
        # 46 m plus waste, in 5 m lengths, is eleven lengths.
        assert "11 יחידות" in copper.note

    def test_waste_is_named_rather_than_folded_into_the_quantity(self, runs):
        takeoff = take_off(runs, waste_pct=15.0)
        copper = next(line for line in takeoff.lines if "28 mm" in line.description)
        assert copper.quantity == 46.0, "the measured length stays measured"
        assert "15%" in copper.note

    def test_insulation_is_counted_for_the_runs_that_carry_it(self, runs):
        takeoff = take_off(runs)
        assert any(line.kind == "בידוד" and line.quantity > 38 for line in takeoff.lines)

    def test_a_bare_hot_run_becomes_a_finding_not_a_silent_omission(self):
        takeoff = take_off([PipeRun(ServiceType.HOT_WATER, "15 mm", 12.0)])
        bare = next(line for line in takeoff.lines if "ללא בידוד" in line.description)
        assert bare.quantity == 12.0 and "לנמק" in bare.note

    def test_the_fixtures_are_on_the_list_too(self, runs):
        takeoff = take_off(runs, schedule=typical_dwelling(2))
        assert any(line.kind == "כלים סניטריים" for line in takeoff.lines)

    def test_negative_waste_is_refused(self, runs):
        from profileos.plumbing import TakeoffError

        with pytest.raises(TakeoffError):
            take_off(runs, waste_pct=-5.0)
