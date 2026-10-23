"""Feature recognition tests.

Each section here is drawn by hand so that every dimension the detector reports
has an arithmetic answer to be checked against — a 15 mm groove is 15.000 mm,
not "about 15". The awkward cases are the ones that matter: a slot in the
middle of a flat face, a groove inside the floor of another groove, and a pair
of channels that only mean "thermal break" because they face each other.
"""

from __future__ import annotations

import math

import pytest

from profileos.geometry.contour import Ring
from profileos.geometry.features import (
    COMMON_STRIP_WIDTHS,
    FeatureKind,
    Pocket,
    classify,
    convex_hull_indices,
    describe_section,
    detect_features,
    find_pockets,
    paint_area_per_metre,
)
from profileos.geometry.primitives import rotate
from profileos.geometry.topology import resolve_topology

# --------------------------------------------------------------------------- #
# The sections under test
# --------------------------------------------------------------------------- #
#: 60 x 40 bar with a plain 15 x 13 hardware groove cut into the top face.
EURO = [(0, 0), (60, 0), (60, 40), (35, 40), (35, 27), (20, 27), (20, 40), (0, 40)]

#: 24 mm glazing rebate 15 mm deep, with a 4 x 5 gasket groove in its floor.
REBATE = [
    (0, 0), (60, 0), (60, 40),
    (34, 40), (34, 25), (24, 25), (24, 20), (20, 20), (20, 25), (10, 25), (10, 40),
    (0, 40),
]

#: A 3 mm slit opening into an 8 mm round cavity: a screw port.
SCREW = [
    (0, 0), (60, 0), (60, 40),
    (31.5, 40), (31.5, 38), (34, 38), (34, 32), (26, 32), (26, 38), (28.5, 38), (28.5, 40),
    (0, 40),
]

#: Two shells with channels facing each other 20 mm apart, 3 mm deep each.
SHELL_LEFT = [
    (20, 0), (20, 12), (18.5, 12), (18.5, 11), (17, 11), (17, 16), (18.5, 16),
    (18.5, 15), (20, 15), (20, 30), (0, 30), (0, 0),
]
SHELL_RIGHT = [
    (40, 30), (40, 15), (41.5, 15), (41.5, 16), (43, 16), (43, 11), (41.5, 11),
    (41.5, 12), (40, 12), (40, 0), (60, 0), (60, 30),
]


def only(features, kind: FeatureKind):
    matching = [f for f in features if f.kind is kind]
    assert matching, f"no {kind.value} found in {[f.kind.value for f in features]}"
    return matching[0]


# --------------------------------------------------------------------------- #
class TestHull:
    def test_points_along_a_face_stay_on_the_hull(self):
        """The whole detector rests on this: a flat face is not one edge."""
        square = [(0, 0), (5, 0), (10, 0), (10, 10), (0, 10)]
        assert len(convex_hull_indices(square)) == 5
        assert len(convex_hull_indices(square, keep_collinear=False)) == 4

    def test_a_kinked_face_is_still_one_face(self):
        """Drawing noise of a hundredth of a millimetre is not a feature."""
        nearly = [(0, 0), (5, 0.01), (10, 0), (10, 10), (0, 10)]
        assert len(convex_hull_indices(nearly, tolerance=0.05)) == 5

    def test_a_convex_outline_has_no_pockets(self):
        assert find_pockets([(0, 0), (60, 0), (60, 40), (0, 40)]) == []


