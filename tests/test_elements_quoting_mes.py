"""Tests for the opening builder, glazing, BOM/quoting and MES layers."""

from __future__ import annotations

import math

import pytest

from profileos.core.errors import ProfileOSError, QuotingError
from profileos.elements import (
    Cell,
    ElementBuilder,
    ElementKind,
    Opening,
    OpeningType,
    Sash,
    build_elements,
    collect_cut_items,
    get_system_rules,
    safety_glass_required,
)
from profileos.elements.builder import Rect
from profileos.glazing import (
    Cavity,
    GasType,
    GlassBuildUp,
    Pane,
    SpacerType,
    make_double_glazing,
    make_monolithic,
    make_triple_glazing,
    window_u_value,
)
from profileos.mes import (
    ItemKind,
    ProductionItem,
    Stage,
    TrackingCode,
    WorkOrder,
    code128_svg,
    render_job_card,
    work_order_from_builds,
)
from profileos.quoting import (
    BomCategory,
    PriceBreak,
    PriceEntry,
    PricingPolicy,
    Supplier,
    build_bom,
    build_quotation,
    register_supplier,
)


# --------------------------------------------------------------------------- #
# Glazing
# --------------------------------------------------------------------------- #

class TestGlazing:
    def test_monolithic_u_value_matches_the_published_figure(self):
        # 6 mm single glazing is universally quoted at about 5.7 W/m^2K.
        assert make_monolithic(6.0).u_value() == pytest.approx(5.7, abs=0.15)

    def test_uncoated_double_glazing(self):
        # 6/16 air/6 uncoated is about 2.7 W/m^2K.
        unit = make_double_glazing(6.0, 16.0, 6.0, gas=GasType.AIR, low_e_emissivity=0.837)
        assert unit.u_value() == pytest.approx(2.7, abs=0.15)

    def test_low_e_argon_double_glazing(self):
        # 6/16 argon/4 with a soft low-E coat is about 1.1 W/m^2K.
        assert make_double_glazing().u_value() == pytest.approx(1.1, abs=0.1)

    def test_triple_glazing_outperforms_double(self):
        assert make_triple_glazing().u_value() < make_double_glazing().u_value()
        assert make_triple_glazing().u_value() == pytest.approx(0.62, abs=0.1)

    def test_low_e_coating_dominates_the_improvement(self):
        coated = make_double_glazing(6.0, 16.0, 4.0, low_e_emissivity=0.03)
        uncoated = make_double_glazing(6.0, 16.0, 4.0, low_e_emissivity=0.837)
        assert coated.u_value() < uncoated.u_value() * 0.6

    def test_argon_beats_air(self):
        argon = make_double_glazing(6.0, 16.0, 4.0, gas=GasType.ARGON)
        air = make_double_glazing(6.0, 16.0, 4.0, gas=GasType.AIR)
        assert argon.u_value() < air.u_value()

    def test_thickness_and_mass(self):
        unit = make_double_glazing(6.0, 16.0, 4.0)
        assert unit.total_thickness == pytest.approx(26.0)
        # 10 mm of glass at 2500 kg/m^3 is 25 kg/m^2, plus a little gas.
        assert unit.mass_per_m2 == pytest.approx(25.0, abs=0.1)

    def test_pane_count_must_match_cavity_count(self):
        with pytest.raises(ValueError):
            GlassBuildUp(panes=[Pane(thickness=4.0), Pane(thickness=4.0)], cavities=[])

    def test_laminated_pane_needs_an_interlayer(self):
        with pytest.raises(ValueError):
            Pane(thickness=8.0, laminated=True, interlayer_thickness=0.0)

    def test_safety_glass_requires_both_outer_faces(self):
        one_side = GlassBuildUp(
            panes=[Pane(thickness=6.0, toughened=True), Pane(thickness=4.0)],
            cavities=[Cavity(width=16.0)],
        )
        both = GlassBuildUp(
            panes=[Pane(thickness=6.0, toughened=True), Pane(thickness=4.0, toughened=True)],
            cavities=[Cavity(width=16.0)],
        )
        assert not one_side.is_safety_glass
        assert both.is_safety_glass

    def test_window_u_value_lies_between_glass_and_frame(self):
        glass = make_double_glazing()
        u_w = window_u_value(
            glass, glass_area=1.5, frame_area=0.5, perimeter=5.0, frame_u_value=2.2
        )
        assert glass.u_value() < u_w < 2.6

    def test_warm_edge_spacer_beats_aluminium(self):
        warm = make_double_glazing(spacer=SpacerType.WARM_EDGE)
        alu = make_double_glazing(spacer=SpacerType.ALUMINIUM)
        args = dict(glass_area=1.5, frame_area=0.5, perimeter=5.0)
        assert window_u_value(warm, **args) < window_u_value(alu, **args)


