"""Structural engine verification against closed-form solutions.

Every value checked here has an exact analytical counterpart, so a regression
in the integration, the parallel-axis shift, the principal rotation or the
plastic bisection shows up as a hard failure rather than a drifting number.
"""

from __future__ import annotations

import math

import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon

from profileos.structural import (
    LoadCase,
    SupportCondition,
    analyse_section,
    check_member,
    maximum_span,
    plastic_modulus_x,
    plastic_modulus_y,
    ring_moments,
    transformed_section_properties,
    wind_line_load,
)
from profileos.models.materials import get_material


# --------------------------------------------------------------------------- #
# Green's theorem
# --------------------------------------------------------------------------- #

class TestGreensTheorem:
    def test_counter_clockwise_ring_has_positive_area(self):
        moments = ring_moments([(0, 0), (10, 0), (10, 10), (0, 10)])
        assert moments.area == pytest.approx(100.0)

    def test_clockwise_ring_has_negative_area(self):
        """A hole must subtract itself purely through its winding."""
        moments = ring_moments([(0, 0), (0, 10), (10, 10), (10, 0)])
        assert moments.area == pytest.approx(-100.0)

    def test_degenerate_ring_is_zero(self):
        assert ring_moments([(0, 0), (1, 1)]).area == 0.0

    def test_first_moments_locate_the_centroid(self):
        moments = ring_moments([(2, 5), (12, 5), (12, 25), (2, 25)])
        cx, cy = moments.centroid
        assert cx == pytest.approx(7.0)
        assert cy == pytest.approx(15.0)


class TestSolidRectangle:
    """A 40 x 120 rectangle, where every property is textbook."""

    B, H = 40.0, 120.0

    @pytest.fixture(scope="class")
    def props(self):
        polygon = Polygon([(0, 0), (self.B, 0), (self.B, self.H), (0, self.H)])
        return analyse_section(polygon)

    def test_area(self, props):
        assert props.area == pytest.approx(self.B * self.H)

    def test_centroid(self, props):
        assert props.centroid_x == pytest.approx(self.B / 2)
        assert props.centroid_y == pytest.approx(self.H / 2)

    def test_second_moments(self, props):
        assert props.ixx == pytest.approx(self.B * self.H**3 / 12)
        assert props.iyy == pytest.approx(self.H * self.B**3 / 12)

    def test_product_of_inertia_vanishes_for_a_symmetric_section(self, props):
        assert props.ixy == pytest.approx(0.0, abs=1e-6)

    def test_elastic_modulus(self, props):
        assert props.sx == pytest.approx(self.B * self.H**2 / 6)
        assert props.sy == pytest.approx(self.H * self.B**2 / 6)

    def test_plastic_modulus(self, props):
        assert props.zx == pytest.approx(self.B * self.H**2 / 4, rel=1e-7)
        assert props.zy == pytest.approx(self.H * self.B**2 / 4, rel=1e-7)

    def test_shape_factor_is_three_halves(self, props):
        assert props.shape_factor_x == pytest.approx(1.5, rel=1e-6)

    def test_radii_of_gyration(self, props):
        assert props.rx == pytest.approx(self.H / math.sqrt(12))
        assert props.ry == pytest.approx(self.B / math.sqrt(12))

    def test_plastic_neutral_axis_coincides_with_centroid_when_symmetric(self, props):
        assert props.plastic_neutral_axis_y == pytest.approx(self.H / 2, abs=1e-6)