class TestMeasurement:
    def test_a_groove_is_measured_exactly(self):
        pocket, = find_pockets(EURO)
        assert pocket.mouth == pytest.approx(15.0)
        assert pocket.depth == pytest.approx(13.0)
        assert pocket.area == pytest.approx(15.0 * 13.0)
        assert pocket.undercut == pytest.approx(0.0)

    def test_the_mouth_points_out_of_the_material(self):
        pocket, = find_pockets(EURO)
        assert pocket.direction == pytest.approx((0.0, 1.0))
        assert pocket.centre == pytest.approx((27.5, 40.0))

    def test_the_bands_account_for_the_whole_void(self):
        """A stepped pocket is described exactly, not sampled approximately."""
        pocket, = find_pockets(REBATE)
        covered = sum(band.open_width * band.height for band in pocket.bands)
        assert covered == pytest.approx(24 * 15 + 4 * 5)

    def test_a_stepped_pocket_reads_as_two_cuts(self):
        pocket, = find_pockets(REBATE)
        assert [(round(s.span, 3), round(s.height, 3)) for s in pocket.steps] == [
            (24.0, 15.0),
            (4.0, 5.0),
        ]

    def test_an_undercut_is_the_cavity_less_its_own_mouth(self):
        pocket, = find_pockets(SCREW)
        assert pocket.mouth == pytest.approx(3.0)
        assert pocket.widest == pytest.approx(8.0)
        assert pocket.undercut == pytest.approx(5.0)

    def test_the_floor_is_the_middle_of_the_deepest_cut(self):
        pocket, = find_pockets(REBATE)
        assert pocket.floor == pytest.approx((22.0, 20.0))

    @pytest.mark.parametrize("angle", [0.0, 37.0, 90.0, 213.5])
    def test_measurements_do_not_depend_on_how_the_part_was_drawn(self, angle):
        """A section rotated in the DXF must give the same groove."""
        turned = [rotate(p, math.radians(angle), (13.0, -4.0)) for p in EURO]
        pocket, = find_pockets(turned)
        assert pocket.mouth == pytest.approx(15.0)
        assert pocket.depth == pytest.approx(13.0)

    def test_a_clockwise_drawing_reads_the_same_as_a_counter_clockwise_one(self):
        forward, = find_pockets(EURO)
        reverse, = find_pockets(list(reversed(EURO)))
        assert reverse.mouth == pytest.approx(forward.mouth)
        assert reverse.depth == pytest.approx(forward.depth)
        assert reverse.direction == pytest.approx(forward.direction)


class TestNaming:
    def test_a_hardware_groove_is_recognised(self):
        feature = only(detect_features(EURO), FeatureKind.EURO_GROOVE)
        assert feature.pocket.mouth == pytest.approx(15.0)
        assert feature.kind.hebrew == "חריץ אירו"

    def test_a_rebate_reports_the_glass_it_takes(self):
        feature = only(detect_features(REBATE), FeatureKind.GLAZING_REBATE)
        assert feature.glass_capacity == pytest.approx(24.0)
        assert feature.bite == pytest.approx(15.0)

    def test_a_slit_into_a_cavity_is_a_screw_port_not_a_gasket_groove(self):
        """Both are small undercut slots; only the cavity tells them apart."""
        feature = only(detect_features(SCREW), FeatureKind.SCREW_PORT)
        assert feature.pocket.widest / feature.pocket.mouth > 2.0

    def test_a_plain_small_groove_is_a_gasket_groove(self):
        outline = [(0, 0), (60, 0), (60, 40), (32, 40), (32, 35), (28, 35), (28, 40), (0, 40)]
        only(detect_features(outline), FeatureKind.GASKET_GROOVE)

    def test_an_unmatched_pocket_is_reported_rather_than_dropped(self):
        """Nothing the table fails to name is allowed to disappear."""
        # 40 mm wide and 2.5 mm deep: too wide for any groove, too shallow for
        # a rebate. It is still a recess, and the operator still gets its size.
        outline = [(0, 0), (60, 0), (60, 40), (50, 40), (50, 37.5), (10, 37.5), (10, 40), (0, 40)]
        features = detect_features(outline)
        assert [f.kind for f in features] == [FeatureKind.POCKET]
        assert features[0].pocket.mouth == pytest.approx(40.0)
        assert features[0].pocket.depth == pytest.approx(2.5)

    def test_a_chamfer_is_not_a_feature(self):
        outline = [(0, 0), (60, 0), (60, 40), (31, 40), (30, 39.5), (29, 40), (0, 40)]
        assert detect_features(outline) == []


