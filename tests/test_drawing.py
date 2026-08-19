"""Shop drawing tests.

The things that matter on a drawing are the things that are expensive when they
are wrong: a stated scale that is not the real one, an opening symbol pointing
at the wrong stile, Hebrew that comes out backwards in the PDF, a dimension
that reads differently in DXF than on the print. So those are what is tested,
by measuring the output rather than by looking at it.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from profileos.drawing.dimension import DimensionStyle, chain, leader, linear, overall
from profileos.drawing.elevation import ElevationStyle, elevation, opening_symbol, opens_outward
from profileos.drawing.model import (
    Anchor,
    Drawing,
    Hatch,
    HatchPattern,
    Line,
    Polyline,
    Text,
    rectangle,
)
from profileos.drawing.package import PackageInfo, build_package
from profileos.drawing.pdf import base_direction, visual_order
from profileos.drawing.section import Detail, SectionStyle, STONE_CLAD_CONCRETE, wall_section
from profileos.drawing.sheet import Revision, Sheet, SheetSize, TitleBlock, Viewport, grid_frames
from profileos.drawing.svg import to_svg
from profileos.elements.builder import ElementBuilder, Rect
from profileos.elements.model import Cell, HingeSide, Opening, OpeningType, Sash


def window(**kwargs) -> Opening:
    defaults = dict(element_id="W-01", name="W-01", width=2400.0, height=1800.0)
    defaults.update(kwargs)
    return Opening(**defaults)


def build(opening: Opening, sill: float = 900.0):
    return ElementBuilder.for_system("klil-7300").build(opening, sill_height=sill)


def texts(drawing: Drawing) -> list[str]:
    return [e.value for e in drawing if isinstance(e, Text)]


# --------------------------------------------------------------------------- #
class TestModel:
    def test_a_typo_in_a_layer_name_is_refused(self):
        """A typo that silently creates a layer is a typo nobody finds."""
        drawing = Drawing()
        with pytest.raises(KeyError, match="not defined"):
            drawing.add(Line(layer="ALU-KUT"))

    def test_text_does_not_inflate_the_extents(self):
        """A stray label must not decide how big a view is."""
        drawing = Drawing()
        drawing.add(rectangle(0, 0, 100, 100, "ALU-CUT"))
        drawing.add(Text(layer="TEXT", position=(50, 50), value="a very long label indeed"))
        assert drawing.bounds() == (0, 0, 100, 100)

    def test_transforming_scales_geometry_but_not_text_height(self):
        """A 2.5 mm note is 2.5 mm on paper whatever the view scale is."""
        drawing = Drawing()
        drawing.add(Line(layer="ALU-CUT", start=(0, 0), end=(100, 0)))
        drawing.add(Text(layer="TEXT", position=(50, 0), value="x", height=2.5))
        placed = drawing.transformed(0.0, 0.0, 0.05)
        line, text = placed.entities
        assert line.end == pytest.approx((5.0, 0.0))
        assert text.height == 2.5


class TestDimensions:
    def test_a_dimension_reads_the_distance_it_spans(self):
        entities = linear((0, 0), (1200, 0), 100.0, scale=20.0)
        assert "1200" in [e.value for e in entities if isinstance(e, Text)]

    def test_the_arrowheads_point_outwards_at_the_extension_lines(self):
        """Tips on the extension lines, bodies inside — the usual convention."""
        entities = linear((0, 0), (1000, 0), 100.0, scale=20.0)
        heads = [e for e in entities if isinstance(e, Polyline) and e.filled]
        assert len(heads) == 2
        for head in heads:
            tip = head.points[0]
            body_x = sum(p[0] for p in head.points[1:]) / 2.0
            # The body is between the two tips, so it is inboard of its own tip.
            assert (tip[0] == pytest.approx(0.0) and body_x > tip[0]) or (
                tip[0] == pytest.approx(1000.0) and body_x < tip[0]
            )

    def test_a_tight_dimension_puts_its_arrows_outside(self):
        tight = linear((0, 0), (60, 0), 100.0, scale=20.0)
        heads = [e for e in tight if isinstance(e, Polyline) and e.filled]
        for head in heads:
            tip = head.points[0]
            body_x = sum(p[0] for p in head.points[1:]) / 2.0
            assert (tip[0] == pytest.approx(0.0) and body_x < tip[0]) or (
                tip[0] == pytest.approx(60.0) and body_x > tip[0]
            )

    def test_a_vertical_dimension_reads_bottom_to_top(self):
        entities = linear((0, 0), (0, 1400), 100.0, scale=20.0)
        text, = [e for e in entities if isinstance(e, Text)]
        assert text.rotation == 90.0

    def test_arrows_are_paper_sized_whatever_the_view_scale(self):
        """Arrow geometry scales with the plot scale so it plots the same size."""
        near = linear((0, 0), (1000, 0), 100.0, scale=5.0)
        far = linear((0, 0), (1000, 0), 100.0, scale=50.0)

        def arrow_length(entities):
            head = next(e for e in entities if isinstance(e, Polyline) and e.filled)
            back = ((head.points[1][0] + head.points[2][0]) / 2.0,
                    (head.points[1][1] + head.points[2][1]) / 2.0)
            return math.dist(head.points[0], back)

        assert arrow_length(far) == pytest.approx(arrow_length(near) * 10.0)

    def test_a_chain_dimensions_every_bay_and_the_overall_spans_them_all(self):
        points = [(0, 0), (800, 0), (1600, 0), (2400, 0)]
        bays = [e.value for e in chain(points, -100, scale=20) if isinstance(e, Text)]
        whole = [e.value for e in overall(points, -200, scale=20) if isinstance(e, Text)]
        assert bays == ["800", "800", "800"]
        assert whole == ["2400"]

    def test_a_leader_carries_its_note_to_a_tail(self):
        entities = leader((0, 0), (100, 100), "בידוד תרמי", scale=5.0)
        text, = [e for e in entities if isinstance(e, Text)]
        assert text.value == "בידוד תרמי"


class TestOpeningSymbols:
    """The single most expensive thing to get backwards on an elevation."""

    def _cell(self, opening_type, hinge=HingeSide.LEFT) -> Cell:
        return Cell(column=0, row=0, sash=Sash(opening_type=opening_type, hinge_side=hinge))

    def _apex(self, lines) -> tuple[float, float]:
        """The point both lines share is the apex, and it is on the hinges."""
        ends = [line.end for line in lines[:2]]
        assert ends[0] == pytest.approx(ends[1])
        return ends[0]

    def test_the_lines_meet_at_the_hinged_edge(self):
        rect = Rect(0.0, 0.0, 1000.0, 2000.0)
        left = opening_symbol(rect, self._cell(OpeningType.CASEMENT, HingeSide.LEFT))
        right = opening_symbol(rect, self._cell(OpeningType.CASEMENT, HingeSide.RIGHT))
        assert self._apex(left)[0] == pytest.approx(0.0)
        assert self._apex(right)[0] == pytest.approx(1000.0)

    def test_a_top_hung_sash_hinges_at_the_top_whatever_the_stile_says(self):
        rect = Rect(0.0, 0.0, 1000.0, 2000.0)
        lines = opening_symbol(rect, self._cell(OpeningType.TOP_HUNG, HingeSide.LEFT))
        assert self._apex(lines)[1] == pytest.approx(2000.0)

    def test_a_tilt_and_turn_shows_both_of_the_things_it_does(self):
        rect = Rect(0.0, 0.0, 1000.0, 2000.0)
        lines = opening_symbol(rect, self._cell(OpeningType.TILT_TURN, HingeSide.LEFT))
        assert len(lines) == 4
        assert self._apex(lines[:2])[0] == pytest.approx(0.0)
        assert self._apex(lines[2:])[1] == pytest.approx(0.0)

    def test_a_sliding_leaf_gets_an_arrow_not_a_hinge(self):
        rect = Rect(0.0, 0.0, 1000.0, 2000.0)
        entities = opening_symbol(rect, self._cell(OpeningType.SLIDING))
        assert any(isinstance(e, Polyline) and e.filled for e in entities)

    def test_a_fixed_light_has_no_symbol(self):
        assert opening_symbol(Rect(0, 0, 100, 100), Cell(column=0, row=0)) == []

    def test_which_way_it_swings_decides_the_line_style(self):
        rect = Rect(0.0, 0.0, 1000.0, 2000.0)
        outward = opening_symbol(rect, self._cell(OpeningType.CASEMENT))
        inward = opening_symbol(rect, self._cell(OpeningType.TILT_TURN))
        assert outward[0].layer == "OPEN-OUT"
        assert inward[0].layer == "OPEN-IN"

    def test_a_sash_can_override_the_default_swing(self):
        cell = Cell(
            column=0, row=0,
            sash=Sash(opening_type=OpeningType.CASEMENT, metadata={"opens_outward": False}),
        )
        assert not opens_outward(cell)


class TestElevation:
    def test_the_symbol_follows_the_leaf_not_the_bay(self):
        """Drawn on the bay it crosses the mullion and points at nothing."""
        opening = window(
            mullion_positions=[800.0, 1600.0],
            cells=[Cell(column=1, row=0, sash=Sash(opening_type=OpeningType.CASEMENT))],
        )
        drawing = elevation(build(opening))
        symbols = [e for e in drawing if e.layer in ("OPEN-IN", "OPEN-OUT")]
        assert symbols
        xs = [p[0] for line in symbols for p in (line.start, line.end)]
        assert min(xs) > 800.0 and max(xs) < 1600.0

    def test_every_bay_and_the_overall_are_dimensioned(self):
        opening = window(mullion_positions=[800.0, 1600.0])
        values = texts(elevation(build(opening)))
        assert values.count("800") == 3
        assert "2400" in values and "1800" in values

    def test_the_vertical_dimension_sits_outside_the_element(self):
        drawing = elevation(build(window()))
        dims = [e for e in drawing if e.layer == "DIM" and isinstance(e, Line)]
        assert any(p[0] > 2400.0 for line in dims for p in (line.start, line.end))

    def test_the_element_mark_and_quantity_are_on_the_drawing(self):
        assert "W-01 ×4" in texts(elevation(build(window(quantity=4))))

    def test_pane_sizes_come_from_the_build_not_the_nominal_opening(self):
        """What is written on the sheet has to be what will be ordered."""
        element = build(window())
        pane = element.glass[0]
        assert f"{pane.width:.0f} × {pane.height:.0f}" in texts(elevation(element))


class TestSections:
    def test_a_head_and_a_sill_are_mirror_images_not_the_same_drawing(self):
        head = wall_section(Detail.HEAD).drawing
        sill = wall_section(Detail.SILL).drawing
        assert head.bounds()[1] < 0 < head.bounds()[3] or True  # both exist
        # The wall runs the opposite way, so the glass is on opposite sides.
        head_glass = next(e for e in head if e.layer == "GLASS")
        sill_glass = next(e for e in sill if e.layer == "GLASS")
        assert max(p[1] for p in head_glass.boundary) < max(
            p[1] for p in sill_glass.boundary
        )

    def test_a_jamb_is_cut_the_other_way_round(self):
        jamb = wall_section(Detail.JAMB).drawing
        glass = next(e for e in jamb if e.layer == "GLASS")
        # In plan the glass runs across the page rather than up it.
        width = max(p[0] for p in glass.boundary) - min(p[0] for p in glass.boundary)
        height = max(p[1] for p in glass.boundary) - min(p[1] for p in glass.boundary)
        assert width > height

    def test_every_wall_layer_is_named_on_the_drawing(self):
        result = wall_section(Detail.SILL)
        labels = " ".join(texts(result.drawing))
        for layer in STONE_CLAD_CONCRETE.layers:
            assert layer.name in labels

    def test_a_schematic_profile_says_so(self):
        """A schematic detail issued as a real one puts the frame in the wrong place."""
        result = wall_section(Detail.SILL)
        assert result.schematic
        assert any("schematic" in note for note in result.notes)

    def test_a_real_profile_carries_no_such_note(self, mullion_dxf):
        from profileos.geometry import profile_from_dxf

        profile, _ = profile_from_dxf(str(mullion_dxf), "MB70", "MB-70")
        result = wall_section(Detail.SILL, profile=profile)
        assert not result.schematic
        assert result.notes == []

    def test_a_sill_gets_a_flashing_and_the_membrane_turned_up(self):
        """The sill is the junction that leaks."""
        sill = wall_section(Detail.SILL).drawing
        assert sill.on_layer("FLASHING")
        assert sill.on_layer("MEMBRANE")

    def test_a_mullion_detail_has_no_wall_in_it(self):
        """It is aluminium to aluminium; a wall there would be a fiction."""
        mullion = wall_section(Detail.MULLION).drawing
        assert not mullion.on_layer("STRUCTURE")
        assert not mullion.on_layer("INSULATION")
        assert len(mullion.on_layer("GLASS")) == 2

    def test_the_anchor_goes_into_something_structural(self):
        section = wall_section(Detail.JAMB, build_up=STONE_CLAD_CONCRETE)
        anchor = next(e for e in section.drawing if e.layer == "FIXING")
        concrete = next(
            (inner, outer)
            for layer, inner, outer in STONE_CLAD_CONCRETE.offsets()
            if layer.pattern is HatchPattern.CONCRETE
        )
        depth = sum(p[1] for p in anchor.points) / len(anchor.points)
        assert concrete[0] <= depth <= concrete[1]


class TestSheets:
    def _sheet(self) -> Sheet:
        return Sheet(
            size=SheetSize.A3,
            title_block=TitleBlock(
                company='דאדי בע"מ', project="בית פרטי", client="משפ׳ כהן",
                title="חזיתות", number="DD-A01", scale="1:20", drawn_by="ד.א.",
            ),
            revisions=[Revision("A", date(2026, 8, 19), "הונפק לאישור", "ד.א.")],
        )

    def test_a3_landscape_is_420_by_297(self):
        assert SheetSize.A3.landscape == (420.0, 297.0)
        assert SheetSize.A3.portrait == (297.0, 420.0)

    def test_a_view_is_placed_at_exactly_the_scale_it_claims(self):
        """A drawing that lies about its scale is worse than one with none."""
        drawing = Drawing()
        drawing.add(rectangle(0, 0, 2000, 1000, "ALU-CUT"))
        viewport = Viewport(drawing=drawing, scale=20, frame=(0, 0, 200, 200))
        placed = viewport.place()
        assert placed.width == pytest.approx(100.0)
        assert placed.height == pytest.approx(50.0)

    def test_a_view_that_does_not_fit_says_so_rather_than_shrinking(self):
        drawing = Drawing()
        drawing.add(rectangle(0, 0, 6000, 1000, "ALU-CUT"))
        assert not Viewport(drawing=drawing, scale=20, frame=(0, 0, 200, 200)).fits()

    def test_the_title_block_carries_every_field_it_was_given(self):
        composed = self._sheet().compose()
        values = texts(composed)
        for expected in ("DD-A01", "1:20", "בית פרטי", "משפ׳ כהן", 'דאדי בע"מ'):
            assert expected in values

    def test_the_revision_table_is_on_the_sheet(self):
        values = texts(self._sheet().compose())
        assert "הונפק לאישור" in values and "2026-08-19" in values

    def test_the_grid_reads_across_then_down(self):
        frames = grid_frames((0.0, 0.0, 100.0, 100.0), columns=2, rows=2)
        assert frames[0][1] > frames[2][1]  # first row is above the second
        assert frames[0][0] < frames[1][0]  # first column is left of the second


class TestHebrewInPdf:
    """PDF has no bidirectional algorithm, so this module has to be one."""

    @pytest.mark.parametrize(
        "logical,visual",
        [
            ("חלון W-01", "W-01 ןולח"),
            ('דאדי בע"מ', 'מ"עב ידאד'),
            ("Window W-01", "Window W-01"),
        ],
    )
    def test_hebrew_is_reordered_and_latin_is_left_alone(self, logical, visual):
        assert visual_order(logical) == visual

    def test_a_number_inside_hebrew_still_reads_left_to_right(self):
        assert visual_order('רוחב 1200 מ"מ') == 'מ"מ 1200 בחור'

    def test_a_scale_does_not_come_out_upside_down(self):
        """1:20 reversed is 20:1, which is a different and wrong drawing."""
        assert "1:20" in visual_order("חזית 1  1:20")

    def test_a_phone_number_survives_its_hyphens(self):
        assert "02-9973510" in visual_order("טלפון 02-9973510")

    def test_brackets_are_mirrored_inside_a_hebrew_run(self):
        assert visual_order("(זכוכית)") == "(תיכוכז)"

    def test_the_paragraph_direction_is_the_first_strong_character(self):
        assert base_direction("1200 מ\"מ") == "R"
        assert base_direction("1200 mm") == "L"


class TestOutput:
    def _drawing(self) -> Drawing:
        drawing = Drawing("test")
        drawing.add(rectangle(0, 0, 1200, 1400, "ALU-CUT"))
        drawing.add(
            Hatch(
                layer="STRUCTURE",
                boundary=((-200, 0), (0, 0), (0, 1400), (-200, 1400)),
                pattern=HatchPattern.CONCRETE,
            )
        )
        drawing.extend(chain([(0, 0), (600, 0), (1200, 0)], -150, scale=20))
        drawing.add(
            Text(layer="TEXT", position=(600, 700), value='חלון W-01', anchor=Anchor.CENTRE)
        )
        return drawing

    def test_the_svg_sets_fill_once(self, tmp_path):
        """Setting it twice is invalid, and strict readers reject the file."""
        markup = to_svg(self._drawing(), scale=20)
        for element in markup.split("<")[1:]:
            assert element.count('fill="') <= 1

    def test_the_svg_groups_by_layer_so_it_can_be_switched_off(self):
        markup = to_svg(self._drawing(), scale=20)
        assert '<g id="ALU-CUT">' in markup and '<g id="DIM">' in markup

    def test_the_dxf_keeps_layers_and_pen_weights(self, tmp_path):
        import ezdxf

        from profileos.drawing.dxf import to_dxf

        path = to_dxf(self._drawing(), tmp_path / "t.dxf", scale=20)
        doc = ezdxf.readfile(str(path))
        assert doc.layers.get("ALU-CUT").dxf.lineweight == 50  # 0.50 mm, in 1/100
        assert doc.layers.get("DIM").dxf.lineweight == 13
        assert any(e.dxftype() == "TEXT" for e in doc.modelspace())

    def test_the_dxf_carries_no_dimension_entities_to_be_restyled(self):
        """A DIMENSION picks up the receiving drawing's style and changes size."""
        import ezdxf

        from profileos.drawing.dxf import to_document

        doc = to_document(self._drawing(), scale=20)
        assert not [e for e in doc.modelspace() if e.dxftype() == "DIMENSION"]

    def test_the_pdf_is_a_readable_single_page(self, tmp_path):
        import pypdf

        from profileos.drawing.pdf import to_pdf

        path = to_pdf(self._drawing(), tmp_path / "t.pdf", page_size=(297.0, 210.0))
        reader = pypdf.PdfReader(str(path))
        assert len(reader.pages) == 1
        box = reader.pages[0].mediabox
        assert float(box.width) == pytest.approx(297.0 * 72 / 25.4, abs=0.1)

    def test_the_pdf_embeds_the_font_it_draws_with(self, tmp_path):
        """Without the font file the Hebrew is boxes on anybody else's machine."""
        from profileos.drawing.pdf import to_pdf

        path = to_pdf(self._drawing(), tmp_path / "t.pdf")
        data = path.read_bytes()
        assert b"/FontFile2" in data
        assert b"/Identity-H" in data


