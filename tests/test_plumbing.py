"""Plumbing engine tests: hydraulics, sizing and network balancing."""

from __future__ import annotations

import math

import pytest

from profileos.core.errors import HydraulicsError
from profileos.plumbing import (
    COPPER_EN1057,
    STEEL_EN10255,
    DesignLimits,
    Fluid,
    Loop,
    Node,
    Pipe,
    PipeNetwork,
    ServiceType,
    colebrook_white,
    darcy_weisbach_loss,
    fitting_loss,
    friction_factor,
    hazen_williams_loss,
    pressure_to_head,
    reynolds_number,
    size_pipe,
    static_head,
    swamee_jain,
    total_k,
    velocity,
    water_at,
)


class TestFluidProperties:
    def test_water_at_20c_matches_the_table(self):
        water = water_at(20.0)
        assert water.density == pytest.approx(998.2)
        assert water.dynamic_viscosity == pytest.approx(1.002e-3)

    def test_interpolation_between_table_rows(self):
        water = water_at(25.0)
        assert 995.6 < water.density < 998.2

    def test_out_of_range_temperature_is_rejected(self):
        with pytest.raises(HydraulicsError):
            water_at(150.0)

    def test_kinematic_viscosity(self):
        water = water_at(20.0)
        assert water.kinematic_viscosity == pytest.approx(1.002e-3 / 998.2)


class TestFlowBasics:
    def test_velocity_from_continuity(self):
        # 10 L/s through a 100 mm bore.
        expected = 0.010 / (math.pi * 0.05**2)
        assert velocity(10.0, 100.0) == pytest.approx(expected)

    def test_zero_diameter_is_rejected(self):
        with pytest.raises(HydraulicsError):
            velocity(1.0, 0.0)

    def test_reynolds_number(self):
        re = reynolds_number(10.0, 100.0)
        assert re == pytest.approx(velocity(10.0, 100.0) * 0.1 / water_at(20.0).kinematic_viscosity, rel=1e-3)


class TestFrictionFactor:
    def test_laminar_uses_the_exact_law(self):
        f = colebrook_white(1e-4, 1000.0)
        assert f == pytest.approx(64.0 / 1000.0)

    @pytest.mark.parametrize(
        "relative,reynolds", [(1e-5, 1e5), (1e-4, 1e5), (1e-3, 1e6), (5e-2, 1e7)]
    )
    def test_colebrook_satisfies_its_own_equation(self, relative, reynolds):
        """The returned f must be a fixed point of Colebrook-White."""
        f = colebrook_white(relative, reynolds)
        residual = 1.0 / math.sqrt(f) + 2.0 * math.log10(
            relative / 3.7 + 2.51 / (reynolds * math.sqrt(f))
        )
        assert abs(residual) < 1e-6

    @pytest.mark.parametrize("relative,reynolds", [(1e-4, 1e5), (1e-3, 1e6), (5e-2, 1e7)])
    def test_swamee_jain_is_within_two_percent_of_colebrook(self, relative, reynolds):
        approximate = swamee_jain(relative, reynolds)
        exact = colebrook_white(relative, reynolds)
        assert abs(approximate - exact) / exact < 0.02

    def test_rougher_pipe_has_more_friction(self):
        smooth = colebrook_white(1e-5, 1e5)
        rough = colebrook_white(1e-2, 1e5)
        assert rough > smooth

    def test_friction_factor_matches_the_moody_chart(self):
        # Re about 1.3e5 on commercial steel (eps/D = 0.00045): f is about 0.0195.
        f, reynolds, regime = friction_factor(10.0, 100.0, 0.045)
        assert f == pytest.approx(0.0195, abs=0.001)
        assert regime.value == "turbulent"


