"""Tests for the 2D sheet-nesting engine.

The interesting property of a glass cutting plan is not its yield but whether
it can be cut at all, so most of what is checked here is manufacturability
against hand-derived answers rather than against recorded output.
"""

from __future__ import annotations

import pytest

from profileos.core.errors import InfeasibleNestingError, NestingError
from profileos.nesting.guillotine import (
    find_outside,
    find_overlaps,
    guillotine_stages,
    pack_guillotine,
    pack_strips,
    verify_guillotine,
)
from profileos.nesting.sheet import (
    FreeRectRule,
    Grain,
    PlacedPart,
    SheetLayout,
    SheetPart,
    SheetSpec,
    SheetStock,
    SplitRule,
    aggregate_parts,
)
from profileos.nesting.sheet_engine import (
    build_sheet_problem,
    nest_project_glass,
    nest_sheets,
)
from profileos.nesting.sheet_exact import solve_exact_2stage
from profileos.nesting.sheet_render import cutting_list, render_layout_svg

FLUSH = SheetSpec(kerf=0.0, edge_trim=0.0)


def layout_of(width: float, height: float, boxes: list[tuple]) -> SheetLayout:
    """Build a layout from ``(name, x, y, w, h)`` tuples for verifier tests."""
    layout = SheetLayout(0, SheetStock(width, height), FLUSH)
    layout.placements = [
        PlacedPart(SheetPart(name, w, h), x, y, w, h) for name, x, y, w, h in boxes
    ]
    return layout


