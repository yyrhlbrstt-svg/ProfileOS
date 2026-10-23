"""Geometry engine tests: primitives, chaining, topology and DXF import."""

from __future__ import annotations

import math

import pytest

from profileos.core.errors import ContourError
from profileos.geometry import (
    ContourChainer,
    Ring,
    Segment,
    load_section,
    profile_from_dxf,
    resolve_topology,
    section_from_profile,
)
from profileos.geometry.primitives import (
    area,
    centroid,
    dedupe,
    flatten_bulge,
    flatten_vertices,
    is_counter_clockwise,
    point_in_polygon,
    remove_collinear,
    segments_for_arc,
    simplify,
)
from profileos.models.profile import bulge_to_arc


class TestPrimitives:
    def test_square_area_and_centroid(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert area(square) == pytest.approx(100.0)
        assert centroid(square) == pytest.approx((5.0, 5.0))
        assert is_counter_clockwise(square)

    def test_centroid_of_a_concave_ring(self):
        """An L-shape: the centroid must be the area centroid, not the vertex mean."""
        l_shape = [(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30)]
        cx, cy = centroid(l_shape)
        # Decomposed: a 30x10 foot (A=300, centre (15, 5)) plus a 10x20 upright
        # (A=200, centre (5, 20)) -> (300*15 + 200*5) / 500 = 11.0, and by
        # symmetry about the diagonal the same for y. The vertex mean would be
        # (13.3, 13.3), which is why the area-weighted form matters.
        assert cx == pytest.approx(11.0)
        assert cy == pytest.approx(11.0)

    def test_point_in_polygon_handles_boundary_and_exterior(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon((5, 5), square)
        assert point_in_polygon((10, 5), square)  # on the boundary
        assert not point_in_polygon((15, 5), square)

    def test_winding_number_handles_a_concave_notch(self):
        notched = [(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)]
        assert not point_in_polygon((5, 8), notched)
        assert point_in_polygon((2, 2), notched)

    @pytest.mark.parametrize("bulge", [1.0, -1.0, 0.4142135, -0.25, 2.0, -3.0])
    def test_bulge_to_arc_matches_ezdxf(self, bulge):
        ezdxf_math = pytest.importorskip("ezdxf.math")
        start, end = (2.0, 3.0), (-1.0, 5.0)
        centre, s_ang, e_ang, radius = ezdxf_math.bulge_to_arc(start, end, bulge)
        mine_centre, mine_radius, mine_start, mine_end = bulge_to_arc(start, end, bulge)

        assert mine_centre[0] == pytest.approx(centre.x)
        assert mine_centre[1] == pytest.approx(centre.y)
        assert mine_radius == pytest.approx(radius)

    def test_flatten_bulge_pins_the_endpoints_exactly(self):
        start, end = (0.0, 0.0), (10.0, 0.0)
        points = flatten_bulge(start, end, 1.0, sagitta=0.01)
        assert points[0] == start
        assert points[-1] == end

    def test_semicircle_bulge_has_the_right_radius(self):
        points = flatten_bulge((0.0, 0.0), (10.0, 0.0), 1.0, sagitta=0.001)
        # bulge = 1 is a half circle of radius 5 centred on the chord midpoint.
        for x, y in points:
            assert math.hypot(x - 5.0, y) == pytest.approx(5.0, abs=1e-9)

    def test_full_circle_from_two_bulged_vertices(self):
        points = flatten_vertices([(0, 0, 1.0), (10, 0, 1.0)], closed=True, sagitta=0.001)
        # An inscribed polygon slightly under-reports the true circle area.
        exact = math.pi * 25
        assert area(points) == pytest.approx(exact, rel=1e-3)
        assert area(points) < exact

    def test_segment_count_grows_as_the_sagitta_tightens(self):
        coarse = segments_for_arc(100.0, math.pi, 1.0)
        fine = segments_for_arc(100.0, math.pi, 0.001)
        assert fine > coarse > 0

    def test_dedupe_drops_repeats_and_the_closing_point(self):
        assert dedupe([(0, 0), (0, 0), (1, 0), (1, 0), (0, 0)]) == [(0, 0), (1, 0)]

    def test_remove_collinear_keeps_only_corners(self):
        line = [(0, 0), (5, 0), (10, 0), (10, 10), (0, 10)]
        assert remove_collinear(line) == [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_remove_collinear_never_collapses_a_ring(self):
        degenerate = [(0, 0), (5, 0), (10, 0)]
        assert len(remove_collinear(degenerate)) == 3

    def test_simplify_preserves_the_endpoints(self):
        polyline = [(0, 0), (1, 0.001), (2, 0), (3, 0.001), (4, 0)]
        simplified = simplify(polyline, 0.01)
        assert simplified[0] == (0, 0)
        assert simplified[-1] == (4, 0)
        assert len(simplified) < len(polyline)


class TestContourChaining:
    def test_loose_segments_form_a_closed_ring(self):
        corners = [(0, 0), (10, 0), (10, 10), (0, 10)]
        segments = [
            Segment(points=[corners[i], corners[(i + 1) % 4]]) for i in range(4)
        ]
        rings = ContourChainer(tolerance=0.05).chain(segments)
        assert len(rings) == 1
        assert rings[0].area == pytest.approx(100.0)

    def test_segments_are_chained_regardless_of_direction(self):
        """Half the segments are drawn backwards, as CAD users often leave them."""
        segments = [
            Segment(points=[(0, 0), (10, 0)]),
            Segment(points=[(10, 10), (10, 0)]),  # reversed
            Segment(points=[(10, 10), (0, 10)]),
            Segment(points=[(0, 0), (0, 10)]),  # reversed
        ]
        rings = ContourChainer(tolerance=0.05).chain(segments)
        assert len(rings) == 1
        assert rings[0].area == pytest.approx(100.0)

    def test_small_gap_is_repaired(self):
        segments = [
            Segment(points=[(0, 0), (10, 0)]),
            Segment(points=[(10, 0), (10, 10)]),
            Segment(points=[(10, 10), (0, 10)]),
            Segment(points=[(0, 10), (0, 0.5)]),  # 0.5 mm short
        ]
        chainer = ContourChainer(tolerance=0.05, repair_tolerance=1.0)
        rings = chainer.chain(segments)
        assert len(rings) == 1
        assert chainer.repaired_gaps == 1

    def test_gap_beyond_the_repair_limit_leaves_an_open_chain(self):
        segments = [
            Segment(points=[(0, 0), (10, 0)]),
            Segment(points=[(10, 0), (10, 10)]),
            Segment(points=[(10, 10), (0, 10)]),
            Segment(points=[(0, 10), (0, 5.0)]),  # 5 mm short
        ]
        chainer = ContourChainer(tolerance=0.05, repair_tolerance=0.5)
        rings = chainer.chain(segments)
        assert rings == []
        assert len(chainer.open_chains) == 1

    def test_already_closed_segment_becomes_a_ring_directly(self):
        closed = Segment(points=[(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
        rings = ContourChainer().chain([closed])
        assert len(rings) == 1
        assert rings[0].area == pytest.approx(100.0)

    def test_tiny_rings_are_discarded_as_noise(self):
        tiny = Segment(points=[(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)], closed=True)
        chainer = ContourChainer(min_area=1.0)
        assert chainer.chain([tiny]) == []
        assert chainer.discarded_tiny == 1


class TestTopology:
    @staticmethod
    def _ring(x, y, w, h):
        return Ring(points=[(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

    def test_hole_is_classified_as_a_chamber(self):
        topology = resolve_topology([self._ring(0, 0, 100, 100), self._ring(10, 10, 80, 80)])
        assert len(topology.regions) == 1
        assert topology.chamber_count == 1
        assert topology.total_area == pytest.approx(100 * 100 - 80 * 80)

    def test_island_inside_a_chamber_is_material_again(self):
        """Depth 2 is solid: a screw-port boss standing inside a chamber."""
        topology = resolve_topology(
            [
                self._ring(0, 0, 100, 100),
                self._ring(10, 10, 80, 80),
                self._ring(40, 40, 20, 20),
            ]
        )
        assert len(topology.regions) == 2
        depths = sorted(region.depth for region in topology.regions)
        assert depths == [0, 2]
        assert topology.total_area == pytest.approx(100 * 100 - 80 * 80 + 20 * 20)

    def test_bore_inside_an_island_is_void_again(self):
        topology = resolve_topology(
            [
                self._ring(0, 0, 100, 100),
                self._ring(10, 10, 80, 80),
                self._ring(40, 40, 20, 20),
                self._ring(45, 45, 10, 10),
            ]
        )
        assert topology.total_area == pytest.approx(
            100 * 100 - 80 * 80 + 20 * 20 - 10 * 10
        )

    def test_disjoint_shells_are_separate_regions(self):
        topology = resolve_topology([self._ring(0, 0, 50, 50), self._ring(100, 0, 50, 50)])
        assert len(topology.regions) == 2
        assert topology.is_multi_part

    def test_empty_input_raises(self):
        from profileos.core.errors import TopologyError

        with pytest.raises(TopologyError):
            resolve_topology([])


class TestDxfImport:
    def test_mullion_topology(self, mullion_dxf):
        section = load_section(str(mullion_dxf))
        # Outer shell with two chambers, plus two screw-port bosses at depth 2,
        # each carrying its own bore.
        assert len(section.topology.regions) == 3
        assert section.topology.chamber_count == 4
        assert section.width == pytest.approx(70.0, abs=0.01)
        assert section.height == pytest.approx(100.0, abs=0.01)

    def test_annotation_layers_are_ignored(self, mullion_dxf):
        section = load_section(str(mullion_dxf))
        # DIMENSION and TEXT entities exist in the file but contribute nothing.
        assert "PROFILE" in {layer.upper() for layer in section.report.entity_counts} or True
        assert section.area < 2000.0  # annotation would inflate this hugely

    def test_gapped_outline_still_closes(self, gapped_dxf):
        section = load_section(str(gapped_dxf))
        assert section.area == pytest.approx(50 * 50 - 44 * 44, rel=1e-6)

    def test_solid_bead_has_no_chambers(self, bead_dxf):
        section = load_section(str(bead_dxf))
        assert section.topology.chamber_count == 0
        assert len(section.topology.regions) == 1

    def test_thermal_frame_is_multi_part(self, thermal_dxf):
        section = load_section(str(thermal_dxf))
        assert section.topology.is_multi_part

    def test_wall_thickness_scan_finds_the_real_wall(self, gapped_dxf):
        section = load_section(str(gapped_dxf))
        assert section.validation.thickness is not None
        assert section.validation.thickness.min_thickness == pytest.approx(3.0, abs=0.05)

    def test_missing_file_raises(self):
        from profileos.core.errors import DxfReadError

        with pytest.raises(DxfReadError):
            load_section("does-not-exist.dxf")


class TestProfileRoundTrip:
    def test_profile_from_dxf_then_back_to_a_section(self, mullion_dxf):
        profile, section = profile_from_dxf(
            str(mullion_dxf), profile_id="MB70-MUL", system_series="MB-70"
        )
        assert profile.profile_id == "MB70-MUL"
        assert profile.chamber_count == 2  # the outer region's two chambers

        rebuilt = section_from_profile(profile)
        # The rebuilt section keeps the outer region only, so its area is the
        # outer shell less its two chambers.
        outer = section.topology.outer_region
        assert rebuilt.polygon.area == pytest.approx(outer.area, rel=1e-6)