class TestThermalBreak:
    def _both_shells(self):
        pockets = [classify(p) for p in find_pockets(SHELL_LEFT)]
        pockets += [classify(p) for p in find_pockets(SHELL_RIGHT)]
        from profileos.geometry.features import _pair_thermal_break

        return _pair_thermal_break(pockets)

    def test_facing_channels_are_a_thermal_break(self):
        features = self._both_shells()
        channels = [f for f in features if f.kind is FeatureKind.THERMAL_BREAK_CHANNEL]
        assert len(channels) == 2
        # 20 mm clear between the faces, rolled 3 mm into each side.
        assert all(c.strip_width == pytest.approx(26.0) for c in channels)
        assert 26.0 in COMMON_STRIP_WIDTHS

    def test_a_lone_channel_is_not_a_thermal_break(self):
        """Otherwise every small undercut groove puts polyamide on the order."""
        features = detect_features(SHELL_LEFT)
        assert [f.kind for f in features] == [FeatureKind.GASKET_GROOVE]
        assert features[0].strip_width is None

    def test_channels_that_do_not_face_each_other_are_not_paired(self):
        """Two grooves on the same side of the gap are not a strip."""
        mirrored = [(120 - x, y) for x, y in SHELL_RIGHT]
        from profileos.geometry.features import _pair_thermal_break

        pockets = [classify(p) for p in find_pockets(SHELL_LEFT)]
        pockets += [classify(p) for p in find_pockets(mirrored)]
        assert not [
            f for f in _pair_thermal_break(pockets)
            if f.kind is FeatureKind.THERMAL_BREAK_CHANNEL
        ]


class TestWholeSection:
    def _hollow_box(self):
        outer = Ring(points=[(0, 0), (60, 0), (60, 100), (0, 100)])
        inner = Ring(points=[(2, 2), (58, 2), (58, 98), (2, 98)])
        return resolve_topology([outer, inner])

    def test_the_inside_of_a_chamber_is_never_painted(self):
        """Counting it would overstate the coating line on every quotation."""
        topology = self._hollow_box()
        assert paint_area_per_metre(topology) == pytest.approx(320.0 / 1000.0)

    def test_the_headline_numbers_come_off_the_drawing(self):
        topology = self._hollow_box()
        area = 60 * 100 - 56 * 96
        report = describe_section(topology, mass_per_metre=area * 1e-6 * 2700)
        assert report.width == pytest.approx(60.0)
        assert report.height == pytest.approx(100.0)
        assert report.mass_per_metre == pytest.approx(624 * 1e-6 * 2700)
        assert report.paint_area_per_metre == pytest.approx(0.320)

    def test_a_round_chamber_of_the_right_size_is_a_screw_port(self):
        outer = Ring(points=[(0, 0), (60, 0), (60, 60), (0, 60)])
        bore = Ring(
            points=[
                (30 + 3 * math.cos(math.tau * i / 64), 30 + 3 * math.sin(math.tau * i / 64))
                for i in range(64)
            ]
        )
        report = describe_section(resolve_topology([outer, bore]), mass_per_metre=1.0)
        assert len(report.screw_ports) == 1
        centre, diameter = report.screw_ports[0]
        assert centre == pytest.approx((30.0, 30.0))
        assert diameter == pytest.approx(6.0, abs=0.02)

    def test_a_large_chamber_is_not_a_screw_port(self):
        report = describe_section(self._hollow_box(), mass_per_metre=1.0)
        assert report.screw_ports == []

    def test_a_section_drawn_in_two_parts_without_channels_says_so(self):
        left = Ring(points=[(0, 0), (20, 0), (20, 30), (0, 30)])
        right = Ring(points=[(40, 0), (60, 0), (60, 30), (40, 30)])
        report = describe_section(resolve_topology([left, right]), mass_per_metre=1.0)
        assert any("polyamide" in warning for warning in report.warnings)

    def test_the_summary_is_in_the_shop_s_language(self):
        report = describe_section(self._hollow_box(), mass_per_metre=1.685)
        labels = [label for label, _ in report.summary_rows()]
        assert "משקל" in labels and "שטח צביעה" in labels