# --------------------------------------------------------------------------- #
# The verifier
# --------------------------------------------------------------------------- #
class TestGuillotineVerification:
    def test_pinwheel_is_rejected(self):
        """The classic counter-example: perfect area use, impossible to cut.

        Four 2x1 rectangles rotating around a 1x1 hole in a 3x3 square. No
        edge-to-edge line separates any two of them, so no cutting table can
        produce it however good the numbers look.
        """
        layout = layout_of(
            3, 3, [("A", 0, 0, 2, 1), ("B", 2, 0, 1, 2), ("C", 1, 2, 2, 1), ("D", 0, 1, 1, 2)]
        )
        assert find_overlaps(layout) == []
        assert find_outside(layout) == []
        assert guillotine_stages(layout) is None
        problems = verify_guillotine(layout, FLUSH)
        assert len(problems) == 1
        assert "no edge-to-edge cutting sequence" in problems[0]

    @pytest.mark.parametrize(
        "name,width,height,boxes,expected",
        [
            # One part filling the sheet needs no cut at all.
            ("exact single", 2, 1, [("a", 0, 0, 2, 1)], 0),
            # A row spanning the sheet: one run of parallel cuts.
            (
                "exact row",
                6,
                1,
                [("a", 0, 0, 2, 1), ("b", 2, 0, 2, 1), ("c", 4, 0, 2, 1)],
                1,
            ),
            # The same row on a taller sheet: every part now needs a trim.
            (
                "row plus trim",
                6,
                2,
                [("a", 0, 0, 2, 1), ("b", 2, 0, 2, 1), ("c", 4, 0, 2, 1)],
                2,
            ),
            # One small part: trimmed in both directions.
            ("single part, two trims", 6, 2, [("a", 0, 0, 2, 1)], 2),
            # Strips then pieces.
            (
                "grid",
                4,
                2,
                [
                    ("a", 0, 0, 2, 1),
                    ("b", 2, 0, 2, 1),
                    ("c", 0, 1, 2, 1),
                    ("d", 2, 1, 2, 1),
                ],
                2,
            ),
        ],
    )
    def test_stage_counts(self, name, width, height, boxes, expected):
        assert guillotine_stages(layout_of(width, height, boxes)) == expected

    def test_strip_stack_short_of_the_edge_is_still_two_stages(self):
        """A trim continues the run it follows, so it costs no extra stage.

        Two strips of 1100 stacked on a 2220 sheet stop 20 mm short. Charging
        that trim as a third stage would wrongly condemn an ordinary layout on
        a two-stage machine.
        """
        layout = layout_of(
            3180,
            2220,
            [
                ("b1", 0, 0, 1400, 1100),
                ("b2", 1400, 0, 1400, 1100),
                ("b3", 0, 1100, 1400, 1100),
                ("b4", 1400, 1100, 1400, 1100),
            ],
        )
        assert guillotine_stages(layout) == 2

    def test_overlap_is_caught(self):
        layout = layout_of(10, 10, [("a", 0, 0, 5, 5), ("b", 4, 4, 5, 5)])
        assert find_overlaps(layout) == [("a", "b")]
        assert "overlap" in verify_guillotine(layout, FLUSH)[0]

    def test_part_off_the_sheet_is_caught(self):
        layout = layout_of(10, 10, [("a", 8, 0, 5, 5)])
        assert find_outside(layout) == ["a"]

    def test_stage_limit_is_enforced(self):
        two_stage = SheetSpec(kerf=0.0, edge_trim=0.0, stages=2)
        assert verify_guillotine(layout_of(6, 2, [("a", 0, 0, 2, 1)]), two_stage) == []

        # Two parts of different heights inside one strip: the shorter one
        # needs a trim after the rip cuts, which is a third turn. A two-stage
        # line cannot produce this, and saying so is the point of the check.
        three_stage = layout_of(
            6, 3, [("a", 0, 0, 2, 2), ("b", 2, 0, 2, 1), ("c", 0, 2, 6, 1)]
        )
        assert guillotine_stages(three_stage) == 3
        problems = verify_guillotine(three_stage, two_stage)
        assert any("cutting stages" in message for message in problems)


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #
class TestPacking:
    def test_perfect_tiling_reaches_full_yield(self):
        """A jumbo divides exactly into six panes; anything short of 100% is a bug."""
        problem = build_sheet_problem(
            "clear-6", [SheetPart("P", 1070.0, 1125.0, 6)], spec=FLUSH
        )
        result = nest_sheets(problem)
        assert result.sheet_count == 1
        assert result.yield_pct == pytest.approx(100.0)
        assert result.optimal is True
        assert result.warnings == []

    def test_every_shipped_layout_is_verified(self):
        parts = [
            SheetPart("LR", 1480.0, 2180.0, 4),
            SheetPart("bed", 1180.0, 1380.0, 6),
            SheetPart("bath", 580.0, 880.0, 3),
        ]
        result = nest_sheets(
            build_sheet_problem("igu", parts, spec=SheetSpec(edge_trim=20.0))
        )
        assert result.warnings == []
        assert result.total_pieces == 13
        for layout in result.layouts:
            assert verify_guillotine(layout, result.spec) == []
            assert layout.stages_used is not None

    def test_grain_locked_parts_are_never_rotated(self):
        parts = [SheetPart("printed", 600.0, 900.0, 8, grain=Grain.VERTICAL)]
        result = nest_sheets(build_sheet_problem("printed", parts, spec=FLUSH))
        placements = [p for layout in result.layouts for p in layout.placements]
        assert placements
        assert not any(p.rotated for p in placements)

    def test_rotation_switch_overrides_free_parts(self):
        spec = SheetSpec(kerf=0.0, edge_trim=0.0, allow_rotation=False)
        part = SheetPart("free", 600.0, 900.0)
        assert spec.orientations(part) == [(600.0, 900.0, False)]

    def test_kerf_is_charged_between_parts(self):
        """Five 640 mm parts fit a 3210 sheet dry but not once the blade is paid."""
        stock = SheetStock(3210.0, 400.0)
        dry = SheetSpec(kerf=0.0, edge_trim=0.0)
        wet = SheetSpec(kerf=5.0, edge_trim=0.0)
        parts = list(SheetPart("p", 640.0, 400.0, 5).expand())
        layout_dry, left_dry = pack_guillotine(parts, stock, dry)
        layout_wet, left_wet = pack_guillotine(parts, stock, wet)
        assert len(layout_dry.placements) == 5 and left_dry == []
        assert len(layout_wet.placements) == 4 and len(left_wet) == 1

    def test_two_stage_packer_produces_two_stage_plans(self):
        parts = [
            SheetPart("a", 900.0, 600.0, 7),
            SheetPart("b", 1400.0, 1100.0, 5),
            SheetPart("c", 500.0, 400.0, 9),
        ]
        spec = SheetSpec(kerf=0.0, edge_trim=15.0, stages=2)
        result = nest_sheets(build_sheet_problem("panel", parts, spec=spec))
        assert result.warnings == []
        assert all(layout.stages_used <= 2 for layout in result.layouts)

    def test_third_stage_books_the_band_above_a_short_part(self):
        """A part shorter than its strip leaves a reachable off-cut, not scrap."""
        spec = SheetSpec(kerf=0.0, edge_trim=0.0, stages=3, min_offcut_side=200.0,
                         min_offcut_area=100_000.0)
        stock = SheetStock(2000.0, 1000.0)
        parts = [SheetPart("tall", 800.0, 900.0), SheetPart("short", 800.0, 400.0)]
        layout, leftovers = pack_strips(parts, stock, spec, third_stage=True)
        assert leftovers == []
        bands = [r for r in layout.free_rects if r.y >= 400.0 - 1e-6 and r.x >= 800.0 - 1e-6]
        assert bands, "the band above the short part should be recorded"

    def test_offcuts_below_the_threshold_are_not_booked(self):
        spec = SheetSpec(kerf=0.0, edge_trim=0.0, min_offcut_side=500.0)
        result = nest_sheets(
            build_sheet_problem(
                "m", [SheetPart("p", 3000.0, 2000.0)],
                stock=[SheetStock(3210.0, 2250.0)], spec=spec,
            )
        )
        # The leftovers are 210 mm and 250 mm wide: real material, but nothing
        # a fabricator would put back in the rack.
        assert result.reusable_offcuts() == []