# --------------------------------------------------------------------------- #
# Element builder
# --------------------------------------------------------------------------- #

class TestElementGeometry:
    def test_grid_size_follows_the_divisions(self):
        opening = Opening(
            name="W", width=3000.0, height=2000.0,
            mullion_positions=[1000.0, 2000.0], transom_positions=[1200.0],
        )
        assert opening.column_count == 3
        assert opening.row_count == 2

    def test_division_outside_the_element_is_rejected(self):
        with pytest.raises(ValueError):
            Opening(name="W", width=1000.0, height=1000.0, mullion_positions=[1500.0])

    def test_cell_outside_the_grid_is_rejected(self):
        with pytest.raises(ValueError):
            Opening(name="W", width=1000.0, height=1000.0, cells=[Cell(column=3, row=0)])

    def test_inner_opening_is_the_frame_face_inset(self):
        opening = Opening(name="W", width=2400.0, height=1800.0)
        rules = get_system_rules("generic")
        inner = ElementBuilder().inner_opening(opening, rules)
        assert inner.width == pytest.approx(2400.0 - 2 * rules.frame.face_width)
        assert inner.height == pytest.approx(1800.0 - 2 * rules.frame.face_width)

    def test_frame_wider_than_the_element_is_an_error(self):
        opening = Opening(name="tiny", width=50.0, height=50.0)
        with pytest.raises(ProfileOSError):
            ElementBuilder().build(opening)

    def test_cells_tile_the_inner_opening(self):
        opening = Opening(
            name="W", width=2400.0, height=1800.0, mullion_positions=[800.0, 1600.0]
        )
        rules = get_system_rules("generic")
        builder = ElementBuilder()
        inner = builder.inner_opening(opening, rules)
        rects = builder.cell_rects(opening, rules)

        # Cell widths plus the mullions they are separated by must span the inner opening.
        widths = sum(rects[(c, 0)].width for c in range(opening.column_count))
        mullion_total = len(opening.mullion_positions) * rules.mullion.face_width
        assert widths + mullion_total == pytest.approx(inner.width)