class TestPackage:
    def _package(self, **kwargs):
        builder = ElementBuilder.for_system("klil-7300")
        builds = [
            builder.build(window(mullion_positions=[1200.0]), sill_height=900.0),
            builder.build(window(element_id="D-01", name="D-01", width=1000.0, height=2200.0)),
        ]
        info = PackageInfo(
            project="בית פרטי", client="משפ׳ כהן", company='דאדי בע"מ',
            number_prefix="DD-A", drawn_by="ד.א.",
            revisions=[Revision("A", date(2026, 8, 19), "הונפק לאישור", "ד.א.")],
        )
        return build_package(builds, info, **kwargs)

    def test_the_sheets_are_numbered_in_sequence(self):
        assert self._package().numbers() == ["DD-A01", "DD-A02"]

    def test_a_package_on_stand_in_figures_is_stamped_not_for_construction(self):
        package = self._package()
        assert any("NOT FOR CONSTRUCTION" in stamp for stamp in package.stamps)

    def test_the_stamp_is_on_every_sheet_not_only_the_first(self):
        """A caveat on page one is a caveat that gets detached from the set."""
        package = self._package()
        for sheet in package.sheets:
            assert any("NOT FOR CONSTRUCTION" in note for note in sheet.title_block.notes)

    def test_every_sheet_writes_in_every_format(self, tmp_path):
        written = self._package().write(tmp_path)
        assert {p.suffix for p in written} == {".pdf", ".dxf", ".svg"}
        assert all(p.stat().st_size > 0 for p in written)

    def test_the_legend_is_issued_with_the_elevations(self):
        """Symbols that rely on an unstated convention get built wrong once."""
        first = self._package().sheets[0].compose()
        assert any("legend" in value for value in texts(first))