# --------------------------------------------------------------------------- #
# Bounds and optimality
# --------------------------------------------------------------------------- #
class TestBounds:
    def test_area_bound_is_never_beaten(self):
        parts = [SheetPart("p", 1000.0, 1000.0, 30)]
        problem = build_sheet_problem("m", parts, spec=FLUSH)
        result = nest_sheets(problem, exact=False)
        assert result.sheet_count >= problem.area_lower_bound()

    def test_large_pieces_force_their_own_sheets(self):
        """Three grain-locked panes over half the sheet in both axes.

        The area bound says two; geometry says three. The engine must report
        three and prove it, which it can only do with the large-piece bound.
        """
        parts = [SheetPart("B", 2000.0, 1400.0, 3, grain=Grain.VERTICAL)]
        problem = build_sheet_problem("m", parts, spec=SheetSpec())
        assert problem.area_lower_bound() == 2
        assert problem.large_piece_lower_bound() == 3
        result = nest_sheets(problem)
        assert result.sheet_count == 3
        assert result.optimal is True

    def test_rotation_defeats_the_large_piece_bound(self):
        """Turned 90 degrees the same pane fits beside another, so it must not count."""
        parts = [SheetPart("B", 2000.0, 1400.0, 3)]
        problem = build_sheet_problem("m", parts, spec=SheetSpec())
        assert problem.large_piece_lower_bound() == 0

    def test_optimality_claim_matches_the_stage_class_proved(self):
        """CP-SAT proves a two/three-stage optimum, not an unlimited-stage one."""
        parts = [
            SheetPart("LR", 1480.0, 2180.0, 4),
            SheetPart("bed", 1180.0, 1380.0, 6),
            SheetPart("bath", 580.0, 880.0, 3),
        ]
        loose = nest_sheets(
            build_sheet_problem("g", parts, spec=SheetSpec(edge_trim=20.0))
        )
        strict = nest_sheets(
            build_sheet_problem("g", parts, spec=SheetSpec(edge_trim=20.0, stages=3))
        )
        assert loose.sheet_count == strict.sheet_count
        # Same plan, different strength of claim.
        assert strict.optimal is True
        assert loose.optimal is False
        assert loose.metadata["optimal_within_stage_limit"] is True


class TestExactSolver:
    def test_exact_matches_a_hand_computed_optimum(self):
        """Eight 1600x1125 panes tile two jumbos exactly: two sheets, no waste."""
        problem = build_sheet_problem(
            "m", [SheetPart("p", 1605.0, 1125.0, 8)], spec=FLUSH
        )
        layouts, stats = solve_exact_2stage(problem, time_limit_s=30.0)
        assert stats.proven_optimal is True
        assert stats.sheets == 2
        assert layouts is not None and len(layouts) == 2
        for layout in layouts:
            assert verify_guillotine(layout, problem.spec) == []

    def test_exact_declines_mixed_stock_with_a_reason(self):
        problem = build_sheet_problem(
            "m",
            [SheetPart("p", 500.0, 500.0, 4)],
            stock=[SheetStock(3210.0, 2250.0), SheetStock(2500.0, 1800.0)],
            spec=FLUSH,
        )
        layouts, stats = solve_exact_2stage(problem)
        assert layouts is None
        assert stats.status == "not_applicable"
        assert "single stock size" in (stats.reason or "")

    def test_exact_declines_oversized_instances_with_a_reason(self):
        problem = build_sheet_problem("m", [SheetPart("p", 300.0, 300.0, 200)], spec=FLUSH)
        layouts, stats = solve_exact_2stage(problem)
        assert layouts is None
        assert stats.status == "too_large"


