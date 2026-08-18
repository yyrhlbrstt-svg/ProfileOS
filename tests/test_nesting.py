"""Nesting engine tests: kerf/mitre maths, packing, and the exact solver."""

from __future__ import annotations

import math

import pytest

from profileos.core.errors import InfeasibleNestingError, NestingError
from profileos.models.orders import CutItem, Project, RemnantBar
from profileos.nesting import (
    CutSpec,
    LengthReference,
    NestingProblem,
    RemnantInventory,
    StockDefinition,
    aggregate_demand,
    best_fit_decreasing,
    build_problem,
    cot_deg,
    first_fit_decreasing,
    nest,
    nest_project,
    ortools_available,
    waste_from_angles,
)


def _pieces(*specs):
    """Build pieces from ``(length, quantity, angle_left, angle_right)`` tuples."""
    items = [
        CutItem(
            profile_id="P",
            length=length,
            quantity=quantity,
            angle_left=al,
            angle_right=ar,
        )
        for length, quantity, al, ar in specs
    ]
    return Project(name="t", items=items).expand_pieces("P")


def _problem(pieces, stock=(6000.0,), kerf=0.0, depth=0.0, trim=0.0):
    spec = CutSpec(kerf=kerf, profile_depth=depth, trim_start=trim, trim_end=0.0)
    return NestingProblem(
        profile_id="P",
        demands=aggregate_demand(pieces, spec),
        stock=[StockDefinition(length=s) for s in stock],
        cut_spec=spec,
    )


class TestCotangent:
    def test_square_cut_is_exactly_zero(self):
        assert cot_deg(90.0) == 0.0

    @pytest.mark.parametrize("angle,expected", [(45.0, 1.0), (135.0, -1.0), (60.0, 1 / math.sqrt(3))])
    def test_known_values(self, angle, expected):
        assert cot_deg(angle) == pytest.approx(expected)

    def test_parallel_cut_is_rejected(self):
        with pytest.raises(NestingError):
            cot_deg(180.0)


class TestKerfAndMitre:
    def test_square_cut_adds_only_the_kerf(self):
        spec = CutSpec(kerf=3.5, profile_depth=100.0)
        assert spec.effective_length(1000.0, 90.0, 90.0) == pytest.approx(1003.5)

    def test_centreline_reference_adds_half_depth_per_end(self):
        """At 45 degrees cot = 1, so each end adds H/2."""
        spec = CutSpec(kerf=3.5, profile_depth=100.0, reference=LengthReference.CENTRELINE)
        assert spec.effective_length(1000.0, 45.0, 45.0) == pytest.approx(1103.5)

    def test_outer_reference_adds_nothing_for_the_mitre(self):
        spec = CutSpec(kerf=3.5, profile_depth=100.0, reference=LengthReference.OUTER)
        assert spec.effective_length(1000.0, 45.0, 45.0) == pytest.approx(1003.5)

    def test_inner_reference_adds_the_full_depth_per_end(self):
        spec = CutSpec(kerf=3.5, profile_depth=100.0, reference=LengthReference.INNER)
        assert spec.effective_length(1000.0, 45.0, 45.0) == pytest.approx(1203.5)

    def test_obtuse_and_acute_mitres_cost_the_same(self):
        """135 degrees is the mirror of 45; |cot| makes them symmetric."""
        spec = CutSpec(kerf=0.0, profile_depth=80.0)
        assert spec.effective_length(1000.0, 45.0, 90.0) == pytest.approx(
            spec.effective_length(1000.0, 135.0, 90.0)
        )

    def test_net_length_inverts_effective_length(self):
        spec = CutSpec(kerf=3.5, profile_depth=100.0)
        effective = spec.effective_length(1234.5, 45.0, 135.0)
        assert spec.net_length(effective, 45.0, 135.0) == pytest.approx(1234.5)

    def test_zero_depth_means_no_mitre_allowance(self):
        spec = CutSpec(kerf=0.0, profile_depth=0.0)
        assert spec.effective_length(1000.0, 45.0, 45.0) == pytest.approx(1000.0)

    def test_waste_from_angles(self):
        assert waste_from_angles(100.0, 45.0, 45.0) == pytest.approx(100.0)
        assert waste_from_angles(100.0, 90.0, 90.0) == pytest.approx(0.0)

    def test_trims_reduce_the_usable_bar(self):
        spec = CutSpec(trim_start=10.0, trim_end=5.0)
        assert spec.usable_length(6000.0) == pytest.approx(5985.0)

    def test_trims_larger_than_the_bar_are_rejected(self):
        spec = CutSpec(trim_start=4000.0, trim_end=4000.0)
        with pytest.raises(NestingError):
            spec.usable_length(6000.0)