class TestHollowTube:
    """100 x 50 rectangular tube with a 3 mm wall."""

    @pytest.fixture(scope="class")
    def polygon(self):
        return Polygon(
            [(0, 0), (100, 0), (100, 50), (0, 50)],
            [[(3, 3), (97, 3), (97, 47), (3, 47)]],
        )

    @pytest.fixture(scope="class")
    def props(self, polygon):
        return analyse_section(polygon)

    def test_area_subtracts_the_chamber(self, props):
        assert props.area == pytest.approx(100 * 50 - 94 * 44)

    def test_second_moments(self, props):
        assert props.ixx == pytest.approx((100 * 50**3 - 94 * 44**3) / 12)
        assert props.iyy == pytest.approx((50 * 100**3 - 44 * 94**3) / 12)

    def test_torsion_constant_is_near_the_bredt_value(self, props):
        """Bredt underestimates a thin-walled tube by a few per cent."""
        mid_area, mid_perimeter, t = 97 * 47, 2 * (97 + 47), 3.0
        bredt = 4 * mid_area**2 * t / mid_perimeter
        assert props.j == pytest.approx(bredt, rel=0.05)
        assert props.j > bredt  # the FEA value is the higher, exact one

    def test_shear_centre_of_a_doubly_symmetric_section_is_the_centroid(self, props):
        if props.shear_centre_x is None:
            pytest.skip("warping analysis unavailable")
        assert props.shear_centre_x == pytest.approx(props.centroid_x, abs=1e-3)
        assert props.shear_centre_y == pytest.approx(props.centroid_y, abs=1e-3)

    def test_torsion_used_finite_elements(self, props):
        assert props.torsion_method == "fea"
        assert props.mesh_element_count and props.mesh_element_count > 0


class TestCircle:
    """A discretised circle, checked against the exact circular formulae."""

    R = 25.0

    @pytest.fixture(scope="class")
    def props(self):
        return analyse_section(Point(0, 0).buffer(self.R, quad_segs=512))

    def test_area(self, props):
        assert props.area == pytest.approx(math.pi * self.R**2, rel=1e-5)

    def test_second_moment(self, props):
        assert props.ixx == pytest.approx(math.pi * self.R**4 / 4, rel=1e-5)

    def test_plastic_modulus(self, props):
        assert props.zx == pytest.approx(4 * self.R**3 / 3, rel=1e-5)

    def test_shape_factor(self, props):
        assert props.shape_factor_x == pytest.approx(16 / (3 * math.pi), rel=1e-4)


class TestPrincipalAxes:
    """A 20 x 100 rectangle rotated through a range of angles."""

    @staticmethod
    def _rotated(angle: float):
        base = Polygon([(0, 0), (20, 0), (20, 100), (0, 100)])
        return affinity.rotate(base, angle, origin="centroid")

    @pytest.mark.parametrize("angle", [0.0, 15.0, 30.0, 45.0, -30.0, 75.0, 89.0])
    def test_principal_angle_tracks_the_rotation(self, angle):
        props = analyse_section(
            self._rotated(angle), compute_plastic=False, compute_torsion_constants=False
        )
        # The unrotated section's major axis is x (it is tall), so the reported
        # angle should follow the applied rotation, wrapped into (-90, 90].
        expected = ((angle + 90.0) % 180.0) - 90.0
        assert props.principal_angle == pytest.approx(expected, abs=1e-6)

    @pytest.mark.parametrize("angle", [0.0, 37.0, -52.0])
    def test_principal_moments_are_rotation_invariant(self, angle):
        props = analyse_section(
            self._rotated(angle), compute_plastic=False, compute_torsion_constants=False
        )
        assert props.i11 == pytest.approx(20 * 100**3 / 12, rel=1e-9)
        assert props.i22 == pytest.approx(100 * 20**3 / 12, rel=1e-9)

    def test_trace_is_invariant(self):
        """I_x + I_y is a rotation invariant of the inertia tensor."""
        base = analyse_section(
            self._rotated(0), compute_plastic=False, compute_torsion_constants=False
        )
        turned = analyse_section(
            self._rotated(37), compute_plastic=False, compute_torsion_constants=False
        )
        assert base.ixx + base.iyy == pytest.approx(turned.ixx + turned.iyy, rel=1e-12)


class TestPlasticNeutralAxis:
    def test_pna_splits_a_tee_into_equal_areas(self):
        """An unsymmetric tee: the PNA must not coincide with the centroid."""
        tee = Polygon([(0, 0), (60, 0), (60, 10), (35, 10), (35, 80), (25, 80), (25, 10), (0, 10)])
        result = plastic_modulus_x(tee)
        assert result.converged
        assert result.area_above == pytest.approx(result.area_below, rel=1e-6)
        assert result.balance_error < 1e-6

        props = analyse_section(tee, compute_torsion_constants=False)
        assert result.axis_position != pytest.approx(props.centroid_y, abs=0.5)

    def test_plastic_modulus_exceeds_elastic(self):
        tee = Polygon([(0, 0), (60, 0), (60, 10), (35, 10), (35, 80), (25, 80), (25, 10), (0, 10)])
        props = analyse_section(tee, compute_torsion_constants=False)
        assert props.zx > props.sx

    def test_vertical_pna_of_a_symmetric_section(self, rect_polygon):
        result = plastic_modulus_y(rect_polygon(80.0, 20.0))
        assert result.axis_position == pytest.approx(40.0, abs=1e-6)