class TestElementBuild:
    @pytest.fixture
    def build(self):
        opening = Opening(
            name="W-04", width=2400.0, height=1800.0, quantity=2,
            mullion_positions=[800.0, 1600.0],
        )
        opening.set_cell(Cell(column=1, row=0, sash=Sash(opening_type=OpeningType.TILT_TURN)))
        return ElementBuilder().build(opening, sill_height=1000.0)

    def test_frame_is_four_mitred_members(self, build):
        frame = [c for c in build.cuts if c.role.startswith("frame")]
        assert sum(c.quantity for c in frame) == 4
        assert all(c.angle_left == 45.0 and c.angle_right == 45.0 for c in frame)

    def test_frame_members_are_the_element_dimensions(self, build):
        horizontals = next(c for c in build.cuts if c.role == "frame_horizontal")
        verticals = next(c for c in build.cuts if c.role == "frame_vertical")
        assert horizontals.length == pytest.approx(2400.0)
        assert verticals.length == pytest.approx(1800.0)

    def test_mullion_length_is_the_inner_height(self, build):
        mullion = next(c for c in build.cuts if c.role == "mullion")
        assert mullion.quantity == 2
        assert mullion.length == pytest.approx(1800.0 - 2 * 45.0)

    def test_one_pane_per_cell(self, build):
        assert len(build.glass) == build.opening.column_count * build.opening.row_count

    def test_sash_cell_glass_is_smaller_than_a_fixed_cell(self, build):
        sash_pane = next(p for p in build.glass if p.cell_key == (1, 0))
        fixed_pane = next(p for p in build.glass if p.cell_key == (0, 0))
        # The sash profile eats into the daylight opening.
        assert sash_pane.height < fixed_pane.height

    def test_glass_sizing_chain_is_consistent(self):
        """Pane size must follow daylight opening + 2*cover - 2*clearance."""
        opening = Opening(name="W", width=1200.0, height=1200.0)
        rules = get_system_rules("generic")
        builder = ElementBuilder()
        build = builder.build(opening)

        inner = builder.inner_opening(opening, rules)
        expected = inner.width - rules.glass.deduction()
        assert build.glass[0].width == pytest.approx(expected, abs=0.05)

    def test_operable_sash_gets_hardware(self, build):
        assert any(item.code == "HW-TT-KIT" for item in build.hardware)

    def test_tall_sash_gets_an_extra_hinge(self, build):
        assert any(item.code == "HW-HINGE-EXTRA" for item in build.hardware)

    def test_gaskets_are_generated_for_every_pane(self, build):
        inner_runs = [g for g in build.gaskets if g.code == "GK-IN"]
        assert len(inner_runs) == len(build.glass)

    def test_cut_items_scale_by_element_quantity(self, build):
        items = build.cut_items()
        frame = next(i for i in items if i.mark and "head/sill" in i.mark)
        assert frame.quantity == 2 * build.opening.quantity

    def test_summary_is_serialisable(self, build):
        summary = build.summary()
        assert summary["quantity"] == 2
        assert summary["glass_panes"] == len(build.glass) * 2


class TestSafetyGlass:
    def test_low_sill_requires_safety_glass(self):
        required, reason = safety_glass_required(Rect(0, 0, 800, 1500), sill_height=100.0)
        assert required and "critical height" in reason

    def test_high_small_pane_does_not(self):
        required, _ = safety_glass_required(Rect(0, 0, 600, 800), sill_height=1200.0)
        assert not required

    def test_large_pane_requires_safety_glass(self):
        required, reason = safety_glass_required(Rect(0, 0, 2000, 1500), sill_height=1500.0)
        assert required and "area" in reason

    def test_door_glazing_always_requires_safety_glass(self):
        required, reason = safety_glass_required(
            Rect(0, 0, 400, 400), sill_height=2000.0, is_door=True
        )
        assert required and "door" in reason

    def test_non_compliant_glass_is_reported(self):
        opening = Opening(name="D", kind=ElementKind.DOOR, width=1000.0, height=2200.0)
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.DOOR)))
        build = ElementBuilder().build(opening)
        assert build.non_compliant_glass
        assert any("safety glass" in w for w in build.warnings)


# --------------------------------------------------------------------------- #
# BOM and quoting
# --------------------------------------------------------------------------- #

class TestBom:
    @pytest.fixture
    def builds(self):
        opening = Opening(name="W", width=2000.0, height=1500.0, quantity=3)
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.CASEMENT)))
        return build_elements([opening])

    def test_categories_are_populated(self, builds):
        bom = build_bom(builds)
        categories = {line.category for line in bom.lines}
        assert BomCategory.PROFILE in categories
        assert BomCategory.GLASS in categories
        assert BomCategory.HARDWARE in categories

    def test_quantities_scale_with_element_quantity(self, builds):
        bom = build_bom(builds)
        handle = next(l for l in bom.lines if l.code == "HW-HANDLE")
        assert handle.quantity == 3.0

    def test_lines_with_the_same_key_merge(self, builds):
        bom = build_bom(builds)
        codes = [(l.category, l.code, l.unit) for l in bom.lines]
        assert len(codes) == len(set(codes))

    def test_unnested_bom_warns_about_offcut(self, builds):
        bom = build_bom(builds)
        assert any("finished length" in w for w in bom.warnings)

    def test_grouping_by_supplier(self, builds):
        bom = build_bom(builds)
        assert "unassigned" in bom.by_supplier()