class TestStripsDrawnAsParts:
    """Most suppliers draw the polyamide bar as its own closed region."""

    def _thermal_section(self, layer: str = "THERMAL"):
        shells = [
            Ring(points=[(0, 0), (62, 0), (62, 30), (0, 30)], source_layers={"PROFILE"}),
            Ring(points=[(0, 54), (62, 54), (62, 80), (0, 80)], source_layers={"PROFILE"}),
            Ring(points=[(8, 30), (16, 30), (16, 54), (8, 54)], source_layers={layer}),
            Ring(points=[(46, 30), (54, 30), (54, 54), (46, 54)], source_layers={layer}),
        ]
        return resolve_topology(shells)

    def test_the_strip_width_is_measured_across_the_gap(self):
        """24 mm across, 8 mm thick — the 24 is the size that gets ordered."""
        report = describe_section(self._thermal_section(), mass_per_metre=1.0)
        assert report.thermal_break_width == pytest.approx(24.0)
        assert len(report.strips) == 2
        assert all(strip.width == pytest.approx(24.0) for strip in report.strips)
        assert not report.warnings

    def test_the_layer_name_is_evidence_but_not_the_only_evidence(self):
        report = describe_section(self._thermal_section(layer="0"), mass_per_metre=1.0)
        assert report.thermal_break_width == pytest.approx(24.0)
        assert report.strips[0].evidence == ("between two shells",)

    def test_a_part_beside_the_shells_is_not_a_strip(self):
        """Something drawn alongside the section is not bridging anything."""
        shells = [
            Ring(points=[(0, 0), (62, 0), (62, 30), (0, 30)]),
            Ring(points=[(0, 54), (62, 54), (62, 80), (0, 80)]),
            Ring(points=[(80, 0), (88, 0), (88, 24), (80, 24)], source_layers={"THERMAL"}),
        ]
        report = describe_section(resolve_topology(shells), mass_per_metre=1.0)
        assert report.strips == []
        assert report.thermal_break_width is None
        assert report.warnings

    def test_a_solid_island_inside_a_chamber_is_not_a_second_shell(self):
        """A screw boss is not half a thermally broken profile."""
        outer = Ring(points=[(0, 0), (60, 0), (60, 60), (0, 60)])
        chamber = Ring(points=[(3, 3), (57, 3), (57, 57), (3, 57)])
        boss = Ring(points=[(28, 28), (32, 28), (32, 32), (28, 32)])
        report = describe_section(resolve_topology([outer, chamber, boss]), mass_per_metre=1.0)
        assert report.warnings == []


class TestRealDrawings:
    """The sample DXFs, read end to end the way an import does."""

    def test_a_thermally_broken_frame_reports_its_strip(self, thermal_dxf):
        from profileos.geometry import load_section
        from profileos.geometry.features import features_for_section

        report = features_for_section(load_section(str(thermal_dxf)))
        assert report.thermal_break_width == pytest.approx(24.0)
        assert report.mass_per_metre > 0
        assert report.paint_area_per_metre > 0

    def test_a_bead_reports_the_glass_it_holds(self, bead_dxf):
        from profileos.geometry import load_section
        from profileos.geometry.features import features_for_section

        report = features_for_section(load_section(str(bead_dxf)))
        assert report.glass_capacity is not None
        assert report.of_kind(FeatureKind.GLAZING_REBATE)

    def test_screw_bosses_are_found_in_the_mullion(self, mullion_dxf):
        from profileos.geometry import load_section
        from profileos.geometry.features import features_for_section

        report = features_for_section(load_section(str(mullion_dxf)))
        assert len(report.screw_ports) == 2
        assert report.warnings == []