class TestPressureLoss:
    def test_darcy_weisbach_scales_with_length(self):
        short = darcy_weisbach_loss(5.0, 50.0, 10.0)
        long = darcy_weisbach_loss(5.0, 50.0, 20.0)
        assert long == pytest.approx(2.0 * short)

    def test_loss_rises_steeply_with_flow(self):
        """Turbulent loss goes roughly as Q^2, so doubling flow near-quadruples it."""
        single = darcy_weisbach_loss(5.0, 50.0, 10.0)
        double = darcy_weisbach_loss(10.0, 50.0, 10.0)
        assert 3.5 < double / single < 4.2

    def test_zero_flow_means_zero_loss(self):
        assert darcy_weisbach_loss(0.0, 50.0, 10.0) == 0.0

    def test_negative_length_is_rejected(self):
        with pytest.raises(HydraulicsError):
            darcy_weisbach_loss(5.0, 50.0, -1.0)

    def test_hazen_williams_is_the_same_order_as_darcy(self):
        darcy = darcy_weisbach_loss(10.0, 100.0, 100.0, roughness_mm=0.045)
        hw = hazen_williams_loss(10.0, 100.0, 100.0, c_factor=130.0)
        assert 0.7 < hw / darcy < 1.5

    def test_fitting_loss_scales_with_k(self):
        one = fitting_loss(5.0, 50.0, 1.0)
        ten = fitting_loss(5.0, 50.0, 10.0)
        assert ten == pytest.approx(10.0 * one)

    def test_total_k_sums_the_schedule(self):
        assert total_k({"elbow_90_long": 4, "gate_valve_open": 1}) == pytest.approx(1.35)

    def test_unknown_fitting_is_rejected(self):
        with pytest.raises(HydraulicsError):
            total_k({"warp_drive": 1})

    def test_static_head_round_trips_through_pressure(self):
        pressure = static_head(15.0)
        assert pressure_to_head(pressure) == pytest.approx(15.0)


class TestPipeSizing:
    def test_bore_is_outer_minus_two_walls(self):
        size = COPPER_EN1057.by_designation("22 mm")
        assert size.internal_diameter == pytest.approx(22.0 - 2 * 0.9)

    def test_sizing_picks_the_smallest_adequate_size(self):
        result = size_pipe(0.3, 10.0, COPPER_EN1057, service=ServiceType.COLD_WATER)
        assert result.ok
        # Everything smaller must have been rejected for a stated reason.
        assert result.rejected
        smaller = [
            s for s in COPPER_EN1057.sorted_sizes()
            if s.internal_diameter < result.size.internal_diameter
        ]
        assert len(result.rejected) == len(smaller)

    def test_velocity_limit_is_respected(self):
        limits = DesignLimits(max_velocity=1.0, min_velocity=0.0, max_loss_per_m=1e9)
        result = size_pipe(2.0, 10.0, COPPER_EN1057, limits=limits)
        assert result.ok
        assert result.velocity <= 1.0

    def test_loss_limit_is_respected(self):
        limits = DesignLimits(max_velocity=10.0, min_velocity=0.0, max_loss_per_m=100.0)
        result = size_pipe(2.0, 10.0, COPPER_EN1057, limits=limits)
        assert result.ok
        assert result.loss_per_metre <= 100.0

    def test_low_velocity_warns_but_still_sizes(self):
        limits = DesignLimits(max_velocity=5.0, min_velocity=2.0, max_loss_per_m=1e9)
        result = size_pipe(0.05, 10.0, COPPER_EN1057, limits=limits)
        assert result.ok
        assert any("self-scouring" in r for r in result.reasons)

    def test_impossible_constraints_report_failure(self):
        result = size_pipe(
            50.0, 100.0, COPPER_EN1057,
            limits=DesignLimits(max_velocity=0.1, min_velocity=0.0, max_loss_per_m=1.0),
        )
        assert not result.ok
        assert result.reasons

    def test_available_pressure_constrains_the_choice(self):
        generous = size_pipe(1.0, 50.0, COPPER_EN1057, available_pressure=1e9)
        tight = size_pipe(1.0, 50.0, COPPER_EN1057, available_pressure=5000.0)
        assert generous.ok and tight.ok
        assert tight.size.internal_diameter >= generous.size.internal_diameter

    def test_static_lift_is_included_in_the_total(self):
        flat = size_pipe(1.0, 20.0, COPPER_EN1057, height_gain_m=0.0)
        lifted = size_pipe(1.0, 20.0, COPPER_EN1057, height_gain_m=10.0)
        assert lifted.static_loss > 0
        assert lifted.total_loss > flat.total_loss

    def test_zero_flow_is_rejected(self):
        with pytest.raises(HydraulicsError):
            size_pipe(0.0, 10.0, COPPER_EN1057)

    def test_water_content_per_metre(self):
        size = STEEL_EN10255.by_designation("DN50")
        # bore 53.1 mm -> area 2.215e-3 m^2 -> 2.215 L/m
        assert size.water_content() == pytest.approx(2.215, abs=0.01)