class TestPricing:
    @pytest.fixture(autouse=True)
    def _suppliers(self):
        register_supplier(
            Supplier(
                id="test-alu", name="Test Aluminium", currency="EUR", categories=["profile"],
                entries=[PriceEntry(code=f"GEN-{n}", price=100.0, unit="m")
                         for n in ("FRAME", "SASH", "MULLION", "TRANSOM", "BEAD")],
            )
        )
        register_supplier(
            Supplier(
                id="test-glass", name="Test Glass", currency="EUR", categories=["glass"],
                entries=[PriceEntry(code="dgu-6-16-4", price=150.0, unit="m2",
                                    breaks=[PriceBreak(min_quantity=100.0, price=120.0)])],
            )
        )

    @pytest.fixture
    def builds(self):
        opening = Opening(name="W", width=2000.0, height=1500.0, quantity=2)
        return build_elements([opening])

    def test_quantity_break_applies(self):
        entry = PriceEntry(code="x", price=150.0, breaks=[PriceBreak(min_quantity=100.0, price=120.0)])
        assert entry.price_for(10.0) == 150.0
        assert entry.price_for(150.0) == 120.0

    def test_minimum_billable_quantity(self):
        entry = PriceEntry(code="x", price=10.0, minimum_quantity=0.5)
        assert entry.total_for(0.2) == pytest.approx(5.0)

    def test_discount_and_surcharge_compose(self):
        supplier = Supplier(
            id="s", name="S", discount_pct=10.0, surcharge_pct=5.0,
            entries=[PriceEntry(code="a", price=100.0)],
        )
        # 100 * 0.9 * 1.05
        assert supplier.net_price("a", 1.0) == pytest.approx(94.5)

    def test_quote_waterfall_is_ordered_and_consistent(self, builds):
        bom = build_bom(builds)
        quote = build_quotation(builds, bom, project_name="T", policy=PricingPolicy(margin_pct=25.0))
        assert quote.net_price > quote.total_cost
        assert quote.gross_price > quote.net_price
        # Margin on selling price: cost / (1 - margin)
        assert quote.net_price == pytest.approx(quote.total_cost / 0.75)

    def test_unpriced_codes_are_reported_not_zeroed(self, builds):
        bom = build_bom(builds)
        quote = build_quotation(builds, bom, project_name="T")
        assert quote.unpriced_codes
        assert any("no price" in w for w in quote.warnings)

    def test_margin_of_100_percent_is_rejected(self):
        with pytest.raises(QuotingError):
            PricingPolicy(margin_pct=100.0).apply_margin(1000.0)

    def test_labour_scales_with_content(self, builds):
        bom = build_bom(builds)
        quote = build_quotation(builds, bom)
        assert sum(quote.labour_hours.values()) > 0
        assert quote.labour_cost > 0


# --------------------------------------------------------------------------- #
# MES
# --------------------------------------------------------------------------- #

class TestBarcodes:
    def test_code128_checksum(self):
        from profileos.mes.barcode import _CODE128_PATTERNS, _code128b_values

        values = _code128b_values("ABC")
        expected = (104 + 33 * 1 + 34 * 2 + 35 * 3) % 103
        assert values[-2] == expected

    @pytest.mark.parametrize("data", ["A", "PC-101", "POS|PRJ|W-04|PC-101|CUT"])
    def test_code128_module_count_matches_the_spec(self, data):
        from profileos.mes.barcode import _CODE128_PATTERNS, _code128b_values

        values = _code128b_values(data)
        modules = sum(int(d) for v in values for d in _CODE128_PATTERNS[v])
        # start + data + checksum at 11 modules each, plus a 13-module stop.
        assert modules == (len(data) + 2) * 11 + 13

    def test_non_ascii_is_rejected(self):
        with pytest.raises(ProfileOSError):
            code128_svg("café")

    def test_svg_is_produced(self):
        svg = code128_svg("PC-101")
        assert svg.startswith("<svg") and "<rect" in svg

    def test_tracking_code_round_trip(self):
        code = TrackingCode(project="P", element="E", piece="PC1", stage="CUT")
        assert TrackingCode.parse(code.payload()) == code

    def test_bad_payload_is_rejected(self):
        with pytest.raises(ProfileOSError):
            TrackingCode.parse("not-a-code")