class TestDemandAggregation:
    def test_identical_sizes_collapse(self):
        pieces = _pieces((1000.0, 5, 90.0, 90.0))
        demands = aggregate_demand(pieces, CutSpec(kerf=0.0))
        assert len(demands) == 1
        assert demands[0].quantity == 5

    def test_different_angles_stay_separate(self):
        pieces = _pieces((1000.0, 2, 90.0, 90.0), (1000.0, 2, 45.0, 45.0))
        demands = aggregate_demand(pieces, CutSpec(kerf=0.0, profile_depth=50.0))
        assert len(demands) == 2

    def test_demands_are_ordered_longest_first(self):
        pieces = _pieces((500.0, 1, 90.0, 90.0), (2000.0, 1, 90.0, 90.0))
        demands = aggregate_demand(pieces, CutSpec(kerf=0.0))
        assert demands[0].length == 2000.0


class TestFeasibility:
    def test_piece_longer_than_every_bar_is_rejected(self):
        with pytest.raises(InfeasibleNestingError):
            _problem(_pieces((7000.0, 1, 90.0, 90.0)))

    def test_piece_made_too_long_by_its_mitre_is_rejected(self):
        """5980 fits a 6000 bar square, but not with two 45 degree mitres."""
        with pytest.raises(InfeasibleNestingError):
            _problem(_pieces((5980.0, 1, 45.0, 45.0)), depth=100.0)


class TestHeuristics:
    def test_exact_fit_wastes_nothing(self):
        problem = _problem(_pieces((3000.0, 2, 90.0, 90.0)))
        layouts, unplaced = first_fit_decreasing(problem)
        assert not unplaced
        assert len(layouts) == 1
        assert layouts[0].remnant_length == pytest.approx(0.0)

    def test_every_piece_is_placed(self):
        problem = _problem(_pieces((2400.0, 3, 90.0, 90.0), (1500.0, 3, 90.0, 90.0)))
        layouts, unplaced = best_fit_decreasing(problem)
        assert not unplaced
        assert sum(l.piece_count for l in layouts) == 6

    def test_kerf_is_charged_per_piece(self):
        """Three 1999 mm pieces plus 3 mm kerf each just exceed one 6000 bar."""
        problem = _problem(_pieces((1999.0, 3, 90.0, 90.0)), kerf=3.0)
        layouts, _ = first_fit_decreasing(problem)
        assert len(layouts) == 2

    def test_limited_stock_leaves_pieces_unplaced(self):
        # trim_start defaults to 10 mm; zero it so the arithmetic is exact.
        spec = CutSpec(kerf=0.0, trim_start=0.0, trim_end=0.0)
        problem = NestingProblem(
            profile_id="P",
            demands=aggregate_demand(_pieces((3000.0, 4, 90.0, 90.0)), spec),
            stock=[StockDefinition(length=6000.0, available=1)],
            cut_spec=spec,
        )
        layouts, unplaced = first_fit_decreasing(problem)
        assert len(layouts) == 1
        assert sum(line.quantity for line in unplaced) == 2