class TestCompositeSection:
    def test_transformed_properties_lie_between_the_pure_materials(self):
        """A polyamide strip contributes less stiffness than aluminium would."""
        alu = get_material("en-aw-6060-t66")
        polyamide = get_material("pa66-gf25")

        top = Polygon([(0, 30), (60, 30), (60, 50), (0, 50)])
        bottom = Polygon([(0, 0), (60, 0), (60, 20), (0, 20)])
        strip = Polygon([(0, 20), (60, 20), (60, 30), (0, 30)])

        composite = transformed_section_properties(
            [(top, alu), (bottom, alu), (strip, polyamide)]
        )
        all_alu = transformed_section_properties(
            [(top, alu), (bottom, alu), (strip, alu)]
        )
        no_strip = transformed_section_properties([(top, alu), (bottom, alu)])

        assert no_strip.ixx < composite.ixx < all_alu.ixx

    def test_real_area_is_the_untransformed_area(self):
        alu = get_material("en-aw-6060-t66")
        square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        props = transformed_section_properties([(square, alu)])
        assert props.area == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# Design checks
# --------------------------------------------------------------------------- #

class TestMemberChecks:
    @pytest.fixture(scope="class")
    def mullion(self):
        polygon = Polygon(
            [(0, 0), (70, 0), (70, 120), (0, 120)],
            [[(2.5, 2.5), (67.5, 2.5), (67.5, 117.5), (2.5, 117.5)]],
        )
        return analyse_section(polygon), get_material("en-aw-6060-t66")

    def test_wind_line_load_conversion(self):
        # 1.0 kN/m^2 over a 1500 mm tributary width is 1.5 N/mm.
        assert wind_line_load(1.0, 1500.0) == pytest.approx(1.5)

    def test_short_span_passes(self, mullion):
        props, material = mullion
        check = check_member(
            props,
            material,
            span=2000.0,
            load=LoadCase(lateral_line_load=wind_line_load(1.2, 1500.0)),
        )
        assert check.passes
        assert check.max_utilisation < 1.0

    def test_long_span_fails(self, mullion):
        props, material = mullion
        check = check_member(
            props,
            material,
            span=9000.0,
            load=LoadCase(lateral_line_load=wind_line_load(1.5, 1500.0)),
        )
        assert not check.passes

    def test_deflection_usually_governs_a_facade_mullion(self, mullion):
        props, material = mullion
        check = check_member(
            props,
            material,
            span=4000.0,
            load=LoadCase(lateral_line_load=wind_line_load(1.2, 1500.0)),
        )
        assert check.governing is not None
        assert check.governing.name == "Deflection"

    def test_maximum_span_is_the_boundary_of_passing(self, mullion):
        props, material = mullion
        span = maximum_span(
            props, material, pressure_kn_m2=1.2, tributary_width_mm=1500.0
        )
        assert span > 0
        load = LoadCase(lateral_line_load=wind_line_load(1.2, 1500.0))
        assert check_member(props, material, span=span - 5.0, load=load).passes
        assert not check_member(props, material, span=span + 10.0, load=load).passes

    def test_support_condition_changes_the_result(self, mullion):
        props, material = mullion
        load = LoadCase(lateral_line_load=wind_line_load(1.2, 1500.0))
        simple = check_member(props, material, span=4000.0, load=load)
        fixed = check_member(
            props,
            material,
            span=4000.0,
            load=load,
            support=SupportCondition.FIXED_BOTH_ENDS,
        )
        # Fixing both ends cuts the mid-span moment and deflection sharply.
        assert fixed.max_utilisation < simple.max_utilisation