class TestNetwork:
    @pytest.fixture
    def network(self) -> PipeNetwork:
        net = PipeNetwork()
        for node_id, demand in [("A", -0.10), ("B", 0.03), ("C", 0.03), ("D", 0.04)]:
            net.add_node(Node(node_id=node_id, demand=demand))

        catalogue = STEEL_EN10255
        def size(name): return catalogue.by_designation(name)

        net.add_pipe(Pipe(pipe_id="AB", start="A", end="B", length=100, size=size("DN50"), catalogue=catalogue, flow=0.06))
        net.add_pipe(Pipe(pipe_id="BC", start="B", end="C", length=100, size=size("DN40"), catalogue=catalogue, flow=0.01))
        net.add_pipe(Pipe(pipe_id="CD", start="C", end="D", length=100, size=size("DN40"), catalogue=catalogue, flow=-0.02))
        net.add_pipe(Pipe(pipe_id="AD", start="A", end="D", length=100, size=size("DN50"), catalogue=catalogue, flow=0.04))
        net.add_pipe(Pipe(pipe_id="BD", start="B", end="D", length=80, size=size("DN32"), catalogue=catalogue, flow=0.02))
        net.add_loop(Loop(loop_id="L1", members=[("AB", 1), ("BD", 1), ("AD", -1)]))
        net.add_loop(Loop(loop_id="L2", members=[("BC", 1), ("CD", 1), ("BD", -1)]))
        return net

    def test_initial_flows_satisfy_continuity(self, network):
        assert all(abs(e) < 1e-9 for e in network.continuity_error().values())

    def test_solve_converges(self, network):
        result = network.solve()
        assert result.converged
        assert result.iterations < 200

    def test_continuity_is_preserved_by_the_solve(self, network):
        network.solve()
        assert all(abs(e) < 1e-9 for e in network.continuity_error().values())

    def test_loop_energy_balances(self, network):
        network.solve(tolerance=1e-9)
        for loop in network.loops:
            total = sum(
                network.pipes[pid].head_loss(network.fluid) * direction
                for pid, direction in loop.members
            )
            assert abs(total) < 0.1  # Pa, against losses of tens of Pa

    def test_duplicate_ids_are_rejected(self, network):
        with pytest.raises(HydraulicsError):
            network.add_node(Node(node_id="A"))

    def test_pipe_to_unknown_node_is_rejected(self, network):
        with pytest.raises(HydraulicsError):
            network.add_pipe(
                Pipe(pipe_id="XX", start="A", end="Z", length=10,
                     size=STEEL_EN10255.by_designation("DN50"), catalogue=STEEL_EN10255)
            )

    def test_loop_with_unknown_pipe_is_rejected(self, network):
        with pytest.raises(HydraulicsError):
            network.add_loop(Loop(loop_id="bad", members=[("NOPE", 1)]))

    def test_solve_without_loops_is_rejected(self):
        net = PipeNetwork()
        net.add_node(Node(node_id="A"))
        with pytest.raises(HydraulicsError):
            net.solve()

    def test_out_of_balance_start_is_refused(self, network):
        network.pipes["AB"].flow += 0.5  # break continuity
        with pytest.raises(HydraulicsError):
            network.solve()

    def test_node_heads_decrease_downstream(self, network):
        network.solve()
        heads = network.node_heads("A")
        assert heads["A"] == pytest.approx(0.0)
        assert all(h <= 1e-9 for h in heads.values())