@pytest.mark.skipif(not ortools_available(), reason="OR-Tools not installed")
class TestExactSolver:
    def test_reaches_the_lower_bound_on_a_textbook_instance(self):
        problem = _problem(
            _pieces((2400.0, 3, 90.0, 90.0), (1500.0, 3, 90.0, 90.0), (1200.0, 3, 90.0, 90.0))
        )
        result = nest(problem, strategy="milp")
        assert result.bar_count == problem.lower_bound_bars() == 3
        assert result.optimal

    def test_does_not_over_produce(self):
        """Equality demand plus a surplus penalty must cut exactly what is ordered."""
        problem = _problem(
            _pieces((2400.0, 3, 90.0, 90.0), (1500.0, 3, 90.0, 90.0), (1200.0, 3, 90.0, 90.0))
        )
        result = nest(problem, strategy="milp")

        produced: dict[float, int] = {}
        for layout in result.layouts:
            for placement in layout.placements:
                produced[placement.demand_key.length] = (
                    produced.get(placement.demand_key.length, 0) + 1
                )
        assert produced == {2400.0: 3, 1500.0: 3, 1200.0: 3}

    def test_never_worse_than_the_heuristic(self):
        pieces = _pieces(
            (1850.0, 7, 90.0, 90.0), (1240.0, 11, 90.0, 90.0), (860.0, 9, 90.0, 90.0)
        )
        problem = _problem(pieces, kerf=3.5)
        heuristic_layouts, _ = first_fit_decreasing(problem)
        exact = nest(problem, strategy="milp")
        assert exact.bar_count <= len(heuristic_layouts)

    def test_yield_excludes_kerf_and_trim(self):
        problem = _problem(_pieces((3000.0, 2, 90.0, 90.0)), kerf=0.0)
        result = nest(problem, strategy="milp")
        assert result.yield_pct == pytest.approx(100.0)


class TestRemnants:
    def test_long_offcut_becomes_reusable_inventory(self):
        problem = _problem(_pieces((2000.0, 1, 90.0, 90.0)))
        result = nest(problem, strategy="ffd")
        remnants = result.to_remnants(threshold=300.0)
        assert len(remnants) == 1
        assert remnants[0].length == pytest.approx(4000.0)

    def test_short_offcut_is_scrap_not_inventory(self):
        problem = _problem(_pieces((5900.0, 1, 90.0, 90.0)))
        result = nest(problem, strategy="ffd")
        assert result.to_remnants(threshold=300.0) == []
        assert result.scrap_length(300.0) == pytest.approx(100.0)

    def test_inventory_round_trip(self, tmp_path):
        store = RemnantInventory(tmp_path / "inv.json")
        store.add(RemnantBar(profile_id="P", length=2500.0))
        store.add(RemnantBar(profile_id="P", length=1800.0))
        store.save()

        reloaded = RemnantInventory(tmp_path / "inv.json")
        assert len(reloaded) == 2
        assert reloaded.stats("P").longest == pytest.approx(2500.0)

    def test_remnants_are_offered_before_fresh_stock(self):
        store = RemnantInventory()
        store.add(RemnantBar(profile_id="P", length=2500.0))
        problem = build_problem(
            "P", _pieces((2000.0, 1, 90.0, 90.0)), stock_lengths=[6000.0],
            inventory=store, kerf=0.0, profile_depth=0.0,
        )
        result = nest(problem, strategy="ffd")
        # The 2000 mm piece fits the 2500 mm remnant, so no fresh bar is opened.
        assert result.layouts[0].is_remnant
        assert result.full_bar_count == 0

    def test_inventory_consume(self):
        store = RemnantInventory()
        store.add(RemnantBar(profile_id="P", length=2500.0, remnant_id="R1"))
        assert store.consume("R1")
        assert len(store) == 0
        assert not store.consume("R1")


class TestProjectLevel:
    def test_multiple_profiles_are_nested_independently(self):
        project = Project(
            name="Tower A",
            items=[
                CutItem(profile_id="MUL", length=2400.0, quantity=4),
                CutItem(profile_id="TRA", length=1200.0, quantity=6),
            ],
        )
        report = nest_project(project)
        assert set(report.results) == {"MUL", "TRA"}
        assert report.total_bars >= 2
        assert 0.0 < report.overall_yield_pct <= 100.0

    def test_report_summary_is_serialisable(self):
        project = Project(name="x", items=[CutItem(profile_id="P", length=1000.0, quantity=3)])
        summary = nest_project(project).summary()
        assert summary["profiles"] == 1
        assert "yield_pct" in summary