class TestProductionTracking:
    @pytest.fixture
    def order(self):
        opening = Opening(name="W", width=2000.0, height=1500.0, quantity=1)
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.CASEMENT)))
        return work_order_from_builds(build_elements([opening]), project_id="P1", name="T")

    def test_work_order_covers_pieces_panes_and_elements(self, order):
        assert order.by_kind(ItemKind.PROFILE_PIECE)
        assert order.by_kind(ItemKind.GLASS_PANE)
        assert len(order.by_kind(ItemKind.ELEMENT)) == 1

    def test_valid_transition_is_accepted(self):
        item = ProductionItem(item_id="X", kind=ItemKind.PROFILE_PIECE)
        assert item.advance(Stage.CUT)[0]
        assert item.stage is Stage.CUT

    def test_stage_skip_is_refused_with_a_reason(self):
        item = ProductionItem(item_id="X", kind=ItemKind.PROFILE_PIECE)
        ok, reason = item.advance(Stage.SHIPPED)
        assert not ok and "אי אפשר לעבור" in reason

    def test_terminal_stage_cannot_move(self):
        item = ProductionItem(item_id="X", kind=ItemKind.PROFILE_PIECE)
        item.advance(Stage.SCRAPPED)
        ok, reason = item.advance(Stage.CUT)
        assert not ok and "לא יכול לזוז" in reason

    def test_rework_returns_to_an_earlier_stage(self):
        item = ProductionItem(item_id="X", kind=ItemKind.PROFILE_PIECE)
        item.advance(Stage.CUT)
        item.advance(Stage.MACHINED)
        assert item.advance(Stage.REWORK)[0]
        assert item.advance(Stage.MACHINED)[0]

    def test_history_is_append_only(self):
        item = ProductionItem(item_id="X", kind=ItemKind.PROFILE_PIECE)
        item.advance(Stage.CUT, operator="Dana")
        assert len(item.history) == 2
        assert item.history[-1].operator == "Dana"

    def test_scan_of_unknown_code_is_reported(self, order):
        ok, message = order.scan("NOPE", Stage.CUT)
        assert not ok and "קוד לא מוכר" in message

    def test_scan_advances_by_barcode(self, order):
        item = order.by_kind(ItemKind.PROFILE_PIECE)[0]
        ok, message = order.scan(item.barcode, Stage.CUT, operator="Dana")
        assert ok and item.stage is Stage.CUT

    def test_bottleneck_finds_the_busiest_stage(self, order):
        for item in order.by_kind(ItemKind.PROFILE_PIECE)[:3]:
            item.advance(Stage.CUT)
        stage, count = order.bottleneck()
        assert stage is Stage.CUT and count == 3

    def test_progress_advances_monotonically(self):
        item = ProductionItem(item_id="X", kind=ItemKind.ELEMENT)
        previous = item.progress()
        for stage in (Stage.CUT, Stage.MACHINED, Stage.ASSEMBLED, Stage.GLAZED):
            item.advance(stage)
            assert item.progress() > previous
            previous = item.progress()


class TestJobCard:
    def test_renders_a_self_contained_document(self):
        opening = Opening(name="W", width=2000.0, height=1500.0)
        opening.set_cell(Cell(column=0, row=0, sash=Sash(opening_type=OpeningType.CASEMENT)))
        builds = build_elements([opening])
        order = work_order_from_builds(builds, project_id="P1", name="T")

        document = render_job_card(order, builds)
        assert document.startswith("<!doctype html>")
        assert "<style>" in document  # styles inlined, not linked
        assert "http://" not in document.replace("http://www.w3.org", "")
        assert "Cut list" in document
        assert "Assembly sequence" in document