# --------------------------------------------------------------------------- #
# Model plumbing
# --------------------------------------------------------------------------- #
class TestModel:
    def test_infeasible_part_is_refused_with_the_numbers(self):
        with pytest.raises(InfeasibleNestingError) as excinfo:
            build_sheet_problem("m", [SheetPart("huge", 4000.0, 4000.0)], spec=SheetSpec())
        message = str(excinfo.value)
        assert "huge" in message and "4000x4000" in message

    def test_negative_dimensions_are_refused(self):
        with pytest.raises(NestingError):
            SheetPart("bad", -1.0, 100.0)
        with pytest.raises(NestingError):
            SheetSpec(kerf=-1.0)
        with pytest.raises(NestingError):
            SheetSpec(stages=1)

    def test_identical_parts_aggregate(self):
        merged = aggregate_parts(
            [
                SheetPart("a", 500.0, 400.0, 2, label="vent"),
                SheetPart("b", 500.0, 400.0, 3, label="vent"),
            ]
        )
        assert len(merged) == 1 and merged[0].quantity == 5

    def test_same_size_different_parts_stay_apart(self):
        """A kitchen pane and a bathroom pane of one size are still two parts.

        Merging them would put the wrong label on the wrong piece of glass at
        the table, which is worse than a slightly larger cutting list.
        """
        merged = aggregate_parts(
            [SheetPart("kitchen", 500.0, 400.0, 2), SheetPart("bath", 500.0, 400.0, 3)]
        )
        assert len(merged) == 2

    def test_square_parts_offer_one_orientation(self):
        assert len(SheetPart("sq", 500.0, 500.0).orientations()) == 1

    def test_expand_gives_one_part_per_piece(self):
        pieces = list(SheetPart("p", 100.0, 200.0, 3).expand())
        assert [p.part_id for p in pieces] == ["p#1", "p#2", "p#3"]

    def test_project_nesting_keeps_build_ups_apart(self):
        report = nest_project_glass(
            {
                "6/16Ar/6": [SheetPart("a", 1000.0, 1000.0, 3)],
                "8/16Ar/8-lam": [SheetPart("b", 1200.0, 900.0, 2)],
            },
            spec=FLUSH,
        )
        assert set(report.results) == {"6/16Ar/6", "8/16Ar/8-lam"}
        assert report.sheet_count == 2
        assert report.warnings == []


class TestReporting:
    def test_svg_is_self_contained_and_labelled(self):
        result = nest_sheets(
            build_sheet_problem(
                "m", [SheetPart("pane", 1000.0, 800.0, 4)], spec=SheetSpec(edge_trim=20.0)
            )
        )
        svg = render_layout_svg(result.layouts[0])
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "http://www.w3.org/2000/svg" in svg
        # No external reference of any kind may appear in a shop-floor print.
        assert "<image" not in svg and "xlink:href" not in svg
        assert "pane" in svg and "yield" in svg

    def test_svg_escapes_part_labels(self):
        layout = layout_of(1000, 1000, [('<script>&"', 0, 0, 500, 500)])
        svg = render_layout_svg(layout)
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg

    def test_cutting_list_covers_every_piece(self):
        result = nest_sheets(
            build_sheet_problem("m", [SheetPart("p", 900.0, 700.0, 9)], spec=FLUSH)
        )
        rows = cutting_list(result)
        assert len(rows) == 9
        assert {row["sheet"] for row in rows} <= set(range(1, result.sheet_count + 1))

    def test_summary_is_json_safe(self):
        import json

        result = nest_sheets(
            build_sheet_problem("m", [SheetPart("p", 900.0, 700.0, 4)], spec=FLUSH)
        )
        json.dumps(result.summary())
        json.dumps([layout.summary() for layout in result.layouts])
